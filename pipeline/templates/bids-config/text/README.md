# {ds_name}

## Overview
OPM-MEG recordings of an active, cued auditory oddball task,
collected at the Scully Center for the Neuroscience of Mind & Behavior, Princeton Neuroscience
Institute, as a teaching and methods dataset (Pygers-MEG / NEU502B). The task is adapted from the
FieldTrip NatMEG 2014 auditory oddball tutorial
(https://www.fieldtriptoolbox.org/workshop/natmeg2014/meg_audodd/). It supports classical
mismatch-negativity (MMN) and P3 analyses of tone-locked event-related fields, and analyses of
lateralized post-movement beta rebound (PMBR) contrasting the cued left and right hands.
Eye movements and pupil size were recorded at the same time with an EyeLink eye tracker.

## Participants
One participant (F, 30) gave written informed consent; procedures were approved by the
Princeton University Institutional Review Board (16967). Demographic information is in
participants.tsv.

## Task: {task}
Each trial starts with a fixation cross (1.5 s), followed by an arrow cue (0.5 s) telling the
participant to prepare a left- or right-hand response. The participant then hears 3–7 standard
tones (1000 Hz) followed by one deviant tone (1200 Hz). Tones last 400 ms with 50 ms cosine
on/off ramps, and the gap between one tone's offset and the next tone's onset is jittered between
700 and 900 ms. The participant presses the index-finger button on the cued hand as quickly as
possible after the deviant, during a 1 s response period with only the fixation cross on screen.
Feedback (0.4 s) is shown only after misses or wrong-hand responses. Every trial ends with a
"BLINK" screen (0.5 s) giving the participant time to blink, so that blinks stay out of the
analysis periods.
The recording has 3 blocks × 100 trials (about 51 minutes), after a 20-trial training block with
feedback. Tones were played through the VPixx DATAPixx system (48 kHz) and room loudspeakers, with
their timing scheduled by the VPixx hardware. The instructions shown to the participant are in
the Instructions field of the task's _meg.json. Event codes in _events.tsv (trial_type = value):
{event_codes}.
Event onsets were decoded from the trigger channel; durations are given in _events.tsv.

## Apparatus
OPM-MEG: Cerca Magnetics system with QuSpin QZFM Gen-3 sensors, inside a magnetically shielded
room. {n_meg_channels} MEG channels and
{n_ref_channels} reference channels, sampled at
{sfreq} Hz. The sensors' analog output is DC-coupled with a 150 Hz bandwidth;
there is no anti-aliasing filter before digitization (see HardwareFilters in _meg.json).
Stimuli were presented with PsychoPy (code: https://github.com/mrribbits/Auditory-Oddball-Psychopy)
through VPixx hardware; triggers were sent in pixel mode. Responses: Current Designs (ASPEN-OIU) with 5-button response pads. Eye tracking: EyeLink
1000 Plus monocular tracking at 500 Hz in Remote Mode.
Auditory stimuli were presented using the wall-mounted loudspeakers in the magnetically-shielded room.
Independent measurements confirm a ~3ms transit time for the audio to reach the subject (i.e. triggers will lead the sound by ~3ms).
Empty-room recordings are stored as task-noise ({emptyroom_layout} layout) and linked from each
recording's _meg.json via AssociatedEmptyRoom.

## Data processing before BIDS conversion
Raw Cerca .cMEG files were converted to FIF with cMEG2FIF {cmeg2fif_version}; no
filtering or other processing was applied during conversion (https://github.com/mrribbits/cMEG2FIF-Scully).
Raw eye data .EDF files were converted to ASC and BIDS structure with the Scully Center branch of
eye2bids (https://github.com/mrribbits/eye2bids/tree/scully-config).
Raw MRI DICOM files were converted to NIfTI and BIDS structure with HeuDiConv v1.5.1.
BIDS conversion: MNE {mne_version}, MNE-BIDS {mne_bids_version}, with the Pygers-MEG
notebook 04_FIF_to_BIDS.
{anonymization_note}

## Known issues
None known.

## Contact
Pygers-MEG@princeton.edu

## How to cite
Please cite this dataset using the metadata in CITATION.cff, which also lists the related
references (MEG-BIDS, MNE-BIDS, FieldTrip, and the oddball, MMN and beta-rebound literature).
