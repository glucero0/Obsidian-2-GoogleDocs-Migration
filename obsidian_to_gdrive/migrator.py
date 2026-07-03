import os
import re
import time

from .constants import MILLISECONDS_BETWEEN_FOLDERS, MIGRATION_ENGINE_VERSION
from .docs_builder import (
    _find_tab,
    build_markdown_requests_from_blocks,
    build_table_cell_insert_requests,
    build_table_cell_style_requests,
    find_table_at_or_after_index,
    get_index_after_table,
    make_insert_text_request,
)
from .drive_client import (
    UploadedDriveImage,
    delete_drive_file,
    ensure_doc_images_folder,
    ensure_drive_folder,
    ensure_drive_folder_path,
    find_drive_doc,
    format_missing_image_text,
    is_migration_complete,
    materialize_data_uri_image,
    resolve_image_path,
    retry_pending_image_revocations,
    stamp_migration_complete,
    try_revoke_public_image_access,
    upload_image,
)
from .markdown_parser import BlockKind, parse_markdown_blocks, strip_frontmatter
from .throttle import throttle_and_execute
from .vault import WorkItem, load_obsidian_attachment_folder, make_tab_title, split_drive_parent_path


def get_first_tab_id(docs_service, doc_id: str) -> str:
    def _get():
        return (
            docs_service.documents()
            .get(documentId=doc_id, includeTabsContent=True)
            .execute()
        )

    doc = throttle_and_execute(_get)
    tabs = doc.get("tabs", [])
    if not tabs:
        raise RuntimeError(f"No tabs returned for document {doc_id}.")
    tab_id = tabs[0].get("tabProperties", {}).get("tabId")
    if not tab_id:
        raise RuntimeError(f"No tab id returned for document {doc_id}.")
    return tab_id


def get_tab_insert_index(docs_service, doc_id: str, tab_id: str) -> int:
    def _get():
        return (
            docs_service.documents()
            .get(documentId=doc_id, includeTabsContent=True)
            .execute()
        )

    doc = throttle_and_execute(_get)
    tab = _find_tab(doc, tab_id)
    content = tab.get("documentTab", {}).get("body", {}).get("content", [])
    if not content:
        return 1
    return content[-1].get("endIndex", 2) - 1


def execute_requests(
    docs_service,
    doc_id: str,
    requests: list[dict],
    context: str = "",
) -> None:
    if not requests:
        return
    try:
        throttle_and_execute(
            lambda: docs_service.documents()
            .batchUpdate(documentId=doc_id, body={"requests": requests})
            .execute()
        )
    except Exception as exc:
        request_kinds = [next(iter(request)) for request in requests]
        label = f" ({context})" if context else ""
        raise RuntimeError(
            f"Docs batchUpdate failed{label}: {len(requests)} request(s) "
            f"[{', '.join(request_kinds[:8])}"
            f"{', ...' if len(request_kinds) > 8 else ''}]: {exc}"
        ) from exc


def insert_table_block(
    docs_service,
    doc_id: str,
    tab_id: str,
    table,
    insert_index: int,
) -> int:
    insert_index = get_tab_insert_index(docs_service, doc_id, tab_id)
    rows = len(table.table_rows)
    columns = max(len(row) for row in table.table_rows)

    execute_requests(
        docs_service,
        doc_id,
        [
            {
                "insertTable": {
                    "rows": rows,
                    "columns": columns,
                    "location": {"tabId": tab_id, "index": insert_index},
                }
            }
        ],
    )

    def _get_doc():
        return (
            docs_service.documents()
            .get(documentId=doc_id, includeTabsContent=True)
            .execute()
        )

    doc = throttle_and_execute(_get_doc)
    table_element = find_table_at_or_after_index(doc, tab_id, insert_index)
    cell_insert_requests = build_table_cell_insert_requests(
        doc, tab_id, table, insert_index
    )

    if cell_insert_requests:
        execute_requests(docs_service, doc_id, cell_insert_requests)
        doc = throttle_and_execute(_get_doc)
        table_element = find_table_at_or_after_index(doc, tab_id, insert_index)
        if table_element:
            style_requests = build_table_cell_style_requests(table_element, table, tab_id)
            if style_requests:
                execute_requests(docs_service, doc_id, style_requests)

    doc = throttle_and_execute(_get_doc)
    table_element = find_table_at_or_after_index(doc, tab_id, insert_index)
    if table_element:
        return get_index_after_table(doc, tab_id, table_element)
    return get_tab_insert_index(docs_service, doc_id, tab_id)


