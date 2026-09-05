# Hermetic equivalents of the Python checks run by GitHub Actions, plus an
# evaluation-only test of the NixOS module so a broken option or unit shows
# up on every platform, not only on a Linux host.
{ self, inputs, ... }:
{
  perSystem =
    {
      pkgs,
      lib,
      checkEnv,
      ...
    }:
    let
      example = inputs.nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          self.nixosModules.naust
          (
            { modulesPath, ... }:
            {
              imports = [ "${modulesPath}/profiles/minimal.nix" ];
              nixpkgs.config.allowUnfreePredicate =
                pkg:
                builtins.elem (lib.getName pkg) [
                  "steamcmd"
                  "steam-unwrapped"
                ];
              boot.loader.grub.enable = false;
              fileSystems."/" = {
                device = "/dev/null";
                fsType = "ext4";
              };
              system.stateVersion = "26.05";
              services.naust = {
                enable = true;
                passwordFile = "/run/keys/valheim-password";
                onDrained = "poweroff";
                extraServerArgs = [
                  "-saveinterval"
                  "1800"
                ];
                settings.agent.backend.save_timeout = "PT4M";
                sinks = [
                  {
                    kind = "discord";
                    urlFile = "/run/keys/discord-webhook";
                  }
                  {
                    kind = "webhook";
                    urlFile = "/run/keys/worker-url";
                    tokenFile = "/run/keys/worker-token";
                  }
                ];
                preStartCommand = "echo restore";
                worlds.midgard = {
                  name = "Midgard";
                  idleTimeout = "PT30M";
                  connectionGracePeriod = "PT10M";
                };
                worlds.asgard = {
                  mode = "steam-direct";
                  gamePort = 2500;
                  openFirewall = true;
                  autoStart = false;
                  idleTimeout = null;
                };
              };
            }
          )
        ];
      };
      c = example.config;
      unit = c.systemd.services."naust-midgard";
      settings = c.services.naust.resolvedSettings;
      worlds = lib.listToAttrs (map (w: lib.nameValuePair w.id w) settings.worlds);
      expect = name: condition: lib.assertMsg condition "naust NixOS module: ${name}";
      ok =
        assert expect "unit runs as the naust user" (unit.serviceConfig.User == "naust");
        assert expect "unit never restarts on its own" (unit.serviceConfig.Restart == "no");
        assert expect "drain has room to finish" (unit.serviceConfig.TimeoutStopSec == 300);
        assert expect "password and sink secrets are systemd credentials" (
          unit.serviceConfig.LoadCredential == [
            "password:/run/keys/valheim-password"
            "sink-0-url:/run/keys/discord-webhook"
            "sink-1-url:/run/keys/worker-url"
            "sink-1-token:/run/keys/worker-token"
          ]
        );
        assert expect "unit is notify-typed" (unit.serviceConfig.Type == "notify");
        assert expect "socket directory is managed" (unit.serviceConfig.RuntimeDirectory == "naust");
        assert expect "pre-start hook runs as root before the update" (
          builtins.length unit.serviceConfig.ExecStartPre == 2
          && lib.hasPrefix "+" (builtins.head unit.serviceConfig.ExecStartPre)
        );
        assert expect "surface is configured" (
          settings.agent.surface.socket_dir == "/run/naust" && settings.agent.surface.metrics_port == 9701
        );
        assert expect "orchestrator mode omits the idle timeout" (!(worlds.asgard ? idle_timeout));
        assert expect "poweroff hook runs as root" (
          lib.any (s: lib.hasPrefix "+" s) unit.serviceConfig.ExecStopPost
        );
        assert expect "world starts at boot" (unit.wantedBy == [ "multi-user.target" ]);
        assert expect "manual world does not start at boot" (
          c.systemd.services."naust-asgard".wantedBy == [ ]
        );
        assert expect "crossplay world has no game port" (!(worlds.midgard ? game_port));
        assert expect "steam-direct world keeps its port" (worlds.asgard.game_port == 2500);
        assert expect "world timings are passed through" (
          worlds.midgard.idle_timeout == "PT30M" && worlds.midgard.connection_grace_period == "PT10M"
        );
        assert expect "settings override merges" (settings.agent.backend.save_timeout == "PT4M");
        assert expect "executable lives under serverDir" (
          settings.agent.backend.executable == "/var/lib/naust/server/valheim_server.x86_64"
        );
        assert expect "steam-direct firewall ports open" (
          c.networking.firewall.allowedUDPPorts == [
            2500
            2501
          ]
        );
        # Forcing the unit text proves no import-from-derivation hides in the
        # module: it must evaluate on every platform without building.
        assert expect "unit text evaluates without building" (
          builtins.isString c.systemd.units."naust-midgard.service".text
        );
        true;
    in
    {
      checks.python = pkgs.runCommand "naust-python-checks" { nativeBuildInputs = [ checkEnv ]; } ''
        export PYTHONDONTWRITEBYTECODE=1
        ruff format --no-cache --check ${../../..}/src ${../../..}/tests
        ruff check --no-cache ${../../..}/src ${../../..}/tests
        pytest -p no:cacheprovider ${../../..}/tests
        touch $out
      '';

      checks.nixos-module = pkgs.writeText "naust-nixos-module-eval" (
        assert ok;
        builtins.toJSON {
          user = unit.serviceConfig.User;
          worlds = builtins.attrNames worlds;
        }
      );
    };
}
