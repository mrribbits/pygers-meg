"""Run the Pygers-MEG notebooks for one subject/session, with HTML reports.

Normally started by run_bids.sh (which submits it to Slurm), but it also runs on its own,
in the pygers-meg environment, from any folder:

    python pipeline/run_pipeline.py /path/to/bids-config/subjects/sub-01.yaml
    python pipeline/run_pipeline.py .../sub-01.yaml --steps 04,05   # only these (plus the rules below)
    python pipeline/run_pipeline.py .../sub-01.yaml --check         # check everything, run nothing

What it does:
  1. Reads and checks the configuration (bids_config.py), finds the raw files, and checks
     that the software each chosen notebook needs is there and starts (pre-flight). Any
     problem stops it here, before anything is changed, with the full list.
  2. Decides which notebooks run: those turned on under "steps:" in study.yaml (or given
     with --steps), minus those whose data the study doesn't have. If 04 runs, 05 runs too
     (04 rewrites a file 05 completes), and 06 (the validation) always runs last.
  3. Runs each notebook with papermill. Each one reads its settings from one settings file
     written for this run. A notebook that fails doesn't stop the others, but the notebooks
     that need its output are skipped.
  4. Saves, in <session>/reports/<date-time>/: each executed notebook, an HTML report of
     each, index.html (what ran, how long, what failed, the validator's counts), the
     settings file, a copy of the configuration files, and the log.

The steps that write into the BIDS dataset (03-06) take a lock on it first, so two jobs
for different subjects don't write the shared files (participants.tsv, the MRI naming
rules, ...) at the same time; the second one waits.

Exit status: 0 = every chosen notebook ran without error; 1 = at least one failed or was
skipped because something it needs is missing; 2 = the pre-flight checks failed.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import getpass
import html
import importlib.util
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent
REPO_DIR = PIPELINE_DIR.parent
NOTEBOOKS_DIR = REPO_DIR / "01-explore-raw-files"
sys.path.insert(0, str(PIPELINE_DIR))
import bids_config as bc  # noqa: E402

log = logging.getLogger("pipeline")

STEP_NAMES = {
    "01": "Convert the raw cMEG recordings to FIF, screen for bad channels",
    "02": "Convert the EyeLink .edf with eye2bids",
    "03": "Convert the MRI DICOMs with HeuDiConv, deface copies",
    "04": "Write the MEG (+ MRI and behavioral files) into the BIDS dataset",
    "05": "Sync the eye tracking to the MEG and add it to the BIDS dataset",
    "06": "Validate the whole BIDS dataset",
}
# The study.yaml "data:" switch a notebook needs (None = always possible).
STEP_DATA = {"02": "eye_tracking", "03": "mri", "05": "eye_tracking"}
# Notebooks that write into (or read the whole of) the BIDS dataset or the shared
# sourcedata/code/heuristic.py: they run under the lock.
LOCKED_STEPS = {"03", "04", "05", "06"}
MIN_CMEG2FIF = (2, 15)


# =====================================================================================
#  Small helpers
# =====================================================================================

def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _duration(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _newest_mtime(paths) -> float | None:
    """The newest modification time among files (folders: the files inside, recursively)."""
    newest = None
    for p in paths:
        p = Path(p)
        if p.is_dir():
            times = [f.stat().st_mtime for f in p.rglob("*") if f.is_file()]
        elif p.is_file():
            times = [p.stat().st_mtime]
        else:
            times = []
        if times:
            newest = max(newest or 0, max(times))
    return newest


def _oldest_mtime(paths) -> float | None:
    times = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            times += [f.stat().st_mtime for f in p.rglob("*") if f.is_file()]
        elif p.is_file():
            times.append(p.stat().st_mtime)
    return min(times) if times else None


def _run(cmd, timeout=300) -> tuple[int, str]:
    """Run a command; (exit code, stdout+stderr). Exit code 127 = could not start."""
    try:
        proc = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]}: no answer after {timeout} s"


def _git_version() -> str:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    try:
        commit = subprocess.run(["git", "-C", str(REPO_DIR), "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, timeout=30, env=env).stdout.strip()
        branch = subprocess.run(["git", "-C", str(REPO_DIR), "rev-parse", "--abbrev-ref", "HEAD"],
                                capture_output=True, text=True, timeout=30, env=env).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(REPO_DIR), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=60, env=env).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if not commit:
        return "unknown (not a git clone)"
    return f"{commit} ({branch})" + (", with local changes" if dirty else "")


def _package_version(name: str) -> str:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return "not installed"


# =====================================================================================
#  The plan: which notebooks run, and what each needs
# =====================================================================================

@dataclass
class Step:
    key: str
    notebook: Path | None = None
    status: str = "planned"   # planned, ok, failed, skipped
    reason: str = ""          # why it was added, skipped, or how it failed
    seconds: float = 0.0
    html: Path | None = None
    executed: Path | None = None


def find_notebooks(folder: Path = NOTEBOOKS_DIR) -> dict:
    """{"01": path, ...}: the one 0N_*.ipynb file per notebook in the folder."""
    found, problems = {}, []
    for key in bc.NOTEBOOKS:
        matches = sorted(folder.glob(f"{key}_*.ipynb"))
        if len(matches) == 1:
            found[key] = matches[0]
        else:
            problems.append(f"{folder}: expected one {key}_*.ipynb, found {[m.name for m in matches] or 'none'}")
    if problems:
        raise bc.ConfigError(problems)
    return found


def plan_steps(cfg: bc.Config, chosen: list | None = None) -> dict:
    """{"01": Step, ...} for every notebook, each "planned" or "skipped" with the reason."""
    data = cfg.study["data"]
    if chosen is None:
        wanted = {k for k, on in cfg.study["steps"].items() if on}
        source = "steps: in study.yaml"
    else:
        wanted = set(chosen)
        source = "--steps"
    steps = {k: Step(k) for k in bc.NOTEBOOKS}
    for k, step in steps.items():
        if k not in wanted:
            step.status, step.reason = "skipped", f"not chosen ({source})"
    if "04" in wanted and "05" not in wanted:
        steps["05"].status, steps["05"].reason = "planned", "added: 04 rewrites _events.json, which 05 completes"
    if "06" not in wanted:
        steps["06"].status, steps["06"].reason = "planned", "added: the validation always runs last"
    for k, flag in STEP_DATA.items():
        if not data[flag]:
            steps[k].status, steps[k].reason = "skipped", f"the study has no {flag.replace('_', ' ')} data (data.{flag}: false)"
    return steps


def _bad_channels_auto(cfg: bc.Config) -> bool:
    bad = cfg.subject["bad_channels"]
    return bad == "auto" or (isinstance(bad, dict) and bool(bad.get("auto")))


def requirements(cfg: bc.Config, settings: dict) -> dict:
    """{step: [(producing step, what, [paths], check)]}: the files each notebook needs from
    another one. check(paths) -> True when the files are there."""
    s04, s05 = settings["04"], settings["05"]
    S = cfg.session_dir
    sub, ses, task = cfg.subject["subject"], cfg.subject["session"], cfg.study["study"]["task"]
    data = cfg.study["data"]
    exists = lambda paths: all(Path(p).exists() for p in paths)              # noqa: E731
    any_match = lambda paths: any(any(Path(p).parent.glob(Path(p).name)) for p in paths)  # noqa: E731

    fif_data, fif_er = Path(s04["FIF_DATA"]), Path(s04["FIF_EMPTYROOM"])
    req = {k: [] for k in bc.NOTEBOOKS}
    req["04"].append(("01", "the FIF files", [fif_data, fif_er], exists))
    if _bad_channels_auto(cfg):
        cand = fif_data.with_name(fif_data.stem + "_bad_channel_candidates.tsv")
        req["04"].append(("01", "the bad-channel candidates (bad_channels: auto)", [cand], exists))
    if data["mri"]:
        req["04"].append(("03", "the converted MRI", [S / "mri-staging" / f"sub-{sub}" / f"ses-{ses}"], exists))
    if data["eye_tracking"]:
        req["05"].append(("02", "the eye2bids files",
                          [Path(s05["EYE2BIDS_DIR"]) / f"{cfg.stem}_task-{task}*_physio.tsv.gz"], any_match))
    req["05"].append(("04", "the MEG recording in the BIDS dataset",
                      [cfg.bids_root / f"sub-{sub}" / f"ses-{ses}" / "meg" / f"{cfg.stem}_task-{task}*_meg.fif"],
                      any_match))
    req["06"].append(("04", "a BIDS dataset", [cfg.bids_root / "dataset_description.json"], exists))
    return req


# =====================================================================================
#  Pre-flight checks
# =====================================================================================

def _container_problems(name: str, sif, runtime: str | None) -> list:
    if not sif:
        return [f"study.yaml: software.{name} is not set"]
    sif = Path(sif)
    if not sif.is_file():
        return [f"study.yaml: software.{name}: {sif} not found"]
    if runtime is None:
        return []   # reported once, separately
    code, out = _run([runtime, "exec", "--cleanenv", sif, "true"])
    if code != 0:
        return [f"study.yaml: software.{name}: {sif} doesn't start with {runtime} (exit code {code}): {out[-300:]}"]
    return []


def preflight(cfg: bc.Config, steps: dict, settings: dict, kernel: str) -> tuple[list, list, list]:
    """(problems, warnings, facts). Problems stop the run; facts are what was found."""
    problems, warnings, facts = [], [], []
    run = {k for k, s in steps.items() if s.status == "planned"}
    raw = cfg.raw_dir
    sw = cfg.study["software"]

    # ---- Python packages and the notebook kernel ----
    needed_modules = {"papermill": "papermill", "nbconvert": "nbconvert", "ipykernel": "ipykernel"}
    if run & {"01", "04", "05"}:
        needed_modules.update({"mne": "mne", "mne_bids": "mne-bids"})
    if "02" in run:
        needed_modules["eye2bids"] = "eye2bids (Scully fork)"
    for module, package in needed_modules.items():
        if importlib.util.find_spec(module) is None:
            problems.append(f"Python package {package} is not installed in this environment ({sys.prefix}); "
                            "is the pygers-meg environment active?")
    if importlib.util.find_spec("jupyter_client") is not None:
        from jupyter_client.kernelspec import KernelSpecManager, NoSuchKernel
        try:
            spec = KernelSpecManager().get_kernel_spec(kernel)
            exe = spec.argv[0] if spec.argv else ""
            if exe in {"python", "python3", "{python}"}:
                exe = sys.executable
            if Path(exe).resolve() != Path(sys.executable).resolve():
                problems.append(
                    f"The Jupyter kernel {kernel!r} runs {exe}, not this environment's Python ({sys.executable}), "
                    f"so the notebooks would run in another environment. Fix: in the pygers-meg environment, run\n"
                    f"      python -m ipykernel install --user --name pygers-meg --display-name 'Python (pygers-meg)'\n"
                    f"    and use --kernel pygers-meg (or remove the other {kernel!r} kernel: jupyter kernelspec list).")
            else:
                facts.append(f"Notebook kernel: {kernel} ({sys.executable})")
        except NoSuchKernel:
            problems.append(f"No Jupyter kernel named {kernel!r} (see: jupyter kernelspec list)")

    # ---- notebook 01: the cMEG recordings, the EinScan files, cmeg2fif ----
    if "01" in run:
        cmeg_dir = raw / "cMEG"
        have = sorted(p.name[:-len("_cMEG_Data")] for p in cmeg_dir.glob("*_cMEG_Data")) if cmeg_dir.is_dir() else []
        for what, prefix in [("emptyroom_prefix", cfg.subject["cmeg"]["emptyroom_prefix"]),
                             ("task_prefix", cfg.subject["cmeg"]["task_prefix"])]:
            f = cmeg_dir / f"{prefix}_cMEG_Data" / f"{prefix}_meg_001.cMEG"
            if f.is_file():
                facts.append(f"cMEG recording ({what}): {f.parent.name}")
            else:
                problems.append(f"{cfg.subject_file.name}: cmeg.{what} {prefix!r}: {f} not found. "
                                f"Recordings in {cmeg_dir}: {have or 'none'}")
        for name in ("XFM_FILE", "XYZ_FILE"):
            p = Path(settings["01"][name])
            if p.is_file():
                facts.append(f"EinScan file: {p.name}")
            else:
                there = sorted(x.name for x in p.parent.iterdir()) if p.parent.is_dir() else "no einscan/ folder"
                problems.append(f"{cfg.subject_file.name}: einscan file {p.name} not found in {p.parent} "
                                f"(files there: {there})")
        cmeg2fif = shutil.which("cmeg2fif") or str(Path(sys.executable).parent / "cmeg2fif")
        code, out = _run([cmeg2fif, "--version"], timeout=120)
        m = re.search(r"(\d+)\.(\d+)", out)
        if code != 0 or not m:
            problems.append(f"cmeg2fif doesn't run ({out[-200:] or f'exit code {code}'}); "
                            "install it in the pygers-meg environment (see scotty-environment.yml)")
        elif (int(m.group(1)), int(m.group(2))) < MIN_CMEG2FIF:
            problems.append(f"cmeg2fif is version {m.group(0)}; the notebooks need "
                            f"{MIN_CMEG2FIF[0]}.{MIN_CMEG2FIF[1]} or newer")
        else:
            facts.append(f"cmeg2fif {m.group(0)}")

    # ---- notebook 02: the EDF (found by bids_config.find_inputs) and edf2asc ----
    if "02" in run:
        facts.append(f"EyeLink file: {Path(settings['02']['EDF_FILE']).name}")
        if sw["edf2asc_dir"]:
            exe = Path(sw["edf2asc_dir"]) / "edf2asc"
            if not (exe.is_file() and os.access(exe, os.X_OK)):
                problems.append(f"study.yaml: software.edf2asc_dir: no edf2asc program in {sw['edf2asc_dir']}")
        elif shutil.which("edf2asc") is None:
            problems.append("edf2asc is not on your PATH; install it (install_edf2asc.sh) or set "
                            "software.edf2asc_dir in study.yaml")

    # ---- notebooks 03 and 06: the containers ----
    if run & {"03", "06"}:
        runtime = shutil.which("apptainer") or shutil.which("singularity")
        if runtime is None:
            problems.append("Neither apptainer nor singularity is on your PATH (needed by notebooks 03 and 06)")
        names = ["bids_validator_sif"] + (["heudiconv_sif", "pydeface_sif"] if "03" in run else [])
        for name in names:
            found = _container_problems(name, sw[name], runtime)
            problems += found
            if not found:
                facts.append(f"Container {name}: {Path(sw[name]).name}")
        if "03" in run:
            facts.append(f"DICOM files: {settings['03']['DICOM_TEMPLATE']}")

    # ---- notebook 04: the behavioral logs ----
    if "04" in run and cfg.study["data"]["behavioral"]:
        task = cfg.study["study"]["task"]
        behav = raw / "behav"
        csvs = sorted(behav.glob(f"*task-{task}_*.csv")) if behav.is_dir() else []
        if csvs:
            facts.append(f"Behavioral logs: {[p.name for p in csvs]}")
        else:
            problems.append(f"No *task-{task}_*.csv files in {behav} "
                            "(or set data.behavioral: false in study.yaml)")

    # ---- what the chosen notebooks need from notebooks that don't run now ----
    for step_key, needs in requirements(cfg, settings).items():
        if step_key not in run:
            continue
        for producer, what, paths, check in needs:
            if producer in run:
                continue
            if steps[producer].status == "skipped" and producer in STEP_DATA \
                    and not cfg.study["data"][STEP_DATA[producer]]:
                continue
            if not check(paths):
                msg = (f"Notebook {step_key} needs {what} from notebook {producer}, which isn't chosen "
                       f"to run, and they aren't there (not found: {paths[0]})")
                if step_key == "06":
                    warnings.append(msg + ". Notebook 06 will be skipped.")
                else:
                    problems.append(msg + f". Add {producer} to the steps.")
    warnings += staleness_warnings(cfg, steps, settings)

    # ---- the output folders can be written ----
    for folder in [cfg.session_dir, cfg.bids_root if cfg.bids_root.exists() else cfg.bids_root.parent]:
        if folder.exists() and not os.access(folder, os.W_OK):
            problems.append(f"You can't write in {folder}")
    if not cfg.session_dir.is_dir():
        problems.append(f"The session folder {cfg.session_dir} doesn't exist")
    return problems, warnings, facts


def staleness_warnings(cfg: bc.Config, steps: dict, settings: dict) -> list:
    """Notebooks that won't run now whose output is older than their input (the output may be
    out of date)."""
    run = {k for k, s in steps.items() if s.status == "planned"}
    S, raw = cfg.session_dir, cfg.raw_dir
    out = []

    def check(producer, inputs, outputs, advice):
        if producer in run:
            return
        newest_in, oldest_out = _newest_mtime(inputs), _oldest_mtime(outputs)
        if newest_in and oldest_out and newest_in > oldest_out + 1:
            out.append(f"Notebook {producer}'s output ({Path(outputs[0]).name}, ...) is older than its input "
                       f"({Path(inputs[0]).name}, ...): it may be out of date. {advice}")

    if run & {"04"}:
        cm = cfg.subject["cmeg"]
        check("01", [raw / "cMEG" / f"{cm['task_prefix']}_cMEG_Data", raw / "cMEG" / f"{cm['emptyroom_prefix']}_cMEG_Data",
                     settings["01"]["XFM_FILE"], settings["01"]["XYZ_FILE"]],
              [settings["04"]["FIF_DATA"], settings["04"]["FIF_EMPTYROOM"]],
              "Run 01 again (with options.force_reconvert: true).")
        if cfg.study["data"]["mri"]:
            check("03", [raw / "mri"], [S / "mri-staging" / f"sub-{cfg.subject['subject']}"], "Run 03 again.")
    if run & {"05"} and cfg.study["data"]["eye_tracking"] and settings["02"]["EDF_FILE"]:
        check("02", [settings["02"]["EDF_FILE"]], [S / "eye-staging"], "Run 02 again.")
    return out


# =====================================================================================
#  The lock on the BIDS dataset
# =====================================================================================

class DatasetLock:
    """A lock file next to the BIDS dataset (<study>/.bids.lock). Works on network file systems:
    it's created atomically, and a lock left by a job that no longer runs is taken over."""

    def __init__(self, bids_root: Path, poll_s: float = 30.0, max_wait_s: float = 24 * 3600):
        self.path = bids_root.parent / f".{bids_root.name}.lock"
        self.poll_s, self.max_wait_s = poll_s, max_wait_s
        self.held = False

    def _info(self) -> dict:
        return {"host": socket.gethostname(), "pid": os.getpid(), "user": getpass.getuser(),
                "slurm_job": os.environ.get("SLURM_JOB_ID"), "since": _now().isoformat(timespec="seconds"),
                "for": f"{sys.argv[1:]}"}

    def _holder_gone(self, info: dict) -> bool:
        job = info.get("slurm_job")
        if job and shutil.which("squeue"):
            code, out = _run(["squeue", "-h", "-j", job, "-o", "%T"], timeout=60)
            if code == 0:
                return out.strip() == ""
            if "Invalid job id" in out:
                return True
            return False
        if info.get("host") == socket.gethostname() and not job:
            try:
                os.kill(int(info["pid"]), 0)
                return False
            except ProcessLookupError:
                return True
            except (PermissionError, ValueError, KeyError):
                return False
        return False

    def acquire(self):
        start, told = time.monotonic(), False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
                with os.fdopen(fd, "w") as f:
                    json.dump(self._info(), f)
                self.held = True
                if told:
                    log.info("Got the lock on the BIDS dataset.")
                return
            except FileExistsError:
                try:
                    info = json.loads(self.path.read_text())
                except (OSError, ValueError):
                    info = {}
                if self._holder_gone(info):
                    log.warning(f"Removing a lock left by a job that no longer runs: {info}")
                    self.path.unlink(missing_ok=True)
                    continue
                if not told:
                    log.info(f"Waiting for the lock on the BIDS dataset ({self.path}), held by {info or 'unknown'}. "
                             "If no job holds it any more, delete that file.")
                    told = True
                if time.monotonic() - start > self.max_wait_s:
                    raise TimeoutError(f"Waited {_duration(self.max_wait_s)} for {self.path}")
                time.sleep(self.poll_s)

    def release(self):
        if self.held:
            self.path.unlink(missing_ok=True)
            self.held = False


