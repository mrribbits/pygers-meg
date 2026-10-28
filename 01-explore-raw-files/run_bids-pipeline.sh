#!/bin/bash
# =====================================================================================
#  run_bids-pipeline.sh: convert one subject/session to BIDS on scotty (the Pygers-MEG-to-BIDS pipeline)
# =====================================================================================
#
#  On scotty, in the pygers-meg environment, from pygers-meg's 01-explore-raw-files folder:
#
#      conda activate pygers-meg
#      bash run_bids-pipeline.sh /jukebox/<lab>/<study>/bids-config/subjects/sub-01.yaml
#
#  This checks the configuration, the raw data and the software right away (so mistakes
#  show up in seconds, not after waiting in the queue), then submits a Slurm job with
#  the time, memory and CPUs from the "slurm:" section of global-study.yaml. The job runs the
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
#  "sbatch run_bids-pipeline.sh <subject file>" also works: then the #SBATCH lines below set the
#  job's resources instead of global-study.yaml, and the checks happen inside the job.
# =====================================================================================
#
#  HOW THIS SCRIPT WORKS
#
#  The same file is used twice:
#    1. You run it on the login node ("bash run_bids-pipeline.sh ..."). It checks everything,
#       then asks Slurm to run a job. The job is this same script, run again.
#    2. Slurm runs it on a compute node. There, the variable SLURM_JOB_ID is set (Slurm
#       sets it in every job), so the script knows it is inside the job and just starts
#       the pipeline (bids-pipeline/run_bids-pipeline.py), which runs the notebooks.
#  The real work (the checks, choosing the notebooks, running them, the reports) is all in
#  bids-pipeline/run_bids-pipeline.py. This script only handles the cluster side.
#
#  The "#SBATCH" lines below are read by Slurm only, as default job settings (to bash
#  they are comments). They are used only if you submit this file with sbatch yourself;
#  normally the options from global-study.yaml, given on the sbatch command line, replace them.
#  Slurm reads #SBATCH lines only up to the first command, so they must stay up here.
# =====================================================================================
#SBATCH --job-name=bids
#SBATCH --time=04:00:00
#SBATCH --mem=32G
#SBATCH --cpus-per-task=4
#SBATCH --nodes=1
#SBATCH --ntasks=1

# Stop at the first problem instead of carrying on:
#   -e  stop if any command fails
#   -u  stop if a variable is used before it was given a value (catches typos)
#   -o pipefail  in "a | b", a failure of "a" also counts as a failure
set -euo pipefail


# -------------------------------------------------------------------------------------
#  usage: print the instructions at the top of this file, then stop
# -------------------------------------------------------------------------------------
# Used when the command line is wrong (or with -h / --help). It prints the lines from
# "On scotty ..." down to the next "# =====" line, without the leading "#  ".
# ">&2" sends the text to the error output, so it shows even if normal output is hidden.
# "exit 2" stops the script with exit status 2 (by convention: wrong usage).
usage() {
    sed -n '/^#  On scotty/,/^# =====/p' "${BASH_SOURCE[0]}" | sed -e '$d' -e 's/^#  //' -e 's/^#$//' >&2 || true
    exit 2
}


# =====================================================================================
#  PART 1: inside the Slurm job (on a compute node)
# =====================================================================================
# Slurm sets SLURM_JOB_ID in every job. "${SLURM_JOB_ID:-}" means "its value, or empty if
# it isn't set" (needed because of "set -u" above). "-n" is true when the text isn't empty.
if [[ -n "${SLURM_JOB_ID:-}" ]]; then

    # Slurm runs a copy of this script from its own spool folder, so the script can't find
    # bids-pipeline/ next to itself. Find this script's real folder (01-explore-raw-files) in:
    #   1. PYGERS_PIPELINE_DIR, which Part 2 below sets before submitting (the usual case);
    #   2. otherwise SLURM_SUBMIT_DIR, the folder sbatch was run from (when you ran
    #      "sbatch run_bids-pipeline.sh ..." yourself, from 01-explore-raw-files);
    #   3. otherwise the current folder.
    # "${A:-${B:-C}}" means: A if it is set, else B if it is set, else C.
    HERE="${PYGERS_PIPELINE_DIR:-${SLURM_SUBMIT_DIR:-$PWD}}"

    # The Python to use: the one Part 2 found in your active pygers-meg environment
    # (PYGERS_PYTHON), or else whichever "python" comes first on the PATH.
    # "$(command -v python)" runs the command and uses what it prints: python's full path.
    PY="${PYGERS_PYTHON:-$(command -v python)}"

    # Make sure the pipeline is really there ("-f" = "is an existing file"; "!" = "not").
    if [[ ! -f "$HERE/bids-pipeline/run_bids-pipeline.py" ]]; then
        echo "run_bids-pipeline.sh: can't find bids-pipeline/run_bids-pipeline.py in $HERE; submit from pygers-meg's 01-explore-raw-files folder." >&2
        exit 2
    fi

    # A few lines at the top of the Slurm log, to see where and when the job ran.
    echo "Slurm job $SLURM_JOB_ID on $(hostname), $(date)"
    echo "Python: $PY"

    # Start the pipeline with the same arguments this script was given ("$@" = all of
    # them: the subject file and any --steps / --kernel). "exec" replaces this script with
    # the Python program, so the job's exit status is the pipeline's: 0 = all notebooks
    # ran, 1 = a notebook failed or was skipped, 2 = a problem found before running.
    # Slurm uses that status for its FAIL email (slurm.mail_type in global-study.yaml).
    exec "$PY" "$HERE/bids-pipeline/run_bids-pipeline.py" "$@"
