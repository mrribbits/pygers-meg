"""Read, check and resolve a study's bids-config files for the Pygers-MEG pipeline.

A study's settings are spread over several YAML files (see templates/bids-config/):

    subjects/sub-01.yaml   one session: labels, recording names, bad channels, participant
    study.yaml             where the data are, what to run, options, job settings, and
                           (at the end) the published dataset's description
    processing.yaml        how each notebook processes the data
    private.yaml           DAYSBACK, the secret date shift
    text/                  README, task description, instructions, license
    facility.yaml          the Scully MEG lab's settings (in this folder), plus the study's
                           facility_overrides

This module reads them all (starting from the subject file), checks them, and turns them
into the exact settings each notebook asks for with from_config(), e.g. "EVENT_ID".

Check a configuration from the command line (on scotty, in the pygers-meg environment, from
pygers-meg's 01-explore-raw-files folder):

    python pipeline/bids_config.py /path/to/bids-config/subjects/sub-01.yaml
    python pipeline/bids_config.py /path/to/bids-config/subjects/sub-01.yaml --show 04

The first form lists every problem it finds (or says the configuration is complete);
--show NN also prints the settings notebook NN would receive.
"""
from __future__ import annotations

import argparse
import copy
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema
import yaml

PIPELINE_DIR = Path(__file__).resolve().parent
SCHEMA_FILE = PIPELINE_DIR / "bids_config.schema.yaml"
DEFAULT_FACILITY_FILE = PIPELINE_DIR / "facility.yaml"

# Text that marks a value the user still has to fill in.
PLACEHOLDER_MARKS = ("EDIT ME", "[yourpath]")

# The files in text/ (key -> file name).
TEXT_FILES = {
    "readme": "README.md",
    "task_description": "task_description.txt",
    "instructions": "instructions.txt",
    "license": "LICENSE.txt",
}

# The sections at the end of study.yaml that describe the published dataset (until
# October 2026 a separate dataset.yaml). load() keeps them apart, in Config.dataset.
DATASET_KEYS = ("description", "citation", "changes", "participants_columns", "task_sidecar")

# The {placeholders} notebook 04 can fill in (its fill_placeholders function). Keep in step
# with that function.
KNOWN_PLACEHOLDERS = {
    "ds_name", "task", "event_codes", "n_meg_channels", "n_ref_channels", "sfreq",
    "emptyroom_layout", "cmeg2fif_version", "mne_version", "mne_bids_version", "license",
    "anonymization_note",
}

NOTEBOOKS = ("01", "02", "03", "04", "05", "06")


class ConfigError(Exception):
    """Raised with a list of problems when the configuration can't be used."""

    def __init__(self, problems):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("\n".join(self.problems))


@dataclass
class Config:
    """Everything read from a study's configuration files."""

    subject_file: Path
    study_file: Path
    files: dict = field(default_factory=dict)      # which file each part came from
    study: dict = field(default_factory=dict)
    subject: dict = field(default_factory=dict)
    processing: dict = field(default_factory=dict)  # with the subject's overrides applied
    dataset: dict = field(default_factory=dict)
    private: dict = field(default_factory=dict)
    facility: dict = field(default_factory=dict)    # with the study's facility_overrides applied
    text: dict = field(default_factory=dict)

    # ---- paths built from the two roots and the labels ----
    @property
    def sourcedata_root(self) -> Path:
        return Path(self.study["paths"]["sourcedata_root"])

    @property
    def bids_root(self) -> Path:
        return Path(self.study["paths"]["bids_root"])

    @property
    def session(self) -> str | None:
        """The session label, or None in a study without sessions."""
        return self.subject.get("session")

    @property
    def ses_parts(self) -> list:
        """["ses-YY"] (a folder level), or [] without sessions."""
        return [f"ses-{self.session}"] if self.session else []

    @property
    def session_dir(self) -> Path:
        """<sourcedata_root>/sub-XX/ses-YY (or sub-XX without sessions): the session's raw data
        and every intermediate file."""
        return self.sourcedata_root.joinpath(f"sub-{self.subject['subject']}", *self.ses_parts)

    def bids_dir(self, root: Path | None = None) -> Path:
        """<root>/sub-XX/ses-YY (or sub-XX): this session's folder in a BIDS-like tree."""
        return Path(root or self.bids_root).joinpath(f"sub-{self.subject['subject']}", *self.ses_parts)

    @property
    def label(self) -> str:
        """e.g. "sub-01, ses-01" or "sub-01", for messages."""
        return f"sub-{self.subject['subject']}" + (f", ses-{self.session}" if self.session else "")

    @property
    def raw_dir(self) -> Path:
        return self.session_dir / "raw"

    @property
    def stem(self) -> str:
        """sub-XX_ses-YY (or sub-XX without sessions), the start of every file name."""
        return f"sub-{self.subject['subject']}" + (f"_ses-{self.session}" if self.session else "")


