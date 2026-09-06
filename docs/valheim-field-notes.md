# Valheim field notes

What the dedicated server actually does, as observed. The first adapter was
written against these facts; the ones that shaped Naust's boundaries are
called out.

## Two networking modes, and they are not a flag

**Steam-direct.** The server binds UDP on the game port (2456 by default) and
the Steam query port, which is always game port + 1. Players connect to an
address. Packets reach your infrastructure, so an always-on component could
hold those ports while the world sleeps, see a join attempt, and wake it.
Naust does not build that component; the world's `mode` and ports are in
its configuration so that whoever does has what they need.

**Crossplay (`-crossplay`).** The server dials out to a PlayFab Party relay
and players connect to the relay. Nothing inbound reaches the host, so
wake-on-connect is impossible by construction, and the world is reachable
only through a six-digit join code that PlayFab issues on every start.
Crossplay is required for Xbox, Game Pass, Microsoft Store, PlayStation, and
Switch players. BepInEx does not load in this mode.

The consequence for anything that sleeps and wakes a crossplay world: the
join code changes on every restart, so publishing it is not a convenience,
it is the product. Naust captures it from the log and emits `backend.join`
before the world is announced ready.

## Presence comes from the log, and only from the log

There is no RCON, no query port under crossplay, and no player list. The
server logs a `Got character ZDOID from <name> : <owner>:<object>` line on
spawn and respawn, an identity-free `RPC_Disconnect`, then cleanup lines
naming the departed owner, then `Closing socket`. ADR 0002 records how those
become joins and leaves without guessing. The `Connections N ZDOS` line
appears about once per session and is not a signal.

## Saving

`SIGINT` makes the server save and exit; the log reports
`World saved ( N ms )` first. The world is a pair, `<name>.db` and
`<name>.fwl`, and they must travel together. The game keeps its own rolling
backups beside them (`<name>_backup_*`), which are safe to copy mid-session;
the live pair is not.

## Versions

`Valheim version: l-0.221.12 (network version 36)` is logged at start. A
client on another version gets a connection failure with no useful message,
which is the most common "the server is broken" report; surface the version
wherever status is shown.

## Sizing

The simulation runs on one thread. Clock speed matters, cores do not; below
roughly 3 GHz players report rubber-banding as a network problem. Memory
grows with the world's age and explored area, not with the player count.
The libraries behind crossplay (`libparty.so`) need `libpulse` and
`libatomic` present on the host.
