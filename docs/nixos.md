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
crossplay needs, and streams its log through the presence tracker. When the
world has been empty for `idleTimeout`, or on `systemctl stop`, the agent
asks the game to save, verifies both world files, waits for the game to exit,
and returns 0. With `onDrained = "poweroff"` that exit shuts the host down.

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
| `worlds.<id>.idleTimeout` | `PT15M` | ISO 8601 duration empty before draining |
| `worlds.<id>.connectionGracePeriod` | `PT3M` | idle timer does not fire this long after start |
| `worlds.<id>.autoStart` | `true` | start at boot |
| `updateOnStart` | `true` | `steamcmd +app_update 896660` before each start |
| `extraServerArgs` | `[]` | e.g. `["-preset" "hard" "-saveinterval" "1800"]` |
| `maxPlayers` | `10` | upper bound the tracker enforces |
| `stopTimeout` | `300` | seconds systemd allows the drain |
| `onDrained` / `postDrainCommand` | `none` / `null` | run after a successful drain, as root |
| `settings` | `{}` | merged into `naust.toml`, e.g. `agent.backend.save_timeout = "PT4M"` |
| `dataDir`, `serverDir`, `saveDir` | `/var/lib/naust`… | Steam root, server install, `-savedir` |

World files live in `${saveDir}/worlds_local/<id>.db` and `.fwl`. Back up the
pair together, after a drain, never during one.

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
- Crossplay join codes change on every start. Read `backend.join_code` from
  the journal and publish it yourself; Discord delivery is a later project.