# =====================================================================================
#  Reading
# =====================================================================================

def deep_merge(base: dict, override: dict) -> dict:
    """A copy of base with override's values on top. Nested dicts are merged key by key;
    any other value (including lists) replaces the one in base."""
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _read_yaml(path: Path, what: str) -> dict:
    if not path.is_file():
        raise ConfigError(f"{what} not found: {path}")
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as err:
        raise ConfigError(f"{what} ({path}) is not valid YAML:\n{err}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{what} ({path}) should hold settings (name: value lines), but it's empty or a plain value.")
    return data


def load(subject_file) -> Config:
    """Read the subject file and every file it leads to. Raises ConfigError if a file is
    missing or isn't valid YAML (it doesn't check the settings themselves: see check())."""
    subject_file = Path(subject_file).expanduser().resolve()
    subject = _read_yaml(subject_file, "Subject file")
    if "study_config" not in subject:
        raise ConfigError(f"{subject_file}: no study_config (the path of study.yaml, e.g. \"../study.yaml\").")
    study_file = (subject_file.parent / subject["study_config"]).resolve()
    study = _read_yaml(study_file, "Study file (study_config)")

    files_section = study.get("files") or {}
    if "dataset" in files_section:
        raise ConfigError(
            f"{study_file}: dataset.yaml is now part of study.yaml. Paste its sections (description, "
            "citation, changes, participants_columns, task_sidecar) at the end of study.yaml, and remove "
            "the line 'dataset: ...' under files:. (See the template's study.yaml.)")
    # The published dataset's description: the last sections of study.yaml
    dataset = {k: study[k] for k in DATASET_KEYS if k in study}
    study = {k: v for k, v in study.items() if k not in DATASET_KEYS}
    config_dir = study_file.parent
    facility_file = files_section.get("facility")
    files = {
        "subject": subject_file,
        "study": study_file,
        "processing": config_dir / files_section.get("processing", "processing.yaml"),
        "private": config_dir / files_section.get("private", "private.yaml"),
        "text_dir": config_dir / files_section.get("text_dir", "text"),
        "facility": (config_dir / facility_file).resolve() if facility_file else DEFAULT_FACILITY_FILE,
    }
    processing = _read_yaml(files["processing"], "processing.yaml")
    private = _read_yaml(files["private"], "private.yaml")
    facility = _read_yaml(files["facility"], "facility.yaml")

    text, missing = {}, []
    for key, name in TEXT_FILES.items():
        path = files["text_dir"] / name
        if path.is_file():
            text[key] = path.read_text(encoding="utf-8")
        else:
            missing.append(str(path))
    if missing:
        raise ConfigError([f"Text file not found: {m}" for m in missing])

    return Config(
        subject_file=subject_file, study_file=study_file, files=files,
        study=study, subject=subject, dataset=dataset, private=private, text=text,
        processing=deep_merge(processing, subject.get("overrides") or {}),
        facility=deep_merge(facility, study.get("facility_overrides") or {}),
    )


# =====================================================================================
#  Checking (nothing here looks at the data on disk)
# =====================================================================================

