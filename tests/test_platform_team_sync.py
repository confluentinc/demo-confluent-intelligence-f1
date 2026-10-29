"""platform-team/ has to work when copied out of the repo, so its developer module
carries its own copies of the workshop's table DDL and of the seed INSERT. These
tests keep the copies in step with the code they were taken from."""

import re
from pathlib import Path

import pytest

from scripts.selfservice.seed import build_insert

ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = ROOT / "platform-team/modules/developer"
SEED_FILE = MODULE_DIR / "driver_race_history_seed.sql"
REGENERATE_SEED = f"uv run python -m scripts.selfservice.seed > {SEED_FILE.relative_to(ROOT)}"

# (table, the file its CREATE TABLE is copied from)
COPIED_TABLES = (
    ("car_telemetry", "terraform/modules/topics/main.tf"),
    ("race_standings", "terraform/modules/topics/main.tf"),
    ("driver_race_history", "terraform/self-service/main.tf"),
)

_HEREDOC = re.compile(r"<<-(\w+)\n(.*?)\n\s*\1$", re.DOTALL | re.MULTILINE)
_CREATE_TABLE = re.compile(r"CREATE TABLE (?:IF NOT EXISTS )?`(\w+)`")


def _create_tables(path: Path) -> dict[str, str]:
    """Every heredoc CREATE TABLE statement in a .tf file, keyed by table name."""
    tables = {}
    for _, body in _HEREDOC.findall(path.read_text()):
        match = _CREATE_TABLE.search(body)
        if match:
            tables[match.group(1)] = body
    return tables


def _normalize(sql: str) -> str:
    # The platform copies are all IF NOT EXISTS, so a re-apply is a no-op
    # (platform-team/tests checks that); self-service's driver_race_history isn't.
    return " ".join(sql.split()).replace("CREATE TABLE IF NOT EXISTS ", "CREATE TABLE ")


def _module_tables() -> dict[str, tuple[Path, str]]:
    return {
        table: (path, body) for path in sorted(MODULE_DIR.glob("*.tf")) for table, body in _create_tables(path).items()
    }


@pytest.mark.parametrize(("table", "source"), COPIED_TABLES, ids=[table for table, _ in COPIED_TABLES])
def test_platform_table_ddl_matches_its_source(table, source):
    source_tables = _create_tables(ROOT / source)
    assert table in source_tables, f"no CREATE TABLE `{table}` heredoc in {source}; update COPIED_TABLES"

    module_tables = _module_tables()
    assert table in module_tables, f"no CREATE TABLE `{table}` heredoc in {MODULE_DIR.relative_to(ROOT)}/*.tf"

    module_path, module_sql = module_tables[table]
    assert _normalize(module_sql) == _normalize(source_tables[table]), (
        f"CREATE TABLE `{table}` in {module_path.relative_to(ROOT)} has drifted from {source}. "
        f"Copy the statement from {source}, keeping IF NOT EXISTS."
    )


def test_platform_seed_matches_build_insert():
    # Byte for byte: any change to the file, even a trailing newline an editor
    # adds, changes the statement Terraform plans for every applied stack.
    assert SEED_FILE.is_file(), f"{SEED_FILE.relative_to(ROOT)} is missing. Generate it with: {REGENERATE_SEED}"
    assert SEED_FILE.read_bytes() == build_insert().encode(), (
        f"{SEED_FILE.relative_to(ROOT)} has drifted from scripts/selfservice/seed.build_insert(). "
        f"Regenerate it from the repo root with: {REGENERATE_SEED}"
    )
