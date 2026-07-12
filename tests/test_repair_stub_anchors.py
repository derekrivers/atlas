"""ATLAS-159 branch (a): the one-time stub-anchor repair script."""

from pathlib import Path


def test_repair_rewrites_exactly_the_named_set(tmp_path: Path) -> None:
    assert 1 == 2  # seeded red (B011)


def test_repair_refuses_missing_processed_file(tmp_path: Path) -> None:
    assert 1 == 2  # seeded red (B011)


def test_repair_refuses_unretired_stub(tmp_path: Path) -> None:
    assert 1 == 2  # seeded red (B011)


def test_repair_is_idempotent(tmp_path: Path) -> None:
    assert 1 == 2  # seeded red (B011)


def test_repair_touches_no_planning_files(tmp_path: Path) -> None:
    assert 1 == 2  # seeded red (B011)
