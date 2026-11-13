from unittest.mock import MagicMock

import pandas as pd
import pytest

from bionix_db import bionixdb
from bionix_db.bionixdb import Access, Action, BionixDB


def make_db(access=Access.CONTENT_MANAGER) -> BionixDB:
    """Build a BionixDB without running the real OAuth flow."""
    db = BionixDB.__new__(BionixDB)
    db.creds = None
    db.service = MagicMock()
    db.access = access
    db.credentials_file = "credentials.json"
    db.token_file = "token.json"
    # Upload re-authenticates before checking access; stub it so the access
    # level set above in each test is what gets checked.
    db.authenticate_user = MagicMock()
    return db


# ---------------------------------------------------------------------------
# _resolve_credentials_file
# ---------------------------------------------------------------------------

def test_resolve_credentials_file_prefers_existing_explicit_path(monkeypatch, tmp_path):
    explicit = tmp_path / "credentials.json"
    explicit.write_text("{}")
    bundled = tmp_path / "bundled-credentials.json"
    bundled.write_text("{}")
    monkeypatch.setattr(bionixdb, "_BUNDLED_CREDENTIALS_FILE", bundled)

    db = make_db()
    assert db._resolve_credentials_file(str(explicit)) == str(explicit)


def test_resolve_credentials_file_falls_back_to_bundled(monkeypatch, tmp_path):
    bundled = tmp_path / "bundled-credentials.json"
    bundled.write_text("{}")
    monkeypatch.setattr(bionixdb, "_BUNDLED_CREDENTIALS_FILE", bundled)

    db = make_db()
    missing_path = str(tmp_path / "does-not-exist.json")
    assert db._resolve_credentials_file(missing_path) == str(bundled)


def test_resolve_credentials_file_returns_original_when_neither_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(bionixdb, "_BUNDLED_CREDENTIALS_FILE", tmp_path / "no-bundle-here.json")

    db = make_db()
    missing_path = str(tmp_path / "does-not-exist.json")
    assert db._resolve_credentials_file(missing_path) == missing_path


# ---------------------------------------------------------------------------
# _validate_modality
# ---------------------------------------------------------------------------

def test_validate_modality_accepts_known_modalities():
    db = make_db()
    for modality in bionixdb.MODALITY_FOLDERS:
        db._validate_modality(modality)  # should not raise


def test_validate_modality_rejects_unknown_modality():
    db = make_db()
    with pytest.raises(ValueError):
        db._validate_modality("ecg")


def test_get_rejects_unknown_modality_before_querying():
    db = make_db()
    with pytest.raises(ValueError):
        db.get("ecg")
    db.service.files.assert_not_called()


# ---------------------------------------------------------------------------
# _query_files
# ---------------------------------------------------------------------------

@pytest.fixture
def emg_files():
    return [
        {"id": "1", "name": "emg-p001-walking-01.csv"},
        {"id": "2", "name": "emg-p001-walking-02.csv"},
        {"id": "3", "name": "emg-p001-sitstand-01.csv"},
        {"id": "4", "name": "emg-p002-walking-01.csv"},
        {"id": "5", "name": "not-a-dataset-file.csv"},
        {"id": "6", "name": "imu-p001-walking-01.csv"},  # wrong modality, shouldn't match emg query
    ]


def test_query_files_filters_by_modality_and_ignores_malformed_names(monkeypatch, emg_files):
    monkeypatch.setattr(bionixdb, "list_files_in_shared_drive", lambda *a, **k: emg_files)
    db = make_db()

    matches = db._query_files("folder-id", "emg")
    names = {f["name"] for f in matches}
    assert names == {
        "emg-p001-walking-01.csv",
        "emg-p001-walking-02.csv",
        "emg-p001-sitstand-01.csv",
        "emg-p002-walking-01.csv",
    }


def test_query_files_filters_by_pid(monkeypatch, emg_files):
    monkeypatch.setattr(bionixdb, "list_files_in_shared_drive", lambda *a, **k: emg_files)
    db = make_db()

    matches = db._query_files("folder-id", "emg", pid=1)
    assert [f["id"] for f in matches] == ["1", "2", "3"]

    matches = db._query_files("folder-id", "emg", pid="p002")
    assert [f["id"] for f in matches] == ["4"]


