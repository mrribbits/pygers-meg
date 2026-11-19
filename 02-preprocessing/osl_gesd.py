"""Outlier tests for bad-segment detection, copied from osl-ephys for preprocessing notebook 04.

Preprocessing notebook 04 (Step 7) finds bad stretches of data the way Harrison Ritz's mne-opm
pipeline does: it calls osl-ephys' ``bad_segments`` (``metric="std"``, ``channel_wise=True``).
Installing all of osl-ephys would bring in many other packages, so only the two functions this
needs are copied here:

* ``gesd``: the Generalized Extreme Studentized Deviate test (Rosner, 1983), unchanged.
* ``bad_windows_channel_wise``: the ``channel_wise=True`` part of osl-ephys'
  ``_find_outliers_in_segments``, changed as listed below.

Source: osl-ephys 2.4.1, osl_ephys/preprocessing/osl_wrappers.py
        https://github.com/OHBA-analysis/osl-ephys
Copyright 2021-2024 University of Oxford, Oxford, UK.
Authors of the copied code: Andrew Quinn, Chetan Gohil, Mats van Es.

Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file
except in compliance with the License. A copy of the License is in LICENSE-osl-ephys.txt next to
this file, and at http://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software distributed under the
License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
express or implied. See the License for the specific language governing permissions and
limitations under the License.

Changes made for Pygers-MEG (Scully Center, Princeton Neuroscience Institute, 2026):

* ``gesd``: unchanged, except that this docstring note was added.
* ``bad_windows_channel_wise`` is ``_find_outliers_in_segments`` with ``channel_wise=True`` and a
  numeric ``threshold``, rewritten to take the metric (one value per channel and window) already
  computed, instead of the data. The notebook computes that metric one channel at a time, so it
  never needs a second copy of the whole recording in memory. The test itself is the same: GESD
  on each channel's window values, then a window is bad when at least ``threshold`` x the number
  of channels are outliers in it. It returns one True/False per window instead of per sample.
* The rest of osl-ephys (and its dependencies) is not included.
"""

import numpy as np
from scipy import stats


def gesd(x, alpha=0.05, p_out=.1, outlier_side=0):
    """Detect outliers using Generalized ESD test

     Parameters
     ----------
     x : vector
        Data set containing outliers
     alpha : scalar
        Significance level to detect at (default = 0.05)
     p_out : int
        Maximum number of outliers to detect (default = 10% of data set)
     outlier_side : {-1,0,1}
        Specify sidedness of the test
           - outlier_side = -1 -> outliers are all smaller
           - outlier_side = 0  -> outliers could be small/negative or large/positive (default)
           - outlier_side = 1  -> outliers are all larger

    Returns
    -------
    idx : boolean vector
        Boolean array with TRUE wherever a sample is an outlier
    x2 : vector
        Input array with outliers removed

    References
    ----------
    B. Rosner (1983). Percentage Points for a Generalized ESD Many-Outlier Procedure. Technometrics 25(2), pp. 165-172.
    http://www.jstor.org/stable/1268549?seq=1

    """

    if outlier_side == 0:
        alpha = alpha/2

    if not isinstance(x, np.ndarray):
        x = np.asarray(x)

    n_out = int(np.ceil(len(x)*p_out))

    if np.any(np.isnan(x)):
        # Need to find outliers only in finite x
        y = np.where(np.isnan(x))[0]
        idx1, x2 = gesd(x[np.isfinite(x)], alpha, n_out, outlier_side)

        # idx1 has the indexes of y which were marked as outliers
        # the value of y contains the corresponding indexes of x that are outliers
        idx = np.zeros_like(x).astype(bool)
        idx[y[idx1]] = True

    n = len(x)
    temp = x.copy()
    R = np.zeros((n_out,))
    rm_idx = np.zeros((n_out,), dtype=int)
    lam = np.zeros((n_out,))

    for j in range(0, int(n_out)):
        i = j+1
        if outlier_side == -1:
            rm_idx[j] = np.nanargmin(temp)
            sample = np.nanmin(temp)
            R[j] = np.nanmean(temp) - sample
        elif outlier_side == 0:
            rm_idx[j] = int(np.nanargmax(abs(temp-np.nanmean(temp))))
            R[j] = np.nanmax(abs(temp-np.nanmean(temp)))
        elif outlier_side == 1:
            rm_idx[j] = np.nanargmax(temp)
            sample = np.nanmax(temp)
            R[j] = sample - np.nanmean(temp)

        R[j] = R[j] / np.nanstd(temp)
        temp[int(rm_idx[j])] = np.nan

        p = 1-alpha/(n-i+1)
        t = stats.t.ppf(p, n-i-1)
        lam[j] = ((n-i) * t) / (np.sqrt((n-i-1+t**2)*(n-i+1)))

    # Create a boolean array of outliers
    idx = np.zeros((n,)).astype(bool)
    idx[rm_idx[np.where(R > lam)[0]]] = True

    x2 = x[~idx]

    return idx, x2


def bad_windows_channel_wise(metric, threshold=0.05, gesd_args=None):
    """Find the bad windows from a channel x window metric (osl-ephys' channel-wise test).

    Parameters
    ----------
    metric : array, shape (n_channels, n_windows)
        One value per channel and window (e.g. the standard deviation of the data in it).
    threshold : float
        A window is bad when at least ``threshold`` x n_channels channels are outliers in it
        (osl-ephys' ``channel_threshold``).
    gesd_args : dict
        Passed to ``gesd`` (e.g. ``{"alpha": 0.05}``).

    Returns
    -------
    bad : array of bool, shape (n_windows,)
        True for the bad windows.
    outliers : array of bool, shape (n_channels, n_windows)
        True where a channel is an outlier in a window.
    """
    if gesd_args is None:
        gesd_args = {}
    metric = np.nan_to_num(np.asarray(metric, dtype=float), nan=0)   # as in osl-ephys
    num_channels = metric.shape[0]
    if not isinstance(threshold, (int, float)):
        raise ValueError("Threshold must be an integer or float.")
    if not 0 < threshold <= 1:
        raise ValueError('Threshold must be between 0 and 1.')
    if num_channels*threshold < 1:
        raise ValueError('Threshold*n_channels must be at least 1 channel.')

    outliers = np.zeros(metric.shape, dtype=bool)
    for ch in range(num_channels):
        # Apply the GESD test to identify outlier segments for each channel
        outliers[ch], _ = gesd(metric[ch], **gesd_args)

    # Combine the channels: a window is bad when enough channels are outliers in it
    bad = np.sum(outliers, axis=0) >= threshold*num_channels
    return bad, outliers
