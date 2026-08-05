from pathlib import Path, PurePosixPath

from scripts.check_markdown_links import check_links, extract_links, markdown_anchors


def test_extracts_inline_reference_and_encoded_links_but_skips_code():
    text = """\
[inline](docs/Some%20File.md#use-it)
[reference][guide]
`[not a link](missing.md)`

[guide]: <docs/guide.md>
"""

    assert [(link.destination, link.line) for link in extract_links(text)] == [
        ("docs/Some%20File.md#use-it", 1),
        ("docs/guide.md", 2),
    ]


def test_github_heading_anchors_include_duplicate_suffixes():
    assert markdown_anchors("# Use it!\n\n## Repeat\n\n## Repeat\n") == {"use-it", "repeat", "repeat-1"}


def test_checker_accepts_anchors_url_encoding_and_directory_links(tmp_path: Path):
    (tmp_path / "README.md").write_text(
        "[file](docs/Some%20File.md#use-it) [directory](docs/) [local](#start-here)\n\n# Start here\n"
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "Some File.md").write_text("# Use it\n")
    tracked = {PurePosixPath("README.md"), PurePosixPath("docs/Some File.md")}

    assert check_links(tmp_path, tracked) == []


def test_checker_rejects_an_existing_but_untracked_target_fixture(tmp_path: Path):
    (tmp_path / "README.md").write_text("[draft](draft.md)\n")
    (tmp_path / "draft.md").write_text("# Not checked in\n")
    tracked = {PurePosixPath("README.md")}

    assert check_links(tmp_path, tracked) == ["README.md:1: target is not tracked: draft.md"]