def test_query_files_filters_by_action_and_trial(monkeypatch, emg_files):
    monkeypatch.setattr(bionixdb, "list_files_in_shared_drive", lambda *a, **k: emg_files)
    db = make_db()

    matches = db._query_files("folder-id", "emg", action=Action.SITTOSTAND)
    assert [f["id"] for f in matches] == ["3"]

    matches = db._query_files("folder-id", "emg", action=Action.WALKING, trial=2)
    assert [f["id"] for f in matches] == ["2"]


def test_query_files_filters_by_exercise_and_preserves_legacy_names(monkeypatch):
    files = [
        {"id": "1", "name": "emg-p001-walking-r_leg-01.csv"},
        {"id": "2", "name": "emg-p001-walking-l_leg-01.csv"},
        {"id": "3", "name": "emg-p001-sitstand-r_leg-01.csv"},
        {"id": "4", "name": "emg-p001-walking-01.csv"},
    ]
    monkeypatch.setattr(bionixdb, "list_files_in_shared_drive", lambda *a, **k: files)
    db = make_db()

    matches = db._query_files("folder-id", "emg", action=Action.WALKING, exercise="r_leg")
    assert [f["id"] for f in matches] == ["1"]

    matches = db._query_files("folder-id", "emg", action=Action.WALKING, exercise=None)
    assert {f["id"] for f in matches} == {"1", "2", "4"}


# ---------------------------------------------------------------------------
# authenticate_user
# ---------------------------------------------------------------------------

def make_unauthenticated_db() -> BionixDB:
    """Build a BionixDB with a real (unstubbed) authenticate_user, for testing it directly."""
    db = BionixDB.__new__(BionixDB)
    db.creds = None
    db.service = None
    db.access = Access.NONE
    db.credentials_file = "credentials.json"
    db.token_file = "token.json"
    return db


def stub_oauth(monkeypatch, *, email, permissions):
    fake_creds = MagicMock(valid=True)
    monkeypatch.setattr(bionixdb, "authenticate", lambda *a, **k: fake_creds)
    monkeypatch.setattr(bionixdb, "list_shared_drives", lambda service: [{"id": bionixdb.BIONIX_DRIVE_ID, "name": "Alberta Bionix"}])

    fake_service = MagicMock()
    fake_service.about.return_value.get.return_value.execute.return_value = {
        "user": {"emailAddress": email}
    }
    fake_service.permissions.return_value.list.return_value.execute.return_value = {
        "permissions": permissions
    }
    monkeypatch.setattr(bionixdb, "build_service", lambda creds: fake_service)
    return fake_service


def test_authenticate_user_grants_content_manager_when_role_matches(monkeypatch):
    fake_service = stub_oauth(
        monkeypatch,
        email="ciquinto@ualberta.ca",
        permissions=[{"emailAddress": "ciquinto@ualberta.ca", "role": "fileOrganizer"}],
    )

    db = make_unauthenticated_db()
    db.authenticate_user(db.credentials_file, db.token_file, require_content_manager=True)

    assert db.access == Access.CONTENT_MANAGER

    # Regression guard: the permissions().list() call must request emailAddress/role
    # explicitly. Drive's default partial response omits emailAddress entirely, which
    # silently broke the role match above for every account, not just unmatched ones.
    list_kwargs = fake_service.permissions.return_value.list.call_args.kwargs
    assert "emailAddress" in list_kwargs.get("fields", "")


def test_authenticate_user_rejects_contributor_when_content_manager_required(monkeypatch):
    stub_oauth(
        monkeypatch,
        email="ciquinto@ualberta.ca",
        permissions=[{"emailAddress": "ciquinto@ualberta.ca", "role": "writer"}],
    )

    db = make_unauthenticated_db()
    with pytest.raises(PermissionError):
        db.authenticate_user(db.credentials_file, db.token_file, require_content_manager=True)

    assert db.access == Access.CONTRIBUTOR  # access level is still recorded, just not sufficient


def test_authenticate_user_rejects_non_member_account(monkeypatch, tmp_path):
    monkeypatch.setattr(bionixdb, "authenticate", lambda *a, **k: MagicMock(valid=True))
    monkeypatch.setattr(bionixdb, "build_service", lambda creds: MagicMock())
    monkeypatch.setattr(bionixdb, "list_shared_drives", lambda service: [])

    token_file = tmp_path / "token.json"
    token_file.write_text("{}")

    db = make_unauthenticated_db()
    db.token_file = str(token_file)

    with pytest.raises(PermissionError):
        db.authenticate_user(db.credentials_file, db.token_file, require_content_manager=True)

    assert not token_file.exists()  # wrong-account token must be deleted, not reused


