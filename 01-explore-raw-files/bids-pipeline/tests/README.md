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

## test_run_pipeline.py: the runner's decisions

| Test | What it makes sure of |
|---|---|
| `test_default_plan_runs_everything` | With every step on, all six notebooks are planned. |
| `test_04_brings_05_and_06_always_runs` | Choosing 04 adds 05; 06 is always added. |
| `test_missing_data_skips_its_notebooks` | Without eye tracking or MRI (`data:` section), their notebooks are skipped. |
| `test_inputs_from_notebooks_that_dont_run_must_exist` | `--steps 04` with no FIF files from 01 (and no files from 02, 03) stops the pre-flight check, which says which notebook to add. |
| `test_paths_without_sessions` | The files the runner checks between notebooks, and the Slurm job name, have no `ses-` in a study without sessions. |
| `test_notebooks_found_by_exact_name` | The six notebooks are found by their exact names; copies with similar names (`04_FIF_to_BIDS_old.ipynb`) are ignored, and a missing notebook is reported with the similar names found. |
| `test_steps_option` | `--steps 4,5` becomes 04, 05; `07` is rejected. |
| `test_slurm_options` | The sbatch options come from `global-study.yaml` (time, memory, CPUs, partition), and the email options only when `mail_user` is set; bad email settings are reported. |
| `test_lock_is_exclusive_and_released` | While one job holds the lock on the BIDS dataset, a second can't take it; once released, it can. |

## When a test fails

The message says what differs. Most often a notebook setting was added, renamed or removed
without updating `bids_config.notebook_settings()`, the schema and the template: see "When
you change a notebook" in `bids-pipeline/README.md`.
