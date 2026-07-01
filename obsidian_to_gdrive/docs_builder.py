from dataclasses import dataclass

from .markdown_parser import (
    BlockKind,
    InlineRun,
    MarkdownBlock,
    block_plain_text,
    parse_markdown_blocks,
    runs_to_plain,
)


@dataclass
class BlockSegment:
    block: MarkdownBlock
    start_index: int
    end_index: int


def make_range(tab_id: str, start: int, end: int) -> dict:
    return {"tabId": tab_id, "startIndex": start, "endIndex": end}


def make_insert_text_request(text: str, tab_id: str, index: int) -> dict:
    return {
        "insertText": {
            "text": text,
            "location": {"tabId": tab_id, "index": index},
        }
    }


def make_text_style_request(tab_id: str, start: int, end: int, run: InlineRun) -> dict | None:
    style: dict = {}
    fields: list[str] = []

    if run.bold:
        style["bold"] = True
        fields.append("bold")
    if run.italic:
        style["italic"] = True
        fields.append("italic")
    if run.strikethrough:
        style["strikethrough"] = True
        fields.append("strikethrough")
    if run.code:
        style["weightedFontFamily"] = {"fontFamily": "Courier New", "weight": 400}
        style["backgroundColor"] = {
            "color": {"rgbColor": {"red": 0.95, "green": 0.95, "blue": 0.95}}
        }
        fields.extend(["weightedFontFamily", "backgroundColor"])
    if run.link_url:
        style["link"] = {"url": run.link_url}
        style["foregroundColor"] = {
            "color": {"rgbColor": {"red": 0.1, "green": 0.2, "blue": 0.8}}
        }
        fields.extend(["link", "foregroundColor"])

    if not fields:
        return None

    return {
        "updateTextStyle": {
            "range": make_range(tab_id, start, end),
            "textStyle": style,
            "fields": ",".join(fields),
        }
    }


def build_list_formatting_batches(
    segments: list[BlockSegment],
    tab_id: str,
    insert_index: int,
    inserted_length: int,
) -> list[list[dict]]:
    """Build one API batch per contiguous list group.

    Google Docs reads leading tabs for nesting, then strips them. Each group is
    applied in its own batch so later ranges are shifted by tabs already removed.
    """
    batches: list[list[dict]] = []
    tabs_stripped = 0
    doc_end = insert_index + inserted_length - 1

    i = 0
    while i < len(segments):
        block = segments[i].block
        if block.kind not in (BlockKind.BULLET_ITEM, BlockKind.ORDERED_ITEM):
            i += 1
            continue

        list_kind = block.kind
        start = i
        while i < len(segments) and segments[i].block.kind == list_kind:
            i += 1

        group = segments[start:i]
        ordered = list_kind == BlockKind.ORDERED_ITEM
        adjusted_start = group[0].start_index - tabs_stripped
        adjusted_end = min(group[-1].end_index - tabs_stripped, doc_end - tabs_stripped)
        tabs_stripped += sum(segment.block.list_indent for segment in group)

        batches.append(
            [
                {
                    "createParagraphBullets": {
                        "range": make_range(tab_id, adjusted_start, adjusted_end),
                        "bulletPreset": (
                            "NUMBERED_DECIMAL_ALPHA_ROMAN"
                            if ordered
                            else "BULLET_DISC_CIRCLE_SQUARE"
                        ),
                    }
                }
            ]
        )

    return batches


def build_markdown_requests_from_blocks(
    blocks: list[MarkdownBlock],
    tab_id: str,
    insert_index: int,
) -> tuple[list[dict], list[list[dict]], int]:
    if not blocks:
        return [], [], 0

    plain_parts: list[str] = []
    segments: list[BlockSegment] = []

    for block in blocks:
        if block.kind == BlockKind.TABLE:
            continue
        start = insert_index + sum(len(p) for p in plain_parts)
        text = block_plain_text(block)
        plain_parts.append(text)
        segments.append(BlockSegment(block=block, start_index=start, end_index=start + len(text)))

    if not plain_parts:
        return [], [], 0

    all_text = "".join(plain_parts)
    requests: list[dict] = [make_insert_text_request(all_text, tab_id, insert_index)]

    for segment in segments:
        block = segment.block
        content_end = segment.end_index - 1

        if block.kind == BlockKind.HEADING and 1 <= block.heading_level <= 6:
            requests.append(
                {
                    "updateParagraphStyle": {
                        "range": make_range(tab_id, segment.start_index, segment.end_index),
                        "paragraphStyle": {
                            "namedStyleType": f"HEADING_{block.heading_level}"
                        },
                        "fields": "namedStyleType",
                    }
                }
            )
        elif block.kind == BlockKind.CODE_BLOCK:
            requests.append(
                {
                    "updateTextStyle": {
                        "range": make_range(tab_id, segment.start_index, content_end),
                        "textStyle": {
                            "weightedFontFamily": {
                                "fontFamily": "Courier New",
                                "weight": 400,
                            },
                            "backgroundColor": {
                                "color": {
                                    "rgbColor": {"red": 0.92, "green": 0.92, "blue": 0.92}
                                }
                            },
                        },
                        "fields": "weightedFontFamily,backgroundColor",
                    }
                }
            )
        elif block.kind == BlockKind.BLOCKQUOTE:
            requests.append(
                {
                    "updateParagraphStyle": {
                        "range": make_range(tab_id, segment.start_index, segment.end_index),
                        "paragraphStyle": {
                            "indentStart": {"magnitude": 36, "unit": "PT"},
                            "indentFirstLine": {"magnitude": 36, "unit": "PT"},
                        },
                        "fields": "indentStart,indentFirstLine",
                    }
                }
            )

        if block.kind == BlockKind.CODE_BLOCK:
            continue

        run_offset = segment.start_index
        if block.kind in (BlockKind.BULLET_ITEM, BlockKind.ORDERED_ITEM):
            run_offset += block.list_indent
        for run in block.runs:
            if not run.text:
                continue
            run_start = run_offset
            run_end = run_offset + len(run.text)
            run_offset = run_end
            style_request = make_text_style_request(tab_id, run_start, run_end, run)
            if style_request:
                requests.append(style_request)

    list_batches = build_list_formatting_batches(
        segments, tab_id, insert_index, len(all_text)
    )
    return requests, list_batches, len(all_text)


