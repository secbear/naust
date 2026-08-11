# Project 0 boundaries

Status: accepted for Project 0. Revisit only at the project named by each
decision; do not generalize the skeleton in anticipation of it.

## Identity

A world has a separate immutable, URL-safe `id` and a human-readable `name`.
Renaming a world therefore does not change API routes, object prefixes, or log
correlation. Revisit when Project 4 introduces registry update operations.

## Owner

`owner` is a required non-empty label, not an authenticated account or an
authorization principal. This preserves the product vocabulary without pulling
the explicitly excluded multi-tenancy problem into v1. Revisit only if the
product adds an identity and authorization model.

## Storage and resources

The S3-compatible endpoint, bucket, region, and credentials are installation
settings. A non-secret object prefix and resource intent belong to each world.
The prefix is derived as `worlds/<world-id>` unless an existing object layout
requires an override. Credentials are loaded once and never serialized in
resolved config. This boundary is provider-neutral within the S3-compatible
protocol: callers supply the endpoint and signing region instead of relying on
AWS endpoint discovery. Revisit in Project 5 if a demonstrated need exists for
per-world providers.

## Networking modes

World configuration is a discriminated union. Steam-direct owns a game port and
derives its query port as `game_port + 1`; crossplay has neither field. The
Steam-only BepInEx option cannot be represented on a crossplay model. Registry
validation, rather than an individual world, owns cross-world port collisions.
Revisit when Project 3 supplies measured gateway requirements.

## Desired configuration versus observed status

`WorldConfig` is user-authored and immutable after validation. `WorldStatus` is
separate and Control-owned; it is not reachable from `NaustSettings`, so CLI,
environment, and TOML cannot set lifecycle state. Project 0 status contains only
the lifecycle enum. Revisit in Project 4 when Control persists observations.

## State-specific data

Project 0 does not invent players, timestamps, join codes, errors, conditions,
or save versions. Later projects add each field beside the behavior that proves
its meaning. This avoids optional-field combinations with no current invariant.

## Initial state and save reference

An installation may start with an empty registry, which lets each component be
configured and health-checked before worlds are registered. Project 0 does not
create worlds or silently choose an initial status. An imported world may enter
`SLEEPING` only after a later persistence boundary has verified a durable save
reference. Brand-new world creation remains an explicit open product decision
for Project 4/5; a missing save must never generate a new world accidentally.
