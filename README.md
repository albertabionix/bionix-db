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

# Using BionixDB in Another Project

Install a tagged release directly from GitHub. No PyPI or local clone is needed:
```
pip install "bionixdb @ git+https://github.com/albertabionix/bionix-db.git@v0.2.0"
```
or put this line in the project's `requirements.txt`:
```
bionixdb @ git+https://github.com/albertabionix/bionix-db.git@v0.2.0
```
Then `from bionix_db import BionixDB`. The bundled OAuth client (`credentials.json`) is installed with the package.

Always pin to a tag (`@v0.2.0`), not `@main`. That way, work in progress on `main` never reaches other projects by accident, and every app release records exactly which BionixDB version it uses.

# Releasing a New Version

Projects that depend on BionixDB, such as [EMG-and-IMU-Data-Collection](https://github.com/albertabionix/EMG-and-IMU-Data-Collection), only get your changes once you publish a new tag and they update their pin.

1. Merge your changes into `main` (tests passing: `pytest tests/`).
2. In a PR, bump `version` in `setup.py` ([semver](https://semver.org): bug fix `0.2.0 → 0.2.1`, new feature `→ 0.3.0`, breaking API change such as a renamed or removed function or parameter `→ 1.0.0`), then merge it.
3. Tag the merged commit on `main` and push the tag. The tag must match the `setup.py` version:
   ```
   git checkout main && git pull
   git tag v0.3.0
   git push origin v0.3.0
   ```
4. In each dependent project, change `@v0.2.0` to `@v0.3.0` in `requirements.txt`, run `pip install -r requirements.txt`, test, and merge. For EMG-and-IMU, then cut a new app release so the executables include the update (see its README).

Never move or re-push an existing tag. If a release is broken, fix it and release the next version.

## Testing unreleased changes in a dependent project

To try BionixDB changes in another project before tagging them, temporarily install your local clone over the pinned version, from that project's venv:
```
pip install -e ../bionix-db        # the project now uses your local working copy
# ...develop and test...
pip uninstall -y bionixdb && pip install -r requirements.txt   # back to the pinned release
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
df = db.get_imu(pid="p002", action="walking", trial=1)  # action can also be the filename token
df = db.get_cvkas(pid=20, action=Action.STAIRS, exercise="stairmaster")

# All modalities recorded for one trial at once. A modality with no file for
# that trial is left out of the result; this is not an error.
session = db.get_session(pid=1, action=Action.WALKING)          # trial=None -> latest trial
session = db.get_session(pid=1, action=Action.WALKING, trial=2) # a specific trial
emg_df = session.get("emg")  # None if EMG wasn't recorded for that trial
```
`pid` accepts an int (`1` -> `p001`) or the zero-padded string (`"p001"`). `trial` accepts an int (`1` -> `01`) or a string. For `get_session`, "latest trial" means the highest trial number found in *any* modality for that pid/action, not only the modalities in the result.

### What the DataFrame looks like
Every matching file is stacked into one long DataFrame, sorted by (pid, action, exercise, trial). Five metadata columns from the filename come first, followed by the file's own columns, unchanged:

| column | example | notes |
|---|---|---|
| `pid` | `p020` | |
| `action` | `stairs` | |
| `exercise` | `stairmaster` | `None` for files named without an exercise |
| `trial` | `3` | int |
| `file` | `emg-p020-stairs-stairmaster-03.csv` | source file on the Drive |

Split it back into individual recordings with `groupby`:
```python
for trial, rec in db.get_emg(pid=20, action=Action.STAIRS).groupby("trial"):
    ...
```

Per-modality columns (raw values, exactly as recorded by EMG-and-IMU-Data-Collection):
- **emg**: `timestamp, emg1, emg2`
- **imu**: `timestamp, imu{1,2}_{ax,ay,az,gx,gy,gz}`, with accelerometer values in m/s² and raw gyro values. No calibration offsets are applied.
- **cvkas**: `timestamp, marker_id, pos_x, pos_y, velocity, acceleration, hip_angle, knee_angle`. There is one row per detected marker per camera frame. A frame with no detected markers is a row with only `timestamp` set. `marker_id` is a nullable integer (`Int64`).

Things to know before analysing:
- `timestamp` is Unix epoch seconds from the recording computer's clock. All modalities in a session share that clock, so they can be aligned with `pd.merge_asof` on `timestamp`. Timestamps are parsed at full precision.
- Timestamps are host arrival times, not sensor sample times, and can repeat. Don't assume a fixed sample rate or unique timestamps.
- EMG and IMU rows are written in pairs with identical timestamps. The IMU updates more slowly than EMG, so IMU values repeat over several consecutive rows. Drop consecutive duplicates if you want only new IMU samples.

### Raw CSV files instead
To work with the original files yourself (in MATLAB, Excel, or your own parsing), download them unchanged:
```python
paths = db.download_emg("data/", pid=20, action=Action.STAIRS)   # -> list[Path]
db.download("cvkas", "data/", pid="p002")
```
`download_imu` and `download_cvkas` also exist. They take the same filters as the `get_*` functions.

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
{modality}-{pid}-{action}[-{exercise}]-{trial}.csv
```
- `modality`: `emg`, `imu`, or `cvkas`
- `pid`: zero-padded participant id, e.g. `p001`
- `action`: one of `seated`, `walking`, `sitstand`, `stairs` (see `ACTION_NAMES` in `bionixdb.py`)
- `exercise` (optional): free-text sub-activity, e.g. `stairmaster`. `-` is replaced with `_` so the name stays parseable; a blank exercise is treated as none
- `trial`: zero-padded trial number, e.g. `01` — assigned automatically by `upload()` as one past the highest existing trial for that modality/pid/action; never chosen by the caller

Examples: `emg-p001-walking-01.csv`, `emg-p020-stairs-stairmaster-03.csv`

## Drive folder structure
```
Google Drive
|---- Datasets
|----|---- cvkas
|----|---- emg
|----|----|---- emg-p001-walking-01.csv
|----|---- imu
```
