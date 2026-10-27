"""Checks the pipeline's step rules and pre-flight checks (no notebooks are run).

Run either way (in the pygers-meg environment, from pygers-meg's 01-explore-raw-files folder):
    python pipeline/tests/test_run_pipeline.py
    python -m pytest pipeline/tests
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import bids_config as bc  # noqa: E402
import run_pipeline as rp  # noqa: E402
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
