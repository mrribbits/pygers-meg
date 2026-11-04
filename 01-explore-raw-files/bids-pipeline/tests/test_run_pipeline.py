"""Checks the pipeline's step rules and pre-flight checks (no notebooks are run).

Run either way (in the pygers-meg environment, from pygers-meg's 01-explore-raw-files folder):
    python bids-pipeline/tests/test_run_pipeline.py
    python -m pytest bids-pipeline/tests

Uses the same fake study as test_bids_config.py (filled_config()), in a temporary folder.

The tests (see tests/README.md for more):
  test_default_plan_runs_everything      with every step on, all six notebooks are planned
  test_06_always_runs_and_04_alone_warns 06 is always added; 04 without 05 gives a warning
  test_missing_data_skips_its_notebooks  no eye tracking / MRI: their notebooks are skipped
  test_inputs_from_notebooks_that_dont_run_must_exist
                                         --steps 04 without 01's FIF files stops the pre-flight
                                         check and says which notebook to add
  test_paths_without_sessions            no "ses-" in the runner's paths or job name
  test_notebooks_found_by_exact_name
                                         the six notebooks by exact name; copies are ignored
  test_steps_option                      "4,5" -> 04, 05; "07" is rejected
  test_slurm_options                     sbatch options from global-study.yaml, incl. optional email
  test_lock_is_exclusive_and_released    only one job at a time holds the BIDS dataset lock
  test_runs_plan                         with runs:, 01/02/04/05 once per run, 03 and 06 once; a run
                                         without eye tracking skips its 02 and 05
  test_runs_preflight                    each run's recording and files are checked, and named in
                                         the messages
  test_runs_main                         a whole run (fake notebooks): one settings file per run, each
                                         notebook gets its run's file, a failed 01 run-02 skips only
                                         04 run-02 and 05 run-02
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import bids_config as bc  # noqa: E402
# run_bids-pipeline.py has a "-" in its name, so it can't be loaded with a plain "import";
# load it from its file instead, under the name run_bids_pipeline.
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("run_bids_pipeline", HERE.parent / "run_bids-pipeline.py")
rp = importlib.util.module_from_spec(_spec)
sys.modules["run_bids_pipeline"] = rp
_spec.loader.exec_module(rp)
from test_bids_config import filled_config, with_runs  # noqa: E402


def _planned(cfg, chosen=None):
    return [k for k, s in rp.plan_steps(cfg, chosen).items() if s.status == "planned"]


def test_default_plan_runs_everything():
    assert _planned(bc.load(filled_config())) == ["01", "02", "03", "04", "05", "06"]


def test_06_always_runs_and_04_alone_warns():
    cfg = bc.load(filled_config())
    assert _planned(cfg, ["04"]) == ["04", "06"]          # 05 is not added...
    assert _planned(cfg, ["01"]) == ["01", "06"]
    settings = bc.notebook_settings(cfg, {"edf_file": None, "dicom_template": None})
    _, warnings, _ = rp.preflight(cfg, rp.plan_steps(cfg, ["04"]), settings, "python3")
    assert any("05 isn't chosen to run" in w for w in warnings), warnings   # ...but there is a warning
    _, warnings, _ = rp.preflight(cfg, rp.plan_steps(cfg, ["04", "05"]), settings, "python3")
    assert not any("05 isn't chosen to run" in w for w in warnings), warnings


def test_missing_data_skips_its_notebooks():
    cfg = bc.load(filled_config())
    cfg.study["data"]["eye_tracking"] = False
    cfg.study["data"]["mri"] = False
    steps = rp.plan_steps(cfg)
    assert [k for k, s in steps.items() if s.status == "planned"] == ["01", "04", "06"]
    assert "eye tracking" in steps["02"].reason


def test_inputs_from_notebooks_that_dont_run_must_exist():
    cfg = bc.load(filled_config())
    steps = rp.plan_steps(cfg, ["04", "05"])          # 05 needs 02's files, 04 needs 01's and 03's
    settings = bc.notebook_settings(cfg, {"edf_file": None, "dicom_template": None})
    needs = rp.requirements(cfg, settings)
    missing = [(producer, what) for step in ("04", "05") for producer, what, paths, check in needs[step]
               if not check(paths)]
    assert {p for p, _ in missing} == {"01", "02", "03", "04"}, missing
    problems, _, _ = rp.preflight(cfg, steps, settings, "python3")
    for producer in ("01", "02", "03"):
        assert any(f"from notebook {producer}, which isn't chosen" in p for p in problems), problems


def test_paths_without_sessions():
    cfg = bc.load(filled_config(sessions=False))
    settings = bc.notebook_settings(cfg, bc.find_inputs(cfg))
    for step, needs in rp.requirements(cfg, settings).items():
        for producer, what, paths, check in needs:
            assert "ses-" not in str(paths[0]), (step, paths[0])
    assert str(rp.requirements(cfg, settings)["05"][-1][2][0]).endswith("bids/sub-01/meg/sub-01_task-oddballftcued*_meg.fif")
    assert "--job-name=bids_sub-01" in rp.slurm_options(cfg)
    assert cfg.session_dir == cfg.sourcedata_root / "sub-01"


def test_notebooks_found_by_exact_name():
    import tempfile
    folder = Path(tempfile.mkdtemp())
    for name in bc.NOTEBOOK_FILES.values():
        (folder / name).write_text("{}")
    # Copies with similar names are ignored
    (folder / "04_FIF_to_BIDS_old.ipynb").write_text("{}")
    (folder / "04_FIF_to_BIDS-Copy1.ipynb").write_text("{}")
    found = rp.find_notebooks(folder)
    assert found["04"].name == "04_FIF_to_BIDS.ipynb"
    # A missing notebook is reported, with the similar names found
    (folder / "04_FIF_to_BIDS.ipynb").unlink()
    try:
        rp.find_notebooks(folder)
    except bc.ConfigError as err:
        assert "04_FIF_to_BIDS.ipynb not found" in str(err) and "04_FIF_to_BIDS_old.ipynb" in str(err)
    else:
        raise AssertionError("a missing notebook was not reported")


def test_steps_option():
    assert rp._parse_steps("4,5") == ["04", "05"]
    try:
        rp._parse_steps("07")
    except Exception as err:
        assert "01-06" in str(err)
    else:
        raise AssertionError("07 accepted")


def test_slurm_options():
    cfg = bc.load(filled_config())
    opts = rp.slurm_options(cfg)
    assert "--time=04:00:00" in opts and "--mem=32G" in opts and "--cpus-per-task=4" in opts
    assert not any(o.startswith("--partition") for o in opts)
    assert not any(o.startswith("--mail") for o in opts)           # mail_user: null = no email
    cfg.study["slurm"].update(mail_user="netid@princeton.edu", mail_type=["BEGIN", "END", "FAIL"])
    opts = rp.slurm_options(cfg)
    assert "--mail-user=netid@princeton.edu" in opts and "--mail-type=BEGIN,END,FAIL" in opts
    assert bc.check(cfg) == []
    cfg.study["slurm"].update(mail_user="not an address", mail_type=["DONE"])
    problems = bc.check(cfg)
    assert any("mail_user" in p for p in problems) and any("mail_type" in p for p in problems), problems


def test_lock_is_exclusive_and_released():
    import tempfile
    bids = Path(tempfile.mkdtemp()) / "bids"
    a, b = rp.DatasetLock(bids, poll_s=0.01, max_wait_s=0.05), rp.DatasetLock(bids, poll_s=0.01, max_wait_s=0.05)
    a.acquire()
    try:
        b.acquire()
    except TimeoutError:
        pass
    else:
        raise AssertionError("second lock acquired while the first was held")
    a.release()
    b.acquire()
    b.release()
    assert not a.path.exists()


def test_runs_plan():
    cfg = bc.load(with_runs(filled_config()))
    units = rp.expand_runs(cfg, rp.plan_steps(cfg))
    assert [u.label for u in units] == ["01 run-01", "01 run-02", "02 run-01", "02 run-02", "03",
                                        "04 run-01", "04 run-02", "05 run-01", "05 run-02", "06"]
    assert all(u.status == "planned" for u in units)
    # A run without eye tracking: its 02 and 05 are skipped
    cfg = bc.load(with_runs(filled_config(), edf_files=("R1.EDF", None)))
    status = {u.label: u.status for u in rp.expand_runs(cfg, rp.plan_steps(cfg))}
    assert status["02 run-02"] == status["05 run-02"] == "skipped" and status["05 run-01"] == "planned"
    # Without runs: the same six steps as always
    cfg = bc.load(filled_config())
    assert [u.label for u in rp.expand_runs(cfg, rp.plan_steps(cfg))] == list(bc.NOTEBOOKS)


def _by_run(cfg):
    inputs = bc.find_inputs(cfg)
    return {r: bc.notebook_settings(cfg, inputs, run=r) for r in cfg.run_labels}


def test_runs_preflight():
    cfg = bc.load(with_runs(filled_config()))
    by_run = _by_run(cfg)
    first = by_run["01"]
    # 04 alone: each run needs its own FIF files from 01, named with the run
    problems, _, _ = rp.preflight(cfg, rp.plan_steps(cfg, ["04", "05"]), first, "python3", by_run)
    assert any("Notebook 04 (run 02) needs the FIF files" in p and "_run-02_meg.fif" in p for p in problems), problems
    assert any("Notebook 05 (run 01) needs the eye2bids files" in p for p in problems), problems
    # 01: each run's recording is looked for (none exist in the fake study)
    problems, _, _ = rp.preflight(cfg, rp.plan_steps(cfg, ["01"]), first, "python3", by_run)
    assert any("runs: run '02': task_prefix '20260929_142000'" in p for p in problems), problems
    # Behavioral logs with runs: not supported yet, said clearly
    cfg.study["data"]["behavioral"] = True
    problems, _, _ = rp.preflight(cfg, rp.plan_steps(cfg, ["04"]), first, "python3", by_run)
    assert any("several runs aren't supported yet" in p for p in problems), problems


def test_runs_main():
    import yaml
    subject_file = with_runs(filled_config())
    cfg = bc.load(subject_file)
    cfg_study = subject_file.parent.parent / "global-study.yaml"
    # No MRI and no behavioral logs, so the fake notebooks only need to make MEG and eye files
    text = cfg_study.read_text(encoding="utf-8").replace("  mri: true", "  mri: false").replace("  behavioral: true", "  behavioral: false")
    cfg_study.write_text(text, encoding="utf-8")
    calls = []

    def fake_run_notebook(step, settings_file, run_dir, kernel, hide_code):
        # What each notebook would make, from the settings it was given
        nb = yaml.safe_load(Path(settings_file).read_text())[step.key]
        calls.append((step.label, Path(settings_file).name, nb.get("RUN")))
        if step.label == "01 run-02":                      # this one fails
            step.status, step.reason = "failed", "RuntimeError: cmeg2fif failed"
            return
        if step.key == "01":
            for f in (nb["OUT_DIR"] + "/sub-01_ses-01_task-oddballftcued_run-" + nb["RUN"] + "_meg.fif",
                      nb["OUT_DIR"] + "/sub-01_ses-01_task-noise_meg.fif"):
                Path(f).parent.mkdir(parents=True, exist_ok=True)
                Path(f).write_bytes(b"x")
        if step.key == "02":
            out = Path(nb["OUT_DIR"]) / f"sub-01_ses-01_task-oddballftcued_run-{nb['RUN']}_recording-eye1_physio.tsv.gz"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"x")
        if step.key == "04":
            meg = Path(nb["BIDS_ROOT"]) / "sub-01" / "ses-01" / "meg"
            meg.mkdir(parents=True, exist_ok=True)
            (meg / f"sub-01_ses-01_task-oddballftcued_run-{nb['RUN']}_meg.fif").write_bytes(b"x")
            (Path(nb["BIDS_ROOT"]) / "dataset_description.json").write_text("{}")
        step.status = "ok"

    saved = (rp.preflight, rp.run_notebook, rp.find_notebooks)
    rp.preflight = lambda *a, **k: ([], [], [])
    rp.run_notebook = fake_run_notebook
    rp.find_notebooks = lambda folder: {k: Path(folder) / n for k, n in bc.NOTEBOOK_FILES.items()}
    try:
        status = rp.main([str(subject_file), "--steps", "01,02,04,05"])
    finally:
        rp.preflight, rp.run_notebook, rp.find_notebooks = saved
    assert status == 1                                     # 01 run-02 failed
    ran = [c[0] for c in calls]
    assert ran == ["01 run-01", "01 run-02", "02 run-01", "02 run-02", "04 run-01", "05 run-01", "06"], ran
    assert all(f == (f"settings_run-{r}.yaml" if r else "settings_run-01.yaml") for _, f, r in calls), calls
    run_dir = max((cfg.session_dir / "reports").glob("2*"))
    assert (run_dir / "settings_run-01.yaml").is_file() and (run_dir / "settings_run-02.yaml").is_file()
    index = (run_dir / "index.html").read_text()
    assert "04 run-02" in index and "notebook 01 run-02 failed" in index


if __name__ == "__main__":
    failed = 0
    for name, func in list(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"PASS  {name}")
            except AssertionError as err:
                failed += 1
                print(f"FAIL  {name}\n      {err}")
    sys.exit(1 if failed else 0)
