import pandas as pd
from enum import Enum
from pathlib import Path

import io

from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from googleapiclient.errors import HttpError

from .oauth import authenticate, build_service, list_shared_drives, list_files_in_shared_drive

# File Naming: {modality}-{pid}-{action}-{exercise?}-{trial}.csv
# `exercise` is optional but preferred for multi-phase actions. Trial numbers are
# zero-padded and auto-increment for the same modality/pid/action/exercise.
# Example: emg-p001-walking-r_leg-01.csv
# Example: emg-p001-sitstand-01.csv

# File Structure:
# Google Drive
# |---- Datasets
# |----|---- cvkas
# |----|---- emg
# |----|----|---- emg-p001-walking-01.csv
# |----|---- imu

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
class Access(Enum):
    NONE = 0
    CONTRIBUTOR = 1
    CONTENT_MANAGER = 2

class Action(Enum):
    SEATED = 0
    WALKING = 1
    SITTOSTAND = 2
    STAIRS = 3
    # ...

# Maps Action enum values to the string tokens used in filenames (e.g. SITTOSTAND -> "sitstand")
ACTION_NAMES = {
    Action.SEATED:     "seated",
    Action.WALKING:    "walking",
    Action.SITTOSTAND: "sitstand",
    Action.STAIRS:     "stairs",
}

# Reverse of ACTION_NAMES, for callers (e.g. EMG-IMU's Flask backend) that receive the
# string token over the wire and need the Action enum member back.
ACTION_BY_NAME = {name: action for action, name in ACTION_NAMES.items()}

# OAuth client secret bundled with the package itself, so individual installs don't each
# need their own Google Cloud project. This only identifies the app to Google — it does not
# grant Drive access by itself; every user still authenticates with their own Google login,
# and BionixDB still checks that account's actual Shared Drive membership/role afterward.
_BUNDLED_CREDENTIALS_FILE = Path(__file__).parent / "credentials.json"

BIONIX_DRIVE_ID = "0APjSE_uRDqsCUk9PVA"

MODALITY_FOLDERS = {
    "emg":   "1Sw_uLumdZ7xvpO3VHwfvgb60IEsWo5Oe",
    "imu":   "1FwzB_WrpIWwz8rOVqm_r5Z1V3ZvtFFy_",
    "cvkas": "1xdVfqltI70pgG8_yToHJb_QKbS9hXD4Z",
}

