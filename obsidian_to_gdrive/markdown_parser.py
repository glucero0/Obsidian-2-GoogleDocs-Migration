import re
from dataclasses import dataclass, field
from enum import Enum, auto


class BlockKind(Enum):
    PARAGRAPH = auto()
    HEADING = auto()
    BULLET_ITEM = auto()
    ORDERED_ITEM = auto()
    CODE_BLOCK = auto()
    BLOCKQUOTE = auto()
    TABLE = auto()


@dataclass
class InlineRun:
    text: str = ""
    bold: bool = False
    italic: bool = False
    code: bool = False
    strikethrough: bool = False
    link_url: str | None = None


@dataclass
class MarkdownBlock:
    kind: BlockKind
    heading_level: int = 0
    list_indent: int = 0
    text: str = ""
    runs: list[InlineRun] = field(default_factory=list)
    table_rows: list[list[list[InlineRun]]] = field(default_factory=list)


INLINE_PATTERN = re.compile(
    r"(?P<wiki>\[\[(?P<wikitext>[^\]|]+)(?:\|(?P<wikialias>[^\]]+))?\]\])|"
    r"(?P<link>\[(?P<linktext>[^\]]+)\]\((?P<linkurl>[^)]+)\))|"
    r"(?P<code>`(?P<codetext>[^`]+)`)|"
    r"(?P<boldbold>\*\*\*(?P<bolditalictext>.+?)\*\*\*)|"
    r"(?P<bold>\*\*(?P<boldtext>.+?)\*\*)|"
    r"(?P<italic>\*(?P<italictext>.+?)\*)|"
    r"(?P<underscoreitalic>_(?P<underscoreitalictext>[^_]+?)_)|"
    r"(?P<strike>~~(?P<striketext>.+?)~~)"
)

BLOCK_START_PATTERN = re.compile(
    r"^(```|#{1,6}\s|\s*[-*+]\s|\s*\d+\.\s|>\s?|\|.*\||(-{3,}|\*{3,}|_{3,})$)"
)

TABLE_SEPARATOR_PATTERN = re.compile(
    r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$"
)


def strip_frontmatter(markdown: str) -> str:
    if not re.match(r"^-{3,}", markdown):
        return markdown
    match = re.match(
        r"\A-{3,}[ \t]*\r?\n.*?\r?\n-{3,}[ \t]*(?:\r?\n|$)",
        markdown,
        re.DOTALL,
    )
    return markdown[match.end() :] if match else markdown


def calc_list_indent(whitespace: str) -> int:
    tabs = whitespace.count("\t")
    spaces = whitespace.count(" ")
    return tabs + (spaces + 1) // 2


def is_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|")


def is_table_separator(line: str) -> bool:
    return bool(TABLE_SEPARATOR_PATTERN.match(line.strip()))


def split_table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def runs_to_plain(runs: list[InlineRun]) -> str:
    return "".join(r.text for r in runs)


def parse_inline(text: str) -> list[InlineRun]:
    runs: list[InlineRun] = []
    last = 0

    for m in INLINE_PATTERN.finditer(text):
        if m.start() > last:
            runs.append(InlineRun(text=text[last : m.start()]))

        if m.group("wiki"):
            target = m.group("wikitext")
            display = m.group("wikialias") if m.group("wikialias") else target
            runs.append(InlineRun(text=display))
        elif m.group("link"):
            runs.append(
                InlineRun(
                    text=m.group("linktext"),
                    link_url=m.group("linkurl").strip(),
                )
            )
        elif m.group("code"):
            runs.append(InlineRun(text=m.group("codetext"), code=True))
        elif m.group("boldbold"):
            runs.append(
                InlineRun(text=m.group("bolditalictext"), bold=True, italic=True)
            )
        elif m.group("bold"):
            runs.append(InlineRun(text=m.group("boldtext"), bold=True))
        elif m.group("italic"):
            runs.append(InlineRun(text=m.group("italictext"), italic=True))
        elif m.group("underscoreitalic"):
            runs.append(InlineRun(text=m.group("underscoreitalictext"), italic=True))
        elif m.group("strike"):
            runs.append(InlineRun(text=m.group("striketext"), strikethrough=True))

        last = m.end()

    if last < len(text):
        runs.append(InlineRun(text=text[last:]))

    if not runs and text:
        runs.append(InlineRun(text=text))

    return runs


