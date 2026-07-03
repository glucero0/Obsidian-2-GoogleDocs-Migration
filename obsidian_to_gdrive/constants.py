MIGRATION_COMPLETE_MARKER = "obsidian-migration-complete:v1"
MIGRATION_ENGINE_VERSION = "2026-07-01-embedded-images"

# Google Docs/Drive quotas: 60 read and 60 write requests per user per minute.
# 1100ms spacing keeps sustained traffic under ~54 requests/minute.
MIN_MILLISECONDS_BETWEEN_API_CALLS = 1100
MILLISECONDS_BETWEEN_FOLDERS = 3000

APPLICATION_NAME = "ObsidianMigrator"

SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
]

SKIPPED_FOLDER_NAMES = frozenset({".obsidian", ".trash", ".git", ".cursor"})

SUPPORTED_IMAGE_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
}
