"""Checks that the bids-config template and the notebooks agree.

Run either way (in the pygers-meg environment, from the pygers-meg folder):
    python pipeline/tests/test_bids_config.py
    python -m pytest pipeline/tests

What it checks:
  * every setting a notebook asks for with from_config() is produced by the configuration
    (and nothing extra), for all six notebooks;
  * the template's values equal the notebooks' own example values (apart from a few known,
    intended differences listed below);
  * the template as shipped reports exactly its placeholders, and a filled-in copy is complete;
  * common mistakes give an error.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIPELINE = HERE.parent
REPO = PIPELINE.parent
NOTEBOOKS_DIR = REPO / "01-explore-raw-files"
TEMPLATE = PIPELINE / "templates" / "bids-config"
sys.path.insert(0, str(PIPELINE))
import bids_config as bc  # noqa: E402

# Values the template sets on purpose differently from the notebooks' own examples.
INTENDED_DIFFERENCES = {
    ("04", "DAYSBACK"),            # the notebook may pick a random one; the pipeline needs a fixed one
    ("04", "DS_AUTHORS"),          # [] instead of [""] (used only without CITATION.cff)
    ("04", "DS_LICENSE"),          # "CC0" instead of "" (matches the LICENSE text)
    ("04", "CFF_AUTHORS"),         # filled in by the test
    ("04", "CFF_DATE_RELEASED"),   # the date of the newest CHANGES entry instead of today
    ("05", "BLINK_PREVIEW"),       # off in the pipeline unless a study sets it up
}


# ---- what the notebooks ask for ------------------------------------------------------

def notebook_settings_in_code() -> dict:
    """{notebook: {setting: example value or None}} from the from_config() calls in the notebooks."""
    out = {}
    for nb_file in sorted(NOTEBOOKS_DIR.glob("0[1-6]_*.ipynb")):
        key, found = nb_file.name[:2], {}
        for cell in json.loads(nb_file.read_text(encoding="utf-8"))["cells"]:
            if cell["cell_type"] != "code":
                continue
            src = "".join(cell["source"])
            calls = [m for m in re.finditer(r"from_config\(([^)]*)\)", src)
                     if "Example:" not in src[max(0, m.start() - 40):m.start()]]
            if not calls:
                continue
            py = "\n".join((re.match(r"\s*", l).group(0) + "pass") if l.lstrip().startswith(("%", "!")) else l
                           for l in src.splitlines())
            cut = py.rfind("\n", 0, calls[0].start()) + 1
            try:
                tree = ast.parse(py[:cut])
            except SyntaxError:
                tree = ast.parse("")
            for m in calls:
                for name in re.findall(r'"([A-Z_0-9]+)"', m.group(1)):
                    value = None
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                            try:
                                value = ("literal", ast.literal_eval(node.value))
                            except Exception:
                                value = None
                    found[name] = value
        out[key] = found
    return out


# ---- a filled-in copy of the template with a fake raw tree ----------------------------

def filled_config(fill=True) -> Path:
    root = Path(tempfile.mkdtemp())
    cfg_dir = root / "bids-config"
    shutil.copytree(TEMPLATE, cfg_dir, ignore=shutil.ignore_patterns(".*", "__pycache__"))
    if fill:
        for f in cfg_dir.rglob("*"):
            if f.is_file():
                s = f.read_text(encoding="utf-8")
                s = s.replace("/jukebox/[yourpath]/auditory-oddball", str(root))
                s = s.replace("/jukebox/[yourpath]/singularity-containers", "/jukebox/pinsk/singularity-containers")
                s = s.replace('family-names: "EDIT ME"', 'family-names: "Test"')
                s = s.replace('given-names: "EDIT ME"', 'given-names: "Person"')
                s = s.replace('"EDIT ME: Institution"', '"Test University"')
                s = re.sub(r'date: "EDIT ME"', 'date: "2026-11-01"', s)
                s = s.replace("EDIT ME: replace this paragraph with the full legal code text if you are publishing the dataset.\n", "")
                s = s.replace("daysback: null", "daysback: 41234")
                f.write_text(s, encoding="utf-8")
    raw = root / "sourcedata" / "sub-01" / "ses-01" / "raw"
    for d in ["cMEG", "einscan", "eye", "mri/dcm", "behav"]:
        (raw / d).mkdir(parents=True, exist_ok=True)
    (raw / "eye" / "test.EDF").write_bytes(b"x")
    (raw / "mri" / "dcm" / "IM_0001.dcm").write_bytes(b"x")
    return cfg_dir / "subjects" / "sub-01.yaml"


# ---- tests ---------------------------------------------------------------------------

def test_names_match_the_notebooks():
    wanted = notebook_settings_in_code()
    produced = bc.notebook_settings(bc.load(filled_config()))
    for nb in bc.NOTEBOOKS:
        got = set(produced[nb]) - {"_paths"}
        assert got == set(wanted[nb]), (f"notebook {nb}: missing {sorted(set(wanted[nb]) - got)}, "
                                        f"extra {sorted(got - set(wanted[nb]))}")


def test_values_match_the_notebook_examples():
    wanted = notebook_settings_in_code()
    produced = bc.notebook_settings(bc.load(filled_config()))
    differences = []
    for nb, settings in wanted.items():
        for name, example in settings.items():
            if example is None or (nb, name) in INTENDED_DIFFERENCES:
                continue
            value = example[1]
            if isinstance(value, tuple):
                value = list(value)
            if produced[nb][name] != value:
                differences.append(f"{nb} {name}: notebook {value!r:.60} vs template {produced[nb][name]!r:.60}")
    assert not differences, "\n".join(differences)


def test_template_lists_its_placeholders():
    problems = bc.check(bc.load(filled_config(fill=False)))
    assert problems, "the template as shipped should not pass"
    assert all("EDIT ME" in p or "[yourpath]" in p or "daysback" in p for p in problems), problems


def test_filled_template_is_complete():
    cfg = bc.load(filled_config())
    assert bc.check(cfg) == []
    inputs = bc.find_inputs(cfg)
    assert inputs["edf_file"].name == "test.EDF"
    assert inputs["dicom_template"] == "sub-{subject}/ses-{session}/raw/mri/dcm/*.dcm"


def _problems_after(file, old, new):
    subject_file = filled_config()
    p = subject_file.parent.parent / file
    text = p.read_text(encoding="utf-8")
    assert old in text, (file, old)
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    return bc.check(bc.load(subject_file))


def test_mistakes_are_caught():
    cases = [
        ("processing.yaml", "  keep_work: false", "  keep_wrok: false", "misspelled"),
        ("study.yaml", "  overwrite: true", "  overwrite: maybe", "true or false"),
        ("processing.yaml", "    error feedback: 32\n", "    error feedback: 32\n    button: 64\n", "no description"),
        ("processing.yaml", '    blink_cue: "blink"', '    blink_cue: "blinks"', "name_map"),
        ("subjects/sub-01.yaml", '  handedness: "right"', '  handedness: "right"\n  weight: 70', "participants_columns"),
        ("text/README.md", "## Task: {task}", "## Task: {task_name}", "unknown placeholder"),
        ("subjects/sub-01.yaml", 'subject: "01"', 'subject: "sub-01"', "letters and digits"),
        ("study.yaml", '/sourcedata"', '/bids/sourcedata"', "inside"),
        ("study.yaml", '  pydeface_sif: "/jukebox/pinsk/singularity-containers/pydeface/pydeface-2026-08-10.sif"',
         "  pydeface_sif: null", "needed because data.mri"),
    ]
    for file, old, new, expect in cases:
        problems = _problems_after(file, old, new)
        assert any(expect in p for p in problems), (expect, problems)


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
