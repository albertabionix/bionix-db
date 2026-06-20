# Developer Setup
## Environment Setup
1) Create a virtual environment and install your dependencies
```
python -m venv .bionixdb
source .bionixdb/bin/activate
pip install -e .
pip install -r requirements-dev.txt
```

2) `BionixDB` ships with a bundled OAuth client (`src/bionix_db/credentials.json`), so you don't need your own Google Cloud project — on first use it opens a browser login and caches the resulting token to `token.json` in your working directory. If you'd rather use your own OAuth client, place a `credentials.json` at the repo root (or pass `credentials_file=` explicitly) — an explicit/local file always takes priority over the bundled one.

Note: the bundled credential only identifies the app to Google; it doesn't grant Drive access by itself. You still log in with your own Google account, and `BionixDB` checks *that account's* actual membership/role on the Alberta Bionix Shared Drive before granting access.

## Running the tests
```
pytest tests/
```
Tests mock the Google Drive API, so no credentials are needed to run them.

## Example usage
See [example.py](example.py) for end-to-end read and upload examples.

```
python example.py
```

# BionixDB Library Functions

## Access Levels
Every account that's a member of the Alberta Bionix Shared Google Drive gets `CONTRIBUTOR` access automatically, which is enough to read datasets. Uploading requires `CONTENT_MANAGER` access (an `organizer` or `fileOrganizer` role on the shared drive). `BionixDB` re-checks the caller's role at the moment of upload, since access can be revoked or changed between sessions — granting `CONTENT_MANAGER` after construction is enough; you don't need to re-create the `BionixDB` instance.

If the signed-in account isn't a member of the shared drive at all, constructing `BionixDB` raises a `PermissionError` immediately.

## Reading data
```python
from bionix_db import BionixDB
from bionix_db.bionixdb import Action

db = BionixDB()

df = db.get_emg()                                    # all EMG files
df = db.get_emg(pid=1, action=Action.WALKING)        # filtered by participant + action
df = db.get_imu(pid="p002", action=Action.WALKING, trial=1)  # a specific trial
df = db.get_cvkas(pid="p002")

# All modalities recorded for one trial at once — modalities with no file for
# that trial are simply omitted from the result, no error.
session = db.get_session(pid=1, action=Action.WALKING)          # trial=None -> latest trial
session = db.get_session(pid=1, action=Action.WALKING, trial=2) # a specific trial
emg_df = session.get("emg")  # None if EMG wasn't recorded for that trial
```
`pid` accepts either an int (`1` -> `p001`) or the zero-padded string (`"p001"`). `trial` accepts an int (`1` -> `01`) or a string. For `get_session`, "latest trial" is the highest trial number found across *any* modality for that pid/action, not just the ones present in the result.

## Uploading data
Uploading requires `CONTENT_MANAGER` access. The local CSV's filename doesn't matter — `upload_*` names the uploaded file itself per the convention below, automatically assigning the next trial number for that participant/action.

```python
db.upload_emg("local_emg_recording.csv", pid=1, action=Action.WALKING)   # -> emg-p001-walking-01.csv
db.upload_emg("local_emg_recording.csv", pid=1, action=Action.WALKING)   # -> emg-p001-walking-02.csv
db.upload_imu("local_imu_recording.csv", pid=1, action=Action.WALKING)
db.upload_cvkas("local_cvkas_recording.csv", pid=1, action=Action.WALKING)
```

When multiple modalities are recorded for the same physical trial, prefer `upload_session` over calling `upload_emg`/`upload_imu`/`upload_cvkas` independently — it assigns one shared trial number across whichever modalities are passed in (any subset of `emg`/`imu`/`cvkas`), so they can't drift out of alignment if one modality is uploaded later or skipped for a trial:

```python
db.upload_session(
    pid=1, action=Action.WALKING,
    emg="local_emg_recording.csv",
    imu="local_imu_recording.csv",
    # cvkas omitted — not every trial needs all three modalities.
)
```
The shared trial number is one past the highest trial number found across *all* modality folders for that pid/action (not just the ones being uploaded), so a session that only ever records EMG+IMU won't reuse a trial number CVKAS already used independently.

Uploads (`upload`/`upload_session`) are rejected (before touching the Drive) if:
- The caller does not have `CONTENT_MANAGER` access (`PermissionError`)
- The local path doesn't exist (`FileNotFoundError`)
- The file isn't a `.csv` (`ValueError`)
- `upload_session` is called with no modalities at all (`ValueError`)

## Dataset naming convention
```
{modality}-{pid}-{action}-{trial}.csv
```
- `modality`: `emg`, `imu`, or `cvkas`
- `pid`: zero-padded participant id, e.g. `p001`
- `action`: one of `seated`, `walking`, `sitstand`, `stairs` (see `ACTION_NAMES` in `bionixdb.py`)
- `trial`: zero-padded trial number, e.g. `01` — assigned automatically by `upload()` as one past the highest existing trial for that modality/pid/action; never chosen by the caller

Example: `emg-p001-walking-01.csv`

## Drive folder structure
```
Google Drive
|---- Datasets
|----|---- cvkas
|----|---- emg
|----|----|---- emg-p001-walking-01.csv
|----|---- imu
```
