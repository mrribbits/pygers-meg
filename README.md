# Pygers-MEG Notebooks

Jupyter notebooks from the Pygers-MEG user group.

| Notebook | What it covers |
|---|---|
| [`01-explore-raw-files/01_Explore_Raw_OPM-MEG_Files.ipynb`](01-explore-raw-files/01_Explore_Raw_OPM-MEG_Files.ipynb) | Exploring raw Cerca `.cMEG` sessions, converting them to FIF with [cMEG2FIF](https://github.com/mrribbits/cMEG2FIF-Scully), and checking the result |
| 01-explore-raw-files/02_Explore_Eye_and_MRI_Files.ipynb | TBD |

---

## Getting the notebooks

Put the notebook wherever Jupyter will run it. That means scotty for Option A, or your laptop for Option B.

**Clone the repository** (recommended, so you can `git pull` future notebooks):

```bash
git clone https://github.com/mrribbits/pygers-meg.git
```

**Or download a single notebook:** open it on GitHub and click **Download raw file**.

---

## 0. Setup

Choose **one** of the two setups below, then set your paths in Section 0.3 of the notebook.

- **0.1 Option A: Cluster (preferred).** Jupyter runs on scotty, and you use it from your laptop's browser. The data never leave the server.
- **0.2 Option B: Local laptop.** Jupyter runs on your laptop and reads the data from your mounted lab partition.

### 0.1 Option A: Cluster setup (preferred)

#### 0.1.1 Pick a port and SSH in with a tunnel

Choose a port number in the 4000–9999 range, avoiding 8800–8899. On a Mac, also avoid 5000 and 7000. The examples below use `4817`. Replace it with your number **everywhere** it appears.

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

Create an environment with MNE and the converter:

```bash
conda create -n pygers-meg -c conda-forge python=3.11 mne jupyterlab pandas ipykernel ipympl
conda activate pygers-meg
pip install "cmeg2fif[plot] @ git+https://github.com/mrribbits/cMEG2FIF-Scully.git"
```

Check that the converter installed:

```bash
cmeg2fif --version
```

On later sessions, you only need `conda activate pygers-meg`.

#### 0.1.3 Launch Jupyter on scotty

```bash
jupyter lab --no-browser --ip=127.0.0.1 --port=4817 --ServerApp.port_retries=0
```

`--ServerApp.port_retries=0` makes Jupyter fail loudly if the port is taken, instead of silently switching to a different port that your tunnel isn't connected to.

#### 0.1.4 Open Jupyter in your laptop's browser

Copy the URL that Jupyter prints, which looks like `http://127.0.0.1:4817/lab?token=...`, and paste it into your local browser.

#### 0.1.5 Shut down

Press **Ctrl-C twice** in the scotty session. If you ever leave a server running by accident, `jupyter server list` shows it and `jupyter server stop 4817` stops it.

### 0.2 Option B: Local laptop setup

#### 0.2.1 Create the Python environment (do this once, in your Terminal)

Create an environment with MNE and the converter:

```bash
conda create -n pygers-meg -c conda-forge python=3.11 mne jupyterlab pandas ipympl
conda activate pygers-meg
pip install "cmeg2fif[plot] @ git+https://github.com/mrribbits/cMEG2FIF-Scully.git"
```

Check that the converter installed:

```bash
cmeg2fif --version
```

To start Jupyter, run `conda activate pygers-meg` and then `jupyter lab`.

#### 0.2.2 Mount your `cup` lab partition

All data stay on the server: **do not copy them to your laptop.** Mount the server instead:

- **macOS:** In Finder, choose *Go → Connect to Server…*
- **Windows:** In File Explorer, choose *Map network drive*.