# =====================================================================================
#  Running a notebook and making its report
# =====================================================================================

TOGGLE_CSS = """
<style id="pipeline-toggle">
  body.pipeline-hide-code .jp-CodeCell .jp-Cell-inputWrapper { display: none; }
  body.pipeline-hide-code .jp-CodeCell.celltag_pipeline-error .jp-Cell-inputWrapper { display: block; }
  body.pipeline-hide-code .jp-CodeCell .jp-OutputPrompt { visibility: hidden; }
  #pipeline-bar { position: sticky; top: 0; z-index: 100; display: flex; gap: 1em; align-items: center;
                  padding: 6px 12px; background: #f3f4f6; border-bottom: 1px solid #d1d5db;
                  font: 14px system-ui, sans-serif; }
  #pipeline-bar button { font: inherit; padding: 2px 10px; cursor: pointer; }
  #pipeline-bar a { color: #1d4ed8; }
</style>
"""
TOGGLE_BAR = """
<div id="pipeline-bar">
  <a href="index.html">&larr; All notebooks of this run</a>
  <button id="pipeline-toggle-button" type="button"></button>
  <span>{title}</span>
</div>
<script>
  (function () {{
    var body = document.body, button = document.getElementById("pipeline-toggle-button");
    function label() {{ button.textContent = body.classList.contains("pipeline-hide-code") ? "Show code" : "Hide code"; }}
    button.addEventListener("click", function () {{ body.classList.toggle("pipeline-hide-code"); label(); }});
    label();
  }})();
</script>
"""


