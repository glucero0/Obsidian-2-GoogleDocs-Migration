import json
import os
from dataclasses import dataclass
from pathlib import Path

from .constants import SKIPPED_FOLDER_NAMES


@dataclass(frozen=True)
class WorkItem:
    doc_title: str
    drive_relative_path: str
    folder_path: str
    md_files: tuple[str, ...]


def should_skip_folder(vault_path: str, folder_path: str) -> bool:
    relative = os.path.relpath(folder_path, vault_path)
    if relative in (".", ""):
        return False

    for segment in Path(relative).parts:
        if not segment:
            continue
        if segment.startswith(".") or segment.lower() in SKIPPED_FOLDER_NAMES:
            return True
    return False


def enumerate_vault_folders(vault_path: str):
    yield vault_path
    for root, dirs, _ in os.walk(vault_path):
        dirs[:] = [d for d in dirs if not should_skip_folder(vault_path, os.path.join(root, d))]
        for name in dirs:
            yield os.path.join(root, name)


def get_drive_relative_path(vault_path: str, folder_path: str) -> str:
    vault_full = os.path.normpath(vault_path)
    folder_full = os.path.normpath(folder_path)
    if folder_full.lower() == vault_full.lower():
        return Path(vault_full).name
    return os.path.relpath(folder_full, vault_full).replace("\\", "/")


def split_drive_parent_path(drive_relative_path: str) -> tuple[str, str]:
    """Split a vault-relative path into (parent_path, leaf_name).

    The leaf name becomes the Google Doc title. Parent folders mirror the vault
    hierarchy; the doc itself is not placed inside a same-named leaf folder.
    """
    normalized = drive_relative_path.replace("\\", "/").strip("/")
    if not normalized:
        return "", ""
    parts = normalized.split("/")
    if len(parts) == 1:
        return "", parts[0]
    return "/".join(parts[:-1]), parts[-1]

def collect_folders_with_markdown(vault_path: str) -> list[WorkItem]:
    vault_full = os.path.normpath(vault_path)
    work_items: list[WorkItem] = []

    for folder_path in enumerate_vault_folders(vault_full):
        md_files = sorted(
            [
                os.path.join(folder_path, name)
                for name in os.listdir(folder_path)
                if name.lower().endswith(".md") and os.path.isfile(os.path.join(folder_path, name))
            ],
            key=str.lower,
        )
        if not md_files:
            continue

        drive_relative_path = get_drive_relative_path(vault_full, folder_path)
        doc_title = Path(folder_path.rstrip(os.sep)).name
        work_items.append(
            WorkItem(
                doc_title=doc_title,
                drive_relative_path=drive_relative_path,
                folder_path=folder_path,
                md_files=tuple(md_files),
            )
        )

    return sorted(work_items, key=lambda w: w.drive_relative_path.lower())


def make_tab_title(file_path: str) -> str:
    return Path(file_path).stem


def load_obsidian_attachment_folder(vault_path: str) -> str | None:
    """Read Obsidian's attachmentFolderPath from .obsidian/app.json."""
    app_json = Path(vault_path) / ".obsidian" / "app.json"
    if not app_json.is_file():
        return None
    try:
        data = json.loads(app_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    setting = data.get("attachmentFolderPath")
    if not isinstance(setting, str) or not setting.strip():
        return None
    return setting.strip()


def resolve_attachment_directory(
    vault_path: str,
    note_directory: str,
    attachment_folder_setting: str | None,
) -> str | None:
    """Map Obsidian's attachmentFolderPath setting to an absolute directory."""
    if not attachment_folder_setting:
        return None

    setting = attachment_folder_setting.strip().replace("/", os.sep)
    if setting in (".", f".{os.sep}"):
        return os.path.normpath(note_directory)
    if setting.startswith(f".{os.sep}"):
        return os.path.normpath(os.path.join(note_directory, setting[2:]))
    return os.path.normpath(os.path.join(vault_path, setting))
