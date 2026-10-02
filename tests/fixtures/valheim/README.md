# Valheim log fixtures

`presence-session.log` is a sanitized capture of one dedicated-server session.
It preserves the ordering and grammar needed by Project 1, including signed
numeric ZDOIDs, `0:0` death events, failed authentication, simultaneous players,
disconnect cleanup, readiness, and save completion.

All dates, player names, connection identifiers, ZDOIDs, paths, storage values,
and secret-adjacent lines are synthetic or redacted. The fixture is evidence for
observed line shapes; it is not proof that future Valheim versions keep the same
format.

Never replace it with a raw runtime capture. Before adding a fixture:

1. Copy the source outside the repository.
2. Replace player, account, host, network, filesystem, and world identifiers with
   stable synthetic values.
3. Redact credentials, join/session codes, and password-related lines entirely.
4. Preserve structural features that the parser must learn: signs, separators,
   zero values, repeated identifiers, and event order.
5. Search the result for the original identifiers and common secret forms.
6. Review the diff manually before staging it.

Root `*.log` files are ignored. Only deliberately reviewed fixtures below
`tests/fixtures/` may be tracked.

`crossplay-session.log` is a sanitized capture of a `-crossplay` start on the
1.0-eve build (l-0.221.12, network version 36), taken on 2026-09-06 from a
real host with a working PlayFab plugin. It preserves the PlayFab login and
registration sequence and the three join-code line shapes: the early line
whose code is empty, `Session "X" registered with join code NNNNNN`, and the
periodic `Session "X" with join code NNNNNN and IP ... is active with N
player(s)`. The IP, PlayFab identities, lobby id, and the join code itself
are replaced.

`release-1.0-session.log` is a sanitized capture of the first fresh-world
start on the 1.0 release build (l-1.0.7, network version 39), taken on
2026-09-09: world generation, PlayFab registration and join code, then the
drain with the five-step save that replaced `World saved ( N ms )`. The
IP, PlayFab identities, lobby and network ids, and the join code are
replaced.

`crossplay-timeout.log` is a sanitized excerpt of a two-player crossplay
session on the 1.0.16 build, taken on 2026-09-29: the joins, a PlayFab
network error the server recovered from, deaths and respawns, the
ten-minutely `Connections N ZDOS` count (and a dungeon's bare `Connections 4`
beside it), then both players lost to `ZRpc timeout detected` with no
`RPC_Disconnect`, their cleanup, and the empty server's zero counts. Player
names, owners, PlayFab peer ids, network and lobby ids, and the join code
are replaced; lines between the excerpts are omitted, and the first count
line's ZDO figures are synthetic.
