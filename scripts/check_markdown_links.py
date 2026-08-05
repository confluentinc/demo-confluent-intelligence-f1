"""Fail when tracked Markdown links point outside the tracked repository tree."""

from __future__ import annotations

import argparse
import html
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

REFERENCE = re.compile(r"^ {0,3}\[([^]]+)\]:\s*(?:<([^>]+)>|(\S+))")
ATX_HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$")
SETEXT_HEADING = re.compile(r"^ {0,3}(=+|-+)\s*$")
EXPLICIT_ANCHOR = re.compile(r"<(?:a\s+(?:name|id)|[^>]+\sid)=[\"']([^\"']+)[\"']", re.IGNORECASE)


@dataclass(frozen=True)
class Link:
    destination: str
    line: int


def _without_code(line: str) -> str:
    """Blank inline-code spans without changing character positions."""
    output = list(line)
    index = 0
    while index < len(line):
        if line[index] != "`":
            index += 1
            continue
        width = 1
        while index + width < len(line) and line[index + width] == "`":
            width += 1
        end = line.find("`" * width, index + width)
        if end < 0:
            break
        output[index : end + width] = " " * (end + width - index)
        index = end + width
    return "".join(output)


def _closing_bracket(text: str, start: int) -> int | None:
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "\\":
            continue
        if text[index] == "[":
            depth += 1
        elif text[index] == "]":
            depth -= 1
            if depth == 0:
                return index
    return None


def _inline_destination(text: str, start: int) -> tuple[str, int] | None:
    index = start + 1
    while index < len(text) and text[index].isspace():
        index += 1
    if index < len(text) and text[index] == "<":
        end = text.find(">", index + 1)
        return (text[index + 1 : end], end + 1) if end >= 0 else None

    beginning = index
    depth = 0
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                return text[beginning:index].rstrip(), index + 1
            depth -= 1
        elif char.isspace() and depth == 0:
            # The remaining text is an optional title. Find its closing paren.
            end = text.find(")", index)
            return (text[beginning:index], end + 1) if end >= 0 else None
        index += 1
    return None


def extract_links(text: str) -> list[Link]:
    """Extract inline and reference Markdown links with a small stateful parser."""
    lines = text.splitlines()
    references: dict[str, str] = {}
    fenced = False
    usable: list[tuple[int, str]] = []

    for number, raw in enumerate(lines, 1):
        stripped = raw.lstrip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = REFERENCE.match(raw)
        if match:
            references[match.group(1).strip().casefold()] = match.group(2) or match.group(3)
            continue
        usable.append((number, _without_code(raw)))

    found: list[Link] = []
    for number, line in usable:
        index = 0
        while index < len(line):
            opening = line.find("[", index)
            if opening < 0:
                break
            closing = _closing_bracket(line, opening)
            if closing is None:
                break
            label = line[opening + 1 : closing].strip()
            after = closing + 1
            if after < len(line) and line[after] == "(":
                parsed = _inline_destination(line, after)
                if parsed:
                    destination, index = parsed
                    found.append(Link(destination, number))
                    continue
            elif after < len(line) and line[after] == "[":
                ref_end = _closing_bracket(line, after)
                if ref_end is not None:
                    key = line[after + 1 : ref_end].strip() or label
                    if key.casefold() in references:
                        found.append(Link(references[key.casefold()], number))
                    index = ref_end + 1
                    continue
            elif label.casefold() in references:
                found.append(Link(references[label.casefold()], number))
            index = closing + 1
    return found


def _github_slug(heading: str) -> str:
    heading = html.unescape(re.sub(r"<[^>]+>", "", heading)).strip().lower()
    heading = re.sub(r"[^\w\- ]", "", heading)
    return re.sub(r"\s+", "-", heading)


def markdown_anchors(text: str) -> set[str]:
    anchors = {unquote(value).casefold() for value in EXPLICIT_ANCHOR.findall(text)}
    counts: dict[str, int] = {}
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = ATX_HEADING.match(line)
        heading = match.group(1) if match else None
        if not heading and index + 1 < len(lines) and SETEXT_HEADING.match(lines[index + 1]):
            heading = line.strip()
        if not heading:
            continue
        slug = _github_slug(heading)
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


def tracked_files(root: Path) -> set[PurePosixPath]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True, text=True
    )
    return {
        PurePosixPath(name)
        for name in result.stdout.split("\0")
        if name and (root / name).is_file()
    }


def check_links(root: Path, tracked: set[PurePosixPath]) -> list[str]:
    errors: list[str] = []
    anchor_cache: dict[PurePosixPath, set[str]] = {}
    markdown = sorted(path for path in tracked if path.suffix.casefold() == ".md")
    for source in markdown:
        source_text = (root / source).read_text(encoding="utf-8")
        for link in extract_links(source_text):
            destination = html.unescape(link.destination.strip())
            parsed = urlsplit(destination)
            if parsed.scheme or parsed.netloc or destination.startswith("//"):
                continue
            decoded_path = unquote(parsed.path)
            if decoded_path.startswith("/"):
                continue
            target = source if not decoded_path else source.parent.joinpath(decoded_path)
            target = PurePosixPath(*[part for part in target.parts if part not in (".", "")])
            if ".." in target.parts:
                normalized = (root / target).resolve()
                try:
                    target = PurePosixPath(normalized.relative_to(root.resolve()).as_posix())
                except ValueError:
                    errors.append(f"{source}:{link.line}: link escapes repository: {destination}")
                    continue

            directory_prefix = target.as_posix().rstrip("/") + "/"
            directory_members = [path for path in tracked if path.as_posix().startswith(directory_prefix)]
            anchor_target = target
            if target not in tracked and directory_members:
                readme = target / "README.md"
                anchor_target = readme if readme in tracked else target
            elif target not in tracked:
                errors.append(f"{source}:{link.line}: target is not tracked: {destination}")
                continue

            fragment = unquote(parsed.fragment).casefold()
            if fragment and anchor_target.suffix.casefold() == ".md":
                anchors = anchor_cache.setdefault(
                    anchor_target, markdown_anchors((root / anchor_target).read_text(encoding="utf-8"))
                )
                fragment = fragment.removeprefix("user-content-")
                if fragment not in anchors:
                    errors.append(f"{source}:{link.line}: anchor not found: {destination}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    errors = check_links(root, tracked_files(root))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("All relative links in tracked Markdown resolve to tracked targets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