def _schema():
    with open(SCHEMA_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


# The newest JSON Schema draft this jsonschema version knows. The schema only uses rules that
# Draft 7 already has, so older jsonschema versions (< 4) work too.
_VALIDATOR_CLASS = getattr(jsonschema, "Draft202012Validator", None) or jsonschema.Draft7Validator


def _where(error) -> str:
    """A readable location for a schema error, e.g. events.event_id.blink"""
    parts = [str(p) if not isinstance(p, int) else f"[{p}]" for p in error.absolute_path]
    return ".".join(parts).replace(".[", "[") or "(top level)"


def _schema_problems(cfg: Config) -> list:
    schema = _schema()
    problems = []
    parts = [("study", cfg.study, "study"), ("subject", cfg.subject, "subject"),
             ("processing", cfg.processing, "processing"), ("dataset", cfg.dataset, "study"),
             ("private", cfg.private, "private"), ("facility", cfg.facility, "facility")]
    for part, data, label in parts:
        sub = {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{part}"}
        validator = _VALIDATOR_CLASS(sub)
        file = cfg.files[label].name if label in cfg.files else label
        if label == "processing" and cfg.subject.get("overrides"):
            file += " (+ the subject file's overrides)"
        if label == "facility" and cfg.study.get("facility_overrides"):
            file += " (+ study.yaml's facility_overrides)"
        for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
            msg = err.message
            if err.validator == "additionalProperties":
                msg = msg.replace("Additional properties are not allowed", "Unknown setting(s), misspelled?")
            elif err.validator == "pattern" and err.validator_value == "^[A-Za-z0-9]+$":
                msg = f"{err.instance!r}: use letters and digits only (no '-', '_' or spaces)"
            elif err.validator == "type" and err.validator_value == "boolean":
                msg = f"{err.instance!r} should be true or false"
            problems.append(f"{file}: {_where(err)}: {msg}")
    return problems


def _walk(value, where=""):
    """Every string in a nested structure, with its location."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(v, f"{where}.{k}" if where else str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, f"{where}[{i}]")
    elif isinstance(value, str):
        yield where, value


def _placeholder_problems(cfg: Config) -> list:
    problems = []
    parts = [("study.yaml", cfg.study), (cfg.subject_file.name, cfg.subject),
             ("processing.yaml", cfg.processing), ("study.yaml", cfg.dataset),
             ("facility.yaml", cfg.facility)]
    for file, data in parts:
        for where, text in _walk(data):
            for mark in PLACEHOLDER_MARKS:
                if mark in text:
                    problems.append(f"{file}: {where} still says {mark!r}: {text[:80]!r}")
    for key, name in TEXT_FILES.items():
        for mark in PLACEHOLDER_MARKS:
            for n, line in enumerate(cfg.text.get(key, "").splitlines(), 1):
                if mark in line:
                    problems.append(f"text/{name}, line {n}: still says {mark!r}")
    return problems


def _unknown_placeholders(text: str) -> set:
    return set(re.findall(r"\{(\w+)\}", text)) - KNOWN_PLACEHOLDERS


def _cross_problems(cfg: Config) -> list:
    """Settings in different places that must agree."""
    p = []
    ev = cfg.processing.get("events", {})
    names = set(ev.get("event_id", {}))

    # Events: every name described; durations / extra columns only use known names
    undescribed = sorted(names - set(ev.get("trial_type_levels", {})))
    if undescribed:
        p.append(f"processing.yaml: events.trial_type_levels has no description for {undescribed}")
    unknown = sorted(set(ev.get("trial_type_levels", {})) - names)
    if unknown:
        p.append(f"processing.yaml: events.trial_type_levels describes {unknown}, which aren't in events.event_id")
    unknown = sorted(set(ev.get("durations", {})) - names)
    if unknown:
        p.append(f"processing.yaml: events.durations lists {unknown}, which aren't in events.event_id")
    for col, spec in (ev.get("extra_columns") or {}).items():
        unknown = sorted(set((spec or {}).get("values", {})) - names)
        if unknown:
            p.append(f"processing.yaml: events.extra_columns.{col}.values lists {unknown}, which aren't in events.event_id")
    codes = list(ev.get("event_id", {}).values())
    dupes = sorted({c for c in codes if codes.count(c) > 1})
    if dupes:
        p.append(f"processing.yaml: events.event_id uses the code(s) {dupes} for more than one name")

    # Sessions: every subject file follows the study's choice
    sessions, ses = cfg.study.get("study", {}).get("sessions"), cfg.subject.get("session")
    if sessions is True and not ses:
        p.append(f"{cfg.subject_file.name}: session is empty, but study.yaml says the study has sessions "
                 "(study.sessions: true); give the session label, e.g. \"01\"")
    if sessions is False and ses:
        p.append(f"{cfg.subject_file.name}: session is {ses!r}, but study.yaml says the study has no sessions "
                 "(study.sessions: false); set session: null")

    # Software: the MRI containers are needed when the study has MRI
    sw = cfg.study.get("software", {})
    if cfg.study.get("data", {}).get("mri"):
        for name in ("heudiconv_sif", "pydeface_sif"):
            if name in sw and not sw[name]:
                p.append(f"study.yaml: software.{name} is needed because data.mri is true")

    # Sync: the MEG event names it refers to exist
    sync = cfg.processing.get("eye_sync", {})
    if cfg.study.get("data", {}).get("eye_tracking"):
        if sync.get("match_mode") == "name" and sync.get("name_map"):
            unknown = sorted(set(sync["name_map"].values()) - names)
            if unknown:
                p.append(f"processing.yaml: eye_sync.name_map maps to {unknown}, which aren't in events.event_id")
        if sync.get("match_mode") == "name" and not sync.get("name_map") and not sync.get("name_pattern"):
            p.append("processing.yaml: eye_sync needs name_map or name_pattern when match_mode is \"name\"")
        unknown = sorted(set(sync.get("meg_trial_types") or []) - names)
        if unknown:
            p.append(f"processing.yaml: eye_sync.meg_trial_types lists {unknown}, which aren't in events.event_id")
        bp = sync.get("blink_preview", {})
        if bp.get("enabled"):
            starts = [bp["trial_start_type"]] if isinstance(bp.get("trial_start_type"), str) else bp.get("trial_start_type", [])
            unknown = sorted(set(starts + [bp.get("trial_end_type")]) - names)
            if unknown:
                p.append(f"processing.yaml: eye_sync.blink_preview uses {unknown}, which aren't in events.event_id")

    # Participants: every column described
    cols = set(cfg.subject.get("participant") or {})
    undescribed = sorted(cols - set(cfg.dataset.get("participants_columns", {})))
    if undescribed:
        p.append(f"{cfg.subject_file.name}: participant has column(s) {undescribed}; describe them in "
                 "study.yaml (participants_columns)")

    # Bad channels: add and keep don't overlap
    bad = cfg.subject.get("bad_channels")
    if isinstance(bad, dict):
        both = sorted(set(bad.get("add", [])) & set(bad.get("keep", [])))
        if both:
            p.append(f"{cfg.subject_file.name}: bad_channels lists {both} in both add and keep")

    # Dates in CHANGES
    for i, entry in enumerate(cfg.dataset.get("changes") or []):
        d = str(entry.get("date", ""))
        if not any(m in d for m in PLACEHOLDER_MARKS) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            p.append(f"study.yaml: changes[{i}].date should look like 2026-11-01, not {d!r}")

    # Anonymization needs a fixed DAYSBACK
    if cfg.processing.get("anonymization", {}).get("anonymize", True) and not isinstance(cfg.private.get("daysback"), int):
        p.append("private.yaml: daysback is not set. Choose a number as explained in the file "
                 "(e.g. python -c \"import random; print(random.randint(39000, 44000))\").")

    # The two roots must be separate (only anonymized data in bids/)
    try:
        src, bids = cfg.sourcedata_root.resolve(), cfg.bids_root.resolve()
        if src == bids or bids in src.parents:
            p.append("study.yaml: paths.sourcedata_root is inside paths.bids_root; keep them side by side "
                     "(sourcedata holds identifying data, bids/ only anonymized data)")
        elif src in bids.parents:
            p.append("study.yaml: paths.bids_root is inside paths.sourcedata_root; keep them side by side")
    except (KeyError, TypeError):
        pass

    # Heuristic: "generate" or a file path
    h = cfg.processing.get("mri_conversion", {}).get("heuristic", "generate")
    if cfg.study.get("data", {}).get("mri") and h != "generate" and not Path(h).is_absolute():
        p.append(f"processing.yaml: mri_conversion.heuristic should be \"generate\" or a full path, not {h!r}")

    # {placeholders} in texts must be ones notebook 04 knows
    texts = {f"text/{TEXT_FILES[k]}": v for k, v in cfg.text.items()}
    texts["facility.yaml: meg.software_versions"] = cfg.facility.get("meg", {}).get("software_versions", "")
    texts["facility.yaml: meg.emptyroom_description"] = cfg.facility.get("meg", {}).get("emptyroom_description", "")
    for i, entry in enumerate(cfg.dataset.get("changes") or []):
        for j, item in enumerate(entry.get("items", [])):
            texts[f"study.yaml: changes[{i}].items[{j}]"] = item
    for where, t in texts.items():
        unknown = _unknown_placeholders(t or "")
        if unknown:
            p.append(f"{where}: unknown placeholder(s) {sorted('{' + u + '}' for u in unknown)}; "
                     f"available: {', '.join(sorted(KNOWN_PLACEHOLDERS))}")
    return p


def check(cfg: Config) -> list:
    """Every problem found in the configuration (an empty list means it's complete).
    The schema comes first; the other checks run only if it passes, since they rely on it."""
    problems = _schema_problems(cfg)
    if problems:
        return problems
    return _placeholder_problems(cfg) + _cross_problems(cfg)


# =====================================================================================
#  Finding the inputs whose names are "auto" (this looks at raw/ on disk)
# =====================================================================================

def _is_dicom(path: Path) -> bool:
    """True if the file has the DICOM marker: the bytes 'DICM' after a 128-byte preamble."""
    try:
        with open(path, "rb") as f:
            f.seek(128)
            return f.read(4) == b"DICM"
    except OSError:
        return False


def find_inputs(cfg: Config) -> dict:
    """The EDF file and the DICOM file pattern for this session. Raises ConfigError, listing
    what it did find, if a needed input can't be found. Only looks for data the study has."""
    found, problems = {"edf_file": None, "dicom_template": None}, []
    data = cfg.study["data"]

    if data["eye_tracking"]:
        eye_dir = cfg.raw_dir / "eye"
        name = cfg.subject["eye"]["edf_file"]
        if not eye_dir.is_dir():
            problems.append(f"No eye/ folder in {cfg.raw_dir} (or set data.eye_tracking: false in study.yaml)")
        elif name == "auto":
            edfs = sorted(p for p in eye_dir.iterdir() if p.suffix.lower() == ".edf")
            if len(edfs) == 1:
                found["edf_file"] = edfs[0]
            else:
                problems.append(f"{eye_dir}: found {len(edfs)} .edf files ({[p.name for p in edfs]}); "
                                "give the right one as eye.edf_file in the subject file")
        elif (eye_dir / name).is_file():
            found["edf_file"] = eye_dir / name
        else:
            problems.append(f"{eye_dir / name} not found; .edf files there: "
                            f"{[p.name for p in eye_dir.iterdir() if p.suffix.lower() == '.edf']}")

    if data["mri"]:
        mri_dir = cfg.raw_dir / "mri"
        # relative to sourcedata_root, as HeuDiConv wants it ({subject}/{session} filled in by HeuDiConv)
        rel = "sub-{subject}/" + ("ses-{session}/" if cfg.session else "") + "raw/mri"
        pattern = cfg.processing["mri_conversion"]["dicom_files"]
        if not mri_dir.is_dir():
            problems.append(f"No mri/ folder in {cfg.raw_dir} (or set data.mri: false in study.yaml)")
        elif pattern == "auto":
            for sub in ["", "dcm/"]:
                folder = mri_dir / sub
                if not folder.is_dir():
                    continue
                files = [p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")]
                if any(p.suffix.lower() == ".dcm" for p in files):
                    found["dicom_template"] = f"{rel}/{sub}*.dcm"
                    break
                if files and _is_dicom(files[0]):
                    found["dicom_template"] = f"{rel}/{sub}*"
                    break
            if found["dicom_template"] is None:
                problems.append(f"No DICOM files in {mri_dir} or {mri_dir / 'dcm'}; set "
                                "mri_conversion.dicom_files in processing.yaml to a pattern inside raw/mri/")
        else:
            if not list(mri_dir.glob(pattern)):
                problems.append(f"mri_conversion.dicom_files {pattern!r} matches nothing in {mri_dir}")
            found["dicom_template"] = f"{rel}/{pattern}"

    if problems:
        raise ConfigError(problems)
    return found


# =====================================================================================
#  The settings each notebook receives
# =====================================================================================

def notebook_settings(cfg: Config, inputs: dict | None = None, summary_file=None) -> dict:
    """{"01": {...}, ..., "06": {...}}: for each notebook, every name it asks from_config()
    for, with its value. Each notebook's dict also lists under "_paths" the names whose
    values the notebook turns into paths.

    inputs: the result of find_inputs(cfg) (looked up on disk if not given).
    summary_file: where notebook 06 saves the validator's counts (the run's report folder).
    """
    if inputs is None:
        inputs = find_inputs(cfg)
    st, sj, pr, ds, fc = cfg.study, cfg.subject, cfg.processing, cfg.dataset, cfg.facility
    data, opts = st["data"], st["options"]
    task, sub, ses = st["study"]["task"], sj["subject"], sj["session"]
    S, raw = cfg.session_dir, cfg.raw_dir
    meg = fc["meg"]

    def text(key):
        return cfg.text[key]

    nb = {}

    nb["01"] = {
        "RAW_DIR": raw,
        "OUT_DIR": S / "fif",
        "SUBJECT": sub, "SESSION": ses, "TASK": task,
        "ER_PREFIX": sj["cmeg"]["emptyroom_prefix"],
        "DATA_PREFIX": sj["cmeg"]["task_prefix"],
        "XFM_FILE": raw / "einscan" / sj["einscan"]["xfm_file"],
        "XYZ_FILE": raw / "einscan" / sj["einscan"]["xyz_file"],
        "LINE_FREQ": meg["line_freq"],
        "FORCE": opts["force_reconvert"],
        "SCREEN_TMIN": pr["meg_conversion"]["screen"]["tmin"],
        "SCREEN_DUR": pr["meg_conversion"]["screen"]["duration"],
        "SCREEN_Z": pr["meg_conversion"]["screen"]["z_threshold"],
        "BROWSE_STARTS": pr["meg_conversion"]["browse_starts"],
    }

    eye_meta = pr["eye_conversion"]["metadata_file"] or fc["eye_tracker"]["metadata_file"]
    nb["02"] = {
        "EDF_FILE": inputs.get("edf_file"),
        "SUBJECT": sub, "SESSION": ses, "TASK": task, "RUN": None,
        "OUT_DIR": S / "eye-staging",
        "METADATA_FILE": eye_meta,
        "EDF2ASC_DIR": st["software"]["edf2asc_dir"],
        "KEEP_WORK": pr["eye_conversion"]["keep_work"],
    }

    mri = pr["mri_conversion"]
    nb["03"] = {
        "DICOM_ROOT": cfg.sourcedata_root,
        "BIDS_OUT": S / "mri-staging",
        "HEURISTIC_PATH": cfg.sourcedata_root / "code" / "heuristic.py",
        "DEFACED_DIR": S / "defaced_anat",
        "SIF_PATH": st["software"]["heudiconv_sif"],
        "PYDEFACE_SIF": st["software"]["pydeface_sif"],
        "VALIDATOR_SIF": st["software"]["bids_validator_sif"],
        "DICOM_TEMPLATE": inputs.get("dicom_template"),
        "SUBJECTS": [sub], "SESSION": ses,
        "EXTRA_BINDS": st["software"]["extra_binds"],
        "OVERWRITE": opts["overwrite"],
        "ADD_RUN": mri["add_run"],
        "SKIP_TOP_LEVEL": True,           # the MRI joins the MEG dataset: no top-level files
        "EXPECTED_SCANS": mri["expected_scans"],
        "T1W_WORDS": mri["t1w_words"], "T2W_WORDS": mri["t2w_words"],
        "SKIP_WORDS": mri["skip_words"], "REQUIRE_WORDS": mri["require_words"],
        "HEURISTIC_SOURCE": mri["heuristic"],
        "DEFACE_SUFFIXES": mri["deface_suffixes"],
        "REDEFACE": mri["redeface"],
    }

    ev, anon, desc, cit = pr["events"], pr["anonymization"], ds["description"], ds["citation"]
    newest = ds["changes"][0]
    nb["04"] = {
        "FIF_DATA": S / "fif" / f"{cfg.stem}_task-{task}_meg.fif",
        "FIF_EMPTYROOM": S / "fif" / f"{cfg.stem}_task-noise_meg.fif",
        "BIDS_ROOT": cfg.bids_root,
        "SUBJECT": sub, "SESSION": ses, "TASK": task, "RUN": None,
        "EMPTYROOM_LAYOUT": opts["emptyroom_layout"],
        "OVERWRITE": opts["overwrite"],
        "MRI_STAGING": S / "mri-staging" if data["mri"] else None,
        "BEHAV_DIR": raw / "behav" if data["behavioral"] else None,
        "WRITE_DATASET_FILES": opts["write_dataset_files"],
        # 4.2 / 4.4 events
        "EVENT_SOURCE": ev["source"], "STIM_CHANNEL": ev["stim_channel"],
        "SHORTEST_EVENT": ev["shortest_event"], "CONSECUTIVE": ev["consecutive"],
        "MIN_DURATION": ev["min_duration"],
        "EVENT_ID": ev["event_id"], "DROP_UNMAPPED": ev["drop_unmapped"],
        # 6 bad channels (notebook 04 resolves "auto" with notebook 01's candidates)
        "BAD_CHANNELS": sj["bad_channels"], "BAD_REASONS": sj["bad_reasons"],
        # 7 anonymization
        "ANONYMIZE": anon["anonymize"], "DAYSBACK": cfg.private["daysback"],
        "KEEP_HIS": anon["keep_his"], "KEEP_SOURCE": anon["keep_source"],
        # 10.1 _meg.json
        "DEWAR_POSITION": meg["dewar_position"], "SOFTWARE_FILTERS": meg["software_filters"],
        "MANUFACTURER": meg["manufacturer"], "MANUFACTURERS_MODEL_NAME": meg["manufacturers_model_name"],
        "SOFTWARE_VERSIONS": meg["software_versions"],
        "DEVICE_SERIAL_NUMBER": meg["device_serial_number"],
        "HARDWARE_FILTERS": meg["hardware_filters"],
        "TASK_DESCRIPTION": text("task_description").strip(),
        "INSTRUCTIONS": text("instructions"),
        "EMPTYROOM_DESCRIPTION": meg["emptyroom_description"],
        "INSTITUTION_NAME": fc["institution"]["name"],
        "INSTITUTION_ADDRESS": fc["institution"]["address"],
        "INSTITUTIONAL_DEPARTMENT_NAME": fc["institution"]["department"],
        # 10.2 _channels.tsv
        "REF_CHANNEL_TYPE": meg["ref_channel_type"],
        "MEG_LOW_CUTOFF": meg["meg_low_cutoff"], "MEG_HIGH_CUTOFF": meg["meg_high_cutoff"],
        "TRIG_CUTOFFS": meg["trig_cutoffs"], "MISC_CUTOFFS": meg["misc_cutoffs"],
        "CHANNEL_DESCRIPTIONS": meg["channel_descriptions"],
        # 10.4 events / 10.5 coordinate system
        "TRIAL_TYPE_LEVELS": ev["trial_type_levels"], "DURATIONS": ev["durations"],
        "EXTRA_COLUMNS": ev["extra_columns"], "STIMULUS_PRESENTATION": ev["stimulus_presentation"],
        "APPLY_COORDSYSTEM_DESCRIPTION": meg["coordsystem"]["apply"],
        "COORDSYSTEM_DESCRIPTION": meg["coordsystem"]["description"],
        # 11 dataset-level files
        "DS_NAME": desc["name"], "USE_CITATION_CFF": desc["use_citation_cff"],
        "DS_AUTHORS": desc["authors"], "DS_LICENSE": desc["license"],
        "DS_BIDS_VERSION": desc["bids_version"], "DS_KEYWORDS": desc["keywords"],
        "DS_ACKNOWLEDGEMENTS": desc["acknowledgements"], "DS_HOW_TO_ACK": desc["how_to_acknowledge"],
        "DS_FUNDING": desc["funding"], "DS_ETHICS": desc["ethics_approvals"],
        "DS_DOI": desc["doi"], "DS_REFERENCES": desc["references"],
        "README_TEXT": text("readme"),
        "PARTICIPANTS": [{"participant_id": f"sub-{sub}", **(sj["participant"] or {})}],
        "PARTICIPANTS_JSON": ds["participants_columns"],
        "CHANGES_ENTRIES": [[c["version"], c["date"], c["items"]] for c in ds["changes"]],
        "LICENSE_TEXT": text("license"),
        "CFF_AUTHORS": cit["authors"], "CFF_MESSAGE": cit["message"],
        "CFF_VERSION_OF_DATA": newest["version"], "CFF_DATE_RELEASED": newest["date"],
        "CFF_PREFERRED_PAPER": cit["preferred_paper"],
        "WRITE_TASK_LEVEL_SIDECAR": ds["task_sidecar"]["write"],
        "COG_ATLAS_ID": ds["task_sidecar"]["cog_atlas_id"],
        "COG_PO_ID": ds["task_sidecar"]["cog_po_id"],
        # 12 MRI, 13 behavioral
        "MRI_SUBJECT": None,                    # same subject label in every notebook
        "MRI_DATATYPES": mri["datatypes"],
        "SCANNER_TIMEZONE": fc["mri"]["scanner_timezone"],
        "BEH_COLUMNS": pr["behavioral"]["columns"],
    }

    sync = pr["eye_sync"]
    bp = sync["blink_preview"]
    nb["05"] = {
        "BIDS_ROOT": cfg.bids_root,
        "SUBJECT": sub, "SESSION": ses, "TASK": task, "RUN": None,
        "RECORDING": sync["recording"],
        "EYE2BIDS_DIR": S / "eye-staging",
        "FIRST_PASS_TOLERANCE_S": sync["first_pass_tolerance_s"],
        "FINAL_TOLERANCE_S": sync["final_tolerance_s"],
        "OUTLIER_MS": sync["outlier_ms"],
        "DERIV_NAME": sync["deriv_name"],
        "OVERWRITE": opts["overwrite"],
        "MAKE_SYNCED_FIF": sync["make_synced_fif"],
        "ET_PREFIX": sync["et_prefix"],
        "RESTORE_NAN": sync["restore_nan"],
        "BLINK_ANNOT": sync["blink_annot"], "MISSING_ANNOT": sync["missing_annot"],
        "ANALOG_EYE_CHANNELS": sync["analog_eye_channels"],
        "MATCH_MODE": sync["match_mode"], "CODE_PATTERN": sync["code_pattern"],
        "NAME_PATTERN": sync["name_pattern"], "NAME_MAP": sync["name_map"],
        "MEG_TRIAL_TYPES": sync["meg_trial_types"],
        "WINDOW_S": sync["window_s"], "PAUSE_FACTOR": sync["pause_factor"],
        "BLINK_PREVIEW": bp["enabled"],
        "TRIAL_START_TYPE": bp["trial_start_type"], "START_SHIFT_S": bp["start_shift_s"],
        "TRIAL_END_TYPE": bp["trial_end_type"], "END_SHIFT_S": bp["end_shift_s"],
        "BLINK_COUNTS": bp["blink_counts"], "TRIALS_PER_GROUP": bp["trials_per_group"],
        "TRIALS_PER_ROW": bp["trials_per_row"],
        "COUNT_BLINKS_STARTING_BEFORE": bp["count_blinks_starting_before"],
    }

    val = st["validator"]
    nb["06"] = {
        "BIDS_ROOT": cfg.bids_root,
        "VALIDATOR_SIF": st["software"]["bids_validator_sif"],
        "IGNORE_WARNINGS": val["ignore_warnings"],
        "SHOW_TEXT_REPORT": val["show_text_report"],
        "FAIL_ON_ERRORS": val["fail_on_errors"],
        "SUMMARY_FILE": summary_file,
    }

    # Paths as plain text (YAML has no path type); "_paths" tells each notebook which to turn back
    # into paths. Lists of paths aren't used.
    for settings in nb.values():
        paths = []
        for name, value in settings.items():
            if isinstance(value, Path):
                settings[name] = str(value)
                paths.append(name)
            elif name in {"EDF_FILE", "METADATA_FILE", "EDF2ASC_DIR", "MRI_STAGING", "BEHAV_DIR",
                          "SIF_PATH", "PYDEFACE_SIF", "VALIDATOR_SIF", "SUMMARY_FILE"}:
                paths.append(name)
        settings["_paths"] = sorted(paths)
    return nb


def write_settings(settings: dict, path) -> Path:
    """Save the per-notebook settings (the file the notebooks read via config_file)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(settings, f, sort_keys=False, allow_unicode=True, width=100)
    return path


# =====================================================================================
#  Command line: check a configuration
# =====================================================================================

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check a Pygers-MEG bids-config (starting from a subject file).")
    parser.add_argument("subject_file", help="e.g. /jukebox/<lab>/<study>/bids-config/subjects/sub-01.yaml")
    parser.add_argument("--show", choices=NOTEBOOKS, help="also print the settings this notebook would receive")
    parser.add_argument("--no-data", action="store_true",
                        help="don't look for the raw data on disk (check the files only)")
    args = parser.parse_args(argv)

    try:
        cfg = load(args.subject_file)
    except ConfigError as err:
        print("Could not read the configuration:\n  " + "\n  ".join(err.problems))
        return 2
    problems = check(cfg)
    inputs = {"edf_file": None, "dicom_template": None}
    if not problems and not args.no_data:
        try:
            inputs = find_inputs(cfg)
        except ConfigError as err:
            problems = err.problems
    if problems:
        print(f"{len(problems)} problem(s) in the configuration for {cfg.subject_file.name}:")
        for item in problems:
            print("  -", item)
        return 1
    print(f"OK: the configuration for {cfg.subject_file.name} is complete "
          f"({cfg.label}, task-{cfg.study['study']['task']}).")
    if args.show:
        print(yaml.safe_dump({args.show: notebook_settings(cfg, inputs)[args.show]},
                             sort_keys=False, allow_unicode=True, width=100))
    return 0


if __name__ == "__main__":
    sys.exit(main())