# ---------------------------------------------------------------------------
# BionixDB
# ---------------------------------------------------------------------------
class BionixDB:
    def __init__(self, credentials_file="credentials.json", token_file="token.json", force_reauth=False):
        self.creds = None
        self.service = None
        self.access = Access.NONE
        self.credentials_file = self._resolve_credentials_file(credentials_file)
        self.token_file = token_file

        try:
            self.authenticate_user(self.credentials_file, token_file, force_reauth)
        except HttpError as e:
            print(f"HTTP Error during authentication: {e}")
            raise PermissionError("Unauthorized Access to BionixDB")
        except PermissionError as e:
            print(f"Permission Error: {e}")
            raise

        if self.access == Access.NONE:
            error_msg = "You do not have access to the Alberta Bionix Shared Google Drive"
            print(f"Access Denied: {error_msg}")
            raise PermissionError(error_msg)


    def _resolve_credentials_file(self, credentials_file: str) -> str:
        # Prefer an explicitly-provided/local credentials.json if it exists; otherwise fall
        # back to the one bundled with the package so installs don't each need their own
        # Google Cloud OAuth client. If neither exists, return the original path unchanged
        # so the FileNotFoundError raised downstream points at the path the caller expected.
        if Path(credentials_file).exists():
            return credentials_file
        if _BUNDLED_CREDENTIALS_FILE.exists():
            return str(_BUNDLED_CREDENTIALS_FILE)
        return credentials_file

    def authenticate_user(self, credentials_file, token_file, force_reauth=False, require_content_manager=False):
        # Deleting the token before calling authenticate() forces a new browser login,
        # since authenticate() reuses a cached token if the file exists.
        if force_reauth:
            Path(token_file).unlink(missing_ok=True)

        creds_temp = authenticate(credentials_file, token_file)

        if not creds_temp or not creds_temp.valid:
            msg = "Invalid or missing credentials — authentication failed"
            print(f"Auth Error: {msg}")
            raise PermissionError(msg)

        service_temp = build_service(creds_temp)

        # Fetch the authenticated email for user-facing feedback only — failure is non-fatal.
        try:
            about = service_temp.about().get(fields="user(emailAddress)").execute()
            email = about.get("user", {}).get("emailAddress", "unknown")
        except Exception:
            email = "unknown"

        shared_drives = list_shared_drives(service_temp)

        for drive in shared_drives:
            if drive['id'] == BIONIX_DRIVE_ID:
                self.access = Access.CONTRIBUTOR
                self.creds = creds_temp
                self.service = service_temp

                if require_content_manager:
                    permissions = service_temp.permissions().list(
                        fileId=BIONIX_DRIVE_ID, supportsAllDrives=True,
                        fields="permissions(emailAddress,role)",
                    ).execute()
                    for perm in permissions.get('permissions', []):
                        if perm.get('emailAddress') == email and perm.get('role') in ['organizer', 'fileOrganizer']:
                            self.access = Access.CONTENT_MANAGER
                            print(f"Authenticated as {email} with CONTENT_MANAGER access")
                            return

                    self.access = Access.CONTRIBUTOR
                    print(f"Access denied for {email} — CONTENT_MANAGER access required")
                    raise PermissionError(
                        f"Authenticated as {email}, but CONTENT_MANAGER access is required and not granted."
                    )

                print(f"Authenticated as {email}")
                return

        # Wrong account — delete the token so the next authenticate() call opens a fresh
        # browser login instead of silently reusing these credentials.
        Path(token_file).unlink(missing_ok=True)
        print(f"Access denied for {email} — please sign in with your Bionix account.")

        raise PermissionError("Failed to authenticate with a valid Bionix account.")

    def get(self, modality: str, pid=None, action=None, trial=None, exercise=None) -> pd.DataFrame:
        self._validate_modality(modality)
        files = self._query_files(MODALITY_FOLDERS[modality], modality, pid=pid, action=action, trial=trial, exercise=exercise)
        return self._load_files(files)

    def get_emg(self, pid=None, action=None, trial=None, exercise=None) -> pd.DataFrame:
        return self.get("emg", pid=pid, action=action, trial=trial, exercise=exercise)

    def get_imu(self, pid=None, action=None, trial=None, exercise=None) -> pd.DataFrame:
        return self.get("imu", pid=pid, action=action, trial=trial, exercise=exercise)

    def get_cvkas(self, pid=None, action=None, trial=None, exercise=None) -> pd.DataFrame:
        return self.get("cvkas", pid=pid, action=action, trial=trial, exercise=exercise)

    def get_session(self, pid, action, trial=None, exercise=None) -> dict[str, pd.DataFrame]:
        """Return {modality: DataFrame} for whichever modalities have a file
        matching this pid/action/trial/exercise. Modalities with no matching file are
        simply omitted — a session need not include all three.
        """
        if trial is None:
            trial = self._latest_trial(pid, action, exercise=exercise)
            if trial is None:
                return {}

        results = {}
        for modality, folder_id in MODALITY_FOLDERS.items():
            files = self._query_files(folder_id, modality, pid=pid, action=action, trial=trial, exercise=exercise)
            if files:
                results[modality] = self._load_files(files)
        return results

    # FSRs, Knee encoder, Other sensor data?

    def _query_files(self, folder_id: str, modality: str, pid=None, action=None, trial=None, exercise=None) -> list[dict]:
        all_files = list_files_in_shared_drive(self.service, BIONIX_DRIVE_ID, folder_id)

        matches = []
        for f in all_files:
            name = f['name'].removesuffix('.csv')
            parts = name.split('-')
            if len(parts) not in (4, 5):
                continue
            if parts[0] != modality:
                continue

            file_pid = parts[1]
            file_action = parts[2]
            file_exercise = None
            file_trial = parts[-1]
            if len(parts) == 5:
                file_exercise = parts[3]

            if pid is not None and file_pid != self._format_pid(pid):
                continue
            if action is not None and file_action != ACTION_NAMES[action]:
                continue
            if exercise is not None:
                normalized = self._normalize_exercise(exercise)
                if file_exercise != normalized:
                    continue
            if trial is not None:
                trial_str = f"{trial:02d}" if isinstance(trial, int) else str(trial)
                if file_trial != trial_str:
                    continue
            matches.append(f)

        return matches

    def _validate_modality(self, modality: str) -> None:
        if modality not in MODALITY_FOLDERS:
            raise ValueError(f"Unknown modality '{modality}'. Expected one of: {sorted(MODALITY_FOLDERS)}")

    def _normalize_exercise(self, exercise) -> str | None:
        if exercise is None:
            return None
        return str(exercise).strip()

    def _format_pid(self, pid) -> str:
        # Accept int (1 -> "p001") or string ("p001") to match the zero-padded convention.
        return f"p{int(pid):03d}" if isinstance(pid, int) else str(pid)

    def _list_trials(self, modality: str, pid, action, exercise=None) -> list[int]:
        # Existing trial numbers for this modality/pid/action/exercise, sorted ascending.
        folder_id = MODALITY_FOLDERS[modality]
        existing = list_files_in_shared_drive(self.service, BIONIX_DRIVE_ID, folder_id)
        trials = []
        for f in existing:
            name = f["name"].removesuffix(".csv")
            parts = name.split('-')
            if len(parts) not in (4, 5):
                continue
            if parts[0] != modality:
                continue
            if parts[1] != self._format_pid(pid):
                continue
            if parts[2] != ACTION_NAMES[action]:
                continue
            file_exercise = parts[3] if len(parts) == 5 else None
            if exercise is not None:
                if file_exercise != self._normalize_exercise(exercise):
                    continue
            elif file_exercise is not None:
                # When no specific exercise is requested, include both legacy files and
                # phase-qualified files in the same action-level trial sequence.
                pass
            trial_str = parts[-1]
            if trial_str.isdigit():
                trials.append(int(trial_str))
        return sorted(set(trials))

    def _latest_trial(self, pid, action, exercise=None) -> int | None:
        # Highest trial number found across any modality for this pid/action/exercise, or None if none exist.
        latest = None
        for modality in MODALITY_FOLDERS:
            trials = self._list_trials(modality, pid, action, exercise=exercise)
            if trials:
                latest = trials[-1] if latest is None else max(latest, trials[-1])
        return latest

    def _load_files(self, files: list[dict]) -> pd.DataFrame:
        if not files:
            return pd.DataFrame()
        frames = [self.read_google_csv(f['id']) for f in files]
        return pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]

    def read_google_csv(self, file_id: str) -> pd.DataFrame:
        request = self.service.files().get_media(fileId=file_id, supportsAllDrives=True)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)

        done = False
        while not done:
            _, done = downloader.next_chunk()

        csv_text = buffer.getvalue().decode("utf-8")
        return pd.read_csv(io.StringIO(csv_text))

    def upload(self, modality: str, csv, pid, action, exercise=None) -> dict:
        self._validate_modality(modality)
        self._check_upload_access()
        csv_path = self._validate_csv_path(csv)

        # Trial number is the next one after the highest existing trial for this
        # modality/pid/action/exercise — the caller never has to track or pass it in.
        trials = self._list_trials(modality, pid, action, exercise=exercise)
        next_trial = (trials[-1] if trials else 0) + 1

        return self._create_dataset_file(modality, csv_path, pid, action, next_trial, exercise=exercise)

    def upload_emg(self, csv, pid, action, exercise=None) -> dict:
        return self.upload("emg", csv, pid, action, exercise=exercise)

    def upload_imu(self, csv, pid, action, exercise=None) -> dict:
        return self.upload("imu", csv, pid, action, exercise=exercise)

    def upload_cvkas(self, csv, pid, action, exercise=None) -> dict:
        return self.upload("cvkas", csv, pid, action, exercise=exercise)

    def upload_session(self, pid, action, emg=None, imu=None, cvkas=None, exercise=None) -> dict[str, dict]:
        """Upload one or more modalities recorded as a single trial, assigning them
        all the same trial number so they stay aligned. Modalities left as None are
        skipped — a session need not include all three.
        """
        provided = {m: c for m, c in {"emg": emg, "imu": imu, "cvkas": cvkas}.items() if c is not None}
        if not provided:
            raise ValueError("upload_session requires at least one of emg, imu, or cvkas")

        self._check_upload_access()
        csv_paths = {modality: self._validate_csv_path(csv) for modality, csv in provided.items()}

        # Shared trial number = next past the highest trial across ALL modality folders
        # for this pid/action/exercise, not just the ones being uploaded now — so a
        # session that only ever recorded EMG+IMU doesn't reuse a trial number CVKAS already took.
        latest = self._latest_trial(pid, action, exercise=exercise)
        next_trial = (latest or 0) + 1

        return {
            modality: self._create_dataset_file(modality, csv_path, pid, action, next_trial, exercise=exercise)
            for modality, csv_path in csv_paths.items()
        }

    def _check_upload_access(self) -> None:
        # Upload permission can change between sessions, so re-check it at the moment of
        # upload rather than trusting whatever access level was granted at login.
        self.authenticate_user(self.credentials_file, self.token_file, require_content_manager=True)
        if self.access != Access.CONTENT_MANAGER:
            raise PermissionError("Uploading datasets requires CONTENT_MANAGER access to the Alberta Bionix Shared Google Drive")

    def _validate_csv_path(self, csv) -> Path:
        csv_path = Path(csv)
        if not csv_path.exists():
            raise FileNotFoundError(f"CSV file not found: {csv}")
        if csv_path.suffix.lower() != ".csv":
            raise ValueError(f"Expected a .csv file, got: {csv_path.name}")
        return csv_path

    def _create_dataset_file(self, modality: str, csv_path: Path, pid, action, trial: int, exercise=None) -> dict:
        folder_id = MODALITY_FOLDERS[modality]
        action_name = ACTION_NAMES[action]
        if exercise is not None:
            filename = f"{modality}-{self._format_pid(pid)}-{action_name}-{self._normalize_exercise(exercise)}-{trial:02d}.csv"
        else:
            filename = f"{modality}-{self._format_pid(pid)}-{action_name}-{trial:02d}.csv"

        metadata = {"name": filename, "parents": [folder_id]}
        media = MediaFileUpload(str(csv_path), mimetype="text/csv", resumable=True)

        file = self.service.files().create(
            body=metadata,
            media_body=media,
            fields="id, name, webViewLink",
            supportsAllDrives=True,
        ).execute()

        print(f"Uploaded '{file['name']}' (id: {file['id']})")
        return file


