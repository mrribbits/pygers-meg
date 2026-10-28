"""Run the Pygers-MEG-to-BIDS notebooks for one subject/session, with HTML reports.

Normally started by run_bids-pipeline.sh (which submits it to Slurm), but it also runs on its own,
in the pygers-meg environment, from pygers-meg's 01-explore-raw-files folder:

    python bids-pipeline/run_bids-pipeline.py /path/to/bids-config/subjects/sub-01.yaml
    python bids-pipeline/run_bids-pipeline.py .../sub-01.yaml --steps 04,05   # only these (plus the rules below)
    python bids-pipeline/run_bids-pipeline.py .../sub-01.yaml --check         # check everything, run nothing

What it does:
  1. Reads and checks the configuration (bids_config.py), finds the raw files, and checks
     that the software each chosen notebook needs is there and starts (pre-flight). Any
     problem stops it here, before anything is changed, with the full list.
  2. Decides which notebooks run: those turned on under "steps:" in global-study.yaml (or given
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

How this file is organized (read main() at the end first: it calls everything else in order):
  - Settings for the whole file (folders, notebook names, rules)
  - Small helpers (times, durations, running a command, version numbers)
  - The plan: which notebooks run, and which files each one needs from another
  - Pre-flight checks
  - The lock on the BIDS dataset
  - Running a notebook and making its HTML report
  - index.html, the summary page
  - main(): reads the command line and does steps 1-4 above
"""
# Lets type hints like "Path | None" work on older Python versions too (they are notes for
# the reader: Python itself doesn't check them).
from __future__ import annotations

# ---- Python's own modules (always installed) ----
import argparse          # reads the command line (the subject file, --steps, --check, ...)
import copy              # makes copies of settings, so the originals stay unchanged
import datetime as dt    # dates and times (the run folder's name, start and end times)
import getpass           # the user's name (for the report and the lock)
import html              # html.escape(): makes text safe to put into a web page
import importlib.util    # checks whether a Python package is installed, without loading it
import json              # reads and writes .json files (the lock file, the validator summary)
import logging           # prints time-stamped messages to the screen and to pipeline.log
import os                # environment variables, file permissions, process numbers
import re                # "regular expressions": finding patterns in text
import shutil            # finds programs on the PATH; copies files
import socket            # the computer's name (for the report and the lock)
import subprocess        # runs other programs (cmeg2fif, apptainer, git, squeue)
import sys               # this Python's path, the command line, exiting with a status
import time              # measuring how long things take; waiting
from dataclasses import dataclass, field   # a short way to write a class that just holds values
from pathlib import Path                   # file paths that work on macOS, Linux and Windows

# =====================================================================================
#  Settings for the whole file
# =====================================================================================

# Where things are, worked out from where this file is:
#   <pygers-meg>/01-explore-raw-files/bids-pipeline/run_bids-pipeline.py
# __file__ is this file's path; .resolve() makes it a full path; .parent is its folder.
PIPELINE_DIR = Path(__file__).resolve().parent
NOTEBOOKS_DIR = PIPELINE_DIR.parent        # the notebooks are in the folder above bids-pipeline/
REPO_DIR = NOTEBOOKS_DIR.parent             # the pygers-meg repository (for its git version)

# bids_config.py (next to this file) reads and checks the YAML files. Tell Python to look
# in this folder for it, then load it under the short name "bc" (e.g. bc.load(...)).
sys.path.insert(0, str(PIPELINE_DIR))
import bids_config as bc  # noqa: E402  (the comment tells code checkers this late import is on purpose)

# The "logger" that prints this program's messages. log.info(...) for normal messages,
# log.warning(...) and log.error(...) for problems. _setup_logging() (further down)
# decides where they go: the screen (the Slurm log) and pipeline.log.
log = logging.getLogger("pipeline")

# A short description of each notebook, for the log and index.html.
STEP_NAMES = {
    "01": "Convert the raw cMEG recordings to FIF, screen for bad channels",
    "02": "Convert the EyeLink .edf with eye2bids",
    "03": "Convert the MRI DICOMs with HeuDiConv, deface copies",
    "04": "Write the MEG (+ MRI and behavioral files) into the BIDS dataset",
    "05": "Sync the eye tracking to the MEG and add it to the BIDS dataset",
    "06": "Validate the whole BIDS dataset",
}
# The global-study.yaml "data:" switch a notebook needs (notebooks not listed always can run).
# E.g. with "data: eye_tracking: false", notebooks 02 and 05 are skipped.
STEP_DATA = {"02": "eye_tracking", "03": "mri", "05": "eye_tracking"}
# Notebooks that write into (or read the whole of) the BIDS dataset or the shared
# sourcedata/code/heuristic.py: they run under the lock (see DatasetLock below).
LOCKED_STEPS = {"03", "04", "05", "06"}
# The oldest cMEG2FIF version notebook 01 works with, as (major, minor): 2.15.
MIN_CMEG2FIF = (2, 15)


# =====================================================================================
#  Small helpers
# =====================================================================================
# Names starting with "_" are only used inside this file.

def _now() -> dt.datetime:
    """The current date and time, with the local time zone."""
    return dt.datetime.now().astimezone()


def _duration(seconds: float) -> str:
    """A number of seconds as "m:ss", or "h:mm:ss" from one hour on (e.g. 75 -> "1:15")."""
    seconds = int(round(seconds))
    h, rest = divmod(seconds, 3600)   # divmod(a, b) gives (a // b, the remainder)
    m, s = divmod(rest, 60)
    # :02d = a whole number with at least 2 digits (5 -> "05")
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _newest_mtime(paths) -> float | None:
    """The newest modification time among files (folders: the files inside, recursively)."""
    newest = None
    for p in paths:
        p = Path(p)
        if p.is_dir():
            # .rglob("*") lists everything inside the folder, in all subfolders
            times = [f.stat().st_mtime for f in p.rglob("*") if f.is_file()]
        elif p.is_file():
            times = [p.stat().st_mtime]   # st_mtime: when the file was last changed (in seconds)
        else:
            times = []                    # doesn't exist: nothing to compare
        if times:
            newest = max(newest or 0, max(times))
    return newest                         # None if none of the paths had any files


