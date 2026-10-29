# bids-config: a study's settings for the Pygers-MEG-to-BIDS pipeline

This folder holds everything the pipeline needs to know about one study. The pipeline
reads it and passes the settings to the six notebooks, so you never edit the notebooks.
The values here are for the sample auditory-oddball dataset; replace them with your
study's.

This README is for setting up and running a study. How the pipeline itself works (and how
to change it) is in `01-explore-raw-files/bids-pipeline/README.md` in the pygers-meg repository.

## Before you start: your software

* **New environment:** create it with the repository's environment file. That already
  includes papermill, cmeg2fif and the fixed eye2bids fork.

  ```bash
  conda env create -f scotty-environment.yml -n pygers-meg
  ```

* **Environment made before October 27, 2026:** add papermill, and reinstall eye2bids at
  the fixed version (`0991c4c`):

  ```bash
  conda activate pygers-meg
  pip install "papermill==2.7.0" "tenacity==9.2.1" "entrypoints==0.4.2"
  pip install --force-reinstall --no-deps "eye2bids @ git+https://github.com/mrribbits/eye2bids.git@0991c4c6e25b581b24a7011f8fdc103fddd7f26a"
  ```

  Then restart any running Jupyter kernels, and check:

  ```bash
  pip freeze | grep -E "papermill|eye2bids|cmeg2fif"
  ```

## Setting up a new study

### Step 1: Copy this folder

Copy this whole `bids-config` folder next to your study's data, for example to
`/jukebox/<lab>/<study>/bids-config/`. Keep it outside the BIDS dataset.

### Step 2: Edit the files

| File | What's in it | How often |
|---|---|---|
| `global-study.yaml` | where the data are, your software (containers, edf2asc), what was recorded, what to run, options, job settings; at the end, the published dataset's description: name, authors, license, version history, participant columns | once per study (the description again for each release) |
| `processing.yaml` | how each notebook processes the data: events, MRI naming words, sync rules, behavioral columns | once per study |
| `private.yaml` | DAYSBACK, the secret date shift (keep this file private) | once per study |
| `text/` | README, task description, instructions, license text | once per study |
| `subjects/sub-01.yaml` | one session: labels, recording names, bad channels, participant details | one file per session |

Every setting has a comment explaining it. Replace everything that says `EDIT ME` or
contains `[yourpath]`.

The Scully MEG lab's own settings (hardware, institution) are not in this folder: they
are in `01-explore-raw-files/bids-pipeline/facility.yaml` in the pygers-meg repository. To
change one for your study only, set it under `facility_overrides:` in `global-study.yaml`.

### Step 3: Check your settings

On scotty, activate the environment and go to the `01-explore-raw-files` folder of your
pygers-meg copy:

```bash
conda activate pygers-meg
cd <your pygers-meg folder>/01-explore-raw-files
```

There are two checks. Neither changes anything.

| Command | What it checks | When to use it |
|---|---|---|
| `python bids-pipeline/bids_config.py <subject file>` | the YAML files: misspelled or missing settings, leftover `EDIT ME`, settings that disagree; and that the .edf and DICOMs can be found | while you edit (quick) |
| `bash run_bids-pipeline.sh <subject file> --check` | all of that, plus everything a real run needs: the raw recordings, your containers and software, and which notebooks would run | before you submit |

For example:

```bash
python bids-pipeline/bids_config.py /jukebox/<lab>/<study>/bids-config/subjects/sub-01.yaml
```

Each check lists every problem it finds, or says everything is complete. Adding
`--show 04` to the first one also prints the exact settings notebook 04 (or any other
notebook) would receive.

### Step 4: Run a session

From the same place:

```bash
bash run_bids-pipeline.sh /jukebox/<lab>/<study>/bids-config/subjects/sub-01.yaml
```

This runs the full check again and, if it passes, submits a Slurm job. When the job is
done, open `index.html` in the session's `reports/<date-time>/` folder.

To run only notebook 01 (for example to choose the bad channels first, see below), add
`--steps 01`.

## Studies without sessions

BIDS allows datasets without session labels: `sub-01/meg/sub-01_task-..._meg.fif` instead
of `sub-01/ses-01/meg/sub-01_ses-01_task-..._meg.fif`. For such a study set
`study.sessions: false` in `global-study.yaml` and `session: null` in every subject file; the raw
data then go in `sourcedata/sub-01/raw/` (no `ses-01/` level). Choose this only if every
participant comes once: adding sessions later means renaming every file already in `bids/`
and `sourcedata/`.

## Placeholders in texts

These texts can contain placeholders, filled in by notebook 04 when it runs:
`text/README.md`, `text/LICENSE.txt`, `facility.yaml`'s `software_versions` and
`emptyroom_description`, and the lines of `changes` in `global-study.yaml`.

| Placeholder | Becomes |
|---|---|
| `{ds_name}` | the dataset name (`global-study.yaml`, `description.name`) |
| `{task}` | the task label |
| `{event_codes}` | the event names and codes, e.g. `standard = 1, deviant = 2` |
| `{n_meg_channels}`, `{n_ref_channels}` | the number of MEG and reference channels in the recording |
| `{sfreq}` | the sampling frequency in Hz |
| `{emptyroom_layout}` | `same-session` or `sub-emptyroom` |
| `{cmeg2fif_version}` | the cMEG2FIF version, from the conversion log |
| `{mne_version}`, `{mne_bids_version}` | the MNE and MNE-BIDS versions used |
| `{license}` | the license name (`global-study.yaml`, `description.license`) |
| `{anonymization_note}` | a sentence on how the data were anonymized (empty if not anonymized) |

## Bad channels

Run notebook 01 first (`bash run_bids-pipeline.sh <subject file> --steps 01`) and look at its report: the
candidate table and the spectra. Then set `bad_channels` in the subject file: a list,
`auto` (notebook 01's candidates), or `auto` with `add`/`keep` changes.