def export_html(executed: Path, out: Path, hide_code: bool, title: str):
    """An HTML report of an executed notebook, with a Show/Hide code button (code hidden at
    first when hide_code; the cell that failed always shows its code)."""
    import nbformat
    from nbconvert import HTMLExporter

    nb = nbformat.read(executed, as_version=4)
    for cell in nb.cells:
        if cell.cell_type == "code" and any(o.get("output_type") == "error" for o in cell.get("outputs", [])):
            tags = cell.metadata.setdefault("tags", [])
            if "pipeline-error" not in tags:
                tags.append("pipeline-error")
    exporter = HTMLExporter(template_name="lab")
    body, _ = exporter.from_notebook_node(nb, resources={"metadata": {"name": title}})
    body = body.replace("</head>", TOGGLE_CSS + "</head>", 1)
    body = re.sub(r"<body([^>]*)>",
                  lambda m: (f"<body{m.group(1)}>" if not hide_code else
                             _add_body_class(m.group(0))) + TOGGLE_BAR.format(title=html.escape(title)),
                  body, count=1)
    out.write_text(body, encoding="utf-8")


def _add_body_class(tag: str) -> str:
    if 'class="' in tag:
        return tag.replace('class="', 'class="pipeline-hide-code ', 1)
    return tag[:-1] + ' class="pipeline-hide-code">'


