# Running Naust on NixOS

The flake exports `nixosModules.naust`. One host, one or more worlds, each a
systemd unit that keeps the dedicated server current, supervises it with
`naust agent`, and drains it cleanly when nobody is playing.

## Minimal configuration

```nix
{
  inputs.naust.url = "github:secbear/naust";

  outputs = { nixpkgs, naust, ... }: {
    nixosConfigurations.valheim = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        naust.nixosModules.naust
        {
          # steamcmd and the Steam runtime are unfree.
          nixpkgs.config.allowUnfreePredicate = pkg:
            builtins.elem (nixpkgs.lib.getName pkg) [ "steamcmd" "steam-unwrapped" ];

          services.naust = {
            enable = true;
            passwordFile = "/run/secrets/valheim-password";
            onDrained = "poweroff";              # on-demand VM: drain, then stop billing
            sinks = [
              { kind = "discord"; urlFile = "/run/secrets/discord-webhook"; }   # join code, session summary
            ];
            worlds.midgard = {
              name = "Midgard";
              idleTimeout = "PT30M";
              connectionGracePeriod = "PT10M";  # players are still loading after a wake
            };
          };
        }
      ];
    };
  };
}
```

`systemctl start naust-midgard` installs or updates the server with steamcmd,
launches it inside Steam's FHS environment with the PlayFab libraries
crossplay needs, and streams its log through the presence tracker. The unit
is `Type=notify`: it becomes active when the game accepts players, and its
status line shows the player count. When the world has been empty for
`idleTimeout`, or on `systemctl stop`, or on `POST /v1/drain`, the agent asks
the game to save, verifies both world files, waits for the game to exit, and
returns 0. With `onDrained = "poweroff"` that exit shuts the host down.

Every lifecycle fact is a CloudEvent to the configured sinks. The Discord
sink is how a crossplay world's join code reaches players; a webhook sink is
how anything else, from a status page to an orchestrator, follows along.

## What the unit guarantees

| Behaviour | Where |
| --- | --- |
| Save before stop, verify before kill | `naust.agent.supervisor.drain`, ADR 0003 |
| Exit 1 leaves the game and files untouched | `Restart = "no"`; the world needs a human |
| Password never appears in logs | systemd credential; redacted argv |
| `systemctl stop` is a drain, not a kill | `KillMode = "mixed"`, `TimeoutStopSec` |
| Poweroff only after a verified drain | `ExecStopPost` checks `$EXIT_STATUS = 0` |

## Options

| Option | Default | Purpose |
| --- | --- | --- |
| `worlds.<id>.mode` | `crossplay` | `crossplay` (join code, no inbound port) or `steam-direct` |
| `worlds.<id>.gamePort` / `openFirewall` | `2456` / `false` | steam-direct only; the query port is one higher |
| `worlds.<id>.idleTimeout` | `PT15M` | ISO 8601 duration empty before draining; `null` is orchestrator mode |
| `worlds.<id>.connectionGracePeriod` | `PT3M` | idle timer does not fire this long after start (Valheim enforces at least 3 minutes) |
| `worlds.<id>.autoStart` | `true` | start at boot |
| `sinks` | `[]` | `{ kind = "discord" or "webhook"; urlFile; tokenFile? }`, secrets via systemd credentials |
| `metricsHost` / `metricsPort` | `127.0.0.1` / `9701` | read-only `/metrics`, `/v1/status`, `/readyz`, `/healthz`; `null` disables |
| `updateOnStart` | `true` | `steamcmd +app_update 896660` before each start |
| `preStartCommand` | `null` | run as root before the update, e.g. a restore |
| `extraServerArgs` | `[]` | e.g. `["-preset" "hard" "-saveinterval" "1800"]` |
| `maxPlayers` | `10` | upper bound the tracker enforces |
| `startTimeout` / `stopTimeout` | `600` / `300` | seconds systemd allows for READY and for the drain |
| `onDrained` / `postDrainCommand` | `none` / `null` | run after a successful drain, as root |
| `settings` | `{}` | merged into `naust.toml`, e.g. `agent.backend.save_timeout = "PT4M"` |
| `dataDir`, `serverDir`, `saveDir` | `/var/lib/naust`… | Steam root, server install, `-savedir`; state in `dataDir/state` |

World files live in `${saveDir}/worlds_local/<id>.db` and `.fwl`. Back up the
pair together, after a drain, never during one; `dataDir/state/<id>/last-verified.json`
records what the last drain verified, and the agent refuses to start a world
whose files are half-present or far smaller than that record.

## The contract, from the host's side

| Surface | Where |
| --- | --- |
| Commands | `systemctl stop naust-<id>` (drain); `curl --unix-socket /run/naust/<id>.sock -X POST http://naust/v1/drain` |
| Status | `curl --unix-socket /run/naust/<id>.sock http://naust/v1/status` or `http://127.0.0.1:9701/v1/status` |
| Probes | `/readyz`, `/healthz` on either listener |
| Metrics | `http://127.0.0.1:9701/metrics` |
| Events | CloudEvents to each `sinks` entry; `journalctl -u naust-<id>` has the same facts as JSON |
| Exit status | 0 saved and verified; 1 needs a human; the unit does not restart on its own |

## Operating it

```console
journalctl -u naust-midgard -f          # structured JSON events: presence, saves, join code
naust parse /path/to/capture.log        # replay a log offline
systemctl stop naust-midgard            # drain now; exit status tells you if it was clean
```

An unexpected exit, a save that never confirms, or a save that fails
verification leaves the unit failed with the game process still running when
that is safer. Read the last `drain.finished` or `backend.startup_failed`
event before touching the world files.

## Limits

- x86_64-linux only; the dedicated server is x86-only.
- The module is evaluated on every platform by `nix flake check`, but only a
  real host proves the Steam runtime. Test a fresh world before trusting it
  with an old one.
- Crossplay join codes change on every start. A `discord` sink posts each
  one; without a sink, read `backend.join` from the journal.
- The join-code log line is documented from hosting guides, not from a
  capture. Verify it on the first real crossplay start.
