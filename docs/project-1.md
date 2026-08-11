# Project 1 guided build: presence from a noisy log

This is the learning companion for [Project 1 — Presence](../README.md#project-1).
The README remains the product specification. This article supplies the order of
work, the questions to ask, and the evidence to test against. It deliberately
does not contain the implementation.

At the end you will have built:

- a pure Valheim line parser;
- a stateful, game-neutral presence tracker;
- a small game-adapter boundary;
- unit, recorded-log, and property tests; and
- an optional `naust parse <logfile>` diagnostic command.

Do not build the supervisor, contact Control, read a live subprocess, or add
async code yet. Those are Project 2 concerns. Project 1 should remain a small,
synchronous core that is easy to reason about exhaustively.

## How to work through this article

Use one chapter per focused session. For each chapter:

1. Read only the linked material named in that chapter.
2. Predict the behavior before running the fixture or a test.
3. Write one failing test that expresses the prediction.
4. Add the smallest behavior that makes it pass.
5. Refactor only while the tests are green.
6. Answer the checkpoint questions in your own words before moving on.

Small commits make the reasoning visible. Good boundaries usually emerge from
several narrow red-green-refactor loops; they rarely arrive as one perfect class
diagram.

## The mental model

The system has four layers of truth:

```text
untrusted log text
        |
        v
game adapter: "what did this line literally report?"
        |
        v
semantic observation, or no observation
        |
        v
presence tracker: "what does this mean given everything seen so far?"
        |
        v
presence transition, but only when observable state genuinely changes
```

The optional CLI is an I/O shell around this core. Project 2 will later feed live
lines into exactly the same core. Control receives only transitions; it must not
know Valheim's log grammar.

Keep these concepts separate:

| Concept | Example | Owner |
| --- | --- | --- |
| Raw fact | A line contains `RPC_Disconnect` | Valheim adapter |
| Observation | A disconnect sequence may have begun | Adapter event type |
| Inference | Cleanup owner `x` belongs to known player `B` | Presence tracker |
| State | The currently supported player identities | Presence tracker |
| Transition | Player set changed from `{A, B}` to `{A}` | Presence tracker |
| Presentation | Print a timestamped leave in the replay CLI | CLI |

This distinction is the heart of the project. A parser recognizes syntax. A
tracker gives observations meaning over time.

---

## Chapter 0: turn the specification into questions

Read these local sections in order:

1. [Agent boundary](../README.md#spec-5-agent)
2. [Presence behavior](../README.md#spec-6-1)
3. [Project 1 acceptance criteria](../README.md#project-1)

Now write a short requirement ledger. Do not write class names yet. Give every
requirement an observable example and, eventually, a test name.

| Requirement | Observable example | Test level |
| --- | --- | --- |
| Death is not a leave | non-zero ZDOID, then `0:0`; set unchanged | unit |
| Respawn is not a second join | known name receives another non-zero ZDOID | unit |
| Failed login cannot evict a player | disconnect with no character/owner evidence | recorded log |
| Transition means change | duplicate observation produces no output | unit |
| Mid-stream input is safe | cleanup for an unknown owner is a no-op | unit |
| Bad text is harmless | truncated and arbitrary lines do not raise | unit/property |
| Count is bounded | after every generated event, `0 <= count <= max` | property |

Add or rename rows as your evidence demands. The ledger is useful because it
prevents one happy-path fixture test from pretending to cover every rule.

### Boundary questions

Answer these before designing types:

- Which component reads logs? The specification assigns that to Agent.
- Which layer knows the words `RPC_Disconnect`? Only the game adapter.
- Which layer remembers earlier lines? The tracker, not the pure parser.
- Does one line always cause one state change? No.
- Is missing evidence equivalent to evidence of absence? No.
- Which error is safer: keeping an empty server awake, or declaring it empty
  while a person is playing?

That last question establishes a production safety policy: ambiguous presence
evidence must fail awake.

### Checkpoint

You are ready when every Project 1 bullet has a future test location and you can
explain why Control, storage, and subprocess supervision do not belong in those
tests.

---

## Chapter 1: read the recorded session as evidence

The fixture is
[`tests/fixtures/valheim/presence-session.log`](../tests/fixtures/valheim/presence-session.log).
It is sanitized, but its event ordering and numeric grammar are preserved. Read
[`tests/fixtures/valheim/README.md`](../tests/fixtures/valheim/README.md) before
adding or replacing captures.

Start with targeted searches rather than scrolling all 355 lines:

```console
rg -n "Game server connected|Got connection SteamID|New peer|Got character ZDOID|RPC_Disconnect|Destroying abandoned|World saved" tests/fixtures/valheim/presence-session.log
```

Then read a little context around every match. Your first pass should reconstruct
this story:

1. The server becomes ready.
2. One connection fails authentication and disconnects before a character is
   observed.
3. `PLAYER_A` connects successfully.
4. A second connection fails and emits `RPC_Disconnect` while `PLAYER_A` is
   still playing.
5. `PLAYER_B` connects successfully.
6. `PLAYER_B` dies and respawns twice.
7. A disconnect is followed by cleanup lines whose owner matches `PLAYER_B`'s
   non-zero ZDOID.
8. Another disconnect is followed by cleanup owned by `PLAYER_A`.
9. Shutdown produces a completed world-save signal.

Notice the decisive counterexample: “on `RPC_Disconnect`, remove some player”
would remove `PLAYER_A` during step 4. The log does not tell you enough at that
moment. The later cleanup owner is the evidence that resolves a real player's
identity.

### Build a line taxonomy

For each useful line shape, record:

- what fields are literally present;
- whether it carries player identity, connection identity, or neither;
- whether it is independently actionable;
- what earlier state it needs; and
- what malformed versions you should reject.

A useful starting taxonomy is:

| Family | Literal evidence | Immediate presence change? |
| --- | --- | --- |
| Ready | game server connected | no |
| Connection setup | connection or handshake identifier | no |
| Accepted peer | peer setup completed | no |
| Character ZDOID, non-zero | name and owner/object pair | maybe: tracker decides |
| Character ZDOID, `0:0` | named character currently has no live ZDOID | no |
| Disconnect marker | no player identity | no; begin correlation |
| Abandoned ZDO cleanup | a ZDOID and owner identifier | maybe: tracker decides |
| Socket close | connection identifier and end of disconnect sequence | no; end correlation |
| Save complete | world save finished | no presence change |
| Everything else | noise for this project | no |

“Maybe” is healthy here. The adapter should not decide whether a repeated
non-zero ZDOID is a join or a respawn; only the tracker knows the prior state.

### Evidence questions

- Is a Steam connection ID the same identifier as a character's ZDOID owner?
- Can cleanup produce many lines for one disconnect?
- How will you remove a player only once when all those lines share an owner?
- Which line closes the correlation window, and why must stale pending evidence
  not survive into a later connection?
- What should happen if the log ends immediately after `RPC_Disconnect`?
- What should happen if cleanup refers to an owner the tracker never saw?
- Which answer keeps the system safe if the stream is truncated?

Do not invent certainty. Record uncertain cases as explicit design assumptions
and tests. Log formats are observations, not a versioned API.

### Checkpoint

Without looking at code, predict the presence count transitions in this fixture.
They should be `1, 2, 1, 0`; failed connections, deaths, respawns, and repeated
cleanup lines must add no transitions.

---

## Chapter 2: learn the state-machine shape on toy data

Before mixing regular expressions with Valheim semantics, complete the synthetic
exercise from [Building Blocks 1](../README.md#bb-1):

- `LOGIN <user>` adds a user;
- `DIED <user>` leaves presence unchanged; and
- anonymous `LOGOUT` removes the least-recently-active user.

Read the [`deque` documentation](https://docs.python.org/3/library/collections.html#collections.deque),
then solve the exercise with both membership and ordering in mind.

Test at least:

- empty input;
- logout while empty;
- one login and logout;
- two logins followed by two logouts;
- repeated login for one user;
- death before and after login;
- starting with a logout or death; and
- irrelevant and malformed lines.

The exercise teaches three independent ideas:

1. A set answers “is this user present?” efficiently.
2. An ordered structure answers a different question: “which candidate is
   first?”
3. An anonymous event needs prior state before it can be interpreted.

Do not copy its least-recently-active rule into Valheim. That rule is guaranteed
by the toy problem and is *not* guaranteed by the real log. The transferable
lesson is how to combine evidence with state, not the particular eviction
policy.

### Checkpoint

Explain why a set alone cannot implement the toy policy, and why a deque alone
is awkward for membership and duplicate suppression. Then explain why neither
structure can manufacture identity that the Valheim stream has not supplied.

---

## Chapter 3: design semantic events before regexes

Read:

- [`dataclasses`](https://docs.python.org/3/library/dataclasses.html), especially
  frozen instances and generated equality;
- [`enum`](https://docs.python.org/3/library/enum.html), for fixed labels with no
  varying payload; and
- [`typing.Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol),
  but do not design the adapter protocol until Chapter 6.

Sketch the smallest vocabulary that can express literal observations. Example
concepts—not required names—include:

- a character/ZDOID observation containing a name and both numeric ZDOID parts;
- a disconnect marker;
- cleanup-owner evidence;
- the boundary that ends a disconnect sequence;
- readiness;
- save completion; and
- a join code, when present.

### Make illegal combinations hard to construct

Compare these two shapes on paper:

- one event with an enum plus several optional fields; and
- separate immutable dataclasses for observations with different payloads.

The first can often express nonsense such as a disconnect event with a player
name or a character event with no name. The second asks Python's type checker
and constructor to reject those combinations earlier.

A small ZDOID value object may also be worthwhile because it keeps the signed
owner and object number together and gives `0:0` one explicit meaning. Decide by
asking whether the pair has behavior or validation that would otherwise be
duplicated—not merely because every pair deserves a class.

These are internal, trusted-after-parsing values. Standard-library dataclasses
are enough; Pydantic is most valuable at external configuration and
serialization boundaries. Do not add validation machinery to a hot line-by-line
path unless it buys a concrete guarantee.

### Separate observations from transitions

An observation describes input. A transition describes a change the tracker
accepted. They should not be the same type.

Decide what downstream code needs from a transition:

- the player set before and after;
- joined and left identities;
- the resulting count;
- source time, if parsing it is justified; or
- only a snapshot.

Prefer immutable snapshots at public boundaries. Returning the tracker's live
mutable set would let callers silently violate its invariants.

### Model uncertainty honestly

At an identity-free disconnect marker, the world may contain `{A, B}`, but the
tracker does not yet know whether a real player disconnected. Do not encode a
guess as fact. Hold correlation state only for that disconnect sequence. Resolve
it if matching owner evidence arrives, and close it at the sequence's socket
boundary. If identity evidence never arrives, failing awake is safer than
starting an idle shutdown from an unconfirmed leave. A stale pending marker must
not make unrelated future cleanup look authoritative.

Name internal collections according to what they actually mean: “known ZDOID
owners,” “pending disconnect,” and “present players” are clearer than a generic
`players`, `map`, or `state`.

### Checkpoint

For every event type, answer:

- Which exact line family constructs it?
- Which fields are guaranteed?
- Can it change presence without prior state?
- Can two identical events occur legitimately?
- Is equality useful in tests?

If several fields are optional only because unrelated event kinds share one
class, revisit the shape.

---

## Chapter 4: build the pure line parser

Read the Python [Regular Expression HOWTO](https://docs.python.org/3/howto/regex.html),
focusing on raw strings, compiled patterns, named groups, and the difference
between `match`, `search`, and `fullmatch`.

The parser contract is intentionally narrow:

```text
one string -> one semantic observation, or no observation
```

It performs no file I/O, changes no tracker state, logs no warning for ordinary
noise, and does not raise for malformed or truncated input.

### Grow it one grammar rule at a time

Use a parameterized test for each line family. The recommended order is:

1. noise and blank lines return no observation;
2. positive-owner character ZDOID;
3. negative-owner character ZDOID;
4. exactly `0:0`;
5. disconnect marker;
6. abandoned-ZDO owner evidence;
7. disconnect-sequence end;
8. ready and save-complete signals; and
9. join-code evidence if your capture actually contains it.

Read [pytest parametrization](https://docs.pytest.org/en/stable/how-to/parametrize.html)
when several input/output pairs exercise one rule.

For every accepted form, add close malformed neighbors:

- missing name;
- missing colon or numeric half;
- non-numeric owner/object values;
- truncated prefix;
- extra whitespace, if observed;
- negative object number, if not valid evidence; and
- arbitrary Unicode.

Avoid a single giant regex. A short ordered table of compiled patterns keeps
each grammar rule named and testable. Order matters only when patterns overlap;
if it matters, capture that fact in a test.

Do not parse timestamps merely because they exist. Add them only when a caller
has a requirement that cannot be met by stream order. Every parsed field is a
compatibility promise and another way for an upstream format change to break
you.

### Checkpoint

You should be able to run parser tests without constructing settings, a service,
a tracker, or a file. If a test needs any of those, the function is not yet
pure enough.

---

## Chapter 5: build the presence tracker

Now write a tracker that consumes semantic observations, never raw text. Its
constructor receives only the policy it needs—such as maximum players—not the
entire `NaustSettings` object.

Work through this state table before writing branches:

| Observation | Relevant prior state | Result |
| --- | --- | --- |
| Non-zero character ZDOID | name unknown | add identity and owner mapping |
| Non-zero character ZDOID | name already present | refresh evidence; no join transition |
| `0:0` for a name | any | no presence transition |
| Disconnect marker | any | open correlation; do not guess identity |
| Cleanup owner | matches present player's owner during disconnect | remove that player once |
| Cleanup owner | repeated for the same resolved disconnect | no-op |
| Cleanup owner | unknown or no pending disconnect | no-op/diagnostic, never negative |
| Disconnect-sequence end | unresolved correlation | close it without evicting anyone |
| Ready/save/join-code | any | not a presence mutation |

The exact internal representation is your design exercise. Judge it against
these constraints:

- one component owns mutation;
- callers receive read-only snapshots;
- a name cannot be counted twice;
- repeated cleanup cannot remove twice;
- unknown cleanup cannot remove an arbitrary player;
- count is derived from truthful state where possible, not maintained as an
  unrelated integer that can drift; and
- every completed mutation leaves `0 <= count <= max_players` true.

### Starting mid-stream

“Recover correctly” does not mean reconstructing unseen history. It means never
inventing negative players or identities, learning from new positive evidence,
and converging as useful events arrive.

Examples:

- `0:0` for an unseen name: no-op;
- cleanup for an unseen owner: no-op;
- non-zero ZDOID for an unseen name: that is the first supported presence fact;
- disconnect with no correlating owner: do not evict a guess.

This is a general production principle: represent the knowledge you have, not
the history you wish you had.

### Transitions, not notifications

Capture the public player snapshot before and after consuming an observation.
Only return or emit a transition when those snapshots differ. Death, respawn,
duplicate join evidence, repeated owner cleanup, and unrelated lines should be
silent.

This makes downstream logic simpler: Control can reset an idle timer from a
real change instead of interpreting Valheim chatter.

### Invariants and external input

An internal assertion can document that your own mutation preserved bounds, but
an assertion is not input validation and can be disabled by Python optimization.
Malformed log text should be rejected by the parser without crashing the agent.
If a well-formed event would exceed `max_players`, choose and test an explicit
policy—ignore with a diagnostic, return an error, or stop processing—without
first constructing illegal state.

### Checkpoint

Run the tracker tests entirely with hand-constructed semantic events. Then ask:

- Would changing Valheim's timestamp break this tracker? It should not.
- Would replacing regex with a structured event source break it? It should not.
- Can a failed login evict `PLAYER_A`? It must not.
- Can death plus respawn produce another join transition? It must not.

---

## Chapter 6: draw the game-adapter boundary

Return to [`typing.Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol).
A protocol describes what a consumer needs without requiring adapters to inherit
from a shared base class.

Design from the caller inward. The agent needs to turn a line into a semantic
observation and understand game-specific readiness, save completion, and join
code evidence. It does not need to know how regexes are stored.

The Project 1 phrase “the adapter owns the pattern table” does not require
exposing a mutable regex list as public API. Prefer a behavioral surface such as
parsing a line; keep compiled patterns encapsulated unless a real consumer needs
them.

Test the boundary with a tiny fake adapter whose input grammar is not Valheim's.
You do not need to implement another game. The proof you want is that the replay
loop and tracker can operate without importing Valheim syntax.

Keep the dependency direction visible:

```text
Agent/replay loop -> GameAdapter protocol <- Valheim adapter
                  -> PresenceTracker
```

The protocol and semantic event types should live somewhere that does not import
the concrete Valheim adapter. Otherwise the apparent abstraction still points
backward.

### Checkpoint

Imagine adding a Minecraft adapter. List the files that would change. If the
presence tracker must gain Minecraft string checks, the boundary is leaking. If
only a concrete adapter and its tests are new, the design is doing its job.

---

## Chapter 7: replay the recorded fixture

Now join the parser and tracker in one integration test. Read the fixture using
[`pathlib`](https://docs.python.org/3/library/pathlib.html), feed it one line at
a time through the adapter, and give observations to the tracker.

Assert behavior, not incidental formatting:

- readiness is observed once;
- the presence transition counts are exactly `1, 2, 1, 0`;
- the first identity is `PLAYER_A`;
- the failed second connection does not remove `PLAYER_A`;
- `PLAYER_B`'s deaths and respawns emit no transition;
- the first identity-bearing disconnect removes `PLAYER_B`;
- the next removes `PLAYER_A`;
- repeated cleanup lines do not create more leaves; and
- save completion is observed once.

Do not assert that the fixture has exactly 355 lines or that unrelated Unity
noise remains byte-for-byte identical. Those facts are not product behavior and
would make harmless fixture cleanup expensive.

Keep focused unit tests even after this passes. The fixture proves the pieces
compose against one observed session; it does not prove every malformed input,
ordering, or game version.

### Add an adversarial mini-stream

Create a short test stream containing:

1. `PLAYER_A` joining;
2. a new connection that fails before character evidence; and
3. `PLAYER_A` producing a later ordinary event or disconnecting normally.

The player set must remain `{PLAYER_A}` after step 2. This is the regression
test for the most dangerous naive implementation.

### Checkpoint

Temporarily imagine deleting every tracker unit test. List three bugs the full
fixture might still fail to isolate clearly. Restore the tests. This exercise
builds intuition for the difference between integration coverage and diagnostic
quality.

---

## Chapter 8: test invariants with generated sequences

Once example tests are green, add Hypothesis as a development dependency. Read
its [quick start](https://hypothesis.readthedocs.io/en/latest/quickstart.html)
and [property-testing tutorial](https://hypothesis.readthedocs.io/en/latest/tutorial/).

Generate sequences of already-valid semantic events first. This isolates the
state machine from the grammar. After each consumed event, check:

- `0 <= count <= max_players`;
- public count agrees with the public snapshot;
- a returned transition corresponds to an actual before/after difference;
- repeated evidence is idempotent where the domain says it is; and
- unknown cleanup cannot remove a known player.

Then fuzz the parser separately with arbitrary text. The basic property is that
it returns either a supported observation or no observation and never raises.

Do not ask Hypothesis to invent the domain specification. Example tests explain
named behaviors such as death and failed authentication; generated tests search
the combinations you did not think to write. You need both.

If a generated failure looks impossible, preserve the minimized example as a
regression test before changing the implementation. The minimized case is often
teaching you that two notions of state were accidentally coupled.

### Checkpoint

Deliberately introduce a small duplicate-removal bug and confirm Hypothesis can
find a sequence that violates an invariant. Revert the bug. A property test you
have seen fail is much easier to trust and maintain.

---

## Chapter 9: add the replay CLI last

Only now add the optional `naust parse <logfile>` command. Read Typer's
[`Path` argument documentation](https://typer.tiangolo.com/tutorial/parameter-types/path/)
and Python's [`pathlib` documentation](https://docs.python.org/3/library/pathlib.html).

The command is a thin composition root:

1. Typer validates that the argument is a readable file, not a directory.
2. The command chooses the Valheim adapter.
3. It constructs a fresh tracker with the selected maximum.
4. It opens the file explicitly as text and handles undecodable bytes according
   to a documented policy.
5. It streams lines rather than loading the full capture into memory.
6. It prints only genuine transitions in a stable, readable format.

The command should contain orchestration, not parsing regexes or state-machine
branches. Test it with Typer's CLI runner and a temporary miniature log. One test
should cover a missing/unreadable path; another should cover a short valid
timeline.

Avoid wiring global `NaustSettings` into the core merely because the CLI already
has it. Extract the one policy value the tracker needs at the composition edge.
That keeps configuration ownership from spreading into domain logic.

### Checkpoint

Run the command against the sanitized fixture and compare its transition
timeline to `1, 2, 1, 0`. Then run it on an empty file and on a file containing
only garbage. Both should finish cleanly with no false transitions.

---

## Suggested file boundaries

Choose names you understand, but keep these responsibilities separate:

| Area | Contains | Must not contain |
| --- | --- | --- |
| Presence domain | semantic event types, tracker, transition/snapshot | regexes, Typer, file I/O, Pydantic settings |
| Adapter contract | consumer-facing game protocol | Valheim-specific strings |
| Valheim adapter | compiled patterns and line-to-observation parsing | mutable presence state, Control calls |
| CLI | file opening, construction, rendering | log grammar, tracker rules |
| Unit tests | one grammar or state rule at a time | dependence on the full fixture |
| Recorded-log test | end-to-end replay of observed evidence | exact assertions about irrelevant noise |

A modest first layout might put the presence domain and protocol under
`naust.agent`, the concrete parser under a game-specific submodule, and tests in
matching focused files. Do not create a directory for every noun on day one.
Split when a module has more than one reason to change.

---

## Definition of done

Project 1 is complete when you can demonstrate every row without explaining
away a gap:

| Capability | Evidence |
| --- | --- |
| Pure parser | parser unit tests need only strings and expected observations |
| Stateful interpretation | tracker unit tests use semantic events, not log text |
| Death/respawn correctness | no extra join or leave transitions |
| Anonymous disconnect correctness | owner cleanup resolves the right player exactly once |
| Failed-auth safety | active player survives an unrelated disconnect marker |
| Mid-stream safety | unknown death/cleanup/disconnect never creates negative state |
| Bounds | generated event sequences always remain within configured maximum |
| Adapter boundary | fake adapter works without tracker changes |
| Recorded evidence | fixture yields `1, 2, 1, 0` and observes ready/save |
| Diagnostic replay | CLI streams a file and prints only genuine transitions |
| Repository hygiene | no raw capture or real player/server identifiers are tracked |

Run the repository gate:

```console
uv run ruff format --check .
uv run ruff check .
uv run pytest
uv run pre-commit run --all-files
```

Then perform a design review in plain language:

1. What does each layer know?
2. Where does mutable state live, and who is allowed to change it?
3. How is uncertainty represented without lying?
4. Which malformed inputs are safely ignored?
5. Which invariants are checked after every mutation?
6. What would change for another game or log version?
7. Can the future live Agent reuse the exact tested core?

If you can answer those precisely and every requirement is traced to a test,
move to [Building Blocks 2](../README.md#bb-2). Do not pull async subprocess
complexity backward into a core that is already simple and testable.

## Reference shelf, in order of first use

Use this as a shelf, not a reading marathon:

1. [README: Agent boundary](../README.md#spec-5-agent) — ownership.
2. [README: presence behavior](../README.md#spec-6-1) — product truth.
3. [Recorded fixture](../tests/fixtures/valheim/presence-session.log) — runtime
   evidence.
4. [Python `deque`](https://docs.python.org/3/library/collections.html#collections.deque)
   — ordered state for the toy exercise.
5. [Python dataclasses](https://docs.python.org/3/library/dataclasses.html) and
   [enums](https://docs.python.org/3/library/enum.html) — internal value types.
6. [Python regex HOWTO](https://docs.python.org/3/howto/regex.html) — line grammar.
7. [pytest parametrization](https://docs.pytest.org/en/stable/how-to/parametrize.html)
   — compact grammar examples.
8. [Python protocols](https://docs.python.org/3/library/typing.html#typing.Protocol)
   — adapter boundary.
9. [Hypothesis tutorial](https://hypothesis.readthedocs.io/en/latest/tutorial/)
   — invariant exploration.
10. [Typer path arguments](https://typer.tiangolo.com/tutorial/parameter-types/path/)
    — replay shell.
11. [Iron Gate's dedicated-server guide](https://www.valheimgame.com/support/a-guide-to-dedicated-servers/)
    — operational context, not a log-format contract.