def _oldest_mtime(paths) -> float | None:
    """The oldest modification time among files (folders: the files inside, recursively)."""
    times = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            times += [f.stat().st_mtime for f in p.rglob("*") if f.is_file()]
        elif p.is_file():
            times.append(p.stat().st_mtime)
    return min(times) if times else None


def _run(cmd, timeout=300) -> tuple[int, str]:
    """Run a command; (exit code, stdout+stderr). Exit code 127 = could not start.

    cmd is a list of words, e.g. ["cmeg2fif", "--version"]. It never stops this program:
    any problem comes back as a non-zero exit code with a message."""
    try:
        # capture_output: keep what it prints (instead of showing it); text: as text;
        # timeout: give up after this many seconds
        proc = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except FileNotFoundError:             # the program doesn't exist
        return 127, f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:     # it took too long
        return 124, f"{cmd[0]}: no answer after {timeout} s"


def _git_version() -> str:
    """Which version of the pygers-meg code this is, for the report: the git commit, the
    branch, and whether files were changed since that commit. E.g. "3fa1824 (pipeline)"."""
    # GIT_OPTIONAL_LOCKS=0: git only reads, it doesn't write its lock files (safe while other
    # git commands might run in the same folder)
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    try:
        commit = subprocess.run(["git", "-C", str(REPO_DIR), "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, timeout=30, env=env).stdout.strip()
        branch = subprocess.run(["git", "-C", str(REPO_DIR), "rev-parse", "--abbrev-ref", "HEAD"],
                                capture_output=True, text=True, timeout=30, env=env).stdout.strip()
        # Any output here = some tracked file differs from the commit
        dirty = subprocess.run(["git", "-C", str(REPO_DIR), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=60, env=env).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):   # no git, or it took too long
        return "unknown"
    if not commit:
        return "unknown (not a git clone)"
    return f"{commit} ({branch})" + (", with local changes" if dirty else "")


def _package_version(name: str) -> str:
    """The installed version of a Python package (e.g. "mne"), or "not installed"."""
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return "not installed"


# =====================================================================================
#  The plan: which notebooks run, and what each needs
# =====================================================================================

# One Step holds everything about one notebook in this run. "@dataclass" writes the
# boring parts of the class for us: Step("04") makes one with the values below as defaults.
@dataclass
class Step:
    key: str                          # "01" ... "06"
    notebook: Path | None = None      # the notebook file
    status: str = "planned"           # planned (will run), ok (ran), failed, skipped
    reason: str = ""                  # why it was added or skipped, or how it failed
    seconds: float = 0.0              # how long it ran
    html: Path | None = None          # its HTML report, once made
    executed: Path | None = None      # the executed copy of the notebook (with all outputs)


def find_notebooks(folder: Path = NOTEBOOKS_DIR) -> dict:
    """{"01": path, ...}: the one 0N_*.ipynb file per notebook in the folder.

    Raises bc.ConfigError (listing the problems) if a notebook is missing or there are two
    candidates, e.g. 04_FIF_to_BIDS.ipynb and 04_old.ipynb. (my_*.ipynb copies don't match.)"""
    found, problems = {}, []
    for key in bc.NOTEBOOKS:                                  # "01" ... "06"
        matches = sorted(folder.glob(f"{key}_*.ipynb"))       # e.g. every 04_*.ipynb
        if len(matches) == 1:
            found[key] = matches[0]
        else:
            problems.append(f"{folder}: expected one {key}_*.ipynb, found {[m.name for m in matches] or 'none'}")
    if problems:
        raise bc.ConfigError(problems)
    return found


def plan_steps(cfg: bc.Config, chosen: list | None = None) -> dict:
    """{"01": Step, ...} for every notebook, each "planned" or "skipped" with the reason.

    chosen: the notebooks given with --steps, or None to use "steps:" in global-study.yaml.
    The rules, in this order: not chosen -> skipped; 04 chosen -> 05 added; 06 always
    added; the study has no data for it -> skipped (this one wins over the others)."""
    data = cfg.study["data"]
    if chosen is None:
        # Every notebook set to true under "steps:" in global-study.yaml
        wanted = {k for k, on in cfg.study["steps"].items() if on}
        source = "steps: in global-study.yaml"
    else:
        wanted = set(chosen)
        source = "--steps"
    steps = {k: Step(k) for k in bc.NOTEBOOKS}                # all six start as "planned"
    for k, step in steps.items():
        if k not in wanted:
            step.status, step.reason = "skipped", f"not chosen ({source})"
    # 04 rewrites the _events.json that 05 adds the eye-tracking events to, so after 04, 05
    # must run again
    if "04" in wanted and "05" not in wanted:
        steps["05"].status, steps["05"].reason = "planned", "added: 04 rewrites _events.json, which 05 completes"
    # The validation of the whole dataset always comes last
    if "06" not in wanted:
        steps["06"].status, steps["06"].reason = "planned", "added: the validation always runs last"
    # Notebooks for data the study doesn't have (e.g. no MRI) never run
    for k, flag in STEP_DATA.items():
        if not data[flag]:
            steps[k].status, steps[k].reason = "skipped", f"the study has no {flag.replace('_', ' ')} data (data.{flag}: false)"
    return steps


def _bad_channels_auto(cfg: bc.Config) -> bool:
    """True if the subject file uses notebook 01's bad-channel candidates ("auto", or a
    {"auto": true, ...} entry), so notebook 04 needs the candidates file."""
    bad = cfg.subject["bad_channels"]
    return bad == "auto" or (isinstance(bad, dict) and bool(bad.get("auto")))


def requirements(cfg: bc.Config, settings: dict) -> dict:
    """{step: [(producing step, what, [paths], check)]}: the files each notebook needs from
    another one. check(paths) -> True when the files are there.

    For example, 04 needs the FIF files that 01 makes. This is used twice: before the run
    (if 01 won't run now, its FIF files must already exist), and during the run (if 01
    failed, 04 is skipped)."""
    s04, s05 = settings["04"], settings["05"]     # the settings notebooks 04 and 05 will get
    S = cfg.session_dir                           # sourcedata/sub-XX/ses-YY
    task = cfg.study["study"]["task"]
    data = cfg.study["data"]
    # Two ways to check. "lambda" makes a small function in one line:
    #   exists:    every listed path exists
    #   any_match: the paths contain a "*" wildcard, and at least one file matches
    exists = lambda paths: all(Path(p).exists() for p in paths)              # noqa: E731
    any_match = lambda paths: any(any(Path(p).parent.glob(Path(p).name)) for p in paths)  # noqa: E731

    fif_data, fif_er = Path(s04["FIF_DATA"]), Path(s04["FIF_EMPTYROOM"])
    req = {k: [] for k in bc.NOTEBOOKS}           # start with "needs nothing" for each notebook
    # 04 needs both FIF files from 01 ...
    req["04"].append(("01", "the FIF files", [fif_data, fif_er], exists))
    # ... and, with bad_channels: auto, the candidates file 01 saves next to the task FIF
    if _bad_channels_auto(cfg):
        cand = fif_data.with_name(fif_data.stem + "_bad_channel_candidates.tsv")
        req["04"].append(("01", "the bad-channel candidates (bad_channels: auto)", [cand], exists))
    # 04 copies the MRI that 03 converted (in mri-staging/sub-XX/ses-YY)
    if data["mri"]:
        req["04"].append(("03", "the converted MRI", [cfg.bids_dir(S / "mri-staging")], exists))
    # 05 needs 02's eye-tracking files ...
    if data["eye_tracking"]:
        req["05"].append(("02", "the eye2bids files",
                          [Path(s05["EYE2BIDS_DIR"]) / f"{cfg.stem}_task-{task}*_physio.tsv.gz"], any_match))
    # ... and the MEG recording 04 wrote into the BIDS dataset
    req["05"].append(("04", "the MEG recording in the BIDS dataset",
                      [cfg.bids_dir() / "meg" / f"{cfg.stem}_task-{task}*_meg.fif"],
                      any_match))
    # 06 needs a BIDS dataset to validate (any subject's: dataset_description.json is enough)
    req["06"].append(("04", "a BIDS dataset", [cfg.bids_root / "dataset_description.json"], exists))
    return req


# =====================================================================================
#  Pre-flight checks
# =====================================================================================
# Everything here only looks: nothing is created or changed. Each check adds to one of
# three lists: problems (stop the run), warnings (shown, but the run goes on) and facts
# (what was found, shown so you can see it looked at the right files).

def _container_problems(name: str, sif, runtime: str | None) -> list:
    """Problems with one container from global-study.yaml's "software:" section (empty list = fine).

    name: the setting's name, e.g. "heudiconv_sif"; sif: its value (the .sif file);
    runtime: the apptainer (or singularity) program, or None if there is none."""
    if not sif:
        return [f"global-study.yaml: software.{name} is not set"]
    sif = Path(sif)
    if not sif.is_file():
        return [f"global-study.yaml: software.{name}: {sif} not found"]
    if runtime is None:
        return []   # reported once, separately
    # Start the container and run "true" (a program that does nothing) inside it. This
    # proves the file is a working container, not just that it exists.
    # --cleanenv: don't pass your environment into the container (as the notebooks do).
    code, out = _run([runtime, "exec", "--cleanenv", sif, "true"])
    if code != 0:
        return [f"global-study.yaml: software.{name}: {sif} doesn't start with {runtime} (exit code {code}): {out[-300:]}"]
    return []


def preflight(cfg: bc.Config, steps: dict, settings: dict, kernel: str) -> tuple[list, list, list]:
    """(problems, warnings, facts). Problems stop the run; facts are what was found.

    Only checks what the notebooks chosen to run need: e.g. the containers only if 03 or
    06 runs."""
    problems, warnings, facts = [], [], []
    run = {k for k, s in steps.items() if s.status == "planned"}   # the notebooks that will run
    raw = cfg.raw_dir                                               # sourcedata/sub-XX/ses-YY/raw
    sw = cfg.study["software"]                                      # your containers and edf2asc

    # ---- Python packages and the notebook kernel ----
    # {name to import: name to show}. Every run needs papermill (runs the notebooks),
    # nbconvert (makes the HTML) and ipykernel (the notebooks' Python).
    needed_modules = {"papermill": "papermill", "nbconvert": "nbconvert", "ipykernel": "ipykernel"}
    if run & {"01", "04", "05"}:          # "&": does "run" share any notebook with this set?
        needed_modules.update({"mne": "mne", "mne_bids": "mne-bids"})
    if "02" in run:
        needed_modules["eye2bids"] = "eye2bids (Scully fork)"
    for module, package in needed_modules.items():
        if importlib.util.find_spec(module) is None:     # None = not installed
            problems.append(f"Python package {package} is not installed in this environment ({sys.prefix}); "
                            "is the pygers-meg environment active?")
    # The notebooks run in a Jupyter "kernel" (a Python started for them). Make sure the
    # kernel's Python is this environment's Python, or the notebooks would run with other
    # packages (e.g. conda's base environment).
    if importlib.util.find_spec("jupyter_client") is not None:
        from jupyter_client.kernelspec import KernelSpecManager, NoSuchKernel
        try:
            spec = KernelSpecManager().get_kernel_spec(kernel)
            exe = spec.argv[0] if spec.argv else ""       # the program the kernel starts
            # A kernel that just says "python" uses the Python that starts it: this one
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
        # The recordings that ARE there (prefixes of the <prefix>_cMEG_Data folders), to
        # list in the message if the subject file names one that isn't
        have = sorted(p.name[:-len("_cMEG_Data")] for p in cmeg_dir.glob("*_cMEG_Data")) if cmeg_dir.is_dir() else []
        for what, prefix in [("emptyroom_prefix", cfg.subject["cmeg"]["emptyroom_prefix"]),
                             ("task_prefix", cfg.subject["cmeg"]["task_prefix"])]:
            # Notebook 01 converts <prefix>_cMEG_Data/<prefix>_meg_001.cMEG
            f = cmeg_dir / f"{prefix}_cMEG_Data" / f"{prefix}_meg_001.cMEG"
            if f.is_file():
                facts.append(f"cMEG recording ({what}): {f.parent.name}")
            else:
                problems.append(f"{cfg.subject_file.name}: cmeg.{what} {prefix!r}: {f} not found. "
                                f"Recordings in {cmeg_dir}: {have or 'none'}")
        # The two EinScan files (head digitization) named in the subject file
        for name in ("XFM_FILE", "XYZ_FILE"):
            p = Path(settings["01"][name])
            if p.is_file():
                facts.append(f"EinScan file: {p.name}")
            else:
                there = sorted(x.name for x in p.parent.iterdir()) if p.parent.is_dir() else "no einscan/ folder"
                problems.append(f"{cfg.subject_file.name}: einscan file {p.name} not found in {p.parent} "
                                f"(files there: {there})")
        # cmeg2fif: on the PATH, or next to this Python (where pip installs it in the
        # environment). Run "cmeg2fif --version" and find "2.16" etc. in what it prints.
        cmeg2fif = shutil.which("cmeg2fif") or str(Path(sys.executable).parent / "cmeg2fif")
        code, out = _run([cmeg2fif, "--version"], timeout=120)
        m = re.search(r"(\d+)\.(\d+)", out)          # the first "<number>.<number>"
        if code != 0 or not m:
            problems.append(f"cmeg2fif doesn't run ({out[-200:] or f'exit code {code}'}); "
                            "install it in the pygers-meg environment (see scotty-environment.yml)")
        elif (int(m.group(1)), int(m.group(2))) < MIN_CMEG2FIF:   # compares major, then minor
            problems.append(f"cmeg2fif is version {m.group(0)}; the notebooks need "
                            f"{MIN_CMEG2FIF[0]}.{MIN_CMEG2FIF[1]} or newer")
        else:
            facts.append(f"cmeg2fif {m.group(0)}")

    # ---- notebook 02: the EDF (found by bids_config.find_inputs) and edf2asc ----
    if "02" in run:
        facts.append(f"EyeLink file: {Path(settings['02']['EDF_FILE']).name}")
        # edf2asc (SR Research's EDF reader, used by eye2bids): in the folder global-study.yaml
        # names, or on the PATH. os.X_OK = "can be run as a program".
        if sw["edf2asc_dir"]:
            exe = Path(sw["edf2asc_dir"]) / "edf2asc"
            if not (exe.is_file() and os.access(exe, os.X_OK)):
                problems.append(f"global-study.yaml: software.edf2asc_dir: no edf2asc program in {sw['edf2asc_dir']}")
        elif shutil.which("edf2asc") is None:
            problems.append("edf2asc is not on your PATH; install it (install_edf2asc.sh) or set "
                            "software.edf2asc_dir in global-study.yaml")

    # ---- notebooks 03 and 06: the containers ----
    if run & {"03", "06"}:
        # The program that runs containers: apptainer (or its older name, singularity)
        runtime = shutil.which("apptainer") or shutil.which("singularity")
        if runtime is None:
            problems.append("Neither apptainer nor singularity is on your PATH (needed by notebooks 03 and 06)")
        # 06 needs the validator; 03 also needs HeuDiConv and pydeface
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
        # Notebook 04 converts every <anything>task-<task>_<table>.csv
        csvs = sorted(behav.glob(f"*task-{task}_*.csv")) if behav.is_dir() else []
        if csvs:
            facts.append(f"Behavioral logs: {[p.name for p in csvs]}")
        else:
            problems.append(f"No *task-{task}_*.csv files in {behav} "
                            "(or set data.behavioral: false in global-study.yaml)")

    # ---- what the chosen notebooks need from notebooks that don't run now ----
    # E.g. "--steps 04": 01 won't run, so its FIF files must already be there.
    for step_key, needs in requirements(cfg, settings).items():
        if step_key not in run:
            continue                       # this notebook won't run: it needs nothing
        for producer, what, paths, check in needs:
            if producer in run:
                continue                   # it runs first and makes the files; checked later
            # Skipped because the study has no such data (e.g. no MRI): nothing to need
            if steps[producer].status == "skipped" and producer in STEP_DATA \
                    and not cfg.study["data"][STEP_DATA[producer]]:
                continue
            if not check(paths):
                msg = (f"Notebook {step_key} needs {what} from notebook {producer}, which isn't chosen "
                       f"to run, and they aren't there (not found: {paths[0]})")
                if step_key == "06":
                    # No dataset to validate yet (e.g. a run of 01 only): not a problem
                    warnings.append(msg + ". Notebook 06 will be skipped.")
                else:
                    problems.append(msg + f". Add {producer} to the steps.")
    warnings += staleness_warnings(cfg, steps, settings)

    # ---- the output folders can be written ----
    # The session folder (reports, staging folders) and the BIDS dataset (or, if it doesn't
    # exist yet, the folder it will be made in). os.W_OK = "can be written".
    for folder in [cfg.session_dir, cfg.bids_root if cfg.bids_root.exists() else cfg.bids_root.parent]:
        if folder.exists() and not os.access(folder, os.W_OK):
            problems.append(f"You can't write in {folder}")
    if not cfg.session_dir.is_dir():
        problems.append(f"The session folder {cfg.session_dir} doesn't exist")
    return problems, warnings, facts


def staleness_warnings(cfg: bc.Config, steps: dict, settings: dict) -> list:
    """Notebooks that won't run now whose output is older than their input (the output may be
    out of date).

    E.g. you replaced a raw recording but run only 04: 04 would use the FIF files 01 made
    from the old recording. This only warns; the run goes on."""
    run = {k for k, s in steps.items() if s.status == "planned"}
    S, raw = cfg.session_dir, cfg.raw_dir
    out = []

    # A small function used only here: warn if notebook "producer" won't run and the newest
    # of its inputs is newer than the oldest of its outputs (the "+ 1" second allows for
    # file systems that round times).
    def check(producer, inputs, outputs, advice):
        if producer in run:
            return                           # it runs now, so its output will be new
        newest_in, oldest_out = _newest_mtime(inputs), _oldest_mtime(outputs)
        if newest_in and oldest_out and newest_in > oldest_out + 1:
            out.append(f"Notebook {producer}'s output ({Path(outputs[0]).name}, ...) is older than its input "
                       f"({Path(inputs[0]).name}, ...): it may be out of date. {advice}")

    if run & {"04"}:
        # 01: the raw recordings and EinScan files vs. the FIF files
        cm = cfg.subject["cmeg"]
        check("01", [raw / "cMEG" / f"{cm['task_prefix']}_cMEG_Data", raw / "cMEG" / f"{cm['emptyroom_prefix']}_cMEG_Data",
                     settings["01"]["XFM_FILE"], settings["01"]["XYZ_FILE"]],
              [settings["04"]["FIF_DATA"], settings["04"]["FIF_EMPTYROOM"]],
              "Run 01 again (with options.force_reconvert: true).")
        # 03: the DICOMs vs. the converted MRI
        if cfg.study["data"]["mri"]:
            check("03", [raw / "mri"], [cfg.bids_dir(S / "mri-staging")], "Run 03 again.")
    # 02: the .edf vs. the eye2bids files
    if run & {"05"} and cfg.study["data"]["eye_tracking"] and settings["02"]["EDF_FILE"]:
        check("02", [settings["02"]["EDF_FILE"]], [S / "eye-staging"], "Run 02 again.")
    return out


# =====================================================================================
#  The lock on the BIDS dataset
# =====================================================================================
# Several jobs (one per subject) may run at the same time, and they all write into the
# same BIDS dataset (participants.tsv, dataset_description.json, ...) and the same
# sourcedata/code/heuristic.py. If two wrote the same file at once, it could end up
# half one and half the other. So before notebook 03-06, a job takes a "lock": it creates
# a lock file, which only one job can do at a time. Other jobs wait until it is deleted.

class DatasetLock:
    """A lock file next to the BIDS dataset (<study>/.bids.lock). Works on network file systems:
    it's created atomically, and a lock left by a job that no longer runs is taken over."""

    def __init__(self, bids_root: Path, poll_s: float = 30.0, max_wait_s: float = 24 * 3600):
        # E.g. bids_root /jukebox/lab/study/bids -> lock /jukebox/lab/study/.bids.lock
        # (outside the dataset, so the validator never sees it; the "." hides it)
        self.path = bids_root.parent / f".{bids_root.name}.lock"
        # How often to look again while waiting (30 s), and the longest wait (24 hours)
        self.poll_s, self.max_wait_s = poll_s, max_wait_s
        self.held = False                    # True while this job holds the lock

    def _info(self) -> dict:
        """What goes into the lock file: who holds it, so others can say so (and can tell
        whether that job is still running)."""
        return {"host": socket.gethostname(), "pid": os.getpid(), "user": getpass.getuser(),
                "slurm_job": os.environ.get("SLURM_JOB_ID"), "since": _now().isoformat(timespec="seconds"),
                "for": f"{sys.argv[1:]}"}

    def _holder_gone(self, info: dict) -> bool:
        """True if the job that made the lock file no longer runs (it was killed, or ran out
        of time, before it could delete the file). When unsure, False (keep waiting)."""
        job = info.get("slurm_job")
        if job and shutil.which("squeue"):
            # A Slurm job: ask Slurm about it. No answer (or "Invalid job id") = it's over.
            code, out = _run(["squeue", "-h", "-j", job, "-o", "%T"], timeout=60)
            if code == 0:
                return out.strip() == ""
            if "Invalid job id" in out:
                return True
            return False
        if info.get("host") == socket.gethostname() and not job:
            # Run directly on this same computer: see whether its process still exists.
            # os.kill(pid, 0) sends no signal; it only fails if there is no such process.
            try:
                os.kill(int(info["pid"]), 0)
                return False
            except ProcessLookupError:
                return True
            except (PermissionError, ValueError, KeyError):
                return False
        return False

    def acquire(self):
        """Take the lock, waiting as long as needed (up to max_wait_s) for another job."""
        start, told = time.monotonic(), False      # told: the "waiting" message was shown
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                # O_CREAT | O_EXCL: create the file, but only if it doesn't exist yet. The file
                # system does this in one step, so of two jobs trying at once, only one succeeds.
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
                with os.fdopen(fd, "w") as f:
                    json.dump(self._info(), f)
                self.held = True
                if told:
                    log.info("Got the lock on the BIDS dataset.")
                return
            except FileExistsError:
                # Another job holds the lock. Read who (if the file can't be read, {}).
                try:
                    info = json.loads(self.path.read_text())
                except (OSError, ValueError):
                    info = {}
                # A lock left behind by a job that no longer runs: delete it and try again
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
                time.sleep(self.poll_s)            # wait, then try again

    def release(self):
        """Give the lock back (delete the lock file), if this job holds it."""
        if self.held:
            self.path.unlink(missing_ok=True)
            self.held = False


# =====================================================================================
#  Running a notebook and making its report
# =====================================================================================

# Added to each notebook's HTML report: the Show/Hide code button. These are web-page
# styles (CSS). With the class "pipeline-hide-code" on the page, code cells are hidden,
# except a cell that failed (tagged "pipeline-error" in export_html), which always shows
# its code; the In/Out numbers are hidden too.
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
# The bar at the top of each report: a link back to index.html, the button, and the
# notebook's title. The small JavaScript program switches the "pipeline-hide-code" class
# on and off when the button is clicked, and sets the button's label to match.
# (The doubled braces {{ }} are single braces in the page; {title} is filled in by Python.)
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
    # Loaded here rather than at the top, so the checks above work even without them
    import nbformat                       # reads .ipynb files
    from nbconvert import HTMLExporter    # turns a notebook into a web page

    nb = nbformat.read(executed, as_version=4)
    # Tag every code cell that ended with an error, so its code stays visible (see TOGGLE_CSS)
    for cell in nb.cells:
        if cell.cell_type == "code" and any(o.get("output_type") == "error" for o in cell.get("outputs", [])):
            tags = cell.metadata.setdefault("tags", [])
            if "pipeline-error" not in tags:
                tags.append("pipeline-error")
    # "lab": the same look as JupyterLab
    exporter = HTMLExporter(template_name="lab")
    body, _ = exporter.from_notebook_node(nb, resources={"metadata": {"name": title}})
    # Put the styles at the end of the page's <head> ...
    body = body.replace("</head>", TOGGLE_CSS + "</head>", 1)
    # ... and the bar right after the <body ...> tag, adding the "hide code" class to the
    # tag first if the code should start hidden. re.sub finds the tag and replaces it with
    # what the small function (lambda m: ...) returns.
    body = re.sub(r"<body([^>]*)>",
                  lambda m: (f"<body{m.group(1)}>" if not hide_code else
                             _add_body_class(m.group(0))) + TOGGLE_BAR.format(title=html.escape(title)),
                  body, count=1)
    out.write_text(body, encoding="utf-8")


def _add_body_class(tag: str) -> str:
    """Add the class "pipeline-hide-code" to a <body ...> tag (to its existing classes, if any)."""
    if 'class="' in tag:
        return tag.replace('class="', 'class="pipeline-hide-code ', 1)
    return tag[:-1] + ' class="pipeline-hide-code">'


def run_notebook(step: Step, settings_file: Path, run_dir: Path, kernel: str, hide_code: bool):
    """Run one notebook with papermill, then make its HTML report. Records the result in
    step (status, reason, seconds, html). Never stops the program: a failing notebook is
    recorded as "failed" and the pipeline goes on."""
    import papermill as pm

    # The executed copy goes into the run folder, under the notebook's own name
    step.executed = run_dir / step.notebook.name
    t0 = time.monotonic()                 # start the clock
    log.info(f"===== Notebook {step.key}: {step.notebook.name} =====")
    try:
        # papermill runs every cell of the notebook, top to bottom, and saves a copy with
        # all outputs. parameters: puts "config_file = <the settings file>" into a new cell
        # right after the cell tagged "parameters"; the notebook then reads its settings from
        # that file (pipeline mode). cwd: the folder the notebook runs in. log_output: copy
        # what the notebook prints into this program's log. request_save_on_cell_execute:
        # save the copy after every cell, so it's there even if the job is killed.
        pm.execute_notebook(str(step.notebook), str(step.executed),
                            parameters={"config_file": str(settings_file)},
                            kernel_name=kernel, cwd=str(run_dir), log_output=True, progress_bar=False,
                            request_save_on_cell_execute=True)
        step.status = "ok"
    except pm.PapermillExecutionError as err:
        # A cell raised an error: keep its type and message, e.g. "KeyError: 'MANUFACTURER'"
        # ([:2000]: at most 2000 characters)
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
    # Make the HTML report, also of a failed notebook (that's when it's most useful). If
    # making it fails, say so, but carry on.
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
# The run's summary page: one row per notebook (status, time, details, link to its
# report), any warnings, and information about the run. It is written at the start and
# again after each notebook, so it shows the progress while the job runs.

# How each status is shown, and in which color (dark green, red, grey, orange)
STATUS_TEXT = {"ok": "OK", "failed": "Failed", "skipped": "Skipped", "planned": "Not run"}
STATUS_COLOR = {"ok": "#15803d", "failed": "#b91c1c", "skipped": "#6b7280", "planned": "#b45309"}


def write_index(path: Path, cfg: bc.Config, steps: dict, info: dict, warnings: list, validator: dict | None):
    """Write index.html. info: {label: value} for the "This run" table; validator: the
    error and warning counts notebook 06 saved (None before 06 has run)."""
    e = html.escape            # short name: makes text safe inside the page (e.g. "<" -> "&lt;")
    rows = []
    for k, s in steps.items():
        name = STEP_NAMES[k]
        # Link to the notebook's report if there is one
        link = f'<a href="{e(s.html.name)}">{e(name)}</a>' if s.html else e(name)
        detail = e(s.reason)
        # For 06, show the validator's counts first
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
    # The page itself. In this f-string, {name} inserts a value and {{ }} are literal braces
    # (needed in the CSS).
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
    """Read --steps, e.g. "4,5" or "04,05" -> ["04", "05"]. Anything else is an error."""
    # Split at commas, drop spaces and empty parts, and pad "4" to "04" (zfill)
    keys = [k.strip().zfill(2) for k in text.split(",") if k.strip()]
    bad = [k for k in keys if k not in bc.NOTEBOOKS]
    if bad or not keys:
        # argparse shows this message and stops
        raise argparse.ArgumentTypeError(f"--steps takes notebook numbers 01-06, e.g. 04,05 (got {text!r})")
    return keys


def slurm_options(cfg: bc.Config) -> list:
    """The sbatch options from global-study.yaml's "slurm:" section (used by run_bids-pipeline.sh)."""
    sl = cfg.study["slurm"]
    # The Slurm log goes into the session's reports/ folder, which must exist before
    # submitting (Slurm can't create it). %j is replaced by Slurm with the job number.
    reports = cfg.session_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    opts = [f"--job-name=bids_{cfg.stem}", f"--time={sl['time']}", f"--mem={sl['mem']}",
            f"--cpus-per-task={sl['cpus']}", "--nodes=1", "--ntasks=1",
            f"--output={reports / 'slurm-%j.log'}"]
    if sl["partition"]:
        opts.append(f"--partition={sl['partition']}")
    # Email notices (optional settings, so older global-study.yaml files without them still work)
    if sl.get("mail_user"):
        opts += [f"--mail-user={sl['mail_user']}", f"--mail-type={','.join(sl.get('mail_type') or ['END', 'FAIL'])}"]
    return opts


def _copy_config(cfg: bc.Config, dest: Path):
    """Copy the configuration files this run read into dest (the study folder's layout).

    So the report folder records exactly which settings were used, even if the files are
    edited later. Files outside the bids-config folder (e.g. facility.yaml) go into
    dest/other/."""
    root = cfg.study_file.parent                  # the bids-config folder
    files = [cfg.subject_file, cfg.study_file] + [Path(p) for p in cfg.files.values() if p]
    # (The loop adds the files inside folders, like text/, to the end of the list as it goes.)
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
            rel = p.resolve().relative_to(root.resolve())   # e.g. subjects/sub-01.yaml
        except ValueError:                                  # not inside bids-config
            rel = Path("other") / p.name
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest / rel)                         # copy2 also keeps the file's date


# papermill writes a line before and after every cell ("Executing Cell 12----"), plus a
# few others at the start. These patterns find those lines, so they can be left out.
_PAPERMILL_NOISE = re.compile(r"^(Executing Cell \d+-*|Ending Cell \d+-*|Executing notebook with kernel:.*|"
                             r"Input Notebook:.*|Output Notebook:.*|Working directory:.*|Black is not installed.*)$")


class _QuietPapermill(logging.Filter):
    """Keep the notebooks' printed output in the log, without papermill's per-cell markers."""

    def filter(self, record):
        # Called for every log message; True = show it, False = leave it out
        if not record.name.startswith("papermill"):
            return True                        # not from papermill: always show
        message = record.getMessage().rstrip()
        if not message.strip() or _PAPERMILL_NOISE.match(message.strip()):
            return False                       # empty lines and per-cell markers
        record.msg, record.args = message, None   # without the trailing blank lines
        return True


def _setup_logging(log_file: Path | None = None):
    """Send the log messages to the screen (in a job: the Slurm log) and, once the run
    folder exists, also to pipeline.log in it. Each line starts with the time and level,
    e.g. "14:02:11 INFO    Notebook 01 finished in 3:12"."""
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)              # show INFO, WARNING and ERROR (not DEBUG)
    # The screen: added only once, even if this is called again for pipeline.log
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in root.handlers):
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(fmt)
        h.addFilter(_QuietPapermill())
        root.addHandler(h)
    # The file
    if log_file is not None:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.addFilter(_QuietPapermill())
        root.addHandler(fh)
    # papermill logs every cell's output under its own name; keep it, without the noise
    logging.getLogger("papermill").setLevel(logging.INFO)
    # Other packages' routine messages: only their warnings and errors
    for noisy in ("traitlets", "asyncio", "blib2to3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main(argv=None) -> int:
    """The whole run, from the command line to the summary. Returns the exit status
    (0 = all ran, 1 = a notebook failed or was skipped, 2 = stopped before running)."""

    # ---- 1. Read the command line ----
    parser = argparse.ArgumentParser(description="Run the Pygers-MEG-to-BIDS notebooks for one subject/session.")
    parser.add_argument("subject_file", help="e.g. /jukebox/<lab>/<study>/bids-config/subjects/sub-01.yaml")
    parser.add_argument("--steps", type=_parse_steps,
                        help="the notebooks to run, e.g. 04,05 (instead of steps: in global-study.yaml)")
    parser.add_argument("--check", action="store_true", help="only check the configuration, data and software")
    parser.add_argument("--kernel", default="python3",
                        help="the Jupyter kernel the notebooks run in (default python3: this environment's)")
    parser.add_argument("--notebooks", type=Path, default=NOTEBOOKS_DIR,
                        help=f"the folder with the notebooks (default {NOTEBOOKS_DIR})")
    parser.add_argument("--slurm-options", action="store_true",
                        help="print the sbatch options from global-study.yaml and stop (used by run_bids-pipeline.sh)")
    args = parser.parse_args(argv)
    _setup_logging()                          # messages to the screen for now

    # ---- 2. Read the configuration ----
    # bc.load reads the subject file and every file it leads to (global-study.yaml, ...). A
    # missing file or broken YAML stops here.
    try:
        cfg = bc.load(args.subject_file)
    except bc.ConfigError as err:
        log.error("Could not read the configuration:\n  " + "\n  ".join(err.problems))
        return 2
    # --slurm-options (asked for by run_bids-pipeline.sh): print the sbatch options and stop
    if args.slurm_options:
        try:
            print(" ".join(slurm_options(cfg)))
        except (KeyError, TypeError, OSError) as err:
            log.error(f"Could not get the Slurm settings from global-study.yaml: {err}")
            return 2
        return 0
    # Check every setting (misspelled names, missing values, leftover EDIT ME, ...)
    problems = bc.check(cfg)
    if problems:
        log.error(f"{len(problems)} problem(s) in the configuration:\n  - " + "\n  - ".join(problems))
        return 2

    # ---- 3. Which notebooks run ----
    steps = plan_steps(cfg, args.steps)
    run_keys = [k for k, s in steps.items() if s.status == "planned"]   # e.g. ["01", "04", "05", "06"]

    # ---- 4. Find the inputs (only those the chosen notebooks use) and the notebooks ----
    problems = []
    # bc.find_inputs looks for the .edf and the DICOMs of the data the study has. Give it a
    # copy of the configuration with eye tracking / MRI turned off when 02 / 03 won't run,
    # so a run without them doesn't look for (or complain about) files it won't use.
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

    # The run folder's name is the start time, e.g. reports/20261027-141503. It isn't
    # created yet (nothing is, until the checks pass), but the settings need to know it:
    # notebook 06 saves the validator's counts into it.
    started = _now()
    stamp = started.strftime("%Y%m%d-%H%M%S")
    run_dir = cfg.session_dir / "reports" / stamp
    summary_file = run_dir / "06_validator_summary.json"
    # The exact settings each notebook will receive ({"01": {...}, ..., "06": {...}})
    settings = bc.notebook_settings(cfg, inputs, summary_file=str(summary_file))

    # ---- 5. Pre-flight checks (only if everything so far was found) ----
    if not problems:
        more, warnings, facts = preflight(cfg, steps, settings, args.kernel)
        problems += more
    else:
        warnings, facts = [], []

    # Show the plan and what was found, then any problems
    log.info(f"Configuration: {cfg.subject_file} ({cfg.label}, task-{cfg.study['study']['task']})")
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
    # --check: stop here, successfully
    if args.check:
        log.info("Pre-flight checks passed. (--check: nothing was run.)")
        return 0

    # ---- 6. Make the run folder ----
    # exist_ok=False: stop if it already exists (two runs started in the same second)
    run_dir.mkdir(parents=True, exist_ok=False)
    _setup_logging(run_dir / "pipeline.log")      # from now on, messages also go to the file
    settings_file = bc.write_settings(settings, run_dir / "settings.yaml")
    _copy_config(cfg, run_dir / "config")
    # reports/latest: a shortcut ("symbolic link") to the newest run folder. Replaced each
    # run; if the file system doesn't allow it, there's simply no shortcut.
    latest = run_dir.parent / "latest"
    try:
        if latest.is_symlink() or not latest.exists():
            latest.unlink(missing_ok=True)
            latest.symlink_to(stamp, target_is_directory=True)
    except OSError:
        pass
    # Information about this run, for the "This run" table in index.html
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
    write_index(index, cfg, steps, info, warnings, None)    # the first version: all "Not run"

    # ---- 7. Run the notebooks, in order ----
    # try/finally: whatever happens inside (even an unexpected error), the "finally" part
    # runs, so the lock is always given back.
    try:
        for k in run_keys:
            step = steps[k]
            # Skip it if a notebook it needs failed or was skipped in this run, or its input
            # is missing. "blocked" holds the reason, or None if it can run.
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
                continue                          # on to the next notebook
            # Before the first notebook that writes shared files (03-06), take the lock;
            # it's kept until the end
            if k in LOCKED_STEPS and not lock.held:
                lock.acquire()
            run_notebook(step, settings_file, run_dir, args.kernel, hide_code)
            write_index(index, cfg, steps, info, warnings, None)   # show the progress
    finally:
        lock.release()

    # ---- 8. The summary ----
    # The validator's counts, saved by notebook 06 (if it ran)
    validator = None
    if summary_file.is_file():
        try:
            validator = json.loads(summary_file.read_text())
        except ValueError:
            pass
    ended = _now()
    info["Finished"] = ended.strftime("%Y-%m-%d %H:%M:%S %Z")
    info["Total time"] = _duration((ended - started).total_seconds())
    write_index(index, cfg, steps, info, warnings, validator)   # the final version

    # The notebooks that should have run but didn't finish OK. Not a failure: 06 skipped
    # because there is no BIDS dataset yet (e.g. a run of 01 only)
    bad = [steps[k] for k in run_keys if steps[k].status != "ok"
           and not (k == "06" and steps[k].status == "skipped" and "04" not in run_keys)]
    # One line per notebook in the log, e.g. "04 OK  12:31", then where the reports are
    log.info("===== Summary =====")
    for s in steps.values():
        log.info(f"  {s.key} {STATUS_TEXT[s.status]:8s} {_duration(s.seconds) if s.status in ('ok', 'failed') else '':>8s}"
                 f"  {s.reason}")
    if validator:
        log.info(f"  BIDS validator: {validator.get('errors')} error(s), {validator.get('warnings')} warning(s)")
    log.info(f"Reports: {index}")
    return 1 if bad else 0


# How a producing notebook's status reads in a "skipped" reason, e.g. "notebook 01 failed,
# and this one needs the FIF files from it" (used in main, step 7)
STEP_TEXT_PAST = {"failed": "failed", "skipped": "was skipped", "planned": "didn't run"}


# This part runs only when the file is started as a program ("python run_bids-pipeline.py ..."),
# not when another file imports it (as the tests do). sys.exit(main()) runs everything and
# ends the program with main's exit status, which run_bids-pipeline.sh and Slurm then see.
if __name__ == "__main__":
    sys.exit(main())