# src/bionix_db/ -> src/ -> repo root (where credentials.json lives)
_REPO_ROOT = Path(__file__).parent.parent.parent

def main():
    import sys
    force_reauth = "--reauth" in sys.argv

    print("=== BIONIX DB TEST ===")
    bionixDB = BionixDB(
        credentials_file=str(_REPO_ROOT / "credentials.json"),
        token_file=str(_REPO_ROOT / "token.json"),
        force_reauth=force_reauth,
    )

    # CLI testing
    while True:
        print("\nSelect an option to test:")
        user_input = input(
            "1. Get EMG\n2. Get IMU\n3. Get CVKAS\n"
            "4. Upload EMG\n5. Upload IMU\n6. Upload CVKAS\n"
            "7. Upload Session\n8. Get Session\n"
            "0. Exit\n> "
        )

        match user_input:
            case "1":
                _cli_get(bionixDB, "emg")
            case "2":
                _cli_get(bionixDB, "imu")
            case "3":
                _cli_get(bionixDB, "cvkas")
            case "4":
                _cli_upload(bionixDB, "emg")
            case "5":
                _cli_upload(bionixDB, "imu")
            case "6":
                _cli_upload(bionixDB, "cvkas")
            case "7":
                _cli_upload_session(bionixDB)
            case "8":
                _cli_get_session(bionixDB)
            case "0":
                print("Bye!")
                break
            case _:
                print("Please select a valid option")


