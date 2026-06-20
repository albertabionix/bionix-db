"""
Example usage of the BionixDB library.

Run from anywhere:
    python example.py

No credentials.json is required.
BionixDB falls back to the OAuth client bundled with the package.
If you'd rather use your own Google Cloud OAuth client, place a credentials.json next to 
this script (or pass credentials_file= explicitly) and it'll take priority.
"""

from bionix_db.bionixdb import BionixDB, Action

def main():
    db = BionixDB()
    
    #----------------------------------------------------------------------#
    # Downloading Data - requires access to the shared drive 
    #----------------------------------------------------------------------#
    # Pull every EMG file on the drive into a single pandas DataFrame
    emg_all = db.get_emg()
    print(f"EMG rows (all): {len(emg_all)}")

    # Filter by participant, action, and/or trial number
    # if pid is omitted, it pulls all participants for that action/trial. Same for action/trial.
    emg_p001_walking = db.get_emg(pid=1, action=Action.WALKING)
    print(f"EMG rows (p001, walking): {len(emg_p001_walking)}")

    imu_trial_one = db.get_imu(pid="p002", action=Action.WALKING, trial=1)
    print(f"IMU rows (p002, walking, trial 1): {len(imu_trial_one)}")

    cvkas_data = db.get_cvkas(action=Action.SITTOSTAND)
    print(f"CVKAS rows (sit-to-stand): {len(cvkas_data)}")


    # get_session pulls all modalities recorded for one trial at once. 
    # Modalities that weren't recorded for that trial (e.g. no cvkas that day) are omitted
    # if pid or action is omitted, it pulls all sessions matching the other parameter (e.g. all walking sessions across participants)
    # if trial is omitted, it pulls the latest trial for that pid/action.
    session = db.get_session(pid=1, action=Action.WALKING) 
    for modality, df in session.items():
        print(f"Session {modality} rows: {len(df)}")

    # ---------------------------------------------------------------------
    # Uploading data — requires CONTENT_MANAGER access on the shared drive
    # ---------------------------------------------------------------------
    try:
        db.upload_emg("local_emg_recording.csv", pid=1, action=Action.WALKING)
        db.upload_imu("local_imu_recording.csv", pid=1, action=Action.WALKING)
        db.upload_cvkas("local_cvkas_recording.csv", pid=1, action=Action.WALKING)
    except PermissionError as e: # Raised if the signed-in account isn't a CONTENT_MANAGER on the shared drive.
        print(f"Upload skipped: {e}")
    except FileNotFoundError as e: # Raised if the local CSV path doesn't exist.
        print(f"Upload skipped: {e}")

    # Prefer upload_session when recording multiple modalities for the same physical trial 
    # if a modality is missing, omit the parameter
    try:
        db.upload_session(
            pid=1, action=Action.WALKING, # trial is optional; if omitted, it'll assign the next sequential trial number for that pid/action
            emg="local_emg_recording.csv",
            imu="local_imu_recording.csv",
            # cvkas omitted — not every trial needs all three modalities.
        )
    except PermissionError as e: # Raised if the signed-in account isn't a CONTENT_MANAGER on the shared drive
        print(f"Upload skipped: {e}")
    except FileNotFoundError as e: # Raised if any of the local CSV paths don't exist
        print(f"Upload skipped: {e}")


if __name__ == "__main__":
    main()
