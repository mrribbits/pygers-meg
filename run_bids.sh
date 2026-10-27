#!/bin/bash
# =====================================================================================
#  run_bids.sh: convert one subject/session to BIDS on scotty (the Pygers-MEG pipeline)
# =====================================================================================
#
#  On scotty, in the pygers-meg environment, from the pygers-meg folder:
#
#      conda activate pygers-meg
#      bash run_bids.sh /jukebox/<lab>/<study>/bids-config/subjects/sub-01.yaml
#
#  This checks the configuration, the raw data and the software right away (so mistakes
#  show up in seconds, not after waiting in the queue), then submits a Slurm job with
#  the time, memory and CPUs from the "slurm:" section of study.yaml. The job runs the
#  notebooks and writes the reports to <session>/reports/<date-time>/ (index.html first);
#  the Slurm log is <session>/reports/slurm-<job id>.log.
#
#  Options (after the subject file):
#      --steps 04,05   run only these notebooks (plus the rules: 04 brings 05, 06 always runs)
#      --check         only check; don't submit anything
#      --local         run here and now instead of submitting a job (e.g. in an interactive
#                      session from salloc); don't do this on the login node
#      --kernel NAME   the Jupyter kernel to run the notebooks in (default: python3, this
#                      environment's Python)
#
#  "sbatch run_bids.sh <subject file>" also works: then the #SBATCH lines below set the
#  job's resources instead of study.yaml, and the checks happen inside the job.
# =====================================================================================
#SBATCH --job-name=bids
#SBATCH --time=04:00:00
#SBATCH --mem=32G
#SBATCH --cpus-per-task=4
#SBATCH --nodes=1
#SBATCH --ntasks=1

set -euo pipefail

usage() {
    sed -n '6,26p' "${BASH_SOURCE[0]}" | sed -e 's/^#  //' -e 's/^#$//' >&2 || true
    exit 2
}

# ---- inside a Slurm job: run the pipeline ----
if [[ -n "${SLURM_JOB_ID:-}" ]]; then
    # sbatch runs a copy of this script, so find the repository from where it was submitted
    REPO="${PYGERS_MEG_DIR:-${SLURM_SUBMIT_DIR:-$PWD}}"
    PY="${PYGERS_PYTHON:-$(command -v python)}"
    if [[ ! -f "$REPO/pipeline/run_pipeline.py" ]]; then
        echo "run_bids.sh: can't find pipeline/run_pipeline.py in $REPO; submit from the pygers-meg folder." >&2
        exit 2
    fi
    echo "Slurm job $SLURM_JOB_ID on $(hostname), $(date)"
    echo "Python: $PY"
    exec "$PY" "$REPO/pipeline/run_pipeline.py" "$@"
fi

# ---- on the login node: check, then submit ----
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ $# -ge 1 && "$1" != -* ]] || usage
SUBJECT_FILE="$(realpath "$1")"; shift

CHECK_ONLY=0; LOCAL=0; ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --check) CHECK_ONLY=1 ;;
        --local) LOCAL=1 ;;
        --steps|--kernel) [[ $# -ge 2 ]] || usage; ARGS+=("$1" "$2"); shift ;;
        --steps=*|--kernel=*) ARGS+=("$1") ;;
        -h|--help) usage ;;
        *) echo "run_bids.sh: unknown option $1" >&2; usage ;;
    esac
    shift
done

PY="$(command -v python || true)"
if [[ -z "$PY" ]] || ! "$PY" -c "import papermill, yaml, jsonschema" 2>/dev/null; then
    echo "run_bids.sh: the pygers-meg environment isn't active (or lacks papermill)." >&2
    echo "    conda activate pygers-meg" >&2
    exit 2
fi

if [[ $LOCAL -eq 1 ]]; then
    exec "$PY" "$REPO/pipeline/run_pipeline.py" "$SUBJECT_FILE" ${ARGS[@]+"${ARGS[@]}"}
fi

"$PY" "$REPO/pipeline/run_pipeline.py" "$SUBJECT_FILE" ${ARGS[@]+"${ARGS[@]}"} --check || exit $?
[[ $CHECK_ONLY -eq 1 ]] && exit 0

read -r -a SBATCH_OPTS <<< "$("$PY" "$REPO/pipeline/run_pipeline.py" "$SUBJECT_FILE" --slurm-options)"
export PYGERS_MEG_DIR="$REPO" PYGERS_PYTHON="$PY"
echo "Submitting: sbatch ${SBATCH_OPTS[*]}"
sbatch --export=ALL "${SBATCH_OPTS[@]}" "$REPO/run_bids.sh" "$SUBJECT_FILE" ${ARGS[@]+"${ARGS[@]}"}
