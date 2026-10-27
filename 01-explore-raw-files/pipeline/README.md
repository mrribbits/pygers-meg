# pipeline/

Runs the six notebooks in this folder's parent, `01-explore-raw-files/`, as one cluster job, with the
settings for a study taken from YAML files instead of edited into the notebooks.
The notebooks still work on their own exactly as before.

| File | What it is |
|---|---|
| `facility.yaml` | the Scully MEG lab's settings: hardware, institution, scanner |
| `templates/bids-config/` | the folder a study copies and fills in (see its README.md) |
| `bids_config.py` | reads a study's configuration, checks it, and turns it into the settings each notebook asks for |
| `bids_config.schema.yaml` | the rules each configuration file must follow |
| `run_pipeline.py` | runs the notebooks for one session with papermill and writes the HTML reports |
| `../run_bids.sh` | checks a session, then submits `run_pipeline.py` as a Slurm job |
| `tests/` | checks that the template and the notebooks agree, and the step rules |

## Running a session

On scotty, in the `pygers-meg` environment, from pygers-meg's 01-explore-raw-files folder:

```bash
bash run_bids.sh /jukebox/<lab>/<study>/bids-config/subjects/sub-01.yaml            # check, then submit
bash run_bids.sh /jukebox/<lab>/<study>/bids-config/subjects/sub-01.yaml --check    # only check
bash run_bids.sh /jukebox/<lab>/<study>/bids-config/subjects/sub-01.yaml --steps 01 # only notebook 01
```

What happens:

1. **Pre-flight**, on the login node and again in the job: the configuration is checked;
   the raw files the chosen notebooks need are found (cMEG recordings, EinScan files, the
   .edf, the DICOMs, the PsychoPy logs); the software is checked (cmeg2fif version,
   edf2asc, apptainer, and that each of your containers starts); files the chosen
   notebooks need from notebooks that won't run must already exist. Any problem stops
   here, before anything is changed, with the whole list. Warnings (not stops): a
   notebook that won't run whose output is older than its input.
2. **Which notebooks run**: `steps:` in study.yaml (or `--steps`), minus those whose data
   the study doesn't have (`data:`). If 04 runs, 05 runs too; 06 always runs last.
3. **Running**: each notebook runs with papermill, reading its settings from the run's
   `settings.yaml`. A failed notebook doesn't stop the others, but the notebooks that need
   its output are skipped. 06 runs whenever there is a BIDS dataset.
4. **Lock**: notebooks 03-06 write shared files (the BIDS dataset, the MRI naming rules),
   so before the first of them the job takes a lock (`<study>/.bids.lock`); a second job
   waits. A lock left by a job that no longer runs is removed automatically.
5. **Reports**, in `<session>/reports/<date-time>/` (`reports/latest` points to the newest):
   `index.html` (status, times, failures, the validator's counts), one HTML report per
   notebook (code hidden or shown with a button; `report.hide_code` sets the start),
   the executed notebooks, `settings.yaml`, `config/` (a copy of the configuration files)
   and `pipeline.log`. The Slurm log is `<session>/reports/slurm-<job id>.log`.
6. **Email** (optional): set `slurm.mail_user` in study.yaml to get Slurm's email when the job
   ends or fails (`mail_type`, e.g. `["BEGIN", "END", "FAIL"]`).

Studies without sessions (`study.sessions: false`, `session: null` in each subject file)
leave out the `ses-` level everywhere: `sourcedata/sub-01/raw/`, `bids/sub-01/meg/`, and
file names without `_ses-01`. Below, "session folder" then means `sourcedata/sub-01/`.

Exit status: 0 = all chosen notebooks ran; 1 = one failed or was skipped; 2 = pre-flight
problems.

The notebooks run in the `python3` Jupyter kernel of the active environment. If the
pre-flight check says that kernel belongs to another environment, register this one
(`python -m ipykernel install --user --name pygers-meg`) and add `--kernel pygers-meg`.

## Checking a study's configuration

On scotty, in the `pygers-meg` environment, from pygers-meg's 01-explore-raw-files folder:

```bash
python pipeline/bids_config.py /jukebox/<lab>/<study>/bids-config/subjects/sub-01.yaml
```

## How a notebook gets its settings

Each notebook has a `config_file = None` cell (tagged `parameters`). The pipeline
writes one settings file per run with a section for each notebook (`"01"` … `"06"`),
and papermill sets `config_file` to it. Each settings cell then ends with a line like

```python
EVENT_ID, DROP_UNMAPPED = from_config("EVENT_ID", "DROP_UNMAPPED")
```

which takes the values from that file. Run by hand, `config_file` stays `None` and
the line changes nothing.

## When you change a notebook

If you add, rename or remove a setting read with `from_config()`, update
`bids_config.notebook_settings()`, the schema and the template to match, then run the
tests (they compare the notebooks with the template), from the 01-explore-raw-files folder:

```bash
python pipeline/tests/test_bids_config.py
python pipeline/tests/test_run_pipeline.py
```