def flush_text_batch(
    docs_service,
    doc_id: str,
    text_batch: list,
    tab_id: str,
    insert_index: int,
) -> int:
    text_requests, list_batches, _length = build_markdown_requests_from_blocks(
        text_batch, tab_id, insert_index
    )
    if text_requests:
        execute_requests(docs_service, doc_id, text_requests)
    for batch in list_batches:
        execute_requests(docs_service, doc_id, batch)
    return get_tab_insert_index(docs_service, doc_id, tab_id)


def append_markdown_content(
    docs_service,
    doc_id: str,
    markdown: str,
    tab_id: str,
    insert_index: int,
) -> int:
    blocks = parse_markdown_blocks(markdown)
    text_batch: list = []

    for block in blocks:
        if block.kind == BlockKind.TABLE:
            if text_batch:
                insert_index = flush_text_batch(
                    docs_service, doc_id, text_batch, tab_id, insert_index
                )
                text_batch.clear()

            insert_index = get_tab_insert_index(docs_service, doc_id, tab_id)
            insert_index = insert_table_block(docs_service, doc_id, tab_id, block, insert_index)
            continue

        text_batch.append(block)

    if text_batch:
        insert_index = flush_text_batch(
            docs_service, doc_id, text_batch, tab_id, insert_index
        )

    return insert_index


def process_content_and_images(
    docs_service,
    doc_id: str,
    content: str,
    note_directory: str,
    vault_path: str,
    tab_id: str,
    images_folder_id: str,
    drive_service,
    pending_image_revocations: list[UploadedDriveImage],
    attachment_folder_setting: str | None = None,
) -> None:
    insert_index = get_tab_insert_index(docs_service, doc_id, tab_id)

    img_regex = re.compile(
        r"!\[\[(?P<wiki>[^\]|]+)(?:\|[^\]]*)?\]\]|!\[(?P<alt>[^\]]*)\]\((?P<md>[^)]+)\)"
    )

    last_index = 0
    for match in img_regex.finditer(content):
        if match.start() > last_index:
            insert_index = append_markdown_content(
                docs_service,
                doc_id,
                content[last_index : match.start()],
                tab_id,
                insert_index,
            )

        img_ref = match.group("wiki") if match.group("wiki") else match.group("md")
        alt_text = match.group("alt")
        if not alt_text or not alt_text.strip():
            alt_text = os.path.splitext(img_ref.split("|")[0])[0]

        if img_ref.lower().startswith(("http://", "https://")):
            insert_index = append_markdown_content(
                docs_service,
                doc_id,
                f"[{alt_text}]({img_ref.strip()})\n",
                tab_id,
                insert_index,
            )
            last_index = match.end()
            continue

        temp_image_path: str | None = None
        if img_ref.lower().startswith("data:"):
            print(f"  Decoding embedded image: {alt_text}")
            temp_image_path = materialize_data_uri_image(img_ref)
            if temp_image_path is None:
                print(f"  Warning: could not decode embedded image '{alt_text}'")

        absolute_img_path = temp_image_path or resolve_image_path(
            img_ref, note_directory, vault_path, attachment_folder_setting
        )
        uploaded = (
            upload_image(drive_service, absolute_img_path, images_folder_id)
            if absolute_img_path
            else None
        )

        if uploaded:
            try:
                execute_requests(
                    docs_service,
                    doc_id,
                    [
                        {
                            "insertInlineImage": {
                                "uri": f"https://drive.google.com/uc?export=view&id={uploaded.file_id}",
                                "location": {"tabId": tab_id, "index": insert_index},
                            }
                        },
                    ],
                    context=f"inline image '{alt_text}'",
                )
                insert_index = get_tab_insert_index(docs_service, doc_id, tab_id)
                execute_requests(
                    docs_service,
                    doc_id,
                    [make_insert_text_request("\n", tab_id, insert_index)],
                    context=f"newline after image '{alt_text}'",
                )
                insert_index = get_tab_insert_index(docs_service, doc_id, tab_id)
            finally:
                if not try_revoke_public_image_access(drive_service, uploaded):
                    pending_image_revocations.append(uploaded)
                if temp_image_path:
                    try:
                        os.unlink(temp_image_path)
                    except OSError:
                        pass
        else:
            if temp_image_path:
                try:
                    os.unlink(temp_image_path)
                except OSError:
                    pass
            insert_index = append_markdown_content(
                docs_service,
                doc_id,
                format_missing_image_text(alt_text, img_ref),
                tab_id,
                insert_index,
            )

        last_index = match.end()

    if last_index < len(content):
        append_markdown_content(
            docs_service,
            doc_id,
            content[last_index:],
            tab_id,
            insert_index,
        )


