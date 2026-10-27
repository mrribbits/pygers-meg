# bids-config: a study's settings for the Pygers-MEG pipeline

This folder holds everything the pipeline needs to know about one study. The pipeline
reads it and passes the settings to the six notebooks, so you never edit the notebooks.
The values here are for the sample auditory-oddball dataset; replace them with your
study's.

## Setting up a new study

1. Copy this whole folder next to your study's data, e.g.
   `/jukebox/<lab>/<study>/bids-config/`. Keep it outside the BIDS dataset.
2. Edit the files, in this order:

   | File | What's in it | Change |
   |---|---|---|
   | `study.yaml` | where the data are, your software (containers, edf2asc), what was recorded, what to run, options, job settings | once per study |
   | `processing.yaml` | how each notebook processes the data: events, MRI naming words, sync rules, behavioral columns | once per study (task-specific) |
   | `dataset.yaml` | the BIDS dataset's name, authors, license, version history, participant columns | once per study |
   | `private.yaml` | DAYSBACK, the secret date shift | once per study; keep it private |
   | `text/` | README, task description, instructions, license text | once per study |
   | `subjects/sub-01.yaml` | one session: labels, recording names, bad channels, participant details | one file per session (per subject, in a study without sessions) |

   Every setting has a comment explaining it. Anything that says `EDIT ME` or contains
   `[yourpath]` must be replaced.
3. Check the configuration (on scotty, in the `pygers-meg` environment, from the
   pygers-meg's
   01-explore-raw-files folder):

   ```bash
   python pipeline/bids_config.py /jukebox/<lab>/<study>/bids-config/subjects/sub-01.yaml
   ```

   It lists every problem it finds (misspelled settings, missing values, event names that
   don't match, raw files it can't find), or says the configuration is complete.
   `--show 04` also prints the settings notebook 04 would receive.
4. Run a session (same place):

   ```bash
   bash run_bids.sh /jukebox/<lab>/<study>/bids-config/subjects/sub-01.yaml
   ```

   This checks everything again (including your containers and the raw recordings), then
   submits a Slurm job. The reports appear in the session's `reports/<date-time>/` folder:
   open `index.html`. `--check` only checks; `--steps 01` runs only notebook 01 (useful for
   choosing the bad channels first, see below).

The Scully MEG lab's own settings (hardware, institution) are in
`01-explore-raw-files/pipeline/facility.yaml` in the pygers-meg repository. To change one for your study only,
set it under `facility_overrides:` in `study.yaml`.

## Studies without sessions

BIDS allows datasets without session labels: `sub-01/meg/sub-01_task-..._meg.fif` instead
of `sub-01/ses-01/meg/sub-01_ses-01_task-..._meg.fif`. For such a study set
`study.sessions: false` in `study.yaml` and `session: null` in every subject file; the raw
data then go in `sourcedata/sub-01/raw/` (no `ses-01/` level). Choose this only if every
participant comes once: adding sessions later means renaming every file already in `bids/`
and `sourcedata/`.

## Placeholders in texts

These texts can contain placeholders, filled in by notebook 04 when it runs:
`text/README.md`, `text/LICENSE.txt`, `facility.yaml`'s `software_versions` and
`emptyroom_description`, and the lines of `changes` in `dataset.yaml`.

| Placeholder | Becomes |
|---|---|
| `{ds_name}` | the dataset name (`dataset.yaml`, `description.name`) |
| `{task}` | the task label |
| `{event_codes}` | the event names and codes, e.g. `standard = 1, deviant = 2` |
| `{n_meg_channels}`, `{n_ref_channels}` | the number of MEG and reference channels in the recording |
| `{sfreq}` | the sampling frequency in Hz |
| `{emptyroom_layout}` | `same-session` or `sub-emptyroom` |
| `{cmeg2fif_version}` | the cMEG2FIF version, from the conversion log |
| `{mne_version}`, `{mne_bids_version}` | the MNE and MNE-BIDS versions used |
| `{license}` | the license name (`dataset.yaml`, `description.license`) |
| `{anonymization_note}` | a sentence on how the data were anonymized (empty if not anonymized) |

## Bad channels

Run notebook 01 first (`bash run_bids.sh <subject file> --steps 01`) and look at its report: the
candidate table and the spectra. Then set `bad_channels` in the subject file: a list,
`auto` (notebook 01's candidates), or `auto` with `add`/`keep` changes.