fi


# =====================================================================================
#  PART 2: on the login node (you ran "bash run_bids-pipeline.sh ..."): check, then submit
# =====================================================================================

# This script's own folder (01-explore-raw-files, with bids-pipeline/ in it), as a full path.
# "${BASH_SOURCE[0]}" is the path used to start this script; "dirname" keeps only its
# folder; "cd ... && pwd" turns that into a full path, whatever folder you are in.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- Read the command line ----
# The first argument must be the subject file (and not an option starting with "-").
# "$#" is the number of arguments. If the test fails, "||" runs usage (which stops).
[[ $# -ge 1 && "$1" != -* ]] || usage

# Turn the subject file's path into a full path ("realpath"), so it still works inside
# the job, which may start in another folder. "shift" then drops it from the arguments,
# leaving only the options.
SUBJECT_FILE="$(realpath "$1")"; shift

# Go through the options one by one.
#   CHECK_ONLY=1 for --check, LOCAL=1 for --local (both handled here, in this script).
#   ARGS collects the options run_bids-pipeline.py itself understands (--steps, --kernel), so
#   they can be passed on unchanged.
CHECK_ONLY=0; LOCAL=0; ARGS=()
while [[ $# -gt 0 ]]; do                      # while there are arguments left
    case "$1" in                              # look at the next one
        --check) CHECK_ONLY=1 ;;
        --local) LOCAL=1 ;;
        # "--steps 04,05": the option and its value are two arguments. Make sure the
        # value is there, keep both, and "shift" once more to skip the value.
        --steps|--kernel) [[ $# -ge 2 ]] || usage; ARGS+=("$1" "$2"); shift ;;
        # "--steps=04,05": option and value in one argument.
        --steps=*|--kernel=*) ARGS+=("$1") ;;
        -h|--help) usage ;;
        *) echo "run_bids-pipeline.sh: unknown option $1" >&2; usage ;;
    esac
    shift                                     # move on to the next argument
done

# ---- Check that the pygers-meg environment is active ----
# Nothing here activates the environment: the script uses the Python of the environment
# you activated. Find "python" on the PATH ("|| true": don't stop if there is none) and
# make sure it has the packages the pipeline needs. If not, say how to fix it and stop.
PY="$(command -v python || true)"
if [[ -z "$PY" ]] || ! "$PY" -c "import papermill, yaml, jsonschema" 2>/dev/null; then
    echo "run_bids-pipeline.sh: the pygers-meg environment isn't active (or lacks papermill)." >&2
    echo "    conda activate pygers-meg" >&2
    exit 2
fi

# ${ARGS[@]+"${ARGS[@]}"} below means "the collected options, or nothing if there are
# none" (written this way so "set -u" doesn't stop the script when ARGS is empty).

# ---- --local: run the pipeline right here, without Slurm ----
# Only for an interactive compute session (e.g. from salloc), never on the login node:
# the notebooks need a lot of memory and time. run_bids-pipeline.py does its own checks first.
if [[ $LOCAL -eq 1 ]]; then
    exec "$PY" "$HERE/bids-pipeline/run_bids-pipeline.py" "$SUBJECT_FILE" ${ARGS[@]+"${ARGS[@]}"}
fi

# ---- The pre-flight check, now, before waiting in the queue ----
# run_bids-pipeline.py --check checks the configuration, the raw data and the software, prints
# what it found, and runs nothing. If it finds a problem, it exits with status 2, and
# "|| exit $?" stops this script with that same status, so nothing is submitted.
"$PY" "$HERE/bids-pipeline/run_bids-pipeline.py" "$SUBJECT_FILE" ${ARGS[@]+"${ARGS[@]}"} --check || exit $?

# With --check, that was all: stop here, successfully.
[[ $CHECK_ONLY -eq 1 ]] && exit 0

# ---- Submit the job ----
# Ask run_bids-pipeline.py for the sbatch options built from global-study.yaml's "slurm:" section
# (job name, time, memory, CPUs, partition, email, and where the Slurm log goes). It
# prints them on one line; "read -r -a" splits that line into the list SBATCH_OPTS.
read -r -a SBATCH_OPTS <<< "$("$PY" "$HERE/bids-pipeline/run_bids-pipeline.py" "$SUBJECT_FILE" --slurm-options)"

# Tell the job (Part 1) where the pipeline is and which Python to use. "export" passes
# these variables on to the programs started from here, and "--export=ALL" below makes
# Slurm pass all of them (and the rest of your environment, e.g. the PATH of the active
# pygers-meg environment) into the job.
export PYGERS_PIPELINE_DIR="$HERE" PYGERS_PYTHON="$PY"

# Show what is submitted, then submit: this same script, with the subject file and the
# options. sbatch prints "Submitted batch job <number>"; the job then waits in the queue.
echo "Submitting: sbatch ${SBATCH_OPTS[*]}"
sbatch --export=ALL "${SBATCH_OPTS[@]}" "$HERE/run_bids-pipeline.sh" "$SUBJECT_FILE" ${ARGS[@]+"${ARGS[@]}"}
