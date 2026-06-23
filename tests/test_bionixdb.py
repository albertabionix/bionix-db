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
