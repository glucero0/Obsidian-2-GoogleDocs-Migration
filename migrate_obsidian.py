#!/usr/bin/env python3
"""Migrate Obsidian vault folders to Google Docs with tabs."""

from obsidian_to_gdrive.auth import get_google_services, validate_configuration
from obsidian_to_gdrive.migrator import run_migration


def main() -> None:
    # --- Configuration ---
    vault_path = r"C:\Users\[username]\ObsidianVaults\Personal"
    credentials_path = r"C:\Obsidian Migration\[secret].json"
    token_folder = r"C:\Obsidian Migration\TokenStore"
    token_path = f"{token_folder}/token.json"

    validate_configuration(vault_path, credentials_path, token_folder)

    print("Initializing Google API Services...")
    docs_service, drive_service = get_google_services(credentials_path, token_path)
    run_migration(docs_service, drive_service, vault_path)


if __name__ == "__main__":
    main()