def run_notebook(step: Step, settings_file: Path, run_dir: Path, kernel: str, hide_code: bool):
    import papermill as pm

    step.executed = run_dir / step.notebook.name
    t0 = time.monotonic()
    log.info(f"===== Notebook {step.key}: {step.notebook.name} =====")
    try:
        pm.execute_notebook(str(step.notebook), str(step.executed),
                            parameters={"config_file": str(settings_file)},
                            kernel_name=kernel, cwd=str(run_dir), log_output=True, progress_bar=False,
                            request_save_on_cell_execute=True)
        step.status = "ok"
    except pm.PapermillExecutionError as err:
        step.status = "failed"
        step.reason = f"{err.ename}: {err.evalue}"[:2000]
    except Exception as err:   # the kernel died, the notebook couldn't be read, ...
        step.status = "failed"
        step.reason = f"{type(err).__name__}: {err}"[:2000]
    step.seconds = time.monotonic() - t0
    if step.status == "ok":
        log.info(f"Notebook {step.key} finished in {_duration(step.seconds)}")
    else:
        log.error(f"Notebook {step.key} FAILED after {_duration(step.seconds)}: {step.reason}")
    if step.executed.is_file():
        try:
            step.html = step.executed.with_suffix(".html")
            export_html(step.executed, step.html, hide_code, f"{step.key}: {STEP_NAMES[step.key]}")
        except Exception as err:
            step.html = None
            log.error(f"Could not make the HTML report of notebook {step.key}: {err}")


