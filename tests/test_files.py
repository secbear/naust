"""The pre-start check and the verified-save marker."""

import json
from pathlib import Path

from naust.agent.files import marker_path, preflight, read_marker, write_marker
from naust.agent.supervisor import SaveFiles


def files(tmp_path: Path) -> SaveFiles:
    worlds = tmp_path / "worlds_local"
    return SaveFiles((worlds / "w.db", worlds / "w.fwl"))


def write(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def test_fresh_world_may_start(tmp_path: Path) -> None:
    assert preflight(files(tmp_path), None) is None


def test_half_present_world_is_refused(tmp_path: Path) -> None:
    write(files(tmp_path).paths[0], 100)

    problem = preflight(files(tmp_path), None)

    assert problem is not None
    assert "missing: w.fwl" in problem


def test_empty_file_is_refused(tmp_path: Path) -> None:
    write(files(tmp_path).paths[0], 100)
    write(files(tmp_path).paths[1], 0)

    problem = preflight(files(tmp_path), None)

    assert problem is not None
    assert "w.fwl is empty" in problem


def test_marker_round_trip_and_shrink_detection(tmp_path: Path) -> None:
    f = files(tmp_path)
    for path in f.paths:
        write(path, 1000)
    marker = marker_path(tmp_path / "state", "w")

    write_marker(f, marker)

    recorded = read_marker(marker)
    assert recorded is not None
    assert recorded["kind"] == "VerifiedSave"
    assert [e["bytes"] for e in recorded["files"]] == [1000, 1000]
    assert preflight(f, marker) is None

    write(f.paths[0], 1200)  # grew: normal
    assert preflight(f, marker) is None
    write(f.paths[0], 10)  # shrank far below verified: a bad restore
    problem = preflight(f, marker)
    assert problem is not None
    assert "below half" in problem


def test_missing_or_corrupt_marker_is_ignored(tmp_path: Path) -> None:
    f = files(tmp_path)
    for path in f.paths:
        write(path, 10)
    marker = tmp_path / "marker.json"

    assert read_marker(marker) is None
    assert preflight(f, marker) is None
    marker.write_text("not json")
    assert read_marker(marker) is None
    assert preflight(f, marker) is None
    marker.write_text(json.dumps(["not", "a", "dict"]))
    assert read_marker(marker) is None


# ---- directory layouts (Valheim 1.0) ---------------------------------------


def folder(tmp_path: Path) -> SaveFiles:
    return SaveFiles(
        directory=tmp_path / "worlds_local" / "w", patterns=("*.fwl2", "*.db2", "*.ok")
    )


def write_generation(directory: Path, n: int, db_bytes: int = 1000) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for suffix, size in (("fwl2", 52), ("db2", db_bytes), ("ok", 4)):
        (directory / f"_main.{n}.{suffix}").write_bytes(b"x" * size)


def test_folder_world_fresh_or_generated_but_unsaved_may_start(tmp_path: Path) -> None:
    f = folder(tmp_path)
    assert preflight(f, None) is None
    f.directory.mkdir(parents=True)
    (f.directory / "_main.0.fwl2").write_bytes(b"x" * 52)
    assert preflight(f, None) is None


def test_folder_world_data_without_header_is_refused(tmp_path: Path) -> None:
    f = folder(tmp_path)
    f.directory.mkdir(parents=True)
    (f.directory / "_main.1.db2").write_bytes(b"x" * 1000)

    problem = preflight(f, None)

    assert problem is not None
    assert "no *.fwl2 header" in problem


def test_folder_marker_round_trip_and_shrink_detection(tmp_path: Path) -> None:
    f = folder(tmp_path)
    write_generation(f.directory, 1)
    marker = marker_path(tmp_path / "state", "w")

    write_marker(f, marker)

    recorded = read_marker(marker)
    assert recorded is not None
    by_pattern = {e["pattern"]: e for e in recorded["files"] if "pattern" in e}
    assert by_pattern["*.db2"]["bytes"] == 1000
    assert by_pattern["*.db2"]["files"] == 1
    assert preflight(f, marker) is None

    for p in f.resolve():
        p.unlink()
    write_generation(f.directory, 2, db_bytes=1500)  # grew: normal
    assert preflight(f, marker) is None
    for p in f.resolve():
        p.unlink()
    write_generation(f.directory, 3, db_bytes=10)  # shrank far below verified
    problem = preflight(f, marker)
    assert problem is not None
    assert "*.db2" in problem and "below half" in problem
