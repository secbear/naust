# Single-node Agent: idle timer and drain policy

Status: accepted for Project 2. Revisit when Control exists as a process.

## Question

Product §5 gives the idle-timer loop to Control. There is no Control process
yet, and the first deployment is one world on one virtual machine that powers
itself off. Who runs the idle timer, and what exactly is "request a save" for
Valheim?

## Evidence

- Product §6.2: the timer starts at zero players, resets on any join, does not
  run during WAKING, and a connection grace period applies after wake
  regardless of count.
- Product §6.3: save, verify, upload, then terminate; a failed save or
  verification leaves the process and the local copy untouched.
- Valheim has no save command. It saves on its autosave timer and on graceful
  shutdown, which SIGINT triggers. After `World saved ( … )` the server keeps
  shutting down for several seconds (asset unload, matchmaking unregister)
  and exits on its own.

## Decision

- **The Agent owns the idle timer in single-node mode.** `run_world` starts
  the timer at readiness, resets it on every presence transition to a non-zero
  count, honours the world's `connection_grace_period`, and drains when the
  world has been empty for `idle_timeout`. SIGTERM and SIGINT request the same
  drain. When Control arrives it replaces this loop with an API call; the
  supervisor and its drain sequence do not change.
- **SIGINT is the save request.** The drain policy's `save_signal` is SIGINT.
  The supervisor then waits for the save line, verifies the files, waits an
  `exit_grace` for the server's own shutdown, and only then sends SIGTERM and,
  after `stop_timeout`, SIGKILL.
- **Verification is size and time based.** Both files present and non-empty,
  neither below half the previous size, both modified after the request (with
  a one-second tolerance between the filesystem clock and the wall clock). The
  ratio is a policy value; tighten it when there is evidence.
- **Upload is not the Agent's job here.** The single-node deployment keeps the
  world on a persistent disk and takes off-box snapshots with restic after a
  verified drain. Product §6.3 steps 5–6 belong to that host's shutdown
  sequence, not to `run_world`.
- **Exit status is the interface.** 0: saved, verified, stopped. 1: startup
  failure, unexpected exit, or a drain that left the backend untouched. A
  service manager must not blindly restart on 1, because the world may need a
  human.
- **The password rides on the command line** because the game accepts it
  nowhere else. It is excluded from resolved configuration and redacted in the
  logged argv.

## Enforcement

`tests/test_agent.py` runs the full runtime against the fake backend for the
idle path, the operator-stop path, startup failure, unexpected exit, and a
failed drain. `tests/test_supervisor.py` covers every drain outcome.

## Revisit when

- Control exists and owns per-world state; move the timer there.
- A Valheim build adds a save command; change `save_signal` and nothing else.
- Verification produces a false failure in practice; the ratio and tolerance
  are the two knobs.