def process_vault_folder(
    docs_service,
    drive_service,
    migration_root_id: str,
    work_item: WorkItem,
    vault_path: str,
    attachment_folder_setting: str | None = None,
) -> None:
    doc_title = work_item.doc_title
    drive_relative_path = work_item.drive_relative_path
    folder_path = work_item.folder_path
    md_files = work_item.md_files

    print(f"\nProcessing folder: '{drive_relative_path}' ({len(md_files)} notes)...")

    parent_path, _leaf_name = split_drive_parent_path(drive_relative_path)
    parent_folder_id = (
        ensure_drive_folder_path(drive_service, migration_root_id, parent_path)
        if parent_path
        else migration_root_id
    )
    images_folder_id = ensure_doc_images_folder(drive_service, parent_folder_id, doc_title)

    existing_doc = find_drive_doc(drive_service, doc_title, parent_folder_id)
    if existing_doc:
        if is_migration_complete(existing_doc.description):
            print(f"  Document '{doc_title}' already migrated [{existing_doc.id}], skipping folder.")
            return
        print(
            f"  Document '{doc_title}' exists but is incomplete [{existing_doc.id}], "
            "removing and re-migrating."
        )
        delete_drive_file(drive_service, existing_doc.id)

    pending_image_revocations: list[UploadedDriveImage] = []
    doc_id: str | None = None

    try:
        created = throttle_and_execute(
            lambda: docs_service.documents().create(body={"title": doc_title}).execute()
        )
        doc_id = created["documentId"]

        current = throttle_and_execute(
            lambda: drive_service.files()
            .get(fileId=doc_id, fields="parents")
            .execute()
        )
        parents = current.get("parents", [])
        throttle_and_execute(
            lambda: drive_service.files()
            .update(
                fileId=doc_id,
                addParents=parent_folder_id,
                removeParents=",".join(parents),
                fields="id",
            )
            .execute()
        )

        print(f"  Created master document: '{doc_title}' [ID: {doc_id}]")

        is_first_tab = True
        migrated_tabs = 0
        failed_tabs = 0

        for file_path in sorted(md_files, key=str.lower):
            tab_title = make_tab_title(file_path)
            print(f"  -> Migrating: {tab_title}")

            try:
                with open(file_path, encoding="utf-8") as f:
                    raw_content = f.read()
                clean_content = strip_frontmatter(raw_content)
                note_directory = os.path.dirname(file_path) or folder_path

                if is_first_tab:
                    tab_id = get_first_tab_id(docs_service, doc_id)
                    execute_requests(
                        docs_service,
                        doc_id,
                        [
                            {
                                "updateDocumentTabProperties": {
                                    "tabProperties": {"tabId": tab_id, "title": tab_title},
                                    "fields": "title",
                                }
                            }
                        ],
                    )
                    is_first_tab = False
                else:
                    result = execute_requests_with_reply(
                        docs_service,
                        doc_id,
                        [
                            {
                                "addDocumentTab": {
                                    "tabProperties": {"title": tab_title},
                                }
                            }
                        ],
                    )
                    tab_id = (
                        result["replies"][0]["addDocumentTab"]["tabProperties"]["tabId"]
                    )
                    if not tab_id:
                        raise RuntimeError(f"Failed to create tab for '{tab_title}'.")

                process_content_and_images(
                    docs_service,
                    doc_id,
                    clean_content,
                    note_directory,
                    vault_path,
                    tab_id,
                    images_folder_id,
                    drive_service,
                    pending_image_revocations,
                    attachment_folder_setting,
                )
                migrated_tabs += 1
            except Exception as exc:
                failed_tabs += 1
                print(f"  ERROR migrating '{tab_title}': {exc}")

        if migrated_tabs == 0:
            raise RuntimeError(
                f"No tabs migrated for '{drive_relative_path}' ({failed_tabs}/{len(md_files)} failed)."
            )

        if failed_tabs > 0:
            print(
                f"  Warning: {failed_tabs}/{len(md_files)} tab(s) failed for '{drive_relative_path}'. "
                f"Document [{doc_id}] left unstamped and will be re-migrated on the next run."
            )
            return

        stamp_migration_complete(drive_service, doc_id)
        print(f"  Migration complete for '{doc_title}' ({migrated_tabs} tabs).")

    except Exception as exc:
        if doc_id:
            try:
                delete_drive_file(drive_service, doc_id)
                print(f"  Removed incomplete document '{doc_title}' [{doc_id}] after failure.")
            except Exception as delete_exc:
                print(f"  Warning: could not delete incomplete document '{doc_id}': {delete_exc}")
        raise RuntimeError(f"Folder '{drive_relative_path}' migration failed: {exc}") from exc
    finally:
        retry_pending_image_revocations(drive_service, pending_image_revocations)


