# Naust

**Scale-to-zero for game servers that cannot integrate an SDK.**

Agones tells its games to call `Ready()`, `Shutdown()`, and
`PlayerConnect()`. Most dedicated servers will never make those calls, so
Naust infers the same facts from the outside: it launches the server, reads
its log, knows who is present, asks for a save and verifies the files
before it stops, and reports all of it through one contract that a systemd
unit, a cloud VM, or a Kubernetes operator can make decisions on.

The name is Old Norse for the boathouse where longships were hauled out of
the water for the winter and launched again when needed.

## What it does

- **Presence** from the server log, with a per-game resolver that turns
  ambiguous lines into joins and leaves without guessing.
- **Verified drain**: a save signal, the log's confirmation, a check of the
  world files, then a grace period, `SIGTERM`, and `SIGKILL` in that order.
  Exit status 0 means saved, verified, stopped; 1 means a human is needed.
- **Idle policy**: drain after a configured time with nobody present, or
  hand that decision to an orchestrator.
- **A host contract**: a status document with conditions on a unix socket,
  CloudEvents to webhook and Discord sinks, Prometheus metrics on localhost,
  `sd_notify`, and a documented on-disk layout.
- **A NixOS module** that runs each world as a systemd unit, keeps the
  server updated with steamcmd, and can power the host off after a clean
  drain.

Naust owns everything between the game process and the host boundary, and
nothing beyond it. How a machine starts, where backups go, and who may press
the button are substrates; [docs/architecture.md](docs/architecture.md)
explains the boundary and [ADR 0004](docs/decisions/0004-agent-contract-and-game-boundary.md)
why it sits there.

## Status

Valheim is the first and only game. The agent has run a crossplay world on
a NixOS host on Google Compute Engine: start, join code, presence, idle
drain with a verified save, operator stop, and poweroff. A sanitized capture
of that session is in `tests/fixtures/valheim/`. Not built, by choice: a
wake-on-connect gateway for Steam-direct worlds, object-store persistence,
and any orchestration above the contract.

## Try it

```sh
nix develop            # or: uv sync
naust parse tests/fixtures/valheim/presence-session.log
uv run pytest
```

To run a world on NixOS, import `nixosModules.naust` and set
`services.naust.worlds.<id>`; [docs/nixos.md](docs/nixos.md) has the
minimal configuration and every option.

## Adding a game

A game is a directory under `src/naust/games/<name>/` with three parts: an
observer that turns log lines into typed observations, a resolver that turns
observations into facts (`PlayerJoined`, `SaveCompleted`, `JoinInfo`, and so
on), and a profile that declares the launch command, the save method, the
files to verify, and the capabilities the agent may claim. The tracker,
supervisor, status document, and every sink are shared.
[docs/valheim-field-notes.md](docs/valheim-field-notes.md) shows what the
first adapter had to learn.

## Layout

| Path | What |
| --- | --- |
| `src/naust/agent/` | supervisor, presence tracker, status, events, sinks, surface, metrics |
| `src/naust/games/` | facts, profile, registry, and one directory per game |
| `src/naust/domain/` | the world configuration model |
| `nix/nixos/naust.nix` | the NixOS module |
| `docs/` | architecture, decisions, NixOS guide, field notes |
| `tests/` | unit and property tests, a fake backend, recorded fixtures |
