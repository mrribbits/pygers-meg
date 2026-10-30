"""Checks the pipeline's step rules and pre-flight checks (no notebooks are run).

Run either way (in the pygers-meg environment, from pygers-meg's 01-explore-raw-files folder):
    python bids-pipeline/tests/test_run_pipeline.py
    python -m pytest bids-pipeline/tests

Uses the same fake study as test_bids_config.py (filled_config()), in a temporary folder.

The tests (see tests/README.md for more):
  test_default_plan_runs_everything      with every step on, all six notebooks are planned
  test_04_brings_05_and_06_always_runs   choosing 04 adds 05; 06 is always added
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
from test_bids_config import filled_config  # noqa: E402


def _planned(cfg, chosen=None):
    return [k for k, s in rp.plan_steps(cfg, chosen).items() if s.status == "planned"]


def test_default_plan_runs_everything():
    assert _planned(bc.load(filled_config())) == ["01", "02", "03", "04", "05", "06"]


def test_04_brings_05_and_06_always_runs():
    cfg = bc.load(filled_config())
    assert _planned(cfg, ["04"]) == ["04", "05", "06"]
    assert _planned(cfg, ["01"]) == ["01", "06"]


def test_missing_data_skips_its_notebooks():
    cfg = bc.load(filled_config())
    cfg.study["data"]["eye_tracking"] = False
    cfg.study["data"]["mri"] = False
    steps = rp.plan_steps(cfg)
    assert [k for k, s in steps.items() if s.status == "planned"] == ["01", "04", "06"]
    assert "eye tracking" in steps["02"].reason


def test_inputs_from_notebooks_that_dont_run_must_exist():
    cfg = bc.load(filled_config())
    steps = rp.plan_steps(cfg, ["04"])
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
