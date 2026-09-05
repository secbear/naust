# NixOS module: run Naust-supervised Valheim worlds on one host.
#
# Each configured world becomes `naust-<id>.service`. The unit updates the
# dedicated server with steamcmd, launches `naust agent --world <id>` inside
# Steam's FHS environment (which the game's PlayFab plugin needs), and stops
# when the world drains: on idle timeout, or on `systemctl stop`. A successful
# drain exits 0, and `onDrained = "poweroff"` turns that into a host shutdown,
# which is the whole point on an on-demand cloud VM.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.naust;
  tomlFormat = pkgs.formats.toml { };

  worldSettings =
    id: world:
    {
      inherit id;
      inherit (world) name owner mode;
      idle_timeout = world.idleTimeout;
      connection_grace_period = world.connectionGracePeriod;
    }
    // lib.optionalAttrs (world.mode == "steam-direct") {
      game_port = world.gamePort;
    };

  generatedSettings = {
    log_level = cfg.logLevel;
    agent.backend = {
      executable = "${cfg.serverDir}/valheim_server.x86_64";
      save_dir = cfg.saveDir;
      max_players = cfg.maxPlayers;
      extra_args = cfg.extraServerArgs;
    };
    worlds = lib.mapAttrsToList worldSettings cfg.worlds;
  };

  configDir = pkgs.writeTextDir "naust.toml" (
    builtins.readFile (tomlFormat.generate "naust.toml" cfg.resolvedSettings)
  );

  # Steam's FHS environment plus the libraries libparty.so (PlayFab, used for
  # crossplay) links against and Steam itself does not guarantee.
  steamRun =
    (pkgs.steam.override {
      extraLibraries =
        p: with p; [
          libpulseaudio
          stdenv.cc.cc.lib
        ];
    }).run;

  steamcmd = pkgs.steamcmd.override { steamRoot = "${cfg.dataDir}/steam"; };

  worldService =
    id: world:
    let
      start = pkgs.writeShellScript "naust-${id}-start" ''
        set -euo pipefail
        if [ -n "''${CREDENTIALS_DIRECTORY:-}" ] && [ -r "$CREDENTIALS_DIRECTORY/password" ]; then
          NAUST_AGENT__BACKEND__PASSWORD="$(cat "$CREDENTIALS_DIRECTORY/password")"
          export NAUST_AGENT__BACKEND__PASSWORD
        fi
        exec ${lib.getExe steamRun} ${lib.getExe cfg.package} agent --world ${lib.escapeShellArg id}
      '';
      update = pkgs.writeShellScript "naust-${id}-update" ''
        set -euo pipefail
        mkdir -p ${lib.escapeShellArg cfg.serverDir} ${lib.escapeShellArg cfg.saveDir}
        ${lib.getExe steamcmd} \
          +@sSteamCmdForcePlatformType linux \
          +force_install_dir ${lib.escapeShellArg cfg.serverDir} \
          +login anonymous \
          +app_update ${toString cfg.steamAppId} \
          +quit
      '';
      afterDrain = pkgs.writeShellScript "naust-${id}-after-drain" ''
        # Only a verified, successful drain (exit 0) may trigger the hook.
        if [ "''${SERVICE_RESULT:-}" = success ] && [ "''${EXIT_STATUS:-1}" = 0 ]; then
          ${lib.optionalString (cfg.onDrained == "poweroff") "${pkgs.systemd}/bin/systemctl poweroff"}
          ${lib.optionalString (cfg.postDrainCommand != null) cfg.postDrainCommand}
        fi
      '';
      hookWanted = cfg.onDrained != "none" || cfg.postDrainCommand != null;
    in
    {
      description = "Naust world ${id} (${world.name})";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      wantedBy = lib.optional world.autoStart "multi-user.target";
      environment = {
        HOME = cfg.dataDir;
        NAUST_LOG_LEVEL = cfg.logLevel;
      };
      serviceConfig = {
        Type = "exec";
        User = cfg.user;
        Group = cfg.group;
        WorkingDirectory = configDir;
        ExecStartPre = lib.optional cfg.updateOnStart update;
        ExecStart = start;
        ExecStopPost = lib.optional hookWanted "+${afterDrain}";
        LoadCredential = lib.optional (cfg.passwordFile != null) "password:${cfg.passwordFile}";
        # naust drains the game on SIGTERM; give the whole sequence room.
        KillMode = "mixed";
        KillSignal = "SIGTERM";
        TimeoutStopSec = cfg.stopTimeout;
        # Exit 1 means the world needs a human. Never loop on it.
        Restart = "no";
        PrivateTmp = true;
        StateDirectory = lib.optional (cfg.dataDir == "/var/lib/naust") "naust";
      };
    };
