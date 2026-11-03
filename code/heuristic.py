
# HeuDiConv heuristic: T1w + T2w anatomicals for MEG studies.
# Written by 03_Dicom_to_BIDS_heudiconv.ipynb (Step 4). Edit the word lists to fit your protocol names.

import re

# ---- EDIT THESE: words that identify each scan (matched in lower case) --------
# Within one list the words act as OR: a scan matches if its name contains ANY of them.
T1W_WORDS  = ["t1w", "mpr"]                  # e.g. anat_ses-01_T1w_acq-0.8mm-MPR
T2W_WORDS  = ["t2w", "t2space"]              # e.g. anat_ses-01_T2w_acq-0.8mm-T2SPACE
SKIP_WORDS = ["scout", "localizer", "phoenix"]  # positioning scans and scanner reports

# Optional AND filter: a scan is only converted if its name contains ALL of these words.
# Leave empty to convert every T1w/T2w. Example: ["0.8mm"] keeps only the 0.8 mm scans
# and skips other T1w/T2w resolutions collected in the same session.
REQUIRE_WORDS = ["0.8mm"]

# ---- Filled in by the notebook from SESSION and ADD_RUN (don't edit here) ------
SES_DIR = "{session}/"   # "{session}/" if there are sessions, else ""
SES_TAG = "_{session}"   # "_{session}" if there are sessions, else ""
RUN     = ""      # "_run-{item:02d}" if ADD_RUN is True, else ""


def create_key(template, outtype=("nii.gz",), annotation_classes=None):
    # HeuDiConv expects each output name in this (template, file types, extra) form.
    return template, outtype, annotation_classes


def acq_tag(name):
    # Copy "acq-0.8mm" from the protocol name into the BIDS name as "_acq-0p8mm".
    # BIDS labels may only contain letters and numbers, so "." becomes "p".
    # The label stops at the first other character, so "acq-hi-res" gives just "_acq-hi".
    m = re.search(r"acq-([0-9a-z.]+)", name)
    if not m:
        # No "acq-" in the name: leave it out. acq is optional in BIDS, so
        # sub-001_ses-01_T1w.nii.gz is a perfectly good name.
        return ""
    return "_acq-" + m.group(1).replace(".", "p")


def anat_key(suffix, name):
    # e.g. sub-{subject}/{session}/anat/sub-{subject}_{session}_acq-0p8mm_T1w
    # HeuDiConv fills in {subject}, {session} and {item} itself.
    return create_key("sub-{subject}/" + SES_DIR + "anat/sub-{subject}" + SES_TAG
                      + acq_tag(name) + RUN + "_" + suffix)


def has(text, words):
    # True if any of the words appears in the text
    return any(w in text for w in words)


def infotodict(seqinfo):
    # HeuDiConv calls this once per subject/session with one record per series.
    # It returns {BIDS name: [series IDs that should get that name]}.
    info = {}
    for s in seqinfo:
        name = f"{s.protocol_name} {s.series_description}".lower()

        # Skip scanner-made images (reformats, maps) and positioning scans / reports
        if s.is_derived or has(name, SKIP_WORDS):
            continue

        # Skip anything missing one of the REQUIRE_WORDS (all() = every word must be there)
        if not all(w in name for w in REQUIRE_WORDS):
            continue

        # Anatomical scans are single 3D volumes (dim4 == 1)
        if s.dim4 == 1 and has(name, T2W_WORDS):
            info.setdefault(anat_key("T2w", name), []).append(s.series_id)
        elif s.dim4 == 1 and has(name, T1W_WORDS):
            info.setdefault(anat_key("T1w", name), []).append(s.series_id)
        else:
            print("Not converted (no rule matches):", s.series_id)

    return info
