#!/bin/bash
XDG_RUNTIME_DIR=""
ipnport=$(shuf -i8000-9999 -n1)
ipnip=$(hostname -i)

## print tunneling instructions to jupyter-log-{jobid}.txt
echo -e "
    Copy/Paste this in your local terminal to ssh tunnel with remote
    -----------------------------------------------------------------
    ssh -N -L $ipnport:$ipnip:$ipnport $USER@scotty.princeton.edu
    -----------------------------------------------------------------

    Then open a browser on your local machine to the following address
    ------------------------------------------------------------------
    localhost:$ipnport  (prefix w/ https:// if using password)
    ------------------------------------------------------------------
    "

## start an ipcluster instance and launch jupyter server

# At PNI, use pyger
#module load pyger
#module load freesurfer
#module load pysurfer

#module load Langs/Python/3.5-anaconda
#module load Pypkgs/brainiak/0.5-anaconda
#module load Pypkgs/NILEARN/0.4.0-anaconda

#module load MPI/OpenMPI
jupyter-notebook --no-browser --port=$ipnport --ip=$ipnip
