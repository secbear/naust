# Agent contract v1alpha1 and the game boundary

Status: accepted. Supersedes the ownership wording in ADR 0003 where they
differ; ADR 0003's drain and single-node policy stand.

## Question

How does Naust stay useful for one world on one VM today and still let an
orchestrator of any kind, on any substrate, make lifecycle decisions later
without a rewrite? And where exactly does a second game plug in?

## Evidence

- Agones proves that a sidecar with ready, health, shutdown, and player
  tracking is enough contract for a fleet controller. Our games cannot call
  an SDK, so the agent must infer those facts, but the contract shape holds.
- The current tracker encodes Valheim's disconnect semantics (an identity-free
  marker attributed by later cleanup). A second game would bend the generic
  core; the leak is the resolver's job, not the tracker's.
- Metrics alone cannot drive orchestration: sampled, lossy, no
  request/response. Kubernetes' status subresource and conditions are the
  proven shape for level-triggered truth; CloudEvents for edge-triggered hints.

## Decision

- Naust owns the host side of the boundary and exposes five surfaces:
  commands (start, SIGTERM/`POST /v1/drain`), exit status, status document,
  CloudEvents to sinks, metrics and files. Full definition in
  `docs/architecture.md`.
- Layering per game: pure observer, stateful resolver, generic tracker.
  Valheim's correlation moves into its resolver.
- Games are declared by a profile with explicit capabilities; capabilities are
  published in status and gate what the agent will do on its own.
- Agent states are `STARTING/READY/DRAINING/STOPPED/FAILED`; `SLEEPING` and
  `WAKING` are orchestrator states and never appear in the agent.
- The idle policy stays in the agent as a default and can be disabled
  (orchestrator mode). No registry, scheduler, operator, or Control service in
  this repository.
- Python remains the implementation language; the contract is
  language-neutral so a port is a drop-in when a measurement justifies it.

## Enforcement

Adding a game must touch only `games/<name>/` and tests; the toy adapter test
guards the tracker. Status and event schemas are versioned `naust/v1alpha1`
and tested as JSON. The migration order in `docs/architecture.md` keeps the
suite green at every step.

## Revisit when

- A second game arrives: the first real test of the profile and resolver split.
- Someone builds a Kubernetes or container substrate: promote the schemas to
  `v1beta1` after their feedback, not before.
- Sidecar cost is measured to matter, or a UDP data-path component is built:
  the port criteria in the architecture document.
