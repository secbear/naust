# Project 0 implementation guide

This document explains the implementation boundaries established by Project 0.
The product requirements remain in the README; the consequential choices and
their revisit conditions live in `docs/decisions/0001-project-0-boundaries.md`.

## Startup flow

```text
Typer command
    -> records only CLI values the user actually supplied
    -> constructs NaustSettings with those values
       -> constructor/CLI overrides
       -> environment variables
       -> naust.toml
       -> model defaults
    -> configures structured logging once
    -> passes one narrow component config to the selected service stub
```

Typer options use `None` to mean “no override.” A real default on a Typer option
would always enter through the highest-priority constructor source and prevent
environment, TOML, and Pydantic defaults from participating.

Nested environment variables use two underscores. For example,
`NAUST_CONTROL__BIND_PORT=8200` addresses `control.bind_port`.

## Model ownership

| Model | Writer and lifetime | What it must not contain |
| --- | --- | --- |
| `NaustSettings` | Operator-supplied startup configuration | Lifecycle status and runtime handles |
| `WorldConfig` | Desired per-world configuration | Players, join code, errors, current save version |
| `WorldStatus` | Control-owned observation | CLI/TOML policy |
| Component `*Config` | Static process inputs | Mutable runtime observations |
| `AgentRuntime` | One running Agent process | Serialized configuration |

`WorldConfig` is a discriminated union. Steam-direct configuration owns a game
port and derives the query port. Crossplay configuration has neither public
port and cannot accept the Steam-only BepInEx field.

## Validation boundaries

One-field constraints live on their field types. Relationships inside one world
live on its variant. Relationships among worlds live on `NaustSettings`, which
can see the complete registry and rejects duplicate IDs, duplicate storage
prefixes, and overlapping public ports.

Distributed invariants are intentionally absent. A Pydantic type cannot prove
that two processes did not start the same backend or that an object upload is
durable; locks, idempotency, verification, and reconciliation arrive with the
projects that implement those behaviors.

## Storage boundary

Project 0 models one S3-compatible provider because that is the product's
durable source of truth. Endpoint and credentials are installation settings;
each world carries only a non-secret object prefix. That prefix defaults to
`worlds/<world-id>` and can be overridden for an existing object layout. A
local MinIO endpoint uses the same configuration shape for development. Project
0 performs no transfers.

Credential fields are removed—not merely masked—from the resolved configuration
logged by every component.

## Running the skeleton

The committed example contains one Steam-direct and one crossplay world:

```console
cp naust.example.toml naust.toml
uv run naust --help
uv run naust control
uv run naust control --port 8300
uv run naust agent
uv run naust gateway
```

Run the complete local gate with:

```console
uv run ruff format --check .
uv run ruff check .
uv run pytest
uv run pre-commit run --all-files
```

The service entry points exit immediately by design. Subprocess supervision,
log parsing, UDP sockets, object transfers, and lifecycle orchestration belong
to later projects.