def _prompt_action() -> Action | None:
    options = list(Action)
    names = ", ".join(f"{a.value}={a.name}" for a in options)
    raw = input(f"Action [{names}] (leave blank to skip): ").strip()
    if not raw:
        return None
    try:
        return Action(int(raw))
    except (ValueError, KeyError):
        print(f"  Invalid action '{raw}' — skipping action filter.")
        return None


def _prompt_trial(label: str = "Trial number (leave blank to skip): ") -> int | None:
    raw = input(label).strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    print(f"  Invalid trial '{raw}' — skipping trial filter.")
    return None


def _cli_get(db: "BionixDB", modality: str) -> None:
    pid = input("PID (e.g. 1 or p001, leave blank to skip): ").strip() or None
    action = _prompt_action()
    trial = _prompt_trial()

    try:
        df = db.get(modality, pid=pid, action=action, trial=trial)
    except Exception as e:
        print(f"  ❌ {e}")
        return

    print(f"  Retrieved {len(df)} row(s).")
    if not df.empty:
        print(df.head())


def _cli_upload(db: "BionixDB", modality: str) -> None:
    csv_path = input("Local CSV path: ").strip()
    pid = input("PID (e.g. 1 or p001): ").strip()
    action = _prompt_action()
    if action is None:
        print("  ❌ Upload requires an action.")
        return

    try:
        db.upload(modality, csv_path, pid, action)
    except Exception as e:
        print(f"  ❌ {e}")


def _cli_upload_session(db: "BionixDB") -> None:
    pid = input("PID (e.g. 1 or p001): ").strip()
    action = _prompt_action()
    if action is None:
        print("  ❌ Upload requires an action.")
        return

    emg = input("EMG CSV path (leave blank to skip): ").strip() or None
    imu = input("IMU CSV path (leave blank to skip): ").strip() or None
    cvkas = input("CVKAS CSV path (leave blank to skip): ").strip() or None

    try:
        results = db.upload_session(pid, action, emg=emg, imu=imu, cvkas=cvkas)
    except Exception as e:
        print(f"  ❌ {e}")
        return

    for modality, file in results.items():
        print(f"  {modality}: uploaded as '{file['name']}'")


def _cli_get_session(db: "BionixDB") -> None:
    pid = input("PID (e.g. 1 or p001): ").strip()
    action = _prompt_action()
    if action is None:
        print("  ❌ Session lookup requires an action.")
        return
    trial = _prompt_trial("Trial number (leave blank for latest): ")

    try:
        session = db.get_session(pid, action, trial=trial)
    except Exception as e:
        print(f"  ❌ {e}")
        return

    if not session:
        print("  No matching session found.")
        return

    for modality, df in session.items():
        print(f"  {modality}: {len(df)} row(s)")


if __name__ == "__main__":
    main()