def build_markdown_requests(
    markdown: str, tab_id: str, insert_index: int
) -> tuple[list[dict], list[list[dict]], int]:
    return build_markdown_requests_from_blocks(
        parse_markdown_blocks(markdown), tab_id, insert_index
    )


def find_table_at_or_after_index(doc: dict, tab_id: str, insert_location_index: int) -> dict | None:
    tab = _find_tab(doc, tab_id)
    content = tab.get("documentTab", {}).get("body", {}).get("content", [])
    if not content:
        return None

    expected_start = insert_location_index + 1
    for element in content:
        if element.get("table") and element.get("startIndex") == expected_start:
            return element

    candidates = [
        e
        for e in content
        if e.get("table") and (e.get("startIndex") or 0) > insert_location_index
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda e: e.get("startIndex", 0))


def get_cell_insert_index(cell: dict) -> int:
    for item in cell.get("content", []):
        paragraph = item.get("paragraph")
        if not paragraph:
            continue
        elements = paragraph.get("elements", [])
        if elements:
            return elements[0].get("startIndex", 1)
        return item.get("startIndex", 1)
    return 1


def build_table_cell_insert_requests(
    doc: dict,
    tab_id: str,
    table: MarkdownBlock,
    table_location_index: int,
) -> list[dict]:
    table_element = find_table_at_or_after_index(doc, tab_id, table_location_index)
    if not table_element or not table_element.get("table", {}).get("tableRows"):
        return []

    api_rows = table_element["table"]["tableRows"]
    requests: list[dict] = []

    for r in range(len(table.table_rows) - 1, -1, -1):
        if r >= len(api_rows):
            continue
        for c in range(len(table.table_rows[r]) - 1, -1, -1):
            if c >= len(api_rows[r]["tableCells"]):
                continue
            text = runs_to_plain(table.table_rows[r][c])
            if not text:
                continue
            cell_index = get_cell_insert_index(api_rows[r]["tableCells"][c])
            requests.append(make_insert_text_request(text, tab_id, cell_index))

    return requests


def get_index_after_table(doc: dict, tab_id: str, table_element: dict) -> int:
    """Return a safe body insert index immediately after a table."""
    tab = _find_tab(doc, tab_id)
    content = tab.get("documentTab", {}).get("body", {}).get("content", [])
    table_start = table_element.get("startIndex")

    seen_table = False
    for element in content:
        if seen_table:
            paragraph = element.get("paragraph")
            if paragraph:
                elements = paragraph.get("elements", [])
                if elements:
                    return elements[0].get("startIndex", element.get("startIndex", 1))
                return element.get("startIndex", 1)
        if element.get("table") and element.get("startIndex") == table_start:
            seen_table = True

    if content:
        return content[-1].get("endIndex", 2) - 1
    return 1


def build_table_cell_style_requests(
    table_element: dict,
    table: MarkdownBlock,
    tab_id: str,
) -> list[dict]:
    api_rows = table_element["table"]["tableRows"]
    requests: list[dict] = []

    for r, row in enumerate(table.table_rows):
        if r >= len(api_rows):
            break
        for c, runs in enumerate(row):
            if c >= len(api_rows[r]["tableCells"]):
                break
            text = runs_to_plain(runs)
            if not text:
                continue
            run_offset = get_cell_insert_index(api_rows[r]["tableCells"][c])
            for run in runs:
                if not run.text:
                    continue
                run_start = run_offset
                run_end = run_offset + len(run.text)
                run_offset = run_end
                styled_run = InlineRun(
                    text=run.text,
                    bold=run.bold or r == 0,
                    italic=run.italic,
                    code=run.code,
                    strikethrough=run.strikethrough,
                    link_url=run.link_url,
                )
                style_request = make_text_style_request(tab_id, run_start, run_end, styled_run)
                if style_request:
                    requests.append(style_request)

    return requests


def _find_tab(doc: dict, tab_id: str) -> dict:
    for tab in doc.get("tabs", []):
        if tab.get("tabProperties", {}).get("tabId") == tab_id:
            return tab
    return {}
