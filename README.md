# Naust

**Scale-to-zero for stateful game servers.**

A design specification and a staged build guide.

---

## Status

The agent is built and tested: configuration and domain vocabulary; a
Valheim observer, resolver, and profile behind a game-neutral boundary; the
supervisor that launches a backend, tracks presence, and drains it with a
verified save; and the host contract from
[docs/architecture.md](docs/architecture.md): a status document with
conditions, CloudEvents to webhook and Discord sinks, a unix-socket command
surface, Prometheus metrics, sd_notify, and exit-status semantics.
`naust parse <logfile>` replays a capture; `naust agent --world <id>` runs a
world. A NixOS module runs worlds as systemd units on one host and can power
the host off after a clean drain, see [docs/nixos.md](docs/nixos.md). Design
decisions live in [docs/decisions/](docs/decisions/).

The gateway, Control, object-store persistence, a Discord bot, and the
Kubernetes projects below are optional substrates above that contract and are
not built. Nothing in this repository has yet been run against a real
dedicated server on Linux; the first crossplay capture is the next piece of
evidence.

## How to use this document

This document has three parts. You should not need to hunt through it: the maps
below give you a reading order, and every project links back to the exact
product requirements it implements.

| Part                                                     | Purpose                                                                              | Use it when                                                          |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------ | -------------------------------------------------------------------- |
| [Part I — Product specification](#product-specification) | Defines observable behavior, invariants, failure handling, and component boundaries. | You need to know what the system must do.                            |
| [Part II — Roadmap](#build-guide)                        | Orders the implementation into staged projects, each tied to the requirements it implements. | You are building or extending a component.                           |
| [Part III — Reference](#reference)                       | Collects libraries, Kubernetes concepts, and further reading.                        | You need to look something up.                                       |

---

<a id="product-specification"></a>

# Part I — Product Specification

### Specification map

| Section                                | Answers                                                              |
| -------------------------------------- | -------------------------------------------------------------------- |
| [§1 — What Naust is](#spec-1)          | What product are we building, and what is outside its boundary?      |
| [§2 — Vocabulary](#spec-2)             | What do the core domain terms mean?                                  |
| [§3 — Networking constraint](#spec-3)  | Why are Steam-direct and crossplay distinct product modes?           |
| [§4 — World lifecycle](#spec-4)        | What states, transitions, and invariants govern a world?             |
| [§5 — Components](#spec-5)             | Which component owns each responsibility?                            |
| [§6 — Behavior specification](#spec-6) | What must presence, sleep, drain, wake, gateway, and persistence do? |
| [§7 — Edge cases](#spec-7)             | Which failure and concurrency cases must become tests?               |
| [§8 — Gotchas](#spec-8)                | Which field constraints invalidate otherwise plausible designs?      |
| [§9 — Non-goals](#spec-9)              | What must v1 refuse to absorb?                                       |

The specification is intentionally strongest on observable behavior and safety.
It does not prescribe Python class names, file layout, or one uniquely correct
schema. Part II tells you which of those decisions you own and when to make
them.

<a id="spec-1"></a>

## 1. What Naust is

Naust runs game servers that sleep when nobody is playing and wake when somebody
wants to.

A conventional game server holds RAM and CPU 24/7. A friend group plays six to
ten hours a week. The other 95% of the time, the server is a paid-for process
simulating an empty forest.

Naust makes the server a _dormant artifact_ — a world file at rest in object
storage — and brings it back to life on demand, fast enough that nobody notices.
The name is Old Norse for the boathouse where longships were hauled out of the
water and stored over winter, then dragged back down and launched when needed.
The ship isn't gone. It's beached.

The first supported game is Valheim. The architecture is game-agnostic behind a
thin adapter interface.

### What it is not

- Not a hosting business. Naust is software you run; it is not a service someone
  buys from you.
- Not a replacement for `lloesche/valheim-server-docker` or any other server
  image. Naust wraps an existing image and adds a lifecycle around it.
- Not a matchmaker, not a fleet manager for ephemeral sessions, not Agones.
  Naust manages **persistent, named, individually-owned worlds** in a 1:1
  relationship with their server process.

<a id="spec-2"></a>

## 2. Vocabulary

| Term           | Meaning                                                                                           |
| -------------- | ------------------------------------------------------------------------------------------------- |
| **World**      | A named, persistent game world. The unit of everything. Has an owner, a config, and a save file.  |
| **Backend**    | The actual game server process for a world. Exists only while the world is awake.                 |
| **Gateway**    | The always-on component in the network data path. Sees packets, answers queries, triggers wakes.  |
| **Control**    | The always-on component that decides. Owns world state, orchestrates transitions, exposes an API. |
| **Agent**      | A per-backend companion that reports player presence and handles the save-on-shutdown sequence.   |
| **Wake**       | The transition from Sleeping to Awake.                                                            |
| **Drain**      | The transition from Awake to Sleeping: save, verify, persist, stop.                               |
| **Cold start** | Wall-clock time from wake trigger to the backend accepting players. The number you optimize.      |

<a id="spec-3"></a>

## 3. The constraint that shapes everything

Valheim has two mutually exclusive networking modes, and they behave completely
differently for our purposes.

<a id="spec-3-steam"></a>

### Steam-direct mode

The server binds UDP on the game port (default 2456) and the Steam query port
(2457). Players connect to your IP. Packets arrive at your infrastructure.

**This means wake-on-connect is possible.** The gateway can bind those ports
while the backend is asleep, observe a connection attempt, and use it as a wake
trigger.

<a id="spec-3-crossplay"></a>

### Crossplay mode (`-crossplay`)

The server makes an **outbound** connection to a Microsoft PlayFab Party relay
node in Azure. Players connect to the relay. The relay shuttles datagrams both
ways. Nothing inbound ever reaches your infrastructure.

**This means wake-on-connect is architecturally impossible.** There is no packet
to observe. There is no socket to hold. When the backend is asleep, the relay
session does not exist, and the world is simply not on the network.

Crossplay is required for Xbox, Game Pass, Microsoft Store, PS5, and Switch 2
players. As of the 1.0 release it is the default for any mixed-platform group,
which is most of them.

### Consequence: Naust has two wake paths, and both are mandatory

|                | Steam-direct                      | Crossplay                                   |
| -------------- | --------------------------------- | ------------------------------------------- |
| Wake trigger   | In-game join attempt (gateway)    | Out-of-band only (Discord, HTTP, CLI)       |
| Player sees    | Server browser entry, live status | Nothing until woken                         |
| Mods (BepInEx) | Yes                               | No                                          |
| Join address   | Stable IP:port                    | **Join code, regenerated on every restart** |

That last cell is the one people miss. **Crossplay join codes are not stable
across restarts.** A world that sleeps and wakes has a _different join code
every time_. For crossplay worlds the Discord integration is not a convenience
feature — it is the only viable way to distribute the current join code, and
shipping crossplay support without it produces a product that does not work.

> **Design implication.** The `World` configuration must carry a `mode` field,
> and large parts of the behavior spec branch on it. Do not treat crossplay as
> "Steam mode with a flag."

<a id="spec-4"></a>

## 4. World lifecycle

```
            ┌──────────────┐
┌──────────►│   SLEEPING   │◄─────────┐
│           └──────┬───────┘          │
│                  │ wake trigger     │
│                  ▼                  │
│           ┌──────────────┐          │
│           │    WAKING    │          │
│           └──────┬───────┘          │
│                  │ ready probe ok   │
│                  ▼                  │
│           ┌──────────────┐          │
│           │    AWAKE     │          │
│           └──────┬───────┘          │
│                  │ idle timeout     │
│                  ▼                  │
│           ┌──────────────┐          │
└───────────┤   DRAINING   ├──────────┘
   persist  └──────┬───────┘  persist
   succeeded       │ failure   succeeded
                   ▼
            ┌──────────────┐
            │    FAILED    │  (needs human)
            └──────────────┘
```

<a id="spec-4-states"></a>

### State definitions

**SLEEPING** — No backend process. World data at rest in the object store.
Gateway holds the ports (Steam-direct mode) and answers queries with a sleeping
status. Costs nothing but a few kilobytes of gateway memory.

**WAKING** — Backend starting. World data being restored. Not accepting players
yet. Gateway answers queries with a waking status and an ETA. All further wake
triggers for this world are absorbed, not duplicated.

**AWAKE** — Backend accepting players. Gateway forwards traffic. Agent reports
presence. Idle timer running.

**DRAINING** — Idle timeout fired or an operator requested a stop. Force a save,
verify it, upload it, then terminate. **This state must complete before the
process dies.** It is the state where world corruption happens if you get it
wrong.

**FAILED** — A transition failed in a way retries won't fix: corrupted save,
version mismatch, missing world file, repeated crash-on-start. Naust stops
trying and surfaces the error. It does not silently retry forever, and it does
not overwrite a good save with a bad one.

<a id="spec-4-invariants"></a>

### Invariants

These hold at all times. Violating any of them is a bug regardless of what else
works.

1. **A world is never in two states at once.** Every transition is guarded by a
   per-world lock.
2. **Only one backend per world, ever.** Two processes writing one world file is
   corruption.
3. **The object store copy is never overwritten with an unverified save.**
   Verify first, upload second.
4. **A world with a player connected never enters DRAINING.**
5. **Wake is idempotent.** Six friends clicking join simultaneously produce one
   backend.
6. **A failed drain does not delete local state.** If the upload fails, the
   local save survives for manual recovery.

<a id="spec-5"></a>

## 5. Components

<a id="spec-5-gateway"></a>

### Gateway

Always-on. In the network data path. One instance serves many worlds.

Responsibilities:

- Bind the UDP game and query ports for every Steam-direct world.
- Answer Steam A2S queries on behalf of sleeping worlds, reporting status in the
  server name field.
- Detect connection attempts to sleeping worlds and signal Control.
- Forward datagrams bidirectionally once a backend is awake.
- Track last-packet-seen time per world as a coarse liveness signal.

Non-responsibilities: it does not make policy decisions, does not talk to the
object store, does not know how to start a backend. It observes and forwards,
and it asks Control for everything else.

Design target: under 20 MB resident, regardless of world count. This is the
component that is always running, so its footprint is the floor of your entire
cost model.

<a id="spec-5-control"></a>

### Control

Always-on. Not in the data path.

Responsibilities:

- Own the world registry and every world's state.
- Execute state transitions, holding the per-world lock.
- Enforce admission control: refuse or queue wakes when concurrency limits are
  reached.
- Expose an HTTP API (wake, sleep, status, list) and serve the Discord bot.
- Run the idle-timer loop.

<a id="spec-5-agent"></a>

### Agent

Runs alongside each backend, one per awake world. Dies with it.

Responsibilities:

- Watch the backend's log stream and maintain the current player set.
- Report presence transitions to Control.
- Detect readiness (the backend is now accepting players).
- On shutdown signal: trigger a save, verify it, upload it, then let the backend
  exit.
- Extract the crossplay join code when the backend emits it, and report it to
  Control.

**Why an agent rather than polling from outside?** Because Valheim has no native
RCON. See [Product §8 — Gotchas](#spec-8).

<a id="spec-6"></a>

## 6. Behavior specification

<a id="spec-6-1"></a>

### 6.1 Presence detection

Valheim ships no query interface for "who is connected." The dedicated server
has no built-in RCON — RCON exists only as a BepInEx plugin, and BepInEx does
not load in crossplay mode. **Therefore log parsing is the only presence
mechanism that works for all worlds**, and Naust must not depend on RCON.

The log is not a clean event stream. Observed behavior:

- A player entering the world produces a line containing
  `Got character ZDOID from <name> : <id>:<n>`.
- The _same line shape_ is emitted when a player **dies**, with the ZDOID `0:0`.
  A naive parser counts a death as a join.
- Disconnection produces `RPC_Disconnect` — **which does not identify the
  player**. It is the same line no matter who left.
- Connection setup produces a sequence: `Got connection SteamID <id>`,
  `Got handshake from client <id>`, `VERSION check their:X mine:Y`,
  `Server: New peer connected`.
- Correlating a disconnect to a specific player requires maintaining a live map
  of name → ZDOID from join events, and matching against subsequent
  zone-destruction events referencing that ZDOID.
- An unauthenticated connection can also emit `RPC_Disconnect`. That marker
  alone is not evidence that a player left; evicting an arbitrary known player
  would create a false-empty world and could start shutdown while someone is
  still playing.

**Required behavior.** The agent maintains a player set. It must:

- Treat `ZDOID ... : 0:0` as a death, not a join.
- Handle a player joining, dying, and respawning without double-counting.
- Treat an identity-free disconnect as pending evidence. Remove a player only
  after correlating cleanup evidence from the same disconnect sequence to a
  known non-zero ZDOID. End the pending sequence at its socket-close boundary so
  an unresolved marker cannot authorize a future removal.
- Ignore an unauthenticated disconnect rather than evicting an unrelated
  player. When evidence is incomplete, bias toward keeping the world awake.
- Recover correctly when it starts mid-stream and misses earlier events.
- Never let the player count go negative.
- Emit a `presence_changed` event only on genuine transitions, not on every log
  line.

> **Gotcha.** These log formats are from observed 0.2x server builds. Verify
> every one against a real 1.0 server before trusting it. Log formats are not an
> API and Iron Gate owes you nothing. Design the parser so patterns live in one
> table that can be swapped per game version.

<a id="spec-6-2"></a>

### 6.2 Idle detection and sleep

A world becomes eligible for DRAINING when the player count has been zero
continuously for `idle_timeout` (default 15 minutes).

Required behavior:

- The idle timer starts when the count reaches zero, and **resets on any player
  joining**.
- The timer does **not** run during WAKING. A world that takes 40 seconds to
  start does not get 40 seconds deducted from its idle budget.
- A **connection grace period** (default 3 minutes) applies after wake
  regardless of player count. A player who triggered a wake is loading; they
  have not appeared in the log yet. Without this grace period the world wakes
  and immediately sleeps, and the player who woke it gets a failed connect.
- Sleeping is **blocked** while any player is connected, unconditionally, even
  on operator request. An operator stop should drain gracefully or refuse, never
  yank.

<a id="spec-6-3"></a>

### 6.3 Drain

This is the sequence that protects people's hundred-hour bases. Get it right.

1. Enter DRAINING. Reject all incoming wake triggers for this world (they
   queue).
2. Signal the backend to save. Valheim autosaves on a timer (~20 min) and on
   clean shutdown; do not rely on the timer.
3. Wait for save confirmation in the log, with a timeout.
4. **Verify** the save on disk: both the `.db` and `.fwl` files exist, are
   non-zero, have a plausible size relative to the previous save, and have a
   modification time after the save was requested.
5. Upload the verified save to the object store as a **new versioned object**.
   Never overwrite in place.
6. Confirm the upload (read back size/etag).
7. Send SIGTERM to the backend. Wait for clean exit with a timeout.
8. If the process does not exit, SIGKILL — but only _after_ steps 4–6 succeeded.
9. Enter SLEEPING. Release queued wake triggers.

Failure handling depends on whether the failure is safe to retry:

- If save confirmation or local verification fails (steps 3–4), enter FAILED,
  keep the local copy, surface the error loudly, and do not kill the backend or
  upload anything.
- If the object store is temporarily unreachable or upload confirmation fails
  (steps 5–6), remain DRAINING, keep the backend and local copy, and retry with
  backoff. After a configurable retry budget is exhausted, enter FAILED with the
  local copy intact. Never turn a transient upload error into data loss.
- A terminal failure never deletes local state. Recovery is an explicit operator
  action, not an automatic transition that could overwrite the last good save.

> **Gotcha.** Valheim writes both a world database (`.db`) and a metadata file
> (`.fwl`), plus `.old` backups of each. They must travel together and be
> restored together. A `.db` restored with a mismatched `.fwl` is a broken
> world.

> **Gotcha.** The save pause on a large world is real — several seconds of
> frozen simulation. Your save timeout must accommodate a 500 MB world on slow
> storage, not just your test world.

<a id="spec-6-4"></a>

### 6.4 Wake

1. Receive a trigger (gateway packet, HTTP call, Discord command, CLI).
2. Take the per-world lock and branch on current state:
   - SLEEPING may proceed to admission control.
   - WAKING or AWAKE absorbs the duplicate and returns the current state.
   - DRAINING queues the trigger for release after the drain.
   - FAILED returns the current failure for explicit operator action. **No
     branch may start a second backend.**
3. Check admission: is there capacity for another concurrent world? If not,
   queue and report a position and estimate.
4. Enter WAKING.
5. Restore the world from the object store to local storage. Verify the
   download.
6. Start the backend.
7. Probe for readiness. Poll until the backend reports ready or the timeout
   expires.
8. For crossplay worlds: capture the join code from the log and publish it.
9. Enter AWAKE. Gateway begins forwarding.

**Failure handling.** A backend that exits during WAKING is retried with
exponential backoff, up to three attempts. After three, FAILED. Do not retry
forever — a version mismatch or corrupt save will never succeed, and a wake loop
burns CPU on every box in the fleet simultaneously.

<a id="spec-6-5"></a>

### 6.5 Gateway behavior for sleeping worlds

Two levels, and you should ship the first before attempting the second.

**Level 1 — trigger only.** Any packet arriving on a sleeping world's game port
triggers a wake. The player's connection attempt fails; they retry after ~30
seconds and get in. Trivial to implement, honest, and how most people will use
it anyway.

**Level 2 — status in the browser.** The gateway implements the Steam A2S query
protocol and answers `A2S_INFO` on behalf of the sleeping world, putting live
status in the server-name field:

```
⏳ Midgard — waking, ~18s
💤 Midgard — sleeping, join to wake
```

The player's own server browser becomes the loading spinner. Nobody has shipped
this for Valheim and it is the single most demoable thing in the project.

**A2S requirements.** The protocol is not simply request/response any more.
Valve added a challenge-response step to prevent reflection amplification
attacks: an initial query gets an `S2C_CHALLENGE` reply containing a token, and
the client must re-send the query with that token before the server answers. Any
query type may be answered with a challenge if the token is absent, wrong, or
expired. Your implementation must:

- Answer `A2S_INFO` with a challenge on first contact, then with real data on
  the challenged retry.
- Issue tokens that are unguessable and expire.
- Rate-limit per source address. You are writing a UDP service that emits a
  larger response than it receives; if you do not rate-limit, you have built a
  DDoS amplifier and someone will find it.
- Bind the **query port**, which is the game port + 1 (2457 for a server on
  2456). This trips people up constantly.

> **Aside (Rust).** This is the component that most wants porting. Answering A2S
> from a sleeping-world table is pure byte manipulation with a per-source rate
> limiter — a natural fit for a small `tokio` UDP task, and the place where a 20
> MB Python process becomes a 3 MB Rust one. Port it after you have a working
> Python version and a benchmark that shows the difference.

<a id="spec-6-6"></a>

### 6.6 Persistence

World data lives in an S3-compatible object store. Local disk is a cache, never
the source of truth.

- Objects are versioned. A new save is a new object; old versions are retained
  per a configurable policy.
- Restore verifies integrity (size and checksum) before starting a backend
  against the data.
- A world whose object-store copy is missing or corrupt goes to FAILED. It does
  not silently generate a fresh world — that reads to the user as "my base is
  gone."

> **Gotcha.** Do not use a Kubernetes PersistentVolumeClaim for world data. A
> PVC is billed while it exists, including while the Deployment sits at zero
> replicas. A design that scales pods to zero but keeps volumes attached has not
> scaled to zero; it has scaled to "still paying." Object store on both ends.

<a id="spec-7"></a>

## 7. Edge cases

Each of these should end up as a test.

| #  | Situation                                                       | Required behavior                                                                                                        |
| -- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| 1  | Six friends click join within two seconds                       | Exactly one backend starts. Five triggers absorbed.                                                                      |
| 2  | Player joins during DRAINING                                    | Trigger queues. Drain completes. Wake begins immediately after. Player retries and gets in.                              |
| 3  | Backend crashes 5s after wake                                   | Retry with backoff, max 3, then FAILED.                                                                                  |
| 4  | Save times out during drain                                     | FAILED. Local copy preserved. Nothing uploaded. Nothing killed.                                                          |
| 5  | Object store unreachable at wake                                | FAILED with a clear error. Do not start a backend on stale local data without an explicit flag.                          |
| 6  | Object store unreachable at drain                               | Keep local copy, retry with backoff, hold the world in DRAINING rather than losing the save.                             |
| 7  | Control restarts while worlds are awake                         | On boot, reconcile: discover running backends, re-attach agents, rebuild state. Do not assume everything is asleep.      |
| 8  | Gateway restarts while worlds are awake                         | Forwarding tables rebuild from Control. Brief packet loss is acceptable; a wrong-world forward is not.                   |
| 9  | Player connected but idle for hours (AFK)                       | World stays awake. Presence, not activity, is the signal. Optionally warn. Never kick by default.                        |
| 10 | Game version updates while world is asleep                      | Detect on wake. Update the image before starting. A mismatched version silently prevents joins, which reads as "broken." |
| 11 | Game version updates while world is awake                       | Do **not** hot-update. Flag it. Update on next natural drain.                                                            |
| 12 | Two Control replicas both try to wake one world                 | Leader election, or a distributed lock. Invariant 2 is not negotiable.                                                   |
| 13 | Disk full during restore                                        | Fail before starting the backend. A partial world file is worse than no world file.                                      |
| 14 | Crossplay world wakes                                           | New join code captured and published _before_ the world is announced as ready.                                           |
| 15 | Wake queued behind capacity limits, then the requester gives up | Queued wake expires after a TTL. Do not wake a world nobody is waiting for.                                              |
| 16 | Backend hangs — process alive, not responding                   | Readiness probe fails. Treat as crash. Do not leave a zombie holding a world lock.                                       |

<a id="spec-8"></a>

## 8. Gotchas

The field guide. Every one of these is something that will otherwise cost you a
weekend.

**Crossplay defeats wake-on-connect entirely.** Covered in
[Product §3 — Networking constraint](#spec-3). It is the single most important
fact in this document.

**Crossplay join codes change on every restart.** Scale-to-zero means restarts.
Automate join-code distribution or crossplay support is decorative.

**Valheim has no native RCON.** The RCON you find in hosting-panel docs is
either a BepInEx plugin or a panel wrapper around log parsing. The plugin route
requires BepInEx, which does not load in crossplay mode. Log parsing is the only
universal mechanism.

**A2S requires the challenge handshake.** Skipping it means the Steam browser
ignores you and you have built an amplification vector. Both bad.

**The query port is game port + 1.** Not the game port.

**Steam's server list is cached.** A server that just came up can take minutes
to appear in the browser. Your wake path must not depend on browser refresh.
Direct-connect works immediately; the list does not.

**Version mismatch fails silently.** Client and server on different versions
produces a connection failure with no useful error. It is the most common
support issue for every Valheim host. Surface the version prominently in status
output.

**`terminationGracePeriodSeconds` defaults to 30.** Your drain sequence is
save + verify + upload, which on a large world exceeds 30 seconds. The default
will SIGKILL you mid-save. Set it to 120 or more and make sure your drain
finishes inside it.

**BepInEx and crossplay are mutually exclusive.** Not a bug, not a version issue
— BepInEx hooks the Steam networking path, which crossplay does not use. Make
this a hard fork in your configuration model with a clear error, not a footnote.

**One LoadBalancer per world destroys the economics.** A cloud network load
balancer runs roughly $16–18/month. Two worlds and you have wiped out any
saving. One gateway, one load balancer, one IP, many worlds multiplexed by port.

**Valheim is single-thread bound.** More cores do not help. Clock speed does.
Below ~3.0 GHz you get rubber-banding, which users report as a network problem.
Your resource requests should reflect this: request one fast core, not four slow
ones.

**Memory grows with world size, not player count.** A ten-player fresh world is
cheaper than a two-player five-hundred-hour world. Size per world, not per user.

**UDP cannot be stalled.** There is no connection to hold open. The client sends
packets, gets nothing, and gives up on its own schedule. This is why Level 1
gateway behavior is "the first attempt fails" and why the A2S status trick
matters — you cannot make the client wait, so you make it _informed_.

<a id="spec-9"></a>

## 9. Non-goals for v1

Say no to all of these. They are how the project doesn't ship.

- A web UI. Discord and a CLI are enough.
- Multi-tenancy, billing, user accounts.
- Multi-region placement.
- Live migration between hosts.
- Mod management. Point at BepInEx and step away.
- Games other than Valheim — but _design the adapter interface_ so the second
  game is a contribution, not a rewrite.

---

<a id="build-guide"></a>

# Part II — Roadmap

Eleven staged projects, Project 0 plus Projects 1–10, in three phases. Phase A
produces something people can use on one host. Phase B makes it durable and
reachable. Phase C makes it Kubernetes-native. Projects 0 through 2 are built;
the rest are optional substrates above the agent contract described in
[docs/architecture.md](docs/architecture.md).

## Build map

“Product sources” are the behavioral requirements each project implements.

| Project                              | Product sources                                                                                                                                                                                                | Result                                     |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------ |
| [0 — Skeleton](#project-0)           | [Vocabulary](#spec-2), [networking modes](#spec-3), [lifecycle](#spec-4), [component boundaries](#spec-5), [sleep timing](#spec-6-2), [gateway ports](#spec-6-5), [persistence](#spec-6-6), [gotchas](#spec-8) | Stable configuration and domain vocabulary |
| [1 — Presence](#project-1)           | [Agent boundary](#spec-5-agent), [presence behavior](#spec-6-1)                                                                                                                                                | Pure parser and presence tracker           |
| [2 — Supervisor](#project-2)         | [Lifecycle](#spec-4), [Agent boundary](#spec-5-agent), [drain contract](#spec-6-3), [edge cases](#spec-7)                                                                                                      | Safe backend supervision and draining      |
| [3 — Gateway](#project-3)            | [Networking constraint](#spec-3), [Gateway boundary](#spec-5-gateway), [gateway behavior](#spec-6-5), [gotchas](#spec-8)                                                                                       | UDP activation, A2S, and forwarding        |
| [4 — Control](#project-4)            | [Lifecycle](#spec-4), [Control boundary](#spec-5-control), [idle behavior](#spec-6-2), [wake contract](#spec-6-4), [edge cases](#spec-7)                                                                       | Working single-node v0.1                   |
| [5 — Persistence](#project-5)        | [Drain](#spec-6-3), [wake](#spec-6-4), [persistence](#spec-6-6)                                                                                                                                                | Durable saves and measured cold starts     |
| [6 — Discord](#project-6)            | [Networking modes](#spec-3), [Agent boundary](#spec-5-agent), [wake](#spec-6-4), [edge cases](#spec-7)                                                                                                         | Viable crossplay wake and code delivery    |
| [7 — Kubernetes by hand](#project-7) | [Components](#spec-5), [drain](#spec-6-3), [persistence](#spec-6-6), [gotchas](#spec-8)                                                                                                                        | Manual Kubernetes deployment               |
| [8 — Operator](#project-8)           | [Vocabulary](#spec-2), [lifecycle](#spec-4), [components](#spec-5), [edge cases](#spec-7)                                                                                                                      | Declarative `World` API and controller     |
| [9 — KEDA scaler](#project-9)        | [Idle behavior](#spec-6-2), [Gateway boundary](#spec-5-gateway), [Control boundary](#spec-5-control)                                                                                                           | Push-driven 0↔1 scaling                    |
| [10 — Observability](#project-10)    | [Full product specification](#product-specification)                                                                                                                                                           | Measurements and dashboard |

## Design discipline used throughout

The guide gives you requirements, not hidden class diagrams. Keep four kinds of
statement separate:

| Kind                   | Meaning                                                         | What to do with it                                                          |
| ---------------------- | --------------------------------------------------------------- | --------------------------------------------------------------------------- |
| **Requirement**        | The product specification says the system must behave this way. | Implement it and trace it to a test.                                        |
| **Derived constraint** | A requirement logically forces a design property.               | Record the reasoning and enforce it.                                        |
| **Design decision**    | Several implementations could satisfy the requirements.         | Choose deliberately and record the trade-off.                               |
| **Open question**      | The product behavior is ambiguous or evidence is missing.       | Spike, measure, or choose a reversible assumption; do not invent certainty. |

For each meaningful decision, a five-line note is enough:

1. **Question:** what must be decided?
2. **Evidence:** which product requirement, experiment, or operational fact
   bears on it?
3. **Decision:** what are you choosing now, and which alternatives did you
   reject?
4. **Enforcement:** is the decision protected by a type, validator, transition,
   registry, lock, or reconciliation loop?
5. **Revisit when:** what new evidence would justify changing it?

Keep each kind of documentation in one home rather than copying requirements
between project notes:

| Artifact                                              | Recommended home                                       | Rule                                                                                               |
| ----------------------------------------------------- | ------------------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| Product behavior and safety requirements              | Part I of this README                                  | Edit the source requirement; link to it elsewhere instead of paraphrasing it into a second spec.   |
| Roadmap and acceptance criteria                       | Part II of this README                                 | Keep each requirement beside the project that first implements it.                                 |
| Architecture decisions                                | `docs/decisions/`                                      | One short record per consequential decision; link the requirement and state the revisit condition. |
| Captured logs, packet traces, and experiment metadata | `docs/evidence/` or a documented fixture directory     | Record version and reproduction context; redact secrets and player data.                           |
| Measurements and conclusions                          | The project-specific document named by its deliverable | Keep raw method and conditions beside the result, not only the headline number.                    |

Those paths are an organizational convention, not a request to create empty
folders in Project 0. Add an artifact when the project first produces it.

### Put each invariant at the narrowest honest boundary

“Make illegal states unrepresentable” is a direction, not permission to pretend
that a Python type can prove a distributed fact.

| Scope of invariant                    | Enforcement boundary                                | Naust example                                                   |
| ------------------------------------- | --------------------------------------------------- | --------------------------------------------------------------- |
| One value                             | Type or field constraint                            | Known mode, valid port, positive duration                       |
| Several fields in one record          | Cross-field validation or a tagged variant          | Steam-direct requires ports; crossplay is not Steam with a flag |
| Several worlds                        | Registry validation                                 | Unique world identity and non-overlapping public ports          |
| Change over time                      | State-transition operation                          | `AWAKE` cannot move to `DRAINING` while players are connected   |
| Several processes or external systems | Lock, idempotency, verification, and reconciliation | Only one backend; only a verified save becomes durable          |

When a fact has different writers or lifetimes, split it before choosing class
names. Desired configuration, observed status, secrets, and ephemeral process
handles are different kinds of state even when all four mention the same world.

---

<a id="project-0"></a>

## Project 0 — The skeleton

**Task:** Establish the repository, module boundaries, configuration pipeline,
and stable domain vocabulary used by every later project.

### Required result

- A `uv`-managed project with ruff and pytest wired into a pre-commit hook and a
  GitHub Actions workflow.
- A single `naust` entry point with three subcommands stubbed: `gateway`,
  `control`, `agent`.
- A validated configuration covering world identity, game mode, idle timeout,
  connection grace period, networking, object-store access, and resource intent.
- A lifecycle state type containing exactly `SLEEPING`, `WAKING`, `AWAKE`,
  `DRAINING`, and `FAILED`.
- Structured logging configured once and used everywhere, with secrets excluded
  from resolved-configuration output.

“Fully” means complete for the contract known at this stage. It does not mean
predicting every status field, queue record, Kubernetes condition, or metric
that later projects will add.

### Model the categories before the classes

Do not begin by placing every field in one settings object. First classify each
fact by writer and lifetime:

| Category                    | Contains                                                                             | Boundary                                                                   |
| --------------------------- | ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------- |
| World identity              | Stable identity, human-readable name, and the unresolved owner concept               | Persists across every sleep/wake cycle                                     |
| Desired world configuration | Mode, timing policy, world-specific networking, storage locator, resource intent     | Supplied by a user; read by all components; changed deliberately           |
| Observed world status       | Lifecycle state and, later, players, timestamps, join code, errors, and save version | Written authoritatively by Control from reported observations              |
| Process/global settings     | Service bind addresses, shared object-store endpoint, credentials, logging           | Belong to a Naust process or installation, not automatically to each World |
| Ephemeral runtime state     | Locks, subprocess handles, tasks, sockets, open files                                | Never serialized as part of the World contract                             |

The exact Python types and nesting are a design decision. The separation of
writers and lifetimes is not. In particular, configuration supplied by a user
must not silently become observed status, and a runtime lock must not leak into
a serialized model.

### Lifecycle contract

Use this table instead of inferring legal transitions from the diagram alone:

| From       | Trigger and precondition                                                                       | To         | If it cannot complete                                                                                          |
| ---------- | ---------- | -------------------------------------------------------------------------------------------------------------- |
| `SLEEPING` | Wake accepted and capacity available                                                           | `WAKING`   | Remain sleeping while queued, or surface a wake failure                                                        |
| `WAKING`   | Restore succeeds, backend is ready, and a crossplay join code has been published when required | `AWAKE`    | Retry eligible startup failures; enter `FAILED` when the retry policy is exhausted                             |
| `AWAKE`    | Idle timeout or operator drain, with zero connected players                                    | `DRAINING` | Refuse the transition while a player is connected                                                              |
| `DRAINING` | Save verified, durable upload confirmed, backend stopped                                       | `SLEEPING` | Retry transient persistence failures in place; enter `FAILED` on terminal failure without deleting local state |
| `FAILED`   | No automatic recovery is specified for v1                                                      | —          | Surface enough context for a human recovery decision                                                           |

Wake triggers are state-specific: start one wake from `SLEEPING`, absorb
duplicate triggers in `WAKING` or `AWAKE`, queue triggers received in
`DRAINING`, and report the existing failure in `FAILED`. A state enum prevents
invented state names; a transition boundary is still required to prevent
invented transitions.

### Configuration facts that are requirements

- The two modes are Steam-direct and crossplay. Their behavior branches at the
  product level; do not represent crossplay as “Steam plus one flag.”
- `idle_timeout` defaults to 15 minutes and connection grace defaults to 3
  minutes. Both are durations, not unitless numbers, and must be positive.
- A Steam-direct world has a game port and a query port; the query port is the
  game port plus one. Port range and collision checks need clear ownership.
- Crossplay does not have a stable public join address. Its restart-generated
  join code is observed status, never desired configuration.
- Persistence is S3-compatible and versioned. Credentials are secrets; a
  per-world object key or prefix is not. Decide which belongs to installation
  settings and which belongs to the World.
- Resource configuration expresses one fast CPU and memory sized for the world.
  Choose explicit units and reject zero or negative values.
- BepInEx and crossplay are an invalid combination if mod configuration is
  exposed at this stage.

### Decisions you own

Record these before implementation. The specification intentionally does not
choose them for you:

| Question                                                                             | Minimum evidence your decision should address                                                             |
| ------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------- |
| Is the name the durable identifier, or is there a separate immutable ID?             | Renames, object keys, API routes, and log correlation                                                     |
| What does `owner` mean in v1?                                                        | [Product §2](#spec-2) requires the concept; [Product §9](#spec-9) rejects user accounts and multi-tenancy |
| Which storage and resource fields are global versus per-world?                       | Secret scope, repeated configuration, and future CRD shape                                                |
| How are mode-specific network settings represented?                                  | Prevent irrelevant or contradictory combinations instead of accumulating optional fields                  |
| Is lifecycle status embedded in the World aggregate or stored beside desired config? | Different writers, persistence, restart reconciliation, and the later Kubernetes `spec`/`status` split    |
| What data accompanies each lifecycle state?                                          | Avoid impossible combinations without inventing fields that later projects have not justified             |
| What is a newly registered world's initial state and save reference?                 | The product describes existing persistent worlds but does not specify world creation                      |

These are design questions, not missing homework answers. Prefer a reversible
decision with a stated consequence over an elaborate abstraction justified only
by imagined future needs.

The Project 0 choices are recorded in
[`docs/decisions/0001-project-0-boundaries.md`](docs/decisions/0001-project-0-boundaries.md).
The completed skeleton is explained in
[`docs/project-0.md`](docs/project-0.md).

### Behavior

`naust --help` lists three subcommands. Each runs and exits cleanly, logging a
startup line with its resolved configuration. The precedence is explicit:
**flags > environment variables > configuration file > defaults**. Invalid
configuration produces a clear error naming the offending field, not a stack
trace, and resolved output never includes secret values.

Keep the component stubs honest: Gateway may observe and request, Agent may
report backend facts, and Control will eventually own lifecycle transitions.
Project 0 does not implement those behaviors yet.

### Acceptance criteria

- Precedence is tested at every layer, including a value supplied by all layers.
- Every constrained field has at least one rejected boundary case with a useful
  error message.
- Both valid networking modes are tested, along with invalid cross-field
  combinations.
- Lifecycle serialization rejects unknown state values.
- `--help` works for the root command and all three component subcommands.
- Each component logs the same resolved non-secret configuration shape and exits
  cleanly.
- The open decisions above exist in short decision notes with their enforcement
  boundary and revisit condition.

Do not pull forward subprocess supervision, log parsing, object transfers,
network sockets, or lifecycle orchestration. Those belong to later projects.

_This foundation is quiet work, but it keeps every later component speaking the
same language._

---

<a id="project-1"></a>

## Project 1 — Presence

**Task:** Build the log parser that determines who is in a world.

**Goals:**

- A pure function from a line to an optional event. No I/O.
- A `PresenceTracker` that consumes events and maintains a player set.
- A game-adapter interface so the pattern table is swappable.

**Behavior:**

The tracker exposes a current player set and a count, and emits transitions when
the set changes — not on every line.

It must correctly handle:

- A join (`Got character ZDOID from <name> : <id>:<n>` with a non-zero ZDOID).
- A death (the same line shape with ZDOID `0:0`) — not a join, not a leave.
- A respawn after death — not a double join.
- A disconnect marker carrying no identity, followed by identity-bearing cleanup
  lines.
- A failed connection that emits the same disconnect marker while another
  player remains online.
- Starting mid-stream, with joins that occurred before the tracker started.
- Interleaved events from multiple simultaneous players.
- Malformed and truncated lines, without raising.

The count never goes negative. The count never exceeds the configured maximum.
Both of those are assertions, not hopes.

Design the adapter interface now:

```
GameAdapter
  patterns       -> the line → event table
  ready_signal   -> what "accepting players" looks like in the log
  save_signal    -> what "save complete" looks like
  join_code      -> how to extract the crossplay code (may be None)
```

A second game should be a new adapter, not a new parser.

**Tests:** Feed recorded logs. Test each edge case above independently. Property
test: for any sequence of well-formed events, the count is in `[0, max]`.

**Extension:** A `naust parse <logfile>` command that replays a log and prints
the presence timeline. You will use this constantly while debugging, and it is
the first thing that makes the project feel real.

---

<a id="project-2"></a>

## Project 2 — Supervisor

**Task:** Build the agent — supervise a game server process through its full
lifecycle.

**Goals:**

- Start a backend, stream its output into the Project 1 tracker.
- Detect readiness.
- Execute [Product §6.3 — Drain](#spec-6-3).
- Expose presence and state to a caller.

**Behavior:**

`naust agent --world <name>` starts a backend and supervises it.

**Startup:** launch the process, begin streaming stdout, watch for the adapter's
ready signal, transition to ready. If the process exits before ready, report a
startup failure with the last N lines of output — not an empty error.

**Running:** feed lines to the tracker, publish presence changes, maintain a
bounded ring buffer of recent output for diagnostics.

**Drain,** on signal or request, in this exact order:

1. Request a save.
2. Wait for the save signal, with timeout.
3. Verify the save files on disk: both present, non-zero, size within a
   plausible band of the previous save, mtime after the request.
4. Signal the caller that the save is verified.
5. SIGTERM the backend. Wait, with timeout.
6. SIGKILL only if step 5 timed out **and** steps 2–4 succeeded.

Any failure in 2–4 aborts the drain and reports failure. **The process is not
killed and no data is discarded.** This is the invariant that protects a
hundred-hour base.

**Tests:** Use a fake backend — a Python script that emits Valheim-shaped log
lines on a schedule and can be configured to hang, to crash, to ignore SIGTERM,
and to write a corrupt save. Test every drain path against it. The fake backend
is a deliverable; you will use it for the rest of the project and it makes your
test suite fast.

_You now have the piece that makes this safe rather than clever._

---

<a id="project-3"></a>

## Project 3 — Gateway

**Task:** Build the always-on UDP component.

**Goals:**

- Bind game and query ports for many worlds.
- Answer A2S for sleeping worlds with live status.
- Trigger wakes on connection attempts.
- Forward datagrams once a backend is awake.

**Behavior:**

**Sleeping world.** A datagram on the game port triggers a wake request to
Control (deduplicated — see below) and is dropped. An A2S query on the query
port receives a challenge, then a well-formed response whose server-name field
reports the world's state. A world in WAKING reports an ETA derived from
Control's estimate.

**Awake world.** Datagrams are forwarded to the backend and responses forwarded
back, preserving client addressing. The gateway tracks last-seen time per client
as a coarse liveness signal, but authoritative presence comes from the agent — a
client can be sending keepalives without having entered the world.

**Wake deduplication.** Multiple triggers for one world within a debounce window
(default 5s) produce exactly one wake request. Six friends clicking join
simultaneously is the normal case, not the exception.

**Rate limiting.** Per source address, on the query port. Required, not
optional. You are emitting responses larger than the requests that provoke them.

**Configuration reload.** Worlds are added and removed at runtime without
restarting the gateway and without dropping traffic for unaffected worlds.

**Tests:** A fake Steam client that speaks A2S including the challenge round
trip. Verify: challenge issued and enforced, expired tokens rejected, status
strings correct per state, dedup window holds under a burst of 100 packets in
100 ms, rate limiter engages, forwarding is correct with three simultaneous
worlds and overlapping client addresses.

**Measurement:** Record gateway RSS with 1, 10, and 100 registered sleeping
worlds. Put these numbers in your README. They are the argument for the whole
design.

_This is the hardest project in Phase A and the one nobody else has built._

---

<a id="project-4"></a>

## Project 4 — Control, and the v0.1 release

**Task:** Build the orchestrator, wire the system together, and ship it.

**Goals:**

- The world registry and state machine from
  [Product §4 — World lifecycle](#spec-4).
- The reconciliation loop.
- The HTTP API.
- A Docker Compose distribution that a stranger can run.

**Behavior:**

Control holds the registry and runs a reconciliation loop that, on each tick,
examines every world's actual state against its desired state and acts. All six
[world invariants](#spec-4-invariants) hold at all times.

The HTTP API exposes: list worlds, get world status, wake, drain, and a health
endpoint. Wake is idempotent and returns current state plus an ETA. Drain
refuses while players are connected.

Admission control caps concurrent awake worlds. Over the cap, wakes queue with a
position and an estimate, and queued entries expire after a TTL.

**On restart, Control reconciles rather than assumes.** It discovers running
backends, re-attaches agents, and rebuilds state from reality. A Control restart
must not orphan a running world or produce a second backend for one.

**The release.** A `docker-compose.yml` that a stranger can `docker compose up`
and have a working sleeping Valheim server. It should:

- Wrap `lloesche/valheim-server-docker` rather than replace it. **Migration cost
  for existing users must be near zero** — adding a service to a compose file
  they already run, not rebuilding their setup.
- Ship a `.env.example` with every setting documented.
- Work on a $5 VPS.

**README requirements.** A 20-second GIF at the top: split screen,
`docker stats` at 0 MB on one side, a player clicking join, the container
starting, memory climbing, the player spawning in. Under it, one measured
number: _wakes in Ns._ Then installation, in under ten lines.

**Tests:** Full integration against the fake backend. Every case in
[Product §7 — Edge cases](#spec-7) that does not require Kubernetes. Chaos: kill
Control mid-wake, mid-drain, and mid-queue, and verify recovery each time.

_Ship this. Everything after is improvement on something real._

---

<a id="project-5"></a>

## Project 5 — Persistence and cold start

**Task:** Move world data to object storage and make wake fast.

**Goals:**

- Versioned save/restore against S3-compatible storage.
- Integrity verification on both ends.
- Full cold-start instrumentation.
- A measured, documented reduction in wake time.

**Behavior:**

Drain uploads a verified save as a new versioned object with a checksum,
confirms the write, and applies a retention policy. Wake downloads, verifies the
checksum, and only then starts a backend. A checksum mismatch is FAILED, never
"start anyway."

Cold start is instrumented as distinct phases: object-store download,
decompress, image pull (if needed), process start, world load, ready. Each phase
gets a histogram with buckets appropriate to its actual range.

Then optimize, measuring each change:

- Compression tradeoffs — `zstd` at several levels against transfer time.
- Local cache with checksum validation, so a world that wakes twice in an hour
  skips the download.
- Pre-warmed pools: keep N backends started but worldless, ready to load.
- Predictive wake: if a world reliably wakes at 8pm Friday, start warming at
  7:55.

**Deliverable:** a `docs/cold-start.md` with a waterfall chart, a before/after
table, and an honest account of what did and did not help. Include the things
that didn't work — that is the part that reads as real.

---

<a id="project-6"></a>

## Project 6 — Discord integration

**Task:** The out-of-band control plane. **Mandatory for crossplay worlds.**

**Behavior:**

Slash commands: `/wake`, `/sleep`, `/status`, `/worlds`, `/backup`, `/restore`.

`/wake` defers immediately, then edits its response with live progress, then
posts the connection details. **For crossplay worlds it posts the
newly-generated join code** — the world is not "ready" from the user's
perspective until the code is published.

Announcements to a configured channel: world woke, world sleeping in N minutes,
world slept, world failed. The sleep warning is important — it gives an AFK
player a chance to object.

Optional and delightful: watch voice channel membership. When three people join
the voice channel associated with a world, start warming it before anyone opens
the game.

**Gotcha:** never put a join code in a public channel by default. Make the
visibility of connection details explicit configuration, and default it to
private.

---

<a id="project-7"></a>

## Project 7 — Kubernetes, by hand

**Task:** Run Naust on Kubernetes with no custom controller. Everything manual.

**Goals:**

- Gateway and control as Deployments.
- One Deployment per world, scaled manually.
- One Service, one LoadBalancer, port-multiplexed for many worlds.
- Correct termination and object-store persistence.

**Behavior:**

Gateway runs at `replicas: 1` behind a single `LoadBalancer` Service with a UDP
port range. Control runs at `replicas: 1`. Each world has a Deployment (game
server + agent sidecar) and a ClusterIP Service.

Control sets `replicas` on world Deployments via the Kubernetes API. Sleep is
`replicas: 0`; wake is `replicas: 1`.

**No PersistentVolumeClaims.** An init container restores from object storage;
the agent uploads on termination. `terminationGracePeriodSeconds` is set to
comfortably exceed the drain sequence.

Resource requests reflect the workload: one CPU core with real clock speed,
memory sized to the world.

**Deliverable:** a Helm chart or Kustomize base that installs the whole thing.
Document what a user must configure.

**Record what is tedious.** Every manual step is a line item in the
operator's justification. Write them down; they become its design notes.

---

<a id="project-8"></a>

## Project 8 — The operator

**Task:** Turn Control into a Kubernetes operator.

**Goals:**

- A `World` CRD with full schema, validation, and a status subresource.
- A Kopf-based controller reconciling Worlds into Deployments, Services, and
  Secrets.
- Finalizers for safe deletion.
- Leader election for multiple replicas.

**Behavior:**

The `World` custom resource is the entire user interface.
`kubectl apply -f midgard.yaml` produces a working, sleeping world.
`kubectl get worlds` shows name, state, players, and last-woken.

The custom resource's desired `spec` carries the user-authored World
configuration established in Project 0. Its `status` reports observed state:
current phase, player count, last wake time, last save time, current join code
for crossplay worlds, and conditions. Keep the two writers separate.

The controller reconciles each World into a Deployment (with agent sidecar and
init container), a ClusterIP Service, and a Secret. Owner references are set so
children are garbage-collected.

**Idempotence is required.** Reconcile may run any number of times for one
change, and running it twice must be indistinguishable from running it once.
Kopf will call your handlers again after restarts, retries, and resyncs.

**Finalizer** flushes the world to object storage before allowing deletion. A
user deleting a World must not lose their save. The finalizer must also be
removable when the flush is impossible — a stuck finalizer means a resource that
cannot be deleted, and you need an escape hatch.

**Leader election** so two Control replicas never both act on one World. Kopf's
peering mechanism handles this; understand it rather than trusting it blindly.

**Tests:** `envtest` or a kind cluster in CI. Reconcile a World and assert the
child objects. Reconcile twice, assert no change. Delete and assert the flush
ran. Kill the operator mid-reconcile and assert recovery.

---

<a id="project-9"></a>

## Project 9 — KEDA external scaler

**Task:** Implement KEDA's external scaler contract so wake is push-driven.

**Behavior:**

A gRPC service implementing all four RPCs, backed by Control's world state.
`IsActive` returns true when a world is awake or a wake is pending.
`StreamIsActive` holds a long-lived stream per world and pushes activation the
moment the gateway sees a packet — no polling delay.

Each World gets a `ScaledObject` with trigger type `external-push`,
`minReplicaCount: 0`, and a cooldown aligned to your idle timeout. Ownership of
the `replicas` field moves from your controller to KEDA; your controller must
stop fighting it.

TLS on the scaler connection, since it can drive scaling for the whole cluster.

**Measurement:** compare wake latency with `external` polling versus
`external-push`. Publish both numbers. The delta is the argument for push-based
activation and it is a genuinely interesting result.

**Gotcha:** KEDA must be optional. Many users will not run it. Keep the
direct-replica path from Project 8 working, and select between them by
configuration.

---

<a id="project-10"></a>

## Project 10 — Observability

**Task:** Make it measurable.

**Goals:**

- Prometheus metrics across all components.
- A Grafana dashboard shipped in the repo.

**Metrics:** cold-start duration by phase (histogram), wake requests by trigger
source and outcome, worlds by state (gauge), drain duration and outcome, save
size and upload duration, gateway memory by registered world count, A2S queries
and rate-limit rejections, KEDA activation latency.

**The dashboard:** worlds awake versus registered, cold-start p50/p95/p99, cost
saved versus always-on, wake success rate. Ship the JSON.

---

<a id="reference"></a>

# Part III — Reference

This part is an index. Use the tables below to resolve a specific question.

## A. Source hierarchy and evidence

When sources disagree, do not quietly pick the convenient one. Record the
disagreement and use this order:

1. **Naust product specification** for the behavior Naust promises.
2. **Official upstream specifications and documentation** for an external
   protocol or platform contract.
3. **Versioned observed evidence** — packet captures, Valheim 1.0 logs, process
   behavior, and measurements — for what the deployed system actually does.
4. **Reference implementations** for interpretation and interoperability tests,
   never as a substitute for the protocol specification.
5. **Community documentation** for discovery and operational clues that you then
   verify.

| Question                                                  | Primary source                                                                                                  | Supporting evidence                                                                                                   | First used    |
| --------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- | ------------- |
| What must Naust do?                                       | [Part I — Product specification](#product-specification)                                                        | [Product edge cases](#spec-7) turned into tests                                                                       | Every project |
| How does Valheim expose server modes and launch behavior? | [Official dedicated-server guide](https://www.valheimgame.com/support/a-guide-to-dedicated-servers/)            | [Community dedicated-server reference](https://valheim.fandom.com/wiki/Dedicated_servers) plus your captured 1.0 logs | BB-1          |
| What bytes does Steam A2S require?                        | [Valve — Source Server Queries](https://developer.valvesoftware.com/wiki/Server_queries)                        | Packet capture and [python-a2s](https://github.com/Yepoleb/python-a2s) as a test client                               | BB-3          |
| How do Discord interactions time out and respond?         | [Discord — Receiving and Responding](https://discord.com/developers/docs/interactions/receiving-and-responding) | [discord.py interactions API](https://discordpy.readthedocs.io/en/stable/interactions/api.html)                       | BB-6          |
| How does Kubernetes behave?                               | [Kubernetes documentation](https://kubernetes.io/docs/home/)                                                    | A local kind or k3s cluster and failure experiments                                                                   | BB-2, BB-7–9  |
| How does KEDA's scaler contract behave?                   | [KEDA external scalers](https://keda.sh/docs/latest/concepts/external-scalers/)                                 | Generated protobuf contract and integration tests                                                                     | BB-9          |

Observed evidence must carry enough context to reproduce it: game version,
container image or binary version, platform, command line, and capture date. Log
formats and timing numbers without that context are anecdotes, not durable
project knowledge.

## B. Library index

| Need                       | Library           | Upstream documentation                                                                                                   |
| -------------------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Project/package management | uv                | [Docs](https://docs.astral.sh/uv/)                                                                                       |
| Lint + format              | Ruff              | [Docs](https://docs.astral.sh/ruff/)                                                                                     |
| Testing                    | pytest            | [Docs](https://docs.pytest.org/)                                                                                         |
| Models & validation        | Pydantic v2       | [Docs](https://docs.pydantic.dev/latest/)                                                                                |
| Settings                   | pydantic-settings | [Docs](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)                                                     |
| CLI                        | Typer             | [Docs](https://typer.tiangolo.com/)                                                                                      |
| Structured logging         | structlog         | [Docs](https://www.structlog.org/)                                                                                       |
| HTTP API                   | FastAPI           | [Docs](https://fastapi.tiangolo.com/)                                                                                    |
| HTTP client                | HTTPX             | [Docs](https://www.python-httpx.org/)                                                                                    |
| Async                      | stdlib `asyncio`  | [Docs](https://docs.python.org/3/library/asyncio.html)                                                                   |
| Binary parsing             | stdlib `struct`   | [Docs](https://docs.python.org/3/library/struct.html)                                                                    |
| Object storage             | boto3 or MinIO    | [boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/), [MinIO Python SDK](https://github.com/minio/minio-py) |
| Discord                    | discord.py        | [Docs](https://discordpy.readthedocs.io/)                                                                                |
| Metrics                    | prometheus-client | [Docs](https://prometheus.github.io/client_python/)                                                                      |
| Kubernetes operator        | Kopf              | [Docs](https://kopf.readthedocs.io/)                                                                                     |
| Kubernetes API client      | kubernetes        | [Repository](https://github.com/kubernetes-client/python)                                                                |
| gRPC                       | grpcio            | [Docs](https://grpc.io/docs/languages/python/)                                                                           |
| A2S reference client       | python-a2s        | [Repository](https://github.com/Yepoleb/python-a2s)                                                                      |

## C. Kubernetes concept index

Ordered as introduced.

| Concept                               | Introduced | Upstream documentation                                                                                                                         |
| ------------------------------------- | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Pod                                   | BB-7       | [Docs](https://kubernetes.io/docs/concepts/workloads/pods/)                                                                                    |
| Deployment                            | BB-7       | [Docs](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/)                                                                  |
| Service                               | BB-7       | [Docs](https://kubernetes.io/docs/concepts/services-networking/service/)                                                                       |
| ConfigMap / Secret                    | BB-7       | [ConfigMap](https://kubernetes.io/docs/concepts/configuration/configmap/), [Secret](https://kubernetes.io/docs/concepts/configuration/secret/) |
| Pod lifecycle & termination           | BB-2, BB-7 | [Docs](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/)                                                                      |
| Resource requests & limits            | BB-7       | [Docs](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/)                                                         |
| Init containers                       | Project 7  | [Docs](https://kubernetes.io/docs/concepts/workloads/pods/init-containers/)                                                                    |
| Controller pattern                    | BB-4       | [Docs](https://kubernetes.io/docs/concepts/architecture/controller/)                                                                           |
| Custom resources                      | BB-8       | [Docs](https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/)                                                  |
| Operator pattern                      | BB-8       | [Docs](https://kubernetes.io/docs/concepts/extend-kubernetes/operator/)                                                                        |
| CRD reference                         | BB-8       | [Docs](https://kubernetes.io/docs/tasks/extend-kubernetes/custom-resources/custom-resource-definitions/)                                       |
| Finalizers                            | BB-8       | [Docs](https://kubernetes.io/docs/concepts/overview/working-with-objects/finalizers/)                                                          |
| Owner references & garbage collection | BB-8       | [Docs](https://kubernetes.io/docs/concepts/architecture/garbage-collection/)                                                                   |
| HPA and its limits                    | BB-9       | [Docs](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)                                                             |
| KEDA external scalers                 | BB-9       | [Docs](https://keda.sh/docs/latest/concepts/external-scalers/)                                                                                 |

## D. Further reading

Everything below is optional background.

**Books**

- Nigel Poulton, _The Kubernetes Book_ (2026 ed.) — the entry point.
- Marko Luksa, _Kubernetes in Action_, 2nd ed. — the reference.
- Hausenblas & Schimanski, _Programming Kubernetes_ — controller internals.
- Brett Slatkin, _Effective Python_, 3rd ed. — you are already reading this.
- Matthew Fowler, _Python Concurrency with asyncio_ — optional, ch. 1–3, 6.

**Specifications**

- [Source Server Queries (A2S)](https://developer.valvesoftware.com/wiki/Server_queries)
- [Valheim dedicated servers (community wiki)](https://valheim.fandom.com/wiki/Dedicated_servers)
- [Valheim official server guide](https://www.valheimgame.com/support/a-guide-to-dedicated-servers/)

**Prior art worth studying**

- [Knative Serving architecture](https://knative.dev/docs/serving/architecture/)
  — the activator. Closest conceptual sibling.
- [KEDA HTTP add-on](https://github.com/kedacore/http-add-on) — the HTTP
  interceptor. Read the source.
- [Agones](https://agones.dev/) — study it to understand _why it does not fit
  here_. Fleets of interchangeable ephemeral sessions, not persistent named
  worlds. Being able to explain that distinction is worth the reading time.
- [lloesche/valheim-server-docker](https://github.com/lloesche/valheim-server-docker)
  — the incumbent you are wrapping.
- [CNCF: GPU autoscaling with KEDA](https://www.cncf.io/blog/2026/05/27/gpu-autoscaling-on-kubernetes-with-keda-building-an-external-scaler/)
  — the inference-side analogue.

**Talks**

- Gil Tene,
  [How Not to Measure Latency](https://www.youtube.com/watch?v=lJ8ydIuPFeU)

---

_Good luck. Build the boathouse._