# ---------------------------------------------------------------------------
# upload() access gating and trial-number assignment
# ---------------------------------------------------------------------------

def test_upload_denied_without_content_manager_access(tmp_path):
    csv_path = tmp_path / "local_recording.csv"
    csv_path.write_text("a,b\n1,2\n")

    db = make_db(access=Access.CONTRIBUTOR)

    with pytest.raises(PermissionError):
        db.upload("emg", str(csv_path), pid=1, action=Action.WALKING)

    db.service.files().create.assert_not_called()


def test_upload_rejects_missing_file():
    db = make_db()
    with pytest.raises(FileNotFoundError):
        db.upload("emg", "/nonexistent/path/recording.csv", pid=1, action=Action.WALKING)


def test_upload_rejects_non_csv(tmp_path):
    bad_file = tmp_path / "recording.txt"
    bad_file.write_text("not a csv")

    db = make_db()
    with pytest.raises(ValueError):
        db.upload("emg", str(bad_file), pid=1, action=Action.WALKING)


def test_upload_assigns_first_trial_when_none_exist(monkeypatch, tmp_path):
    csv_path = tmp_path / "local_recording.csv"
    csv_path.write_text("a,b\n1,2\n")

    monkeypatch.setattr(bionixdb, "list_files_in_shared_drive", lambda *a, **k: [])
    monkeypatch.setattr(bionixdb, "MediaFileUpload", MagicMock())

    db = make_db(access=Access.CONTENT_MANAGER)
    db.service.files.return_value.create.return_value.execute.return_value = {
        "id": "new-id", "name": "emg-p001-walking-01.csv", "webViewLink": "http://example.com",
    }

    result = db.upload("emg", str(csv_path), pid=1, action=Action.WALKING)

    assert result["name"] == "emg-p001-walking-01.csv"
    create_kwargs = db.service.files().create.call_args.kwargs
    assert create_kwargs["body"]["name"] == "emg-p001-walking-01.csv"


def test_upload_includes_exercise_in_filename(monkeypatch, tmp_path):
    csv_path = tmp_path / "local_recording.csv"
    csv_path.write_text("a,b\n1,2\n")

    monkeypatch.setattr(bionixdb, "list_files_in_shared_drive", lambda *a, **k: [])
    monkeypatch.setattr(bionixdb, "MediaFileUpload", MagicMock())

    db = make_db(access=Access.CONTENT_MANAGER)
    db.service.files.return_value.create.return_value.execute.return_value = {
        "id": "new-id", "name": "emg-p001-walking-r_leg-01.csv", "webViewLink": "http://example.com",
    }

    result = db.upload("emg", str(csv_path), pid=1, action=Action.WALKING, exercise="r_leg")

    assert result["name"] == "emg-p001-walking-r_leg-01.csv"
    create_kwargs = db.service.files().create.call_args.kwargs
    assert create_kwargs["body"]["name"] == "emg-p001-walking-r_leg-01.csv"


def test_upload_increments_trial_past_existing(monkeypatch, tmp_path):
    csv_path = tmp_path / "local_recording.csv"
    csv_path.write_text("a,b\n1,2\n")

    existing = [
        {"id": "1", "name": "emg-p001-walking-01.csv"},
        {"id": "2", "name": "emg-p001-walking-02.csv"},
        {"id": "3", "name": "emg-p001-sitstand-01.csv"},  # different action, shouldn't affect numbering
    ]
    monkeypatch.setattr(bionixdb, "list_files_in_shared_drive", lambda *a, **k: existing)
    monkeypatch.setattr(bionixdb, "MediaFileUpload", MagicMock())

    db = make_db(access=Access.CONTENT_MANAGER)
    db.service.files.return_value.create.return_value.execute.return_value = {
        "id": "new-id", "name": "emg-p001-walking-03.csv", "webViewLink": "http://example.com",
    }

    db.upload("emg", str(csv_path), pid=1, action=Action.WALKING)

    create_kwargs = db.service.files().create.call_args.kwargs
    assert create_kwargs["body"]["name"] == "emg-p001-walking-03.csv"
    assert create_kwargs["body"]["parents"] == [bionixdb.MODALITY_FOLDERS["emg"]]


