# Pygers-MEG Notebooks

Jupyter notebooks from the Pygers-MEG user group.

| Raw to BIDS Notebooks (01-explore-raw-files/) | What it covers |
|---|---|
| [`01_Explore_Raw_OPM-MEG_Files.ipynb`](01-explore-raw-files/01_Explore_Raw_OPM-MEG_Files.ipynb) | Exploring raw Cerca `.cMEG` sessions, converting them to FIF with [cMEG2FIF](https://github.com/mrribbits/cMEG2FIF-Scully), and checking the result |
| [`02_Explore_Eye_Data_Files.ipynb`](01-explore-raw-files/02_Explore_Eye_Data_Files.ipynb) | Converting raw `.EDF` files to BIDS with [edf2bids](https://github.com/mrribbits/eye2bids/tree/scully-config), and checking the result |
| [`03_Dicom_to_BIDS_heudiconv.ipynb`](01-explore-raw-files/03_Dicom_to_BIDS_heudiconv.ipynb) | Converting DICOM files to NIFTI BIDS with [HeuDiConv](https://heudiconv.readthedocs.io/en/latest/), and checking the result |
| [`04_FIF_to_BIDS.ipynb`](01-explore-raw-files/04_FIF_to_BIDS.ipynb) | Transitioning the FIF MEG data into the BIDS structure |
| [`05_Sync_eye_to_MEG.ipynb`](01-explore-raw-files/05_Sync_eye_to_MEG.ipynb) | Aligning eye data with MEG data |
| [`06_Validate_BIDS.ipynb`](01-explore-raw-files/06_Validate_BIDS.ipynb) | Final step running BIDS Validator |
---

| Preprocessing Notebooks (02-preprocessing/) | What it covers |
|---|---|
| [`01_Explore_Data_and_Sensor_Quality.ipynb`](02-preprocessing/01_Explore_Data_and_Sensor_Quality.ipynb) | A first look at the recording and a check of every OPM sensor: flat and noisy channels, power spectra, time courses; saves the bad channels with reasons |
| [`TBD1_FreeSurfer_recon-all.ipynb`](02-preprocessing/TBD1_FreeSurfer_recon-all.ipynb) | Generate surface reconstructions and BEM/scalp surfaces (step number to be assigned) |
| [`TBD2_Coreg_and_Forward.ipynb`](02-preprocessing/TBD2_Coreg_and_Forward.ipynb) | Perform coregistration, create surface and volume source spaces and forward solutions (step number to be assigned) |
---

## Getting the notebooks

Put the notebook wherever Jupyter will run it. That means scotty for Option A, or your laptop for Option B.

**Clone the repository** (recommended, so you can `git pull` future notebooks):

```bash
git clone https://github.com/mrribbits/pygers-meg.git
```

**Or download a single notebook:** open it on GitHub and click **Download raw file**.

### Work in your own copy

Before running a notebook, make a personal copy and work in that instead of the original:

```bash
cd pygers-meg/01-explore-raw-files
cp 01_Explore_Raw_OPM-MEG_Files.ipynb my_01_Explore_Raw_OPM-MEG_Files.ipynb
```

Running a notebook saves outputs into the file. Working in a `my_` copy keeps the original unchanged, so updates always pull cleanly and your notes and outputs are never overwritten. Git ignores files starting with `my_`.

### Updating

To get new notebooks and fixes, run this in your clone:

```bash
cd pygers-meg
git pull
```

If `git pull` stops with *"Your local changes … would be overwritten by merge"*, you edited or ran an original notebook. Discard those changes (keep anything you want in a `my_` copy first), then pull again:

```bash
git restore 01-explore-raw-files/01_Explore_Raw_OPM-MEG_Files.ipynb
git pull
```

After an update, make a fresh `my_` copy of any notebook that changed. Your old copy won't pick up the changes.

---

## 0. Setup

Choose **one** of the two setups below, then set your paths in Section 0.3 of the notebook.

- **0.1 Option A: Cluster (preferred).** Jupyter runs on scotty, and you use it from your laptop's browser. The data never leave the server.
- **0.2 Option B: Local laptop.** Jupyter runs on your laptop and reads the data from your mounted lab partition.

### 0.1 Option A: Cluster setup (preferred)

#### 0.1.1 Pick a port and SSH in with a tunnel

Choose a port number in the 4000–9999 range, avoiding 8800–8899. On a Mac, also avoid 5000 and 7000. The examples below use `4817`. Replace it with your number **everywhere** it appears, and use the same number every time you start Jupyter (0.1.4 explains why).

In your laptop's Terminal, SSH to scotty with the tunnel:

```bash
ssh -L 4817:localhost:4817 NETID@scotty.pni.princeton.edu
```

On scotty, check that nobody else is using the port:

```bash
ss -ltn | grep 4817
```

If this prints nothing, the port is free. If it prints a line, log out and start again with a different port.

#### 0.1.2 Create the conda environment (do this once, on scotty)

##### 0.1.2.1 Install miniconda (if you have already installed/used conda on the server, skip this step)
You should install miniconda in your folder on your lab directory so that you do not run out of space in your home directory. You can then create a symlink in your home directory.

```bash
cd /jukebox/<lab>/<personal folder>

#download
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh

#install
bash Miniconda3-latest-Linux-x86_64.sh

#if you don't want this base environment automatically loaded when you login:
conda config --set auto_activate_base false
source .bashrc

#remove install file
rm -r Miniconda3-latest-Linux-x86_64.sh

#create symlink in home directory
ln -s /jukebox/<lab>/<personal folder>/miniconda3 ~/.miniconda3

#check your setup
cd ~
conda info
conda env list
conda --help
```

##### 0.1.2.2 Create pygers-meg conda environment

###### 0.1.2.2.1 Either create the environment from scratch:

```bash
conda create -n pygers-meg -c conda-forge python=3.11 mne mne-bids jupyterlab pandas ipykernel ipympl
conda activate pygers-meg
pip install "cmeg2fif[plot] @ git+https://github.com/mrribbits/cMEG2FIF-Scully.git"
```

###### 0.1.2.2.2 Or use the provided scotty-environment.yml file:

```bash
conda env create -f scotty-environment.yml -n pygers-meg
conda activate pygers-meg
```

###### 0.1.2.2.3 Check that the converter installed:

```bash
cmeg2fif --version
```

On later sessions, you only need `conda activate pygers-meg`.

#### 0.1.3 Launch Jupyter on scotty

```bash
jupyter lab --no-browser --ip=127.0.0.1 --port=4817 --ServerApp.port_retries=0 \
  --ServerApp.custom_display_url=http://localhost:4817
```

`--ServerApp.port_retries=0` makes Jupyter fail loudly if the port is taken, instead of silently switching to a different port that your tunnel isn't connected to. `--ServerApp.custom_display_url` makes Jupyter print its address with `localhost` (see 0.1.4).

#### 0.1.4 Open Jupyter in your laptop's browser

Copy the URL that Jupyter prints, which looks like `http://localhost:4817/lab?token=...`, and paste it into your local browser.

**Why the same port, and `localhost`?** Each Jupyter session saves a login cookie in your browser, named after its port, and browsers don't keep cookies apart by port. A new port every time piles up cookies until some tools refuse the request; the 3D coregistration window in the preprocessing notebooks fails with `LineTooLong` ("Got more than 8190 bytes"). With one port you keep one cookie. Using `localhost` instead of `127.0.0.1` also leaves behind any old cookies piled up for `127.0.0.1`.

#### 0.1.5 Shut down

Press **Ctrl-C twice** in the scotty session. If you ever leave a server running by accident, `jupyter server list` shows it and `jupyter server stop 4817` stops it.

### 0.2 Option B: Local laptop setup

#### 0.2.1 Create the Python environment

```bash
conda create -n pygers-meg -c conda-forge python=3.11 mne jupyterlab pandas ipympl mne-bids
conda activate pygers-meg
pip install "cmeg2fif[plot] @ git+https://github.com/mrribbits/cMEG2FIF-Scully.git"
```

#### 0.2.2 Check that the converter installed:

```bash
cmeg2fif --version
```

To start Jupyter, run `conda activate pygers-meg` and then `jupyter lab`.

#### 0.2.3 Mount your `cup` lab partition

All data stay on the server: **do not copy them to your laptop.** Mount the server instead:

- **macOS:** In Finder, choose *Go → Connect to Server…*
- **Windows:** In File Explorer, choose *Map network drive*.


---

---

## 1. Streamlining Cluster Access

### 1.1 Set up SSH key authentication (passwordless SSH login)

#### 1.1.1 From your Mac's Terminal:
```bash
ssh-keygen -t ed25519 -C "my-mac"           # accept defaults; set a passphrase
ssh-copy-id NETID@scotty.pni.princeton.edu  # the last time you type your password
```

#### 1.1.2 On your Mac, add this to ~/.ssh/config
```bash
Host scotty scotty.pni.princeton.edu
    HostName scotty.pni.princeton.edu
    User NETID #EDIT THIS
    IdentityFile ~/.ssh/id_ed25519
    AddKeysToAgent yes
    UseKeychain yes
    ControlMaster auto
    ControlPath ~/.ssh/cm-%r@%h:%p
    ControlPersist 4h
```
### 1.2 Download the `scotty-jupyter` script to your Mac

#### 1.2.1 Edit the script
* Add your NETID, PROJECT_DIR, and CONDA_ENV

#### 1.2.2 Run these lines in your Mac's Terminal:
```bash
   mkdir -p ~/bin && mv ~/Downloads/scotty-jupyter ~/bin/ && chmod +x ~/bin/scotty-jupyter
   echo 'export PATH="$HOME/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```
These lines will create a ~/bin folder and move the script from ~/Downloads to it.
It will then make the script executable and add it to your path.

### 1.3 Now you can use the `scotty-jupyter` script. 

* The script will SSH into Scotty for you without a password.
* It uses your own port, the same every time (worked out from your NetID; if it's busy, the script tries the next one), and opens Jupyter at `localhost`. See "Why the same port, and `localhost`?" in 0.1.4.
* It will activate your pygers-meg environment.
* `cd` you into the pygers-meg folder.
* And launch JupyterLab so it prints the URL you can use to paste into your Mac's browser.
* Pressing Ctrl-C twice stops Jupyter and closes the tunnel.