def parse_table_row_cells(line: str) -> list[list[InlineRun]]:
    return [parse_inline(cell) for cell in split_table_cells(line)]


def is_block_start(line: str) -> bool:
    return bool(BLOCK_START_PATTERN.match(line))


def parse_markdown_blocks(markdown: str) -> list[MarkdownBlock]:
    blocks: list[MarkdownBlock] = []
    lines = markdown.replace("\r\n", "\n").split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]

        if line.startswith("```"):
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code_lines.append(lines[i])
                i += 1
            blocks.append(
                MarkdownBlock(
                    kind=BlockKind.CODE_BLOCK,
                    text="\n".join(code_lines).rstrip("\r\n"),
                )
            )
            i += 1
            continue

        if not line.strip():
            i += 1
            continue

        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", line):
            i += 1
            continue

        if is_table_row(line) and i + 1 < len(lines) and is_table_separator(lines[i + 1]):
            table_rows = [parse_table_row_cells(line)]
            i += 2
            while i < len(lines) and is_table_row(lines[i]):
                table_rows.append(parse_table_row_cells(lines[i]))
                i += 1
            i -= 1

            column_count = max(len(row) for row in table_rows)
            for row in table_rows:
                while len(row) < column_count:
                    row.append([InlineRun(text="")])

            blocks.append(MarkdownBlock(kind=BlockKind.TABLE, table_rows=table_rows))
            i += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            blocks.append(
                MarkdownBlock(
                    kind=BlockKind.HEADING,
                    heading_level=len(heading.group(1)),
                    runs=parse_inline(heading.group(2)),
                )
            )
            i += 1
            continue

        bullet = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        if bullet:
            blocks.append(
                MarkdownBlock(
                    kind=BlockKind.BULLET_ITEM,
                    list_indent=calc_list_indent(bullet.group(1)),
                    runs=parse_inline(bullet.group(2)),
                )
            )
            i += 1
            continue

        ordered = re.match(r"^(\s*)\d+\.\s+(.*)$", line)
        if ordered:
            blocks.append(
                MarkdownBlock(
                    kind=BlockKind.ORDERED_ITEM,
                    list_indent=calc_list_indent(ordered.group(1)),
                    runs=parse_inline(ordered.group(2)),
                )
            )
            i += 1
            continue

        quote = re.match(r"^>\s?(.*)$", line)
        if quote:
            blocks.append(
                MarkdownBlock(
                    kind=BlockKind.BLOCKQUOTE,
                    runs=parse_inline(quote.group(1)),
                )
            )
            i += 1
            continue

        paragraph_lines = [line]
        i += 1
        while (
            i < len(lines)
            and lines[i].strip()
            and not is_block_start(lines[i])
        ):
            paragraph_lines.append(lines[i])
            i += 1

        blocks.append(
            MarkdownBlock(
                kind=BlockKind.PARAGRAPH,
                runs=parse_inline("\n".join(paragraph_lines)),
            )
        )

    return blocks


def block_plain_text(block: MarkdownBlock) -> str:
    if block.kind == BlockKind.CODE_BLOCK:
        return block.text + "\n"
    text = runs_to_plain(block.runs)
    if block.kind in (BlockKind.BULLET_ITEM, BlockKind.ORDERED_ITEM):
        # Google Docs derives list nesting from leading tabs at bullet-creation time.
        text = ("\t" * block.list_indent) + text
    return text + "\n"
