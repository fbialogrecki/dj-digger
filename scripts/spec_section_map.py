#!/usr/bin/env python3
"""Maintain the generated section map in PROJECT-SPECIFICATION.md.

Numbered Markdown headings are recognised in the form ``## 1. Title`` or
``### 1.2 Title``.  A large subsection may expose selected internal blocks by
placing ``<!-- spec-map-block: Block name -->`` immediately before the block.
Only those explicit markers are treated as blocks; ordinary bold text is not.

The script only rewrites the generated-map region and the optional, explicitly
marked total-line and map-limit values.  Everything else remains byte-for-byte
unchanged.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "PROJECT-SPECIFICATION.md"

MAP_BEGIN = "<!-- BEGIN GENERATED SECTION MAP -->"
MAP_END = "<!-- END GENERATED SECTION MAP -->"
TOTAL_BEGIN = "<!-- SPEC TOTAL LINES -->"
TOTAL_END = "<!-- END SPEC TOTAL LINES -->"
LIMIT_BEGIN = "<!-- SPEC MAP LIMIT -->"
LIMIT_END = "<!-- END SPEC MAP LIMIT -->"

HEADING_RE = re.compile(
    r"^(?P<marks>#{2,6})\s+(?P<number>\d+(?:\.\d+)*)(?:\.)?\s+"
    r"(?P<title>.+?)(?:\s+#+)?\s*$"
)
BLOCK_RE = re.compile(r"^<!--\s*spec-map-block:\s*(?P<title>.+?)\s*-->\s*$")


class SpecMapError(RuntimeError):
    """The specification cannot be updated without guessing."""


@dataclass(frozen=True)
class Section:
    number: str
    title: str
    level: int
    start: int
    end: int


@dataclass(frozen=True)
class Block:
    title: str
    owner: Section
    start: int
    end: int


def _replace_region(text: str, begin: str, end: str, content: str) -> str:
    if text.count(begin) != 1 or text.count(end) != 1:
        raise SpecMapError(f"expected exactly one {begin!r} and one {end!r}")
    before, remainder = text.split(begin, 1)
    _old, after = remainder.split(end, 1)
    return f"{before}{begin}\n{content.rstrip()}\n{end}{after}"


def _replace_value(text: str, begin: str, end: str, value: int) -> str:
    count = text.count(begin)
    if count == 0:
        return text
    if count != 1 or text.count(end) != 1:
        raise SpecMapError(f"expected at most one {begin!r}/{end!r} value marker")
    pattern = re.compile(re.escape(begin) + r"[^\r\n]*?" + re.escape(end))
    return pattern.sub(f"{begin}{value}{end}", text, count=1)


def _sections(lines: list[str]) -> list[Section]:
    headings: list[tuple[str, str, int, int]] = []
    for line_number, line in enumerate(lines, start=1):
        match = HEADING_RE.match(line)
        if match:
            headings.append(
                (
                    match.group("number"),
                    match.group("title").strip(),
                    len(match.group("marks")),
                    line_number,
                )
            )

    sections: list[Section] = []
    for index, (number, title, level, start) in enumerate(headings):
        end = len(lines)
        for _next_number, _next_title, next_level, next_start in headings[index + 1 :]:
            if next_level <= level:
                end = next_start - 1
                break
        sections.append(Section(number, title, level, start, end))
    return sections


def _blocks(lines: list[str], sections: list[Section]) -> list[Block]:
    markers: list[tuple[int, str, Section]] = []
    for line_number, line in enumerate(lines, start=1):
        match = BLOCK_RE.match(line)
        if not match:
            continue
        owners = [section for section in sections if section.start < line_number <= section.end]
        if not owners:
            raise SpecMapError(
                f"named block on line {line_number} is outside a numbered section"
            )
        owner = max(owners, key=lambda section: section.level)
        markers.append((line_number, match.group("title").strip(), owner))

    blocks: list[Block] = []
    for index, (marker_line, title, owner) in enumerate(markers):
        end = owner.end
        for next_line, _next_title, next_owner in markers[index + 1 :]:
            if next_owner == owner:
                end = next_line - 1
                break
            if next_line > owner.end:
                break
        start = min(marker_line + 1, end)
        blocks.append(Block(title, owner, start, end))
    return blocks


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _map_table(text: str) -> str:
    lines = text.splitlines()
    sections = _sections(lines)
    if not sections:
        raise SpecMapError("no numbered Markdown headings found")
    blocks = _blocks(lines, sections)
    blocks_by_owner: dict[Section, list[Block]] = {}
    for block in blocks:
        blocks_by_owner.setdefault(block.owner, []).append(block)

    rows = ["| § | Section | Lines |", "| --- | --- | --- |"]
    base_level = min(section.level for section in sections)
    for section in sections:
        indent = "↳ " * max(0, section.level - base_level)
        rows.append(
            f"| {section.number} | {indent}{_cell(section.title)} | "
            f"{section.start}–{section.end} |"
        )
        for block in blocks_by_owner.get(section, []):
            rows.append(
                f"| {section.number} · block | ↳ ↳ {_cell(block.title)} | "
                f"{block.start}–{block.end} |"
            )
    return "\n".join(rows)


def render(text: str) -> str:
    """Return the stable generated form without writing it."""

    current = text
    for _iteration in range(20):
        table = _map_table(current)
        updated = _replace_region(current, MAP_BEGIN, MAP_END, table)
        total = len(updated.splitlines())
        updated = _replace_value(updated, TOTAL_BEGIN, TOTAL_END, total)
        updated = _replace_value(updated, LIMIT_BEGIN, LIMIT_END, total)
        if updated == current:
            return updated
        current = updated
    raise SpecMapError("section map did not converge after 20 iterations")


def _short_header_and_map(text: str) -> str:
    lines = text.splitlines()
    purpose_line = next(
        (index for index, line in enumerate(lines) if line == "## Purpose of this file"),
        None,
    )
    if purpose_line is None:
        raise SpecMapError("missing '## Purpose of this file' heading")
    header = "\n".join(lines[:purpose_line]).rstrip()
    _before, remainder = text.split(MAP_BEGIN, 1)
    table, _after = remainder.split(MAP_END, 1)
    return f"{header}\n\n## Section map\n\n{table.strip()}\n"


def _read_spec() -> str:
    try:
        return SPEC_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SpecMapError(f"expected specification at {SPEC_PATH}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="fail if generated data is stale")
    mode.add_argument("--print-map", action="store_true", help="print only metadata and the map")
    args = parser.parse_args(argv)

    try:
        original = _read_spec()
        expected = render(original)
    except (OSError, SpecMapError) as exc:
        print(f"spec-section-map: {exc}", file=sys.stderr)
        return 2

    if args.print_map:
        try:
            sys.stdout.write(_short_header_and_map(expected))
        except SpecMapError as exc:
            print(f"spec-section-map: {exc}", file=sys.stderr)
            return 2
        return 0

    if args.check:
        if original == expected:
            print("PROJECT-SPECIFICATION.md section map is current.")
            return 0
        print("PROJECT-SPECIFICATION.md section map is stale.", file=sys.stderr)
        diff = difflib.unified_diff(
            original.splitlines(),
            expected.splitlines(),
            fromfile=str(SPEC_PATH),
            tofile=f"{SPEC_PATH} (generated)",
            lineterm="",
        )
        for line in diff:
            print(line, file=sys.stderr)
        return 1

    if original != expected:
        SPEC_PATH.write_text(expected, encoding="utf-8")
        print(f"Updated {SPEC_PATH.relative_to(ROOT)}.")
    else:
        print(f"{SPEC_PATH.relative_to(ROOT)} is already current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
