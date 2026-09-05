# Presence evidence and the fail-awake policy

Status: accepted for Project 1. Revisit at the points named below.

## Question

How does the Agent decide who is present when the log never says "player X
left", and which way should it err when the evidence is incomplete?

## Evidence

- [Product §6.1](../../README.md#spec-6-1) requires log parsing because
  crossplay disables BepInEx and therefore RCON.
- `tests/fixtures/valheim/presence-session.log` shows that `RPC_Disconnect`
  carries no identity and is also emitted by failed logins (lines 229 and
  259) while another player is connected. The identity arrives afterwards in
  `Destroying abandoned non persistent zdo … owner <id>` lines, several per
  disconnect, and the sequence ends at `Closing socket <connection>`.
- The same fixture shows `Got character ZDOID from <name> : 0:0` on death and
  a fresh non-zero ZDOID on respawn.
- The periodic `Connections N ZDOS:…` line appeared once in an eleven-minute
  session. It is not frequent enough to be a primary idle signal.

## Decision

- **Parser and tracker are separate.** The adapter (`naust.agent.valheim`)
  turns one line into at most one immutable observation and holds no state.
  The tracker (`naust.agent.presence`) holds all state and returns a
  transition only when the player set changes.
- **A disconnect marker opens a correlation window; only owner-bearing cleanup
  closes it, at most once; the socket boundary discards it.** Cleanup that
  arrives with no open window, or for an owner nobody present has, is a
  no-op. A marker that never gets identity evidence evicts nobody.
- **Death and respawn are silent.** `0:0` never changes presence. A non-zero
  ZDOID for a known name refreshes the owner mapping without a transition,
  which also covers a reconnect that changed the owner.
- **Fail awake.** Every ambiguous case keeps the player present. An empty
  server kept awake costs money; a false-empty server can start a drain under
  somebody's feet.
- **Bounds are enforced by construction.** A join that would exceed
  `max_players` is counted in `rejected_joins` and ignored, so
  `0 <= count <= max_players` holds after every mutation. This case means the
  tracker's model is already wrong, and the diagnostic is the useful output.
- **Timestamps are stripped, not parsed.** No caller needs them; every parsed
  field is a compatibility promise.
- **Save duration is parsed.** Telemetry wants it, and the field is stable
  across the observed builds.

Rejected: evicting the least-recently-active player on a bare marker (the toy
exercise's rule, which the real log does not guarantee); polling A2S (returns
zero players under crossplay); any timer-based inference of leaves.

## Enforcement

- `tests/test_presence.py` names each rule; `tests/test_presence_properties.py`
  searches generated sequences for violations of the bounds, the
  one-marker-one-leave rule, idempotence, and stranger-cleanup safety.
- `tests/test_replay.py` replays the recorded fixture and asserts the
  `1, 2, 1, 0` timeline, readiness and save observed once, and that a toy
  adapter drives the same loop.
- The tracker asserts its own bounds after every observation.

## Revisit when

- A crossplay capture exists. The join-code pattern is documented as
  unverified until then, and the `Session … is active with N player(s)` line
  may turn out to be a cheap corroborating count.
- Valheim 1.0 (September 9, 2026) changes any line shape. The pattern table is
  one module; the tracker must not need to change.
- Project 2 feeds a live subprocess through the same loop. If it needs
  timestamps for anything, add them then with a stated requirement.
