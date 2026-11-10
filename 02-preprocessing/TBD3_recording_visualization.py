# Local data browser (Qt) for the synced OPM recording, matching Step 14 of 01_Explore_Data_and_Sensor_Quality.ipynb
import re
import warnings
from pathlib import Path

import pandas as pd
import mne
import mne_bids

# ---------------------------------------------------------------------------
# SETTINGS (same names as Step 1 of the notebook)
# ---------------------------------------------------------------------------
BIDS_ROOT = Path(r"Y:\Alessia\pygers-meg\data\auditory-oddball\bids")   
SUBJECT = "001"          # adjust this to 01 if needed
SESSION = "001"          # adjust this to 01 if needed
TASK = "oddballftcued"
RUN = None
EYESYNC_DERIV = "eyesync"
SENSORQC_DERIV = "sensorqc"

BROWSE_START_S = 60.0
BROWSE_DURATION_S = 10.0
BROWSE_HIGHPASS_HZ = 1.0      # display only; None = unfiltered
# ---------------------------------------------------------------------------

mne.set_log_level("WARNING")
warnings.filterwarnings("ignore", message="participants.tsv file not found", category=RuntimeWarning)

# ---- Find and read the synced recording (Step 2) -------------------------------------------------
sub_ses = [f"sub-{SUBJECT}"] + ([f"ses-{SESSION}"] if SESSION else [])
base = "_".join(sub_ses + [f"task-{TASK}"] + ([f"run-{RUN}"] if RUN else []))
sub_dir = Path(*sub_ses)

bids_path = mne_bids.BIDSPath(root=BIDS_ROOT / "derivatives" / EYESYNC_DERIV, subject=SUBJECT, session=SESSION,
                              task=TASK, run=RUN, processing="eyesync", datatype="meg", suffix="meg",
                              extension=".fif")
if not bids_path.fpath.is_file() and bids_path.copy().update(split="01").fpath.is_file():
    bids_path.update(split="01")
meg_file = bids_path.fpath
if not meg_file.is_file():
    raise FileNotFoundError(f"Synced MEG file not found:\n    {meg_file}")

raw = mne_bids.read_raw_bids(bids_path)   # reads only the header; the data are loaded below, Z channels only
print("Found:", meg_file)

# ---- Step 2b: put back the eye-tracker coil types -------------------------------------------------
header = mne.io.read_info(meg_file, verbose=False)
for ch_in_file in header["chs"]:
    ch = raw.info["chs"][raw.ch_names.index(ch_in_file["ch_name"])]
    if ch["kind"] == ch_in_file["kind"]:
        ch["coil_type"] = ch_in_file["coil_type"]

# ---- The bad channels and breaks saved by Step 7c (derivatives/sensorqc) ---------------------------
sensorqc_dir = BIDS_ROOT / "derivatives" / SENSORQC_DERIV / sub_dir / "meg"
channels_name = bids_path.copy().update(split=None, suffix="channels", extension=".tsv").basename
channels_tsv = sensorqc_dir / channels_name
if channels_tsv.is_file():
    channels = pd.read_csv(channels_tsv, sep="\t")
    raw.info["bads"] = list(channels.loc[channels["status"] == "bad", "name"])
    print(f"{len(raw.info['bads'])} bad channels from sensorqc")
else:
    print("No sensorqc _channels.tsv found (run Step 7c in the notebook first); using the bads of the eyesync file.")

breaks_tsv = sensorqc_dir / channels_name.replace("_channels.tsv", "_desc-breaks_events.tsv")
if breaks_tsv.is_file():
    breaks = pd.read_csv(breaks_tsv, sep="\t")
    if len(breaks):
        raw.annotations.append(breaks["onset"] + raw.first_time, breaks["duration"], breaks["trial_type"])
    print(f"{len(breaks)} break(s) added as BAD_break")
else:
    print("No breaks file found; no BAD_break marks shown.")

# ---- The Z channels (Step 4) ----------------------------------------------------------------------
NAME_PATTERN = re.compile(r"^(.*?)\s*\[([XYZ])\]$")
opm_names = [raw.ch_names[i] for i in mne.pick_types(raw.info, meg="mag", ref_meg=False, exclude=[])]
OPMZ = [n for n in opm_names if (m := NAME_PATTERN.match(n)) and m.group(2) == "Z"]
print(f"{len(OPMZ)} Z channels")

# Load only the Z channels: the browser shows nothing else, and the full recording (all channels) is ~7 GB
raw.pick(OPMZ)
raw.load_data()
print(f"Loaded {len(raw.ch_names)} channels ({raw.times[-1] / 60:.1f} min)")

# ---- The browser (Step 14, with the Qt backend) -----------------------------------------------------
bads_before = set(raw.info["bads"])
mne.viz.set_browser_backend("qt")
raw.plot(picks=OPMZ, start=BROWSE_START_S, duration=BROWSE_DURATION_S, n_channels=30,
         scalings=dict(mag=2e-11), highpass=BROWSE_HIGHPASS_HZ, lowpass=40.0, title=f"{base}: Z channels",
         precompute=False,   # filter only the shown window; the default ("auto") filters all 52 min at startup and freezes the window
         use_opengl=True,    # draw with the GPU (needs the pyopengl package): without it each scroll takes ~1 min on Windows
         show=True, block=True)      # block=True: the script waits until you close the window

# ---- What you changed by clicking (not saved anywhere) ------------------------------------------------
now = set(raw.info["bads"])
print("\nMarked bad in the browser:  ", sorted(now - bads_before) or "none")
print("Unmarked in the browser:    ", sorted(bads_before - now) or "none")
print("Add the newly marked ones to BAD_CHANNELS (with BAD_REASONS) in Step 7b of the notebook.")