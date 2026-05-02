# This program was made with the help of Claude
"""
Google Drive OAuth2 Access
--------------------------
Authenticates via OAuth 2.0 and provides utilities to interact
with a shared Google Drive (list, download, upload, search files).

Setup:
  1. pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client
  2. Create a project in Google Cloud Console (https://console.cloud.google.com)
  3. Enable the Google Drive API
  4. Create OAuth 2.0 credentials (Desktop App) and download as credentials.json
  5. Place credentials.json in the same directory as this script
  6. Run: python google_drive_oauth.py
"""

import os
import io
import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# If you modify these scopes, delete token.json and re-authenticate.
SCOPES = ["https://www.googleapis.com/auth/drive"] # Full Drive access

CREDENTIALS_FILE = "credentials.json"   # Downloaded from Google Cloud Console
TOKEN_FILE = "token.json"               # Created automatically after first login
DOWNLOAD_DIR = Path("downloads")        # Local folder for downloaded files


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

from google.auth.exceptions import OAuthError

def authenticate(credentials_file: str = CREDENTIALS_FILE, token_file: str = TOKEN_FILE) -> Credentials:
    creds = None

    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"❌ Token refresh failed: {e}")
                os.remove(token_file)
                creds = None

        if not creds:
            if not os.path.exists(credentials_file):
                raise FileNotFoundError(
                    f"'{credentials_file}' not found.\n"
                    "Download it from Google Cloud Console → APIs & Services → Credentials."
                )
            try:
                flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
                creds = flow.run_local_server(port=0)
            except OAuthError as e:
                error_str = str(e)
                print(f"❌ OAuth Error: {error_str}")
                if "org_internal" in error_str:
                    raise PermissionError(
                        "Access blocked: This app is restricted to users within the Bionix organization.\n"
                        "Please sign in with your Bionix Google account, not a personal Gmail."
                    )
                raise
            except Exception as e:
                print(f"❌ Unexpected error during authentication: {e}")
                raise

        with open(token_file, "w") as token:
            token.write(creds.to_json())
        print(f"✅ Token saved to '{token_file}'")

    return creds

# ---------------------------------------------------------------------------
# Drive helpers
# ---------------------------------------------------------------------------

def build_service(creds: Credentials):
    """Return an authenticated Drive API service object."""
    return build("drive", "v3", credentials=creds)


def list_files(service, folder_id: str = None, max_results: int = 20) -> list[dict]:
    """
    List files in My Drive or a specific folder.

    Args:
        service:     Authenticated Drive service.
        folder_id:   Google Drive folder ID to list (None = root / all files).
        max_results: Maximum number of files to return.

    Returns:
        List of file metadata dicts.
    """
    query = f"'{folder_id}' in parents and trashed=false" if folder_id else "trashed=false"

    results = (
        service.files()
        .list(
            q=query,
            pageSize=max_results,
            fields="nextPageToken, files(id, name, mimeType, size, modifiedTime, owners)",
            orderBy="modifiedTime desc",
        )
        .execute()
    )
    return results.get("files", [])


def list_shared_drives(service) -> list[dict]:
    """Return all Shared Drives the authenticated user can access."""
    results = service.drives().list(pageSize=50, fields="drives(id, name)").execute()
    return results.get("drives", [])


def list_files_in_shared_drive(service, drive_id: str, folder_id: str) -> list[dict]:
    """
    List files inside a specific folder in the Shared Drive.

    Args:
        service:     Authenticated Drive service.
        drive_id:    ID of the Shared Drive.
        folder_id:   ID of the folder.

    Returns:
        List of file metadata dicts.
    """
    results = (
        service.files()
        .list(
            q=f"'{folder_id}' in parents and trashed=false",
            corpora="drive",
            driveId=drive_id,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            fields="nextPageToken, files(id, name, mimeType, modifiedTime)",
            orderBy="modifiedTime desc",
        )
        .execute()
    )
    return results.get("files", [])


def search_files(service, query_term: str, drive_id: str = None) -> list[dict]:
    """
    Search for files by name across My Drive or a Shared Drive.

    Args:
        service:    Authenticated Drive service.
        query_term: Text to search for in file names.
        drive_id:   Limit search to this Shared Drive (None = My Drive).

    Returns:
        List of matching file metadata dicts.
    """
    q = f"name contains '{query_term}' and trashed=false"

    kwargs = dict(
        q=q,
        pageSize=20,
        fields="files(id, name, mimeType, size, modifiedTime)",
    )

    if drive_id:
        kwargs.update(
            corpora="drive",
            driveId=drive_id,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        )

    results = service.files().list(**kwargs).execute()
    return results.get("files", [])


def download_file(service, file_id: str, file_name: str) -> Path:
    """
    Download a binary file from Drive to the local DOWNLOAD_DIR.

    Args:
        service:   Authenticated Drive service.
        file_id:   ID of the file to download.
        file_name: Name to save the file as locally.

    Returns:
        Path to the downloaded file.
    """
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    dest = DOWNLOAD_DIR / file_name

    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)

    done = False
    while not done:
        status, done = downloader.next_chunk()
        print(f"  Downloading… {int(status.progress() * 100)}%")

    dest.write_bytes(buffer.getvalue())
    print(f"  ✅ Saved to '{dest}'")
    return dest


