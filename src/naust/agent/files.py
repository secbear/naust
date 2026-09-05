"""On-disk safety for the files that travel together.

Two guarantees the host boundary promises:

- after a verified drain, a marker records what was verified, so backup tools
  and the next start have something to compare against;
- before a start, a half-present set or a file that has shrunk far below its
  verified size is refused. Files *newer* than the marker are normal (an
  autosave followed by a crash) and start.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from naust.agent.supervisor import SaveFiles

MARKER_NAME = "last-verified.json"


def marker_path(state_dir: Path, world_id: str) -> Path:
    return state_dir / world_id / MARKER_NAME


def write_marker(files: SaveFiles, marker: Path) -> None:
    """Record the sizes and mtimes of a just-verified save."""

    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "apiVersion": "naust/v1alpha1",
        "kind": "VerifiedSave",
        "verifiedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": [
            {"path": str(path), "bytes": path.stat().st_size, "mtime": path.stat().st_mtime}
            for path in files.paths
        ],
    }
    tmp = marker.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(marker)


def read_marker(marker: Path) -> dict[str, object] | None:
    try:
        loaded = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def preflight(files: SaveFiles, marker: Path | None, *, min_size_ratio: float = 0.5) -> str | None:
    """Return why the world must not start, or ``None`` if it may.

    A fresh world (no files at all) may start. A complete, non-empty set may
    start unless a file has shrunk below ``min_size_ratio`` of the size in
    the marker, which is how a bad restore looks.
    """

    present = [path for path in files.paths if path.exists()]
    if not present:
        return None
    if len(present) != len(files.paths):
        missing = ", ".join(p.name for p in files.paths if not p.exists())
        return f"world files are incomplete; missing: {missing}"
    for path in present:
        if path.stat().st_size == 0:
            return f"{path.name} is empty"
    if marker is None:
        return None
    recorded = read_marker(marker)
    if recorded is None:
        return None
    verified = {
        entry["path"]: int(entry["bytes"])
        for entry in recorded.get("files", [])
        if isinstance(entry, dict) and "path" in entry and "bytes" in entry
    }
    for path in files.paths:
        previous = verified.get(str(path))
        if previous and path.stat().st_size < previous * min_size_ratio:
            return (
                f"{path.name} is {path.stat().st_size} bytes, below half of the "
                f"{previous} bytes verified at the last drain"
            )
    return None
