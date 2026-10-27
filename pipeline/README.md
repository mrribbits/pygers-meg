# pipeline/

Runs the six notebooks in `01-explore-raw-files/` as one cluster job, with the
settings for a study taken from YAML files instead of edited into the notebooks.
The notebooks still work on their own exactly as before.

| File | What it is |
|---|---|
| `facility.yaml` | the Scully MEG lab's settings: hardware, institution, containers, tools |
| `templates/bids-config/` | the folder a study copies and fills in (see its README.md) |
| `bids_config.py` | reads a study's configuration, checks it, and turns it into the settings each notebook asks for |
| `bids_config.schema.yaml` | the rules each configuration file must follow |
| `tests/test_bids_config.py` | checks that the template and the notebooks agree |

Still to come: `run_pipeline.py` (runs the notebooks with papermill and writes the
HTML reports) and `run_bids.sh` (the Slurm job script).

## Checking a study's configuration

On scotty, in the `pygers-meg` environment, from the pygers-meg folder:

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
tests (they compare the notebooks with the template):

```bash
python pipeline/tests/test_bids_config.py
```
