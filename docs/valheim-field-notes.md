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

`SIGINT` makes the server save and exit. Before 1.0 the log said
`World saved ( N ms )` and the world was a pair, `<id>.db` and `<id>.fwl`, in
`worlds_local/`. From 1.0 (l-1.0.7) the world is a folder,
`worlds_local/<id>/`, holding `_main.N.fwl2` (header: name and seed),
`_main.N.db2` (data), `_main.N.ok` (the game's completion marker), and
`*.chunk` files that are rewritten only when dirty; `N` increases on every
save. The log reports five steps and ends with
`World save (5/5) done. Total time [N ms]`. The folder must travel whole.

A world generated but never saved has only `_main.0.fwl2`. The game keeps
its own rolling backups beside the live world (`-backups`, `-backupshort`,
`-backuplong`), which are safe to copy mid-session; the live folder is not.

1.0's first start of a pre-1.0 world converts it (`ZNet.LoadOldWorld`), but
the first save then failed on the host that had `<id>.fwl.old` and
`<id>_backup_*` files beside the pair: `Error saving world! The file
'<id>_backup_<date>.fwl' already exists`, the old pair was moved to a backup
name, and the new folder stayed empty. Naust reports that line as a failed
save and refuses to call the drain clean. Keep an off-host copy of a
pre-1.0 world before its first 1.0 start.

## Versions

`Valheim version: l-1.0.7 (network version 39)` is logged at start (1.0 day
was l-1.0.7; the last pre-release build was l-0.221.12, network version 36). A
client on another version gets a connection failure with no useful message,
which is the most common "the server is broken" report; surface the version
wherever status is shown.

## Sizing

The simulation runs on one thread. Clock speed matters, cores do not; below
roughly 3 GHz players report rubber-banding as a network problem. Memory
grows with the world's age and explored area, not with the player count.
The libraries behind crossplay (`libparty.so`) need `libpulse` and
`libatomic` present on the host.
