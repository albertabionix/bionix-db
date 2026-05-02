import pandas as pd
from enum import Enum
from pathlib import Path

import io

from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.errors import HttpError

from .oauth import authenticate, build_service, list_shared_drives, list_files_in_shared_drive

# File Naming: pid-task-yyyymmdd.csv?
# Example: p001-walking-20260321
# Example: p001-sitstand-20260321

# File Structure:
# Google Drive
# |---- Datasets
# |----|---- cvkas
# |----|---- emg
# |----|----|---- emg-p001-walking-20260321
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

BIONIX_DRIVE_ID = "0APjSE_uRDqsCUk9PVA"
EMG_FOLDER_ID = '1Sw_uLumdZ7xvpO3VHwfvgb60IEsWo5Oe'
IMU_FOLDER_ID = '1FwzB_WrpIWwz8rOVqm_r5Z1V3ZvtFFy_'
CVKAS_FOLDER_ID = '1xdVfqltI70pgG8_yToHJb_QKbS9hXD4Z'

# ---------------------------------------------------------------------------
# BionixDB
# ---------------------------------------------------------------------------
class BionixDB:
    def __init__(self, credentials_file="credentials.json", token_file="token.json", force_reauth=False):
        self.creds = None
        self.service = None
        self.access = Access.NONE

        try:
            self.authenticate_user(credentials_file, token_file, force_reauth)
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


    def authenticate_user(self, credentials_file, token_file, force_reauth=False):
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
                print(f"Authenticated as {email}")
                return

        # Wrong account — delete the token so the next authenticate() call opens a fresh
        # browser login instead of silently reusing these credentials.
        Path(token_file).unlink(missing_ok=True)
        print(f"Access denied for {email} — please sign in with your Bionix account.")

        raise PermissionError("Failed to authenticate with a valid Bionix account.")

    def get_emg(self, pid=None, action=None, date=None) -> pd.DataFrame:
        files = self._query_files(EMG_FOLDER_ID, "emg", pid=pid, action=action, date=date)
        return self._load_files(files)

    def get_imu(self, pid=None, action=None, date=None) -> pd.DataFrame:
        files = self._query_files(IMU_FOLDER_ID, "imu", pid=pid, action=action, date=date)
        return self._load_files(files)

    def get_cvkas(self, pid=None, action=None, date=None) -> pd.DataFrame:
        files = self._query_files(CVKAS_FOLDER_ID, "cvkas", pid=pid, action=action, date=date)
        return self._load_files(files)

    def _query_files(self, folder_id: str, modality: str, pid=None, action=None, date=None) -> list[dict]:
        all_files = list_files_in_shared_drive(self.service, BIONIX_DRIVE_ID, folder_id)

        matches = []
        for f in all_files:
            # Expected filename format: {modality}-{pid}-{action}-{date}
            # Any file that doesn't match exactly 4 parts is not a dataset file.
            parts = f['name'].removesuffix('.csv').split('-')
            if len(parts) != 4 or parts[0] != modality:
                continue
            if pid is not None:
                # Accept int (1 -> "p001") or string ("p001") to match the zero-padded convention.
                pid_str = f"p{int(pid):03d}" if isinstance(pid, int) else str(pid)
                if parts[1] != pid_str:
                    continue
            if action is not None and parts[2] != ACTION_NAMES[action]:
                continue
            if date is not None:
                date_str = date if isinstance(date, str) else date.strftime("%Y%m%d")
                if parts[3] != date_str:
                    continue
            matches.append(f)

        return matches

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

    def upload_emg(self, pid, action):
        pass

    def upload_imu(self, pid, action):
        pass

    def upload_cvkas(self, pid, action):
        pass


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
        print("Select an option to test:")
        user_input = input("1. Get EMG\n2. Get IMU\n3. Get CVKAS\n> ")

        match user_input:
            case "1":
                pass
            case "2":
                pass
            case "3":
                pass
            case _:
                print("Please select a valid option")


if __name__ == "__main__":
    main()
