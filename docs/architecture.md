# Naust architecture

Naust is a sidecar for game servers that cannot integrate an SDK. Agones tells
its games to call `Ready()`, `Shutdown()`, and `PlayerConnect()`; Naust infers
those facts for games that will never make the call, and exposes them through
the same kind of contract, so anything from a systemd unit to a Kubernetes
operator can make lifecycle decisions on top.

This document is the target design. The [status section of the
README](../README.md#status) says how much of it exists; the decision records
in [`docs/decisions/`](decisions/) say why each piece looks the way it does.

## The one rule

**Naust owns everything between the game process and the host boundary, and
nothing beyond it.** Inside the boundary live the hard, game-specific,
safety-critical problems: is anyone here, is the save real, is it safe to
stop. Outside it live substrates: how a machine starts, where backups go, who
may press the button. Substrates are many and they change; the game does not
care which one you picked.

The boundary is a contract with five surfaces. Anything that speaks it can
orchestrate Naust. Naust never reaches through it.

| Surface | Direction | Form |
| --- | --- | --- |
| Commands | in | start the unit; SIGTERM or `POST /v1/drain` means drain |
| Exit status | out | 0 saved-verified-stopped; 1 needs a human; 2 configuration |
| Status | out, pulled | `GET /v1/status`: typed, level-triggered truth |
| Events | out, pushed | CloudEvents to configured sinks, sequence-numbered |
| Metrics and files | out | Prometheus text on localhost; a documented on-disk layout |

Events are hints; status is truth. A consumer that misses an event re-reads
status and is correct again. That rule is what lets the contract scale from
one systemd unit to a fleet controller without a message bus in between.

## Layers

```
                 game process stdout / query port / rcon
                                 |
        +------------------------v-------------------------+
        |  Observer      game-specific, pure               |  games/<name>/observer.py
        |  one line or one poll -> raw observation or None |
        +------------------------+-------------------------+
                                 |
        +------------------------v-------------------------+
        |  Resolver      game-specific, stateful           |  games/<name>/resolver.py
        |  raw observations -> presence facts:             |
        |  PlayerJoined, PlayerLeft, PlayerCount, Ready,   |
        |  SaveCompleted, JoinInfo, Version                |
        +------------------------+-------------------------+
                                 |
        +------------------------v-------------------------+
        |  Tracker       generic                           |  agent/presence.py
        |  set membership, bounds, transitions             |
        +------------------------+-------------------------+
                                 |
   +----------------+   +--------v---------+   +----------------------+
   |  Supervisor    |<->|  Agent runtime   |-->|  Sinks               |
   |  process,      |   |  status, policy, |   |  webhook (CloudEvents)|
   |  drain, verify |   |  surfaces        |   |  discord (formatted) |
   +----------------+   +------------------+   +----------------------+
                                 |
                  /v1/status  /v1/drain  /metrics  sd_notify  exit code
```

The observer knows a game's syntax. The resolver knows a game's semantics,
including the ugly parts: Valheim's identity-free disconnect marker that only
later cleanup lines can attribute to a player lives in Valheim's resolver, not
in the tracker. The tracker knows only sets. Adding a game adds an observer, a
resolver, a profile, and a fixture; it touches nothing else, and that is the
test the abstraction is held to.

## The game boundary: profile, adapter, capabilities

A game is described by a **profile** (declarative) and an **adapter** (code).

```python
GameProfile(
    name="valheim",
    steam_app_id=896660,
    launch=ValheimLaunch,            # world + launch config -> argv, cwd, env
    files=ValheimFiles,              # world -> the files that travel together
    save=SaveMethod.signal(SIGINT),  # or SaveMethod.command("save-all") over rcon
    join=JoinKind.CODE,              # or JoinKind.ADDRESS
    capabilities=Capabilities(
        presence="inferred",         # exact | inferred | count-only | none
        identity="name",             # stable-id | name | none
        save="signal",               # signal | command | autosave-only
        join="code",                 # code | address
        query=None,                  # a2s | minecraft-slp | None
        version="log",               # log | query | none
    ),
)
```

Capabilities are part of the status document. They tell an orchestrator how
much to trust what it sees. Presence that is *inferred* must never be acted on
without a grace period, and the agent enforces that minimum from the profile
alone; a game with `save="autosave-only"` can only be drained after its own
timer, and the agent's drain waits for it. Exposing as much as the game allows
means exposing how much of it can be believed.

Package layout:

```
src/naust/
  agent/        presence.py  supervisor.py  runtime.py  status.py  events.py  sinks/  surfaces/
  games/        profile.py  (GameProfile, Capabilities, SaveMethod, JoinKind, PresenceFact types)
  games/valheim/  observer.py  resolver.py  profile.py
```

`naust.agent` imports `naust.games.profile` and never a concrete game.
Concrete games import `naust.games.profile` and the observation vocabulary.
Selection happens once, in the composition root, by profile name.

## The contract, `naust/v1alpha1`

### Status

```json
{
  "apiVersion": "naust/v1alpha1",
  "kind": "BackendStatus",
  "world": "midgard",
  "game": "valheim",
  "sequence": 1842,
  "observedAt": "2026-09-12T03:10:00Z",
  "state": "READY",
  "conditions": [
    {"type": "Ready",        "status": "True",  "reason": "GameServerConnected", "since": "2026-09-12T02:41:12Z"},
    {"type": "Draining",     "status": "False"},
    {"type": "SaveVerified", "status": "True",  "reason": "PreStartCheck",       "since": "2026-09-12T02:40:58Z"},
    {"type": "VersionKnown", "status": "True",  "reason": "LogLine"}
  ],
  "backend":  {"pid": 1234, "startedAt": "2026-09-12T02:40:58Z", "version": "1.0.0"},
  "presence": {"count": 2, "players": [{"id": "PLAYER_A", "since": "2026-09-12T02:44:03Z"}],
               "quality": "inferred", "idleSince": null},
  "join":     {"kind": "code", "code": "604510"},
  "save":     {"lastCompletedAt": "2026-09-12T03:01:40Z", "lastDurationMs": 61.5,
               "files": [{"path": "worlds_local/midgard.db", "bytes": 188000000}]},
  "capabilities": {"presence": "inferred", "identity": "name", "save": "signal",
                   "join": "code", "query": null, "version": "log"},
  "game": {"valheim": {"zdoCount": 412390}}
}
```

States are the agent's, not the world's: `STARTING`, `READY`, `DRAINING`,
`STOPPED`, `FAILED`. A world's `SLEEPING` and `WAKING` describe a world with no
process and therefore belong to whatever orchestrates the agent. That split is
Agones' GameServer versus Fleet, and it is why an orchestrator can be added
later without touching the safety code.

Conditions follow the Kubernetes convention: a type, `True`/`False`/`Unknown`,
a machine-readable reason, and the time the status last changed. The `game`
block is a per-game extension for anything the adapter can see that has no
generic meaning.

### Events

CloudEvents 1.0, JSON structured mode, with a `naustsequence` extension so
consumers can detect gaps.

| Type | Data |
| --- | --- |
| `io.naust.backend.starting` | argv (redacted), profile, capabilities |
| `io.naust.backend.ready` | pid, startup seconds |
| `io.naust.backend.version` | version string as the game reports it |
| `io.naust.backend.join` | `{kind, code}` or `{kind, address, port}` |
| `io.naust.presence.changed` | count, players, joined, left, quality |
| `io.naust.save.completed` | duration ms, files with sizes |
| `io.naust.drain.started` | trigger: idle, signal, command |
| `io.naust.drain.finished` | outcome, detail, exit code, session summary |
| `io.naust.backend.exited` | exit code, last output lines |
| `io.naust.backend.failed` | reason, recent output |

`source` is `naust://<host>/worlds/<id>`. `drain.finished` carries a session
summary (duration, peak players, saves, last save duration, bytes written) so a
consumer with no metrics stack still learns what a session cost.

Sinks are pluggable and never block a drain: a bounded queue, retries with
backoff, and a delivery-failure counter. Two ship: `webhook` (CloudEvents to a
URL with a bearer token) and `discord` (the same events rendered as a short
human message to a Discord webhook). Discord is not a bot framework; it is a
formatter, and it exists because join-code delivery is what makes crossplay
usable. Secrets for sinks arrive as files, never as configuration values.

### Commands

Over a unix socket by default, optionally a localhost TCP port so container
probes can reach it.

| Route | Meaning |
| --- | --- |
| `GET /v1/status` | the document above |
| `GET /readyz`, `GET /healthz` | probe-shaped views of the same truth |
| `POST /v1/drain` | idempotent; same as SIGTERM |
| `POST /v1/save` | only when the profile's save capability is `command` |

There is no wake command. The agent does not exist while the world sleeps.

### Metrics

Prometheus text on a localhost TCP port, derived from status:
`naust_backend_state{state}` (labelled gauge), `naust_players`,
`naust_backend_ready`, `naust_save_duration_seconds` (histogram),
`naust_last_save_timestamp_seconds`, `naust_world_file_bytes{file}`,
`naust_backend_start_timestamp_seconds`, `naust_events_total{type}`,
`naust_event_delivery_failures_total{sink}`, and
`naust_build_info{version,game,game_version}`. Enough for a Grafana panel and
for a KEDA Prometheus trigger to scale a Deployment to zero, which is the
simplest orchestration anyone will write and needs no Control service.

### Host integration

- `Type=notify`: READY=1 when the game is ready, STATUS with the player count,
  STOPPING=1 and EXTEND_TIMEOUT_USEC during a drain.
- Exit status as above. A service manager must never blindly restart on 1.
- Files: the profile names the set that travels together. After a verified
  drain the agent writes `<stateDir>/<world>/last-verified.json` (paths, sizes,
  mtimes). Before start it refuses a half-present set or a file that has shrunk
  below half of its verified size; files newer than the marker are normal (an
  autosave followed by a crash) and start.

## Policies

The agent ships one policy, idle drain: start the timer at readiness, reset on
any transition to a non-zero count, honour the world's connection grace period
and the profile's minimum for inferred presence, then drain. Setting the idle
timeout to null disables it; that is **orchestrator mode**, in which the agent
reports and obeys but never decides. Everything else, from "start a second
world when this one is near capacity" to "snapshot after every save", is a
consumer of events and status and lives outside.

## Substrates

| Substrate | Wake | Drain | Sleep cost | Status |
| --- | --- | --- | --- | --- |
| NixOS module, on-demand VM | cloud API starts the host; `autoStart` starts the unit | idle policy, exit 0, `postDrainCommand`, `onDrained = "poweroff"` | one disk | built |
| NixOS module, always-on host | `systemctl start` from any trigger | same | zero | built |
| Container image | orchestrator starts the container; agent is PID 1 | SIGTERM; exit code drives restart policy | none | planned, thin |
| Kubernetes pod | Deployment 0→1 by an autoscaler or operator | SIGTERM with `terminationGracePeriodSeconds` above the drain budget; probes hit `/readyz` | none | not built; the contract is designed for it |

The Steam-direct gateway (wake on an incoming packet, A2S status in the server
browser) is a separate always-on component that speaks the same events. It is
not part of the agent and is deferred until Steam-direct demand exists.

## What Naust does not do

No registry, scheduler, fleet, operator, or Control service: those are
substrates above the contract. No object-store restore on the wake path: a
persistent disk is the source of truth and snapshots are backups, wired
through the pre-start and post-drain hooks. No mod management, no RCON for
games that lack it, no web UI, no multi-tenancy. No second game until someone
needs one; the boundary is proven by the toy adapter in the tests.

## Implementation language

Python, now. The agent is I/O-bound: it reads a few lines a second and makes a
few HTTP calls a minute. Its cost is roughly 40 MB of memory beside a game that
uses gigabytes. The contract is language-neutral by construction (JSON,
CloudEvents, HTTP over a socket, Prometheus text, signals, exit codes), so a
port is a drop-in. Port when a measurement says so: thousands of sidecars per
host, or a component in the UDP data path, which is the gateway and only the
gateway. A rewrite before that trades tested code for a smaller binary nobody
asked for.

## Patterns this follows

- **Agones SDK, inverted.** Same sidecar contract; the agent infers what
  Agones is told.
- **Ports and adapters.** Domain in the middle; observers in, sinks out, a
  launcher and a file store as ports; Valheim as one adapter set.
- **Anti-corruption layer.** The resolver keeps a game's vocabulary out of the
  model.
- **Operator pattern.** Conditions, level-triggered truth, edge-triggered
  hints; desired state belongs to whoever orchestrates.
- **CloudEvents**, **Kubernetes probes**, **sd_notify**: existing shapes for
  the surfaces instead of new ones.

## Migration from the current code

In order, each a small change with the tests kept green:

1. Split Valheim's disconnect correlation out of `PresenceTracker` into
   `games/valheim/resolver.py`; the tracker consumes `PlayerJoined`,
   `PlayerLeft`, `PlayerCount`. Move `agent/valheim.py` to `games/valheim/`.
2. Add `GameProfile` and `Capabilities`; the composition root selects by name.
   Add the pre-start file check and the `last-verified.json` marker.
3. Add the status document with conditions and the sequence counter; add
   CloudEvents with the `webhook` and `discord` sinks; add `sd_notify`.
4. Add the local HTTP surface and `/metrics`.
5. Extend the NixOS module: sinks with credential files, metrics port,
   orchestrator mode, `preStartCommand`.
6. Carry a session summary on `drain.finished`.

## Fit with the on-demand VM design

The single-node design (GCP `us-central1`, NixOS, Cloudflare Worker,
restic to R2, Grafana Alloy) maps onto the contract without a custom script:

| Design item | Naust surface |
| --- | --- |
| Join code posted to Discord | `discord` sink on `backend.join` |
| Worker status page and `/status` command | `webhook` sink to the Worker's event endpoint, latest event kept in KV |
| Idle shutdown | idle policy → exit 0 → `postDrainCommand` (restic) → `onDrained = "poweroff"` |
| Mid-session offsite backup | restic timer over the game's own rolling backups, never the live pair |
| Right-sizing telemetry | Alloy scrapes `/metrics`; node and per-thread metrics stay in Alloy |
| Session summary line | `drain.finished` data, rendered by the Discord sink |
| Build watcher | unchanged in the Worker; `backend.version` confirms the server really updated |
| Container hooks and the idle shell script | gone; the module and the agent replace them |
