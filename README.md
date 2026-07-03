# Obsidian to Google Docs Migration

Migrate an Obsidian vault to Google Drive as Google Docs. Each vault folder that contains markdown notes becomes one Google Doc with a tab per note. Folder structure, headings, lists, tables, links, and embedded images are preserved where supported.

Created and maintained by **Cursor Composer 2.5 Standard**, with direction from **Gary Lucero**.

## What it does

- Walks your Obsidian vault and finds every folder that contains `.md` files.
- Creates a matching folder hierarchy in Google Drive under a configurable root folder (default: `Notebooks` at the top level of My Drive).
- Creates one Google Doc per vault folder. The folder name becomes the document title.
- Adds each markdown file in that folder as a **tab** inside the doc (tab title = filename without `.md`).
- Uploads embedded images to a per-document `_images/<doc title>/` folder on Drive and inserts them into the doc.
- Skips hidden/system folders such as `.obsidian`, `.trash`, `.git`, and `.cursor`.
- Strips headmatter from .md files.
- On re-run, skips documents that were fully migrated previously; incomplete docs are deleted and re-migrated.

## Requirements

- **Python 3.10+** (the code uses modern type syntax such as `str | None`)
- An **Obsidian vault** on your local machine
- A **Google Cloud project** with the Google Docs API and Google Drive API enabled
- OAuth 2.0 **Desktop app** credentials downloaded as a JSON file
- All filenames must be 50 characters or less (limitation of Google Docs tabs)

## Google Cloud setup

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project (or select an existing one).
3. Enable these APIs for the project:
   - [Google Docs API](https://console.cloud.google.com/apis/library/docs.googleapis.com)
   - [Google Drive API](https://console.cloud.google.com/apis/library/drive.googleapis.com)
4. Configure the **OAuth consent screen** (External or Internal, depending on your Google account type).
5. Create **OAuth 2.0 Client ID** credentials:
   - Application type: **Desktop app**
   - Download the JSON file and store it somewhere safe on your machine.
6. The first time you run the script, a browser window opens for Google sign-in. After authorization, a `token.json` file is saved locally for future runs.

The script requests these OAuth scopes:

- `https://www.googleapis.com/auth/documents`
- `https://www.googleapis.com/auth/drive`

## Installation

Clone or download this repository, then install dependencies:

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## Configuration

All user-facing settings are in `migrate_obsidian.py`. Open that file and edit the values in the **Configuration** section:

| Setting | Description |
| --- | --- |
| `vault_path` | Absolute path to your Obsidian vault root |
| `credentials_path` | Absolute path to your Google OAuth client JSON file |
| `token_folder` | Directory where `token.json` will be stored after first login |
| `token_path` | Full path to the saved OAuth token (usually `{token_folder}/token.json`) |
| `drive_migration_folder_name` | Name of the root folder created in Google Drive (default: `Notebooks`) |

Example:

```python
vault_path = r"C:\Users\you\ObsidianVaults\Personal"
credentials_path = r"C:\Obsidian Migration\client_secret.json"
token_folder = r"C:\Obsidian Migration\TokenStore"
token_path = f"{token_folder}/token.json"
drive_migration_folder_name = "Notebooks"
```

### Drive output layout

- Root: `<drive_migration_folder_name>` at the top level of **My Drive**
- Subfolders mirror your vault hierarchy (excluding the leaf folder name)
- Each Google Doc sits in the parent folder that matches its vault path
- Images: `<parent folder>/_images/<doc title>/`

Example vault:

```text
Personal/
  Projects/
    Website/
      index.md
      todo.md
```

Drive result:

```text
Notebooks/
  Personal/
    Projects/
      Website          ← Google Doc titled "Website"
        ├─ tab: index
        └─ tab: todo
      _images/
        Website/       ← uploaded images for that doc
```

## Running the migration

From the repository root with your virtual environment activated:

```bash
python migrate_obsidian.py
```

On first run:

1. The script validates that the vault and credentials file exist.
2. A browser opens for Google OAuth (unless a valid `token.json` already exists).
3. Migration progress is printed to the console.

The script is rate-limited to stay under Google API quotas (roughly one API call every ~1.1 seconds, with a longer pause between folders). Large vaults can take a while.

## Markdown support

Supported elements include:

- Headings (`#` through `######`)
- Bold, italic, bold+italic, strikethrough, inline code
- Bullet and numbered lists (with nesting)
- Block quotes and fenced code blocks
- Markdown tables
- Standard links `[text](url)` and Obsidian wiki links `[[Note]]` / `[[Note|alias]]`
- Images: `![alt](path)`, including paths resolved via Obsidian's `attachmentFolderPath` setting in `.obsidian/app.json`
- PNG, JPG/JPEG, and GIF images (including some `data:` URI embedded images)

YAML frontmatter at the top of a note is stripped before conversion.

## Re-running safely

Completed documents are marked in Drive file metadata. If you run the script again:

- **Completed docs** are skipped.
- **Incomplete docs** (from a prior failed run) are deleted and recreated.
- To force a full re-migration of a folder, delete the corresponding Google Doc in Drive first.

## Security notes

- **Do not commit** OAuth credentials or tokens. This repository's `.gitignore` excludes common secret filenames (`credentials.json`, `client_secret*.json`, `token.json`, `TokenStore/`, etc.).
- Keep your OAuth client JSON and `token.json` on your local machine only.
- The script temporarily grants public read access to uploaded images so Google Docs can embed them, then revokes that access after insertion.

## Troubleshooting

| Problem | Things to check |
| --- | --- |
| `Vault path not found` | `vault_path` in `migrate_obsidian.py` points to the vault root |
| `Credentials file not found` | `credentials_path` points to the downloaded OAuth JSON |
| Browser does not open / auth fails | APIs enabled, consent screen configured, Desktop client type used |
| Images missing | Image path relative to note or Obsidian attachment folder; supported format (PNG/JPG/GIF) |
| Slow migration | Expected — API throttling is intentional for quota safety |

## Project layout

```text
migrate_obsidian.py          Entry point and configuration
obsidian_to_gdrive/
  auth.py                    Google OAuth and service setup
  migrator.py                Migration orchestration
  vault.py                   Vault scanning and path logic
  drive_client.py            Drive folder/file operations
  docs_builder.py            Google Docs API request building
  markdown_parser.py         Markdown parsing
  constants.py               Internal defaults (scopes, rate limits, etc.)
requirements.txt             Python dependencies
```

## License

MIT License