def execute_requests_with_reply(docs_service, doc_id: str, requests: list[dict]) -> dict:
    return throttle_and_execute(
        lambda: docs_service.documents()
        .batchUpdate(documentId=doc_id, body={"requests": requests})
        .execute()
    )


def run_migration(
    docs_service,
    drive_service,
    vault_path: str,
    drive_migration_folder_name: str,
) -> None:
    from .vault import collect_folders_with_markdown

    attachment_folder_setting = load_obsidian_attachment_folder(vault_path)
    if attachment_folder_setting:
        print(f"Obsidian attachment folder: '{attachment_folder_setting}'")

    migration_root_id = ensure_drive_folder(drive_service, drive_migration_folder_name, None)
    print(f"Drive output folder: '{drive_migration_folder_name}' [{migration_root_id}]")

    work_items = collect_folders_with_markdown(vault_path)
    if not work_items:
        print("No markdown files found in the vault.")
        return

    print(f"Found {len(work_items)} folders with markdown to migrate.")
    print(f"Migration engine version: {MIGRATION_ENGINE_VERSION}")
    print(f"Loaded from: {os.path.dirname(os.path.abspath(__file__))}")

    for i, work_item in enumerate(work_items):
        try:
            process_vault_folder(
                docs_service,
                drive_service,
                migration_root_id,
                work_item,
                vault_path,
                attachment_folder_setting,
            )
        except Exception as exc:
            print(f"ERROR processing folder '{work_item.drive_relative_path}': {exc}")

        if i < len(work_items) - 1:
            time.sleep(MILLISECONDS_BETWEEN_FOLDERS / 1000.0)

    print("\nMigration complete across all targeted folders.")