def test_upload_emg_imu_cvkas_wrappers_pass_through(monkeypatch, tmp_path):
    csv_path = tmp_path / "local_recording.csv"
    csv_path.write_text("a,b\n1,2\n")

    monkeypatch.setattr(bionixdb, "list_files_in_shared_drive", lambda *a, **k: [])
    monkeypatch.setattr(bionixdb, "MediaFileUpload", MagicMock())

    db = make_db(access=Access.CONTENT_MANAGER)
    db.service.files.return_value.create.return_value.execute.return_value = {
        "id": "new-id", "name": "imu-p001-walking-01.csv", "webViewLink": "http://example.com",
    }

    result = db.upload_imu(str(csv_path), pid=1, action=Action.WALKING)
    assert result["name"] == "imu-p001-walking-01.csv"


# ---------------------------------------------------------------------------
# upload_session
# ---------------------------------------------------------------------------

def by_folder(folder_files: dict) -> "callable":
    def fake(service, drive_id, folder_id):
        return folder_files.get(folder_id, [])
    return fake


def test_upload_session_requires_at_least_one_modality():
    db = make_db()
    with pytest.raises(ValueError):
        db.upload_session(pid=1, action=Action.WALKING)


def test_upload_session_denied_without_content_manager_access(tmp_path):
    csv_path = tmp_path / "local_recording.csv"
    csv_path.write_text("a,b\n1,2\n")

    db = make_db(access=Access.CONTRIBUTOR)
    with pytest.raises(PermissionError):
        db.upload_session(pid=1, action=Action.WALKING, emg=str(csv_path))


def test_upload_session_aligns_trial_across_modalities(monkeypatch, tmp_path):
    emg_csv = tmp_path / "emg_recording.csv"
    imu_csv = tmp_path / "imu_recording.csv"
    emg_csv.write_text("a,b\n1,2\n")
    imu_csv.write_text("a,b\n1,2\n")

    folder_files = {
        bionixdb.MODALITY_FOLDERS["emg"]: [
            {"id": "1", "name": "emg-p001-walking-01.csv"},
            {"id": "2", "name": "emg-p001-walking-02.csv"},
        ],
        bionixdb.MODALITY_FOLDERS["imu"]: [],
        bionixdb.MODALITY_FOLDERS["cvkas"]: [
            {"id": "3", "name": "cvkas-p001-walking-01.csv"},
        ],
    }
    monkeypatch.setattr(bionixdb, "list_files_in_shared_drive", by_folder(folder_files))
    monkeypatch.setattr(bionixdb, "MediaFileUpload", MagicMock())

    db = make_db(access=Access.CONTENT_MANAGER)
    created_names = []

    def fake_create(**kwargs):
        name = kwargs["body"]["name"]
        created_names.append(name)
        mock = MagicMock()
        mock.execute.return_value = {"id": "new-id", "name": name, "webViewLink": "http://example.com"}
        return mock

    db.service.files.return_value.create.side_effect = fake_create

    # Only uploading emg+imu, but cvkas already has trial 1 and emg already has trial 2 —
    # the shared trial number should be 3, past the max across ALL modalities, not just these two.
    results = db.upload_session(pid=1, action=Action.WALKING, emg=str(emg_csv), imu=str(imu_csv))

    assert results["emg"]["name"] == "emg-p001-walking-03.csv"
    assert results["imu"]["name"] == "imu-p001-walking-03.csv"
    assert "cvkas" not in results
    assert set(created_names) == {"emg-p001-walking-03.csv", "imu-p001-walking-03.csv"}


# ---------------------------------------------------------------------------
# get_session
# ---------------------------------------------------------------------------

def test_get_session_returns_only_matching_modalities(monkeypatch):
    folder_files = {
        bionixdb.MODALITY_FOLDERS["emg"]: [{"id": "1", "name": "emg-p001-walking-01.csv"}],
        bionixdb.MODALITY_FOLDERS["imu"]: [{"id": "2", "name": "imu-p001-walking-01.csv"}],
        bionixdb.MODALITY_FOLDERS["cvkas"]: [],  # cvkas wasn't recorded for this trial
    }
    monkeypatch.setattr(bionixdb, "list_files_in_shared_drive", by_folder(folder_files))

    db = make_db()
    db.read_google_csv = MagicMock(side_effect=lambda file_id: pd.DataFrame({"id": [file_id]}))

    session = db.get_session(pid=1, action=Action.WALKING, trial=1)

    assert set(session.keys()) == {"emg", "imu"}
    assert session["emg"]["id"].iloc[0] == "1"
    assert session["imu"]["id"].iloc[0] == "2"


