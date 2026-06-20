# BionixDB
## 1.0 Introduction
## 2.0 Requirements
### 2.1 Storing Data in Compliance with REB Protocols
### 2.2 Connecting to ROS2
### 2.3 Integrating with Python Code
## 3.0 Proposed Software Stack
### 3.1 Storage Layer
Datasets live in a Google Shared Drive ("Alberta Bionix"), under a `Datasets` folder with one subfolder per modality (`emg`, `imu`, `cvkas`).

### 3.2 Python Library
The `BionixDB` class (`src/bionix_db/bionixdb.py`) wraps the Google Drive API v3. It authenticates via OAuth2 on construction and exposes `get_*`/`upload_*` methods per modality, backed by generic `get(modality, ...)`/`upload(modality, ...)` implementations.

### 3.3 Authentication and Access Control
Two access levels, modeled by the `Access` enum:
- `CONTRIBUTOR`: any account that's a member of the shared drive. Sufficient for reading datasets.
- `CONTENT_MANAGER`: an `organizer` or `fileOrganizer` role on the shared drive. Required to upload datasets.

`BionixDB.__init__` authenticates at `CONTRIBUTOR` level and raises `PermissionError` if the account has no access to the drive at all. `upload()` re-authenticates and re-checks for `CONTENT_MANAGER` access at the moment of upload (rather than trusting the access level captured at construction), since role membership can change between sessions.

### 3.4 Data Serving Mechanism
`get_*` lists files in the relevant modality folder, filters them by filename (see 3.5), downloads matching CSVs in-memory, and concatenates them into a single `pandas.DataFrame`. `get_session(pid, action, trial=None)` does this across all three modality folders for one trial at once, returning `{modality: DataFrame}` for whichever modalities have a matching file — a session need not include all three. If `trial` is omitted, it resolves to the highest trial number found across *any* modality folder for that pid/action.

### 3.5 Data Organization and Metadata
Dataset files are named `{modality}-{pid}-{action}-{trial}.csv`, e.g. `emg-p001-walking-01.csv`:
- `modality`: `emg`, `imu`, or `cvkas`
- `pid`: zero-padded participant id (`p001`)
- `action`: `seated`, `walking`, `sitstand`, or `stairs` (`Action` enum / `ACTION_NAMES`)
- `trial`: zero-padded trial number (`01`, `02`, ...)

The trial number is assigned by the library, not the caller. `upload()` (single modality) lists existing files matching `{modality}-{pid}-{action}-` and uses one past the highest existing trial number *for that modality*. `upload_session(pid, action, emg=, imu=, cvkas=)` instead computes one trial number shared across whichever modalities are passed in, set to one past the highest existing trial across *all three* modality folders for that pid/action — not just the modalities being uploaded. This is what keeps EMG/IMU/CVKAS aligned to the same physical trial even if, e.g., CVKAS for trial 2 is uploaded later/separately than the EMG+IMU for that trial: `upload_session` is the only way that alignment is enforced — calling `upload_emg`/`upload_imu`/`upload_cvkas` independently does not check or maintain it, since each only looks at its own modality's existing trials.

This auto-increment has a (currently unhandled) race if two uploads for the same modality/pid/action happen concurrently. `get_*` parses the same convention and silently skips any file in a modality folder that doesn't match it.

### 3.6 ROS2 Integration Layer
## 4.0 Points of Contact
## 5.0 Summary