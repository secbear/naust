"""A Valheim-shaped backend for supervisor tests.

It prints the log lines the Valheim adapter understands, reacts to SIGINT the
way the real server does (save, then exit), and can be told to misbehave in
each way the drain contract must survive. Player activity is driven over
stdin so tests control timing exactly:

    join NAME OWNER      ->  Got character ZDOID from NAME : OWNER:1
    die NAME             ->  Got character ZDOID from NAME : 0:0
    leave OWNER CONN     ->  RPC_Disconnect / cleanup for OWNER / Closing socket CONN
    autosave             ->  World saved ( 5.000ms )
    say TEXT             ->  TEXT, verbatim
"""

import argparse
import os
import queue
import signal
import sys
import threading
import time
from pathlib import Path

BEHAVIOURS = (
    "normal",
    "crash-before-ready",
    "crash-after-ready",
    "hang-after-ready",
    "no-save",
    "corrupt-save",
    "ignore-term",
    "slow-save",
)


def emit(message: str) -> None:
    print(f"01/01/2026 00:00:00: {message}", flush=True)


def write_save(save_dir: Path, world: str, *, corrupt: bool, size: int) -> None:
    worlds = save_dir / "worlds_local"
    worlds.mkdir(parents=True, exist_ok=True)
    (worlds / f"{world}.db").write_bytes(b"" if corrupt else os.urandom(size))
    (worlds / f"{world}.fwl").write_bytes(b"fwl" * 16)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--save-dir", type=Path, required=True)
    parser.add_argument("--world", default="testworld")
    parser.add_argument("--behaviour", choices=BEHAVIOURS, default="normal")
    parser.add_argument("--ready-delay", type=float, default=0.0)
    parser.add_argument("--save-size", type=int, default=4096)
    parser.add_argument("--slow-save-seconds", type=float, default=5.0)
    args = parser.parse_args()

    print("Starting server PRESS CTRL-C to exit", flush=True)
    if args.behaviour == "crash-before-ready":
        emit("Failed to load world")
        return 3

    save_requested = False

    def on_sigint(*_: object) -> None:
        nonlocal save_requested
        save_requested = True

    if args.behaviour == "hang-after-ready":
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    else:
        signal.signal(signal.SIGINT, on_sigint)
        if args.behaviour == "ignore-term":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)

    time.sleep(args.ready_delay)
    emit("Game server connected")
    if args.behaviour == "crash-after-ready":
        return 4
    if args.behaviour == "hang-after-ready":
        while True:
            time.sleep(3600)

    commands: queue.Queue[str] = queue.Queue()

    def read_stdin() -> None:
        for line in sys.stdin:
            commands.put(line)

    threading.Thread(target=read_stdin, daemon=True).start()

    while not save_requested:
        try:
            line = commands.get(timeout=0.05)
        except queue.Empty:
            continue
        command, _, rest = line.strip().partition(" ")
        match command:
            case "join":
                name, _, owner = rest.partition(" ")
                emit("Server: New peer connected,sending global keys")
                emit(f"Got character ZDOID from {name} : {owner}:1")
            case "die":
                emit(f"Got character ZDOID from {rest} : 0:0")
            case "leave":
                owner, _, conn = rest.partition(" ")
                emit("RPC_Disconnect")
                emit(f"Destroying abandoned non persistent zdo {owner}:43 owner {owner}")
                emit(f"Destroying abandoned non persistent zdo {owner}:42 owner {owner}")
                emit(f"Closing socket {conn or 1}")
            case "autosave":
                write_save(args.save_dir, args.world, corrupt=False, size=args.save_size)
                emit("World saved ( 5.000ms )")
            case "say":
                print(rest, flush=True)
            case _:
                pass

    emit("Shutting down")
    if args.behaviour == "no-save":
        return 0
    if args.behaviour == "slow-save":
        time.sleep(args.slow_save_seconds)
    write_save(
        args.save_dir,
        args.world,
        corrupt=args.behaviour == "corrupt-save",
        size=args.save_size,
    )
    emit("World saved ( 61.499ms )")
    if args.behaviour == "ignore-term":
        while True:
            time.sleep(3600)
    return 0


if __name__ == "__main__":
    sys.exit(main())