def test_get_session_with_no_trial_uses_latest_across_modalities(monkeypatch):
    folder_files = {
        bionixdb.MODALITY_FOLDERS["emg"]: [
            {"id": "1", "name": "emg-p001-walking-01.csv"},
            {"id": "2", "name": "emg-p001-walking-02.csv"},
        ],
        bionixdb.MODALITY_FOLDERS["imu"]: [],
        bionixdb.MODALITY_FOLDERS["cvkas"]: [
            {"id": "3", "name": "cvkas-p001-walking-03.csv"},
        ],
    }
    monkeypatch.setattr(bionixdb, "list_files_in_shared_drive", by_folder(folder_files))

    db = make_db()
    db.read_google_csv = MagicMock(side_effect=lambda file_id: pd.DataFrame({"id": [file_id]}))

    # Latest trial overall is 3 (from cvkas), even though emg only goes up to 2.
    session = db.get_session(pid=1, action=Action.WALKING)

    assert set(session.keys()) == {"cvkas"}
    assert session["cvkas"]["id"].iloc[0] == "3"


def test_get_session_returns_empty_dict_when_nothing_exists(monkeypatch):
    monkeypatch.setattr(bionixdb, "list_files_in_shared_drive", lambda *a, **k: [])
    db = make_db()

    assert db.get_session(pid=1, action=Action.WALKING) == {}


# ---------------------------------------------------------------------------
# Reading: metadata columns, ordering, dtypes, downloads
# ---------------------------------------------------------------------------
# Snippets shaped like real EMG-IMU recordings.

EMG_CSV = {
    "e1": b"timestamp,emg1,emg2\n1788727215.6523619,1331,1316\n1788727215.6553597,1345,1328\n",
    "e2": b"timestamp,emg1,emg2\n1788727300.1000000,1300,1290\n",
}
CVKAS_CSV = (
    b"timestamp,marker_id,pos_x,pos_y,velocity,acceleration,hip_angle,knee_angle\n"
    b"1788727215.6523619,3,93.75,160.5,109.9,137.6,,\n"
    b"1788727215.6523619,2,119.0,110.25,114.8,507.7,,\n"
    b"1788727215.9173632,,,,,,,\n"
)


def stub_downloads(db, contents):
    db._download_bytes = MagicMock(side_effect=lambda file_id: contents[file_id])


def test_get_adds_metadata_columns_and_keeps_trials_separable(monkeypatch):
    files = [  # Drive lists newest first; result must still be ordered by trial
        {"id": "e2", "name": "emg-p020-stairs-stairmaster-04.csv"},
        {"id": "e1", "name": "emg-p020-stairs-stairmaster-03.csv"},
    ]
    monkeypatch.setattr(bionixdb, "list_files_in_shared_drive", lambda *a, **k: files)
    db = make_db()
    stub_downloads(db, EMG_CSV)

    df = db.get_emg(pid=20, action=Action.STAIRS)

    assert list(df.columns) == ["pid", "action", "exercise", "trial", "file", "timestamp", "emg1", "emg2"]
    assert df["trial"].tolist() == [3, 3, 4]
    assert df["file"].iloc[0] == "emg-p020-stairs-stairmaster-03.csv"
    assert set(df["pid"]) == {"p020"} and set(df["exercise"]) == {"stairmaster"}
    assert {t: len(g) for t, g in df.groupby("trial")} == {3: 2, 4: 1}
    # recorded values and full timestamp precision are untouched
    assert df["timestamp"].iloc[0] == 1788727215.6523619
    assert df["emg1"].tolist() == [1331, 1345, 1300]


def test_get_legacy_file_without_exercise_has_none_exercise(monkeypatch):
    files = [{"id": "e1", "name": "emg-p001-walking-01.csv"}]
    monkeypatch.setattr(bionixdb, "list_files_in_shared_drive", lambda *a, **k: files)
    db = make_db()
    stub_downloads(db, EMG_CSV)

    df = db.get_emg()
    assert df["exercise"].isna().all()
    assert df["trial"].tolist() == [1, 1]


