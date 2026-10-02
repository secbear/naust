# Departures without a marker, and the server's own count

Status: accepted. Amends ADR 0002: replaces the correlation window and the
"one marker, one leave" rule. Fail awake, death and respawn handling, and the
tracker's bounds are unchanged.

## Question

ADR 0002 counted a leave only when owner-bearing cleanup followed an
`RPC_Disconnect` marker. On 2026-09-29 both players of a crossplay session
dropped by network timeout; neither removal logged `RPC_Disconnect`, the
cleanup was ignored, and the world stayed "occupied" for two days on a VM
billed by the hour. What evidence of a departure is complete, and what
catches the departures we have not seen yet?

## Evidence

From the 1.0.16 server log and the decompiled `assembly_valheim`:

- Every removal path calls `ZNet.Disconnect(peer)`: `RPC_Disconnect` on a
  clean quit, `ZRpc.UpdatePing` closing the socket and logging
  `ZRpc timeout detected` on a lost connection, `InternalKick` logging
  `Kicking <name>`. Only the preamble differs.
- `Disconnect` calls `ZDOMan.RemovePeer`, which runs
  `RemoveOrphanNonPersistentZDOS`. That sweep destroys every non-persistent
  object that has no owner or whose owner `IsPeerConnected` says is gone,
  logging `Destroying abandoned non persistent zdo <id> owner <owner>`. It
  runs nowhere else.
- A character is a non-persistent object owned by its player's peer, so a
  departing player's character is in the sweep unless it was already
  destroyed (a player who quits while dead).
- A failed login is removed by the same call but never owned a character;
  its sweep names an owner nobody joined as.
- `Game.ServerLog` logs ` Connections N ZDOS:… sent:… recv:…` every 600 s
  (not once per session, as ADR 0002 recorded from an eleven-minute capture).
  N is `ZNet.GetPeerConnections()`: peers whose handshake completed, which
  happens before their character line.
- `Room` logs a bare `Connections N` during dungeon generation. PlayFab's
  `Player connection lost … now N player(s)` reported 2 after both players
  had dropped.

## Decision

- **A cleanup line naming a present player's current owner is that player's
  departure.** The game only writes it for owners it has disconnected. No
  marker is needed or consulted, so any removal path, including ones not yet
  seen, is covered. Cleanup naming a stranger or owner `0` removes nobody.
  A reconnect refreshes the owner, so a late sweep of the old connection
  cannot evict the new one.
- **A peer count of zero names everybody.** Every known player leaves and the
  count becomes 0. This is the backstop for a departure whose cleanup never
  named the player; it bounds a missed leave by the ten-minute interval.
- **Any other count names nobody.** It is passed on as `PlayerCount`, which
  the tracker uses only when it knows no names (the agent started
  mid-session), so such a world stays awake until the server says zero.
- `RPC_Disconnect` and `Closing socket` are no longer observed.

Rejected: treating `ZRpc timeout detected` as a second marker (it would fix
this incident and miss the next unseen path); trusting PlayFab's player
count; inferring departures from timers.

## Enforcement

- `tests/test_valheim_resolver.py` names each rule, including the incident,
  the reconnect race, and the zero count.
- `tests/test_presence_properties.py` checks over generated sequences that a
  present player is removed only by cleanup naming their current owner or by
  a zero count.
- `tests/test_replay.py` replays `crossplay-timeout.log` as captured, and
  again with every cleanup line deleted, so the backstop is tested alone.
- `tests/test_supervisor.py` drives a lost connection and the counts through
  the real supervisor loop.

## Revisit when

- A build changes `RemoveOrphanNonPersistentZDOS`, `ServerLog`, or the
  shape of either line. The observer's patterns and this ADR's evidence are
  the things to re-check against the decompiled source.
- A count of N > 0 with more names known than N turns out to be common. It
  means a missed departure among several players; the zero count only
  catches it once the server empties.
