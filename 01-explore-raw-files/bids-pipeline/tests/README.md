# bids-pipeline/tests/

Automatic checks for the Pygers-MEG-to-BIDS pipeline's own code. Run them after you change
a notebook, the bids-config template or anything in `bids-pipeline/`, so mistakes show up here
in seconds instead of in a cluster job.

They never run a notebook, touch real data, or need Slurm, MNE or the containers: each test
builds a small fake study (a filled-in copy of the template and an empty raw folder tree)
in a temporary folder and checks what the pipeline makes of it.

## Running them

In the `pygers-meg` environment, from pygers-meg's `01-explore-raw-files` folder:

```bash
python bids-pipeline/tests/test_bids_config.py
python bids-pipeline/tests/test_run_pipeline.py
```

Each test prints `PASS`, or `FAIL` and what went wrong. (`python -m pytest bids-pipeline/tests`
also works, if pytest is installed.)

## test_bids_config.py: the notebooks, the template and bids_config.py agree

| Test | What it makes sure of |
|---|---|
| `test_names_match_the_notebooks` | Every setting a notebook reads with `from_config("...")` is provided by the configuration, and nothing extra, for all six notebooks. Fails if a setting was added to a notebook but not to `bids_config.notebook_settings()`, or the other way round. |
| `test_values_match_the_notebook_examples` | The template's values are the same as the example values typed in the notebooks, apart from a few intended differences listed at the top of the file. |
| `test_template_lists_its_placeholders` | The template as shipped is reported as incomplete, and only because of its `EDIT ME` / `[yourpath]` placeholders and DAYSBACK. |
| `test_filled_template_is_complete` | With those filled in, the check passes and the .edf and DICOM files are found. |
| `test_mistakes_are_caught` | Typical mistakes are reported: a misspelled setting, `overwrite: maybe`, `subject: "sub-01"`, an event without a description, a sync name that isn't an event, an undescribed participant column, an unknown `{placeholder}`, a bad CHANGES date, sourcedata inside bids, a missing MRI container in a study with MRI. |
| `test_study_without_sessions` | With `sessions: false`, no setting or path contains `ses-`. |
| `test_sessions_must_match_the_study` | A subject file whose `session:` disagrees with the study's `sessions:` setting is reported. |
| `test_runs` | A session with `runs:` gets per-run settings (its recording, its .edf, file names with `run-NN`), and 03 and 06 get the same settings for every run. |
| `test_run_mistakes_are_caught` | `runs:` mistakes are reported: both `cmeg.task_prefix` and `runs:`, neither, a run listed twice, `edf_file: "auto"` or no `edf_file` in a run. |

## test_run_pipeline.py: the runner's decisions

| Test | What it makes sure of |
|---|---|
| `test_default_plan_runs_everything` | With every step on, all six notebooks are planned. |
| `test_06_always_runs_and_04_alone_warns` | 06 is always added; choosing 04 without 05 doesn't add 05 but gives a warning. |
| `test_missing_data_skips_its_notebooks` | Without eye tracking or MRI (`data:` section), their notebooks are skipped. |
| `test_inputs_from_notebooks_that_dont_run_must_exist` | `--steps 04,05` with no FIF files from 01 (and no files from 02, 03) stops the pre-flight check, which says which notebook to add. |
| `test_paths_without_sessions` | The files the runner checks between notebooks, and the Slurm job name, have no `ses-` in a study without sessions. |
| `test_notebooks_found_by_exact_name` | The six notebooks are found by their exact names; copies with similar names (`04_FIF_to_BIDS_old.ipynb`) are ignored, and a missing notebook is reported with the similar names found. |
| `test_steps_option` | `--steps 4,5` becomes 04, 05; `07` is rejected. |
| `test_slurm_options` | The sbatch options come from `global-study.yaml` (time, memory, CPUs, partition), and the email options only when `mail_user` is set; bad email settings are reported. |
| `test_lock_is_exclusive_and_released` | While one job holds the lock on the BIDS dataset, a second can't take it; once released, it can. |
| `test_runs_plan` | With `runs:`, notebooks 01, 02, 04 and 05 are planned once per run and 03 and 06 once; a run with `edf_file: null` skips its 02 and 05. Without `runs:`, the plan is the usual six notebooks. |
| `test_runs_preflight` | With `runs:`, each run's recording and files are checked, and the messages name the run. |
| `test_runs_behavioral_logs` | With `runs:`, one set of behavioral logs for the session goes to the first run's 04 only, without a run label; with `behav_files` in every run, each run's 04 gets its own pattern. A pattern that finds nothing, a file two runs' patterns pick, and `behav_files` in only some runs are reported. |
| `test_runs_main` | A whole run with fake notebooks: one settings file per run, each notebook gets its run's file, and a failed 01 run-02 skips only 04 run-02 and 05 run-02. |

## When a test fails

The message says what differs. Most often a notebook setting was added, renamed or removed
without updating `bids_config.notebook_settings()`, the schema and the template: see "When
you change a notebook" in `bids-pipeline/README.md`.