in
{
  options.services.naust = {
    enable = lib.mkEnableOption "Naust, scale-to-zero supervision for Valheim worlds";

    package = lib.mkOption {
      type = lib.types.package;
      description = "The naust package providing the `naust` command.";
    };

    user = lib.mkOption {
      type = lib.types.str;
      default = "naust";
      description = "System user the game server and agent run as.";
    };

    group = lib.mkOption {
      type = lib.types.str;
      default = "naust";
      description = "Group of {option}`services.naust.user`.";
    };

    dataDir = lib.mkOption {
      type = lib.types.path;
      default = "/var/lib/naust";
      description = "Home of the service user; holds the Steam root, server, and worlds by default.";
    };

    serverDir = lib.mkOption {
      type = lib.types.path;
      default = "${cfg.dataDir}/server";
      defaultText = lib.literalExpression ''"''${config.services.naust.dataDir}/server"'';
      description = "Where steamcmd installs the dedicated server (app 896660).";
    };

    saveDir = lib.mkOption {
      type = lib.types.path;
      default = "${cfg.dataDir}/worlds";
      defaultText = lib.literalExpression ''"''${config.services.naust.dataDir}/worlds"'';
      description = "Passed to the game as `-savedir`; worlds live in `worlds_local/` beneath it.";
    };

    steamAppId = lib.mkOption {
      type = lib.types.int;
      default = 896660;
      description = "Steam app id of the dedicated server that steamcmd installs.";
    };

    updateOnStart = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Run `steamcmd +app_update` before every start. Keeps the server on the
        current Steam build, which the game requires for clients to join.
      '';
    };

    passwordFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = ''
        File containing the server password, loaded with systemd credentials.
        The game only accepts the password on its command line, so it is
        visible in the process table of this host.
      '';
    };

    maxPlayers = lib.mkOption {
      type = lib.types.ints.between 1 64;
      default = 10;
      description = "Player limit the presence tracker enforces as an upper bound.";
    };

    extraServerArgs = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [
        "-preset"
        "hard"
        "-saveinterval"
        "1800"
      ];
      description = "Extra arguments appended to the dedicated server command line.";
    };

    logLevel = lib.mkOption {
      type = lib.types.enum [
        "DEBUG"
        "INFO"
        "WARNING"
        "ERROR"
      ];
      default = "INFO";
      description = "Log level for naust's structured JSON log (journald).";
    };

    stopTimeout = lib.mkOption {
      type = lib.types.int;
      default = 300;
      description = ''
        Seconds systemd allows for the drain (save, verify, exit) before it
        kills the unit. Must exceed the agent's own save and stop timeouts.
      '';
    };

    onDrained = lib.mkOption {
      type = lib.types.enum [
        "none"
        "poweroff"
      ];
      default = "none";
      description = ''
        What to do after a world drains successfully. `poweroff` shuts the
        host down, which is how an on-demand VM stops billing.
      '';
    };

    postDrainCommand = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "/run/current-system/sw/bin/systemctl start restic-backups-worlds.service";
      description = ''
        Shell command run as root after a successful drain, before
        {option}`services.naust.onDrained` acts.
      '';
    };

    settings = lib.mkOption {
      type = tomlFormat.type;
      default = { };
      description = ''
        Extra `naust.toml` settings merged over the generated ones. Use it for
        timeouts under `agent.backend` or anything this module does not expose.
      '';
    };

    resolvedSettings = lib.mkOption {
      type = tomlFormat.type;
      readOnly = true;
      internal = true;
      description = "The generated configuration, for inspection and tests.";
    };

    worlds = lib.mkOption {
      default = { };
      description = "Worlds to supervise, keyed by their stable id.";
      type = lib.types.attrsOf (
        lib.types.submodule (
          { name, ... }:
          {
            options = {
              name = lib.mkOption {
                type = lib.types.str;
                default = name;
                description = "Display name shown to players.";
              };
              owner = lib.mkOption {
                type = lib.types.str;
                default = "naust";
                description = "A label for who this world belongs to.";
              };
              mode = lib.mkOption {
                type = lib.types.enum [
                  "crossplay"
                  "steam-direct"
                ];
                default = "crossplay";
                description = ''
                  `crossplay` uses the PlayFab backend and a join code; every
                  platform can join and no inbound port is needed.
                  `steam-direct` listens on {option}`gamePort` and its query
                  port for Steam clients only.
                '';
              };
              gamePort = lib.mkOption {
                type = lib.types.port;
                default = 2456;
                description = "UDP game port for steam-direct worlds; the query port is one higher.";
              };
              openFirewall = lib.mkOption {
                type = lib.types.bool;
                default = false;
                description = "Open the game and query ports for a steam-direct world.";
              };
              idleTimeout = lib.mkOption {
                type = lib.types.str;
                default = "PT15M";
                description = "ISO 8601 duration the world may be empty before it drains.";
              };
              connectionGracePeriod = lib.mkOption {
                type = lib.types.str;
                default = "PT3M";
                description = "ISO 8601 duration after start during which the idle timer does not fire.";
              };
              autoStart = lib.mkOption {
                type = lib.types.bool;
                default = true;
                description = "Start this world at boot.";
              };
            };
          }
        )
      );
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.worlds != { };
        message = "services.naust.enable is set but services.naust.worlds is empty";
      }
      {
        assertion = pkgs.stdenv.hostPlatform.isx86_64 && pkgs.stdenv.hostPlatform.isLinux;
        message = "the Valheim dedicated server only runs on x86_64-linux";
      }
    ];

    services.naust.resolvedSettings = lib.recursiveUpdate generatedSettings cfg.settings;

    users.users.${cfg.user} = lib.mkIf (cfg.user == "naust") {
      isSystemUser = true;
      inherit (cfg) group;
      home = cfg.dataDir;
      createHome = true;
      description = "Naust game server supervisor";
    };
    users.groups.${cfg.group} = lib.mkIf (cfg.group == "naust") { };

    systemd.services = lib.mapAttrs' (
      id: world: lib.nameValuePair "naust-${id}" (worldService id world)
    ) cfg.worlds;

    networking.firewall.allowedUDPPorts = lib.concatLists (
      lib.mapAttrsToList (
        _: world:
        lib.optionals (world.mode == "steam-direct" && world.openFirewall) [
          world.gamePort
          (world.gamePort + 1)
        ]
      ) cfg.worlds
    );

    environment.systemPackages = [ cfg.package ];
  };
}
