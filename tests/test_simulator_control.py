"""Focused tests for rebuilding idempotent multi-statement lab jobs."""

from scripts.common import simulator_control


class _Session:
    def __init__(self) -> None:
        self.submitted: list[str] = []

    def submit(self, sql: str) -> str:
        self.submitted.append(sql)
        return f"statement-{len(self.submitted)}"

    def wait(self, name: str, timeout: int = 180) -> dict:
        phase = "COMPLETED" if name in {"statement-1", "statement-2"} else "RUNNING"
        return {"status": {"phase": phase}}


def test_create_lab_objects_submits_ddl_then_restartable_insert(tmp_path, monkeypatch) -> None:
    reference = tmp_path / "demo-reference"
    reference.mkdir()
    (reference / "job-one.sql").write_text(
        "CREATE TABLE IF NOT EXISTS `sink` (`id` INT);\n"
        "INSERT INTO `sink` SELECT 1;\n"
    )
    (reference / "job-two.sql").write_text(
        "CREATE TABLE IF NOT EXISTS `sink_two` (`id` INT);\n"
        "INSERT INTO `sink_two` SELECT 2;\n"
    )
    session = _Session()
    monkeypatch.setattr(
        simulator_control,
        "LAB_BUILDS",
        [("job-one.sql", "sink"), ("job-two.sql", "sink_two")],
    )
    monkeypatch.setattr(simulator_control, "flink_session", lambda _tf: session)

    assert simulator_control.create_lab_objects({}, tmp_path)
    assert session.submitted == [
        "CREATE TABLE IF NOT EXISTS `sink` (`id` INT)",
        "CREATE TABLE IF NOT EXISTS `sink_two` (`id` INT)",
        "INSERT INTO `sink` SELECT 1",
        "INSERT INTO `sink_two` SELECT 2",
    ]


def test_create_lab_objects_rejects_wrong_terminal_phase(tmp_path, monkeypatch) -> None:
    reference = tmp_path / "demo-reference"
    reference.mkdir()
    (reference / "job.sql").write_text(
        "CREATE TABLE IF NOT EXISTS `sink` (`id` INT);\n"
        "INSERT INTO `sink` SELECT 1;\n"
    )
    session = _Session()
    monkeypatch.setattr(simulator_control, "LAB_BUILDS", [("job.sql", "sink")])
    monkeypatch.setattr(simulator_control, "flink_session", lambda _tf: session)
    monkeypatch.setattr(
        session,
        "wait",
        lambda name, timeout=180: {"status": {"phase": "RUNNING"}},
    )

    assert not simulator_control.create_lab_objects({}, tmp_path)
    assert session.submitted == ["CREATE TABLE IF NOT EXISTS `sink` (`id` INT)"]