def export_google_doc(service, file_id: str, file_name: str, mime_type: str = "application/pdf") -> Path:
    """
    Export a Google Workspace document (Docs, Sheets, Slides) to a local file.

    Args:
        service:   Authenticated Drive service.
        file_id:   ID of the Google Doc / Sheet / Slide.
        file_name: Base name for the exported file.
        mime_type: Export format (default: PDF).

    Returns:
        Path to the exported file.
    """
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    ext = {"application/pdf": ".pdf", "text/plain": ".txt",
           "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx"}.get(mime_type, ".pdf")
    dest = DOWNLOAD_DIR / (file_name + ext)

    request = service.files().export_media(fileId=file_id, mimeType=mime_type)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)

    done = False
    while not done:
        status, done = downloader.next_chunk()
        print(f"  Exporting… {int(status.progress() * 100)}%")

    dest.write_bytes(buffer.getvalue())
    print(f"  ✅ Exported to '{dest}'")
    return dest


def upload_file(service, local_path: str, parent_folder_id: str = None, drive_id: str = None) -> dict:
    """
    Upload a local file to Drive (My Drive or a Shared Drive folder).

    Args:
        service:          Authenticated Drive service.
        local_path:       Path to the local file to upload.
        parent_folder_id: Drive folder ID to upload into (None = root).
        drive_id:         Shared Drive ID (None = My Drive).

    Returns:
        Metadata dict of the uploaded file.
    """
    path = Path(local_path)
    if not path.exists():
        raise FileNotFoundError(f"Local file not found: {local_path}")

    metadata = {"name": path.name}
    if parent_folder_id:
        metadata["parents"] = [parent_folder_id]

    media = MediaFileUpload(local_path, resumable=True)

    kwargs = dict(body=metadata, media_body=media, fields="id, name, webViewLink")
    if drive_id:
        kwargs["supportsAllDrives"] = True

    file = service.files().create(**kwargs).execute()
    print(f"  ✅ Uploaded '{file['name']}' (id: {file['id']})")
    print(f"     Link: {file.get('webViewLink', 'N/A')}")
    return file


# ---------------------------------------------------------------------------
# Pretty-print helpers
# ---------------------------------------------------------------------------

GOOGLE_MIME_LABELS = {
    "application/vnd.google-apps.folder":       "📁 Folder",
    "application/vnd.google-apps.document":     "📄 Google Doc",
    "application/vnd.google-apps.spreadsheet":  "📊 Google Sheet",
    "application/vnd.google-apps.presentation": "📊 Google Slides",
    "application/vnd.google-apps.form":         "📝 Google Form",
    "application/pdf":                          "📕 PDF",
}


def fmt_size(size) -> str:
    if size is None:
        return "—"
    size = int(size)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def print_files(files: list[dict]) -> None:
    if not files:
        print("  (no files found)")
        return
    for f in files:
        label = GOOGLE_MIME_LABELS.get(f["mimeType"], f["mimeType"].split("/")[-1])
        size  = fmt_size(f.get("size"))
        mod   = f.get("modifiedTime", "")[:10]
        print(f"  [{label:<22}] {f['name']:<45} size={size:<10} modified={mod}  id={f['id']}")


# ---------------------------------------------------------------------------
# Interactive demo / main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  Google Drive OAuth2 Access Tool")
    print("=" * 60)

    # 1. Authenticate
    print("\n🔐 Authenticating…")
    creds   = authenticate()
    service = build_service(creds)
    print("✅ Authenticated successfully.\n")

    # 2. List Shared Drives
    print("📂 Shared Drives you have access to:")
    shared_drives = list_shared_drives(service)
    if shared_drives:
        for d in shared_drives:
            print(f"  • {d['name']}  (id: {d['id']})")
    else:
        print("  (none — listing My Drive instead)")

    # 3. List recent files from the first Shared Drive (or My Drive)
    print("\n📋 Recent files:")
    if shared_drives:
        drive_id = shared_drives[0]["id"]
        print(f"  (from Shared Drive: '{shared_drives[0]['name']}')")
        files = list_files_in_shared_drive(service, drive_id)
    else:
        drive_id = None
        files = list_files(service)

    print_files(files)

    # 4. Optional: interactive menu
    while True:
        print("\n" + "-" * 40)
        print("What would you like to do?")
        print("  1. Search files")
        print("  2. Download a file by ID")
        print("  3. Upload a local file")
        print("  4. List files in a specific folder")
        print("  0. Exit")
        choice = input("Choice: ").strip()

        if choice == "0":
            print("Bye!")
            break

        elif choice == "1":
            term = input("Search term: ").strip()
            results = search_files(service, term, drive_id)
            print(f"\nResults for '{term}':")
            print_files(results)

        elif choice == "2":
            fid   = input("File ID: ").strip()
            fname = input("Save as (filename): ").strip()
            # Detect Google Workspace docs and export as PDF
            meta  = service.files().get(fileId=fid, supportsAllDrives=True,
                                        fields="mimeType,name").execute()
            mime  = meta["mimeType"]
            if mime.startswith("application/vnd.google-apps"):
                print("  (Google Workspace file — exporting as PDF)")
                export_google_doc(service, fid, fname or meta["name"])
            else:
                download_file(service, fid, fname or meta["name"])

        elif choice == "3":
            local = input("Local file path: ").strip()
            pfid  = input("Destination folder ID (leave blank for root): ").strip() or None
            try:
                upload_file(service, local, pfid, drive_id)
            except FileNotFoundError as e:
                print(f"  ❌ {e}")

        elif choice == "4":
            fid = input("Folder ID: ").strip()
            folder_files = list_files(service, folder_id=fid)
            print_files(folder_files)

        else:
            print("  Invalid choice.")


if __name__ == "__main__":
    try:
        main()
    except HttpError as error:
        print(f"❌ Google API error: {error}")
    except KeyboardInterrupt:
        print("\nInterrupted.")