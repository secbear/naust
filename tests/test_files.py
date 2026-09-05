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