# =====================================================================================
#  index.html
# =====================================================================================

STATUS_TEXT = {"ok": "OK", "failed": "Failed", "skipped": "Skipped", "planned": "Not run"}
STATUS_COLOR = {"ok": "#15803d", "failed": "#b91c1c", "skipped": "#6b7280", "planned": "#b45309"}


def write_index(path: Path, cfg: bc.Config, steps: dict, info: dict, warnings: list, validator: dict | None):
    e = html.escape
    rows = []
    for k, s in steps.items():
        name = STEP_NAMES[k]
        link = f'<a href="{e(s.html.name)}">{e(name)}</a>' if s.html else e(name)
        detail = e(s.reason)
        if k == "06" and validator:
            detail = (f"{validator.get('errors', '?')} error(s), {validator.get('warnings', '?')} warning(s) "
                      f"(BIDS validator {e(str(validator.get('validator', '')))})") + (f"<br>{detail}" if detail else "")
        rows.append(f"<tr><td>{k}</td><td>{link}</td>"
                    f'<td style="color:{STATUS_COLOR[s.status]};font-weight:600">{STATUS_TEXT[s.status]}</td>'
                    f"<td>{_duration(s.seconds) if s.status in ('ok', 'failed') else ''}</td>"
                    f'<td class="detail">{detail}</td></tr>')
    info_rows = "".join(f"<tr><th>{e(a)}</th><td>{e(str(b))}</td></tr>" for a, b in info.items())
    warn = ("<h2>Warnings</h2><ul>" + "".join(f"<li>{e(w)}</li>" for w in warnings) + "</ul>") if warnings else ""
    title = f"BIDS pipeline: {cfg.stem}_task-{cfg.study['study']['task']}"
    path.write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<style>
  body {{ font: 15px/1.45 system-ui, sans-serif; margin: 2em auto; max-width: 70em; padding: 0 16px; color: #111827; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 1.5em; }}
  th, td {{ text-align: left; padding: 6px 10px; border-bottom: 1px solid #e5e7eb; vertical-align: top; }}
  thead th {{ background: #f3f4f6; }}
  td.detail {{ font-size: 13px; color: #374151; white-space: pre-wrap; word-break: break-word; }}
  .info th {{ width: 12em; font-weight: 500; color: #4b5563; }}
  a {{ color: #1d4ed8; }}
</style></head><body>
<h1>{e(title)}</h1>
<table><thead><tr><th>#</th><th>Notebook (report)</th><th>Status</th><th>Time</th><th>Details</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
{warn}
<h2>This run</h2>
<table class="info">{info_rows}</table>
<p>Also in this folder: the executed notebooks (.ipynb), <a href="pipeline.log">pipeline.log</a>,
<a href="settings.yaml">settings.yaml</a> (what each notebook received) and config/ (a copy of the
configuration files).</p>
</body></html>
""", encoding="utf-8")


# =====================================================================================
#  Main
# =====================================================================================

def _parse_steps(text: str) -> list:
    keys = [k.strip().zfill(2) for k in text.split(",") if k.strip()]
    bad = [k for k in keys if k not in bc.NOTEBOOKS]
    if bad or not keys:
        raise argparse.ArgumentTypeError(f"--steps takes notebook numbers 01-06, e.g. 04,05 (got {text!r})")
    return keys


def slurm_options(cfg: bc.Config) -> list:
    sl = cfg.study["slurm"]
    reports = cfg.session_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    opts = [f"--job-name=bids_{cfg.stem}", f"--time={sl['time']}", f"--mem={sl['mem']}",
            f"--cpus-per-task={sl['cpus']}", "--nodes=1", "--ntasks=1",
            f"--output={reports / 'slurm-%j.log'}"]
    if sl["partition"]:
        opts.append(f"--partition={sl['partition']}")
    return opts


def _copy_config(cfg: bc.Config, dest: Path):
    """Copy the configuration files this run read into dest (the study folder's layout)."""
    root = cfg.study_file.parent
    files = [cfg.subject_file, cfg.study_file] + [Path(p) for p in cfg.files.values() if p]
    for p in files:
        p = Path(p)
        if p.is_dir():
            for f in p.rglob("*"):
                if f.is_file():
                    files.append(f)
            continue
        if not p.is_file():
            continue
        try:
            rel = p.resolve().relative_to(root.resolve())
        except ValueError:
            rel = Path("other") / p.name
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest / rel)


_PAPERMILL_NOISE = re.compile(r"^(Executing Cell \d+-*|Ending Cell \d+-*|Executing notebook with kernel:.*|"
                             r"Input Notebook:.*|Output Notebook:.*|Working directory:.*|Black is not installed.*)$")


class _QuietPapermill(logging.Filter):
    """Keep the notebooks' printed output in the log, without papermill's per-cell markers."""

    def filter(self, record):
        if not record.name.startswith("papermill"):
            return True
        message = record.getMessage().rstrip()
        if not message.strip() or _PAPERMILL_NOISE.match(message.strip()):
            return False
        record.msg, record.args = message, None
        return True


def _setup_logging(log_file: Path | None = None):
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in root.handlers):
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(fmt)
        h.addFilter(_QuietPapermill())
        root.addHandler(h)
    if log_file is not None:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.addFilter(_QuietPapermill())
        root.addHandler(fh)
    # papermill logs every cell's output under its own name; keep it, without the noise
    logging.getLogger("papermill").setLevel(logging.INFO)
    for noisy in ("traitlets", "asyncio", "blib2to3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the Pygers-MEG notebooks for one subject/session.")
    parser.add_argument("subject_file", help="e.g. /jukebox/<lab>/<study>/bids-config/subjects/sub-01.yaml")
    parser.add_argument("--steps", type=_parse_steps,
                        help="the notebooks to run, e.g. 04,05 (instead of steps: in study.yaml)")
    parser.add_argument("--check", action="store_true", help="only check the configuration, data and software")
    parser.add_argument("--kernel", default="python3",
                        help="the Jupyter kernel the notebooks run in (default python3: this environment's)")
    parser.add_argument("--notebooks", type=Path, default=NOTEBOOKS_DIR,
                        help=f"the folder with the notebooks (default {NOTEBOOKS_DIR})")
    parser.add_argument("--slurm-options", action="store_true",
                        help="print the sbatch options from study.yaml and stop (used by run_bids.sh)")
    args = parser.parse_args(argv)
    _setup_logging()

    # ---- read the configuration ----
    try:
        cfg = bc.load(args.subject_file)
    except bc.ConfigError as err:
        log.error("Could not read the configuration:\n  " + "\n  ".join(err.problems))
        return 2
    if args.slurm_options:
        try:
            print(" ".join(slurm_options(cfg)))
        except (KeyError, TypeError, OSError) as err:
            log.error(f"Could not get the Slurm settings from study.yaml: {err}")
            return 2
        return 0
    problems = bc.check(cfg)
    if problems:
        log.error(f"{len(problems)} problem(s) in the configuration:\n  - " + "\n  - ".join(problems))
        return 2

    steps = plan_steps(cfg, args.steps)
    run_keys = [k for k, s in steps.items() if s.status == "planned"]

    # ---- find the inputs (only those the chosen notebooks use) and the notebooks ----
    problems = []
    look = copy.copy(cfg)
    look.study = copy.deepcopy(cfg.study)
    look.study["data"]["eye_tracking"] = cfg.study["data"]["eye_tracking"] and "02" in run_keys
    look.study["data"]["mri"] = cfg.study["data"]["mri"] and "03" in run_keys
    try:
        inputs = bc.find_inputs(look)
    except bc.ConfigError as err:
        inputs, problems = {"edf_file": None, "dicom_template": None}, err.problems
    try:
        notebooks = find_notebooks(args.notebooks)
        for k, s in steps.items():
            s.notebook = notebooks[k]
    except bc.ConfigError as err:
        problems += err.problems

    started = _now()
    stamp = started.strftime("%Y%m%d-%H%M%S")
    run_dir = cfg.session_dir / "reports" / stamp
    summary_file = run_dir / "06_validator_summary.json"
    settings = bc.notebook_settings(cfg, inputs, summary_file=str(summary_file))

    if not problems:
        more, warnings, facts = preflight(cfg, steps, settings, args.kernel)
        problems += more
    else:
        warnings, facts = [], []

    log.info(f"Configuration: {cfg.subject_file} (sub-{cfg.subject['subject']}, ses-{cfg.subject['session']}, "
             f"task-{cfg.study['study']['task']})")
    for k, s in steps.items():
        log.info(f"  {k} {'run ' if s.status == 'planned' else 'skip'}  {STEP_NAMES[k]}"
                 + (f"  [{s.reason}]" if s.reason else ""))
    for f in facts:
        log.info(f"  found: {f}")
    for w in warnings:
        log.warning(w)
    if problems:
        log.error(f"{len(problems)} problem(s) found before running; nothing was changed:\n  - "
                  + "\n  - ".join(problems))
        return 2
    if args.check:
        log.info("Pre-flight checks passed. (--check: nothing was run.)")
        return 0

    # ---- the run folder ----
    run_dir.mkdir(parents=True, exist_ok=False)
    _setup_logging(run_dir / "pipeline.log")
    settings_file = bc.write_settings(settings, run_dir / "settings.yaml")
    _copy_config(cfg, run_dir / "config")
    latest = run_dir.parent / "latest"
    try:
        if latest.is_symlink() or not latest.exists():
            latest.unlink(missing_ok=True)
            latest.symlink_to(stamp, target_is_directory=True)
    except OSError:
        pass
    info = {
        "Subject file": cfg.subject_file, "Started": started.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "Host": socket.gethostname(), "User": getpass.getuser(),
        "Slurm job": os.environ.get("SLURM_JOB_ID", "none (run directly)"),
        "pygers-meg": _git_version(), "Python": sys.version.split()[0],
        "Packages": ", ".join(f"{p} {_package_version(p)}" for p in
                              ("mne", "mne-bids", "eye2bids", "cmeg2fif", "papermill")),
        "BIDS dataset": cfg.bids_root, "Command": " ".join([Path(sys.argv[0]).name] + sys.argv[1:]),
    }
    log.info(f"Reports and logs: {run_dir}")
    hide_code = cfg.study["report"]["hide_code"]
    needs = requirements(cfg, settings)
    lock = DatasetLock(cfg.bids_root)
    index = run_dir / "index.html"
    write_index(index, cfg, steps, info, warnings, None)

    try:
        for k in run_keys:
            step = steps[k]
            # skip it if a notebook it needs failed or was skipped in this run, or its input is missing
            blocked = None
            for producer, what, paths, check in needs[k]:
                p = steps[producer]
                # 06 checks the whole dataset, so it runs even when 04 failed for this session
                if producer in run_keys and p.status != "ok" and k != "06":
                    blocked = f"notebook {producer} {STEP_TEXT_PAST[p.status]}, and this one needs {what} from it"
                elif not check(paths):
                    blocked = f"needs {what} from notebook {producer}, not found: {paths[0]}"
                if blocked:
                    break
            if blocked:
                step.status, step.reason = "skipped", blocked
                log.warning(f"Skipping notebook {k}: {blocked}")
                write_index(index, cfg, steps, info, warnings, None)
                continue
            if k in LOCKED_STEPS and not lock.held:
                lock.acquire()
            run_notebook(step, settings_file, run_dir, args.kernel, hide_code)
            write_index(index, cfg, steps, info, warnings, None)
    finally:
        lock.release()

    validator = None
    if summary_file.is_file():
        try:
            validator = json.loads(summary_file.read_text())
        except ValueError:
            pass
    ended = _now()
    info["Finished"] = ended.strftime("%Y-%m-%d %H:%M:%S %Z")
    info["Total time"] = _duration((ended - started).total_seconds())
    write_index(index, cfg, steps, info, warnings, validator)

    # Not a failure: 06 skipped because there is no BIDS dataset yet (e.g. a run of 01 only)
    bad = [steps[k] for k in run_keys if steps[k].status != "ok"
           and not (k == "06" and steps[k].status == "skipped" and "04" not in run_keys)]
    log.info("===== Summary =====")
    for s in steps.values():
        log.info(f"  {s.key} {STATUS_TEXT[s.status]:8s} {_duration(s.seconds) if s.status in ('ok', 'failed') else '':>8s}"
                 f"  {s.reason}")
    if validator:
        log.info(f"  BIDS validator: {validator.get('errors')} error(s), {validator.get('warnings')} warning(s)")
    log.info(f"Reports: {index}")
    return 1 if bad else 0


STEP_TEXT_PAST = {"failed": "failed", "skipped": "was skipped", "planned": "didn't run"}


if __name__ == "__main__":
    sys.exit(main())
