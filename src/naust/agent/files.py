"""On-disk safety for the files that travel together.

Two guarantees the host boundary promises:

- after a verified drain, a marker records what was verified, so backup tools
  and the next start have something to compare against;
- before a start, a half-present set or files that have shrunk far below
  their verified size are refused. Files *newer* than the marker are normal
  (an autosave followed by a crash) and start.

Fixed files are compared one by one. A directory layout is compared per
pattern, by total bytes, because the game renumbers its files on every save.
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
    entries: list[dict[str, object]] = [
        {"path": str(path), "bytes": path.stat().st_size, "mtime": path.stat().st_mtime}
        for path in files.paths
    ]
    if files.directory is not None:
        totals = files.pattern_totals()
        entries += [
            {
                "directory": str(files.directory),
                "pattern": pattern,
                "bytes": totals[pattern],
                "files": len(files.matching(pattern)),
            }
            for pattern in files.patterns
        ]
    payload = {
        "apiVersion": "naust/v1alpha1",
        "kind": "VerifiedSave",
        "verifiedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": entries,
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

    A fresh world (nothing on disk) may start. A complete, non-empty set may
    start unless it has shrunk below ``min_size_ratio`` of what the marker
    recorded, which is how a bad restore looks. In a directory layout a
    world that was generated but never saved has only its header file and
    may start; a data file without its header may not.
    """

    problem = _preflight_fixed(files)
    if problem is not None:
        return problem
    problem = _preflight_directory(files)
    if problem is not None:
        return problem
    recorded = read_marker(marker) if marker is not None else None
    if recorded is None:
        return None
    entries = [e for e in recorded.get("files", []) if isinstance(e, dict) and "bytes" in e]
    verified_paths = {e["path"]: int(e["bytes"]) for e in entries if "path" in e}
    for path in files.paths:
        previous = verified_paths.get(str(path))
        if previous and path.stat().st_size < previous * min_size_ratio:
            return (
                f"{path.name} is {path.stat().st_size} bytes, below half of the "
                f"{previous} bytes verified at the last drain"
            )
    if files.directory is not None and files.matching(files.patterns[0]):
        verified_patterns = {e["pattern"]: int(e["bytes"]) for e in entries if "pattern" in e}
        totals = files.pattern_totals()
        for pattern, previous in verified_patterns.items():
            current = totals.get(pattern, 0)
            if previous and current < previous * min_size_ratio:
                return (
                    f"{pattern} in {files.directory.name}/ totals {current} bytes, below half "
                    f"of the {previous} bytes verified at the last drain"
                )
    return None


def _preflight_fixed(files: SaveFiles) -> str | None:
    present = [path for path in files.paths if path.exists()]
    if not present:
        return None
    if len(present) != len(files.paths):
        missing = ", ".join(p.name for p in files.paths if not p.exists())
        return f"world files are incomplete; missing: {missing}"
    for path in present:
        if path.stat().st_size == 0:
            return f"{path.name} is empty"
    return None


def _preflight_directory(files: SaveFiles) -> str | None:
    if files.directory is None or not files.patterns:
        return None
    header = files.patterns[0]
    if not files.matching(header):
        if any(files.matching(p) for p in files.patterns[1:]):
            return f"{files.directory.name}/ has world data but no {header} header"
        return None
    for pattern in files.patterns:
        for path in files.matching(pattern):
            if path.stat().st_size == 0:
                return f"{files.directory.name}/{path.name} is empty"
    return None