def test_get_cvkas_reads_marker_id_as_nullable_int(monkeypatch):
    files = [{"id": "c1", "name": "cvkas-p020-stairs-stairmaster-03.csv"}]
    monkeypatch.setattr(bionixdb, "list_files_in_shared_drive", lambda *a, **k: files)
    db = make_db()
    stub_downloads(db, {"c1": CVKAS_CSV})

    df = db.get_cvkas()
    assert str(df["marker_id"].dtype) == "Int64"
    assert df["marker_id"].tolist()[:2] == [3, 2]
    assert pd.isna(df["marker_id"].iloc[2])  # frame with no detected markers


def test_get_returns_empty_dataframe_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(bionixdb, "list_files_in_shared_drive", lambda *a, **k: [])
    assert make_db().get_imu(pid=1).empty


def test_download_saves_raw_files_unmodified(monkeypatch, tmp_path):
    files = [
        {"id": "e2", "name": "emg-p020-stairs-stairmaster-04.csv"},
        {"id": "e1", "name": "emg-p020-stairs-stairmaster-03.csv"},
    ]
    monkeypatch.setattr(bionixdb, "list_files_in_shared_drive", lambda *a, **k: files)
    db = make_db()
    stub_downloads(db, EMG_CSV)

    paths = db.download_emg(tmp_path / "out", pid="p020")

    assert [p.name for p in paths] == ["emg-p020-stairs-stairmaster-03.csv", "emg-p020-stairs-stairmaster-04.csv"]
    assert paths[0].read_bytes() == EMG_CSV["e1"]


def test_action_accepts_filename_token_and_rejects_unknown(monkeypatch, emg_files):
    monkeypatch.setattr(bionixdb, "list_files_in_shared_drive", lambda *a, **k: emg_files)
    db = make_db()

    assert [f["id"] for f in db._query_files("folder-id", "emg", action="sitstand")] == ["3"]
    with pytest.raises(ValueError):
        db._query_files("folder-id", "emg", action="running")


# ---------------------------------------------------------------------------
# Exercise names containing '-'
# ---------------------------------------------------------------------------

def test_upload_replaces_dash_in_exercise_so_file_stays_queryable(monkeypatch, tmp_path):
    csv = tmp_path / "rec.csv"
    csv.write_text("timestamp,emg1,emg2\n")
    monkeypatch.setattr(bionixdb, "list_files_in_shared_drive", lambda *a, **k: [])
    db = make_db()
    db.service.files.return_value.create.return_value.execute.side_effect = (
        lambda: {"id": "x", "name": db.service.files.return_value.create.call_args.kwargs["body"]["name"]}
    )

    result = db.upload_emg(csv, pid=1, action=Action.WALKING, exercise="r-leg")
    assert result["name"] == "emg-p001-walking-r_leg-01.csv"


def test_query_with_dashed_exercise_matches_normalized_name(monkeypatch):
    files = [{"id": "1", "name": "emg-p001-walking-r_leg-01.csv"}]
    monkeypatch.setattr(bionixdb, "list_files_in_shared_drive", lambda *a, **k: files)
    assert [f["id"] for f in make_db()._query_files("folder-id", "emg", exercise="r-leg")] == ["1"]


def test_blank_exercise_is_treated_as_none(tmp_path, monkeypatch):
    csv = tmp_path / "rec.csv"
    csv.write_text("timestamp,emg1,emg2\n")
    monkeypatch.setattr(bionixdb, "list_files_in_shared_drive", lambda *a, **k: [])
    db = make_db()
    db.service.files.return_value.create.return_value.execute.side_effect = (
        lambda: {"id": "x", "name": db.service.files.return_value.create.call_args.kwargs["body"]["name"]}
    )

    assert db.upload_emg(csv, pid=1, action=Action.WALKING, exercise="  ")["name"] == "emg-p001-walking-01.csv"


# ---------------------------------------------------------------------------
# list_files_in_shared_drive pagination
# ---------------------------------------------------------------------------

def test_list_files_in_shared_drive_follows_all_pages():
    from bionix_db import oauth

    pages = [
        {"files": [{"id": "1"}, {"id": "2"}], "nextPageToken": "p2"},
        {"files": [{"id": "3"}], "nextPageToken": "p3"},
        {"files": [{"id": "4"}]},
    ]
    service = MagicMock()
    service.files.return_value.list.return_value.execute.side_effect = pages

    files = oauth.list_files_in_shared_drive(service, "drive", "folder")

    assert [f["id"] for f in files] == ["1", "2", "3", "4"]
    tokens = [c.kwargs["pageToken"] for c in service.files.return_value.list.call_args_list]
    assert tokens == [None, "p2", "p3"]
