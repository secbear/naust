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
