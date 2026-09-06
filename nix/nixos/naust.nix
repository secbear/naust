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
      game = "valheim";
      connection_grace_period = world.connectionGracePeriod;
    }
    // lib.optionalAttrs (world.idleTimeout != null) {
      idle_timeout = world.idleTimeout;
    }
    // lib.optionalAttrs (world.mode == "steam-direct") {
      game_port = world.gamePort;
    };

  generatedSettings = {
    log_level = cfg.logLevel;
    agent = {
      state_dir = "${cfg.dataDir}/state";
      backend = {
        executable = "${cfg.serverDir}/valheim_server.x86_64";
        # Only the game runs inside Steam's FHS sandbox. naust stays the unit's
        # main process so SIGTERM reaches it; it signals the game by PID
        # through the sandbox, which does not forward signals itself.
        wrapper = [ (lib.getExe steamRun) ];
        save_dir = cfg.saveDir;
        max_players = cfg.maxPlayers;
        extra_args = cfg.extraServerArgs;
      };
      surface = {
        socket_dir = "/run/naust";
        metrics_host = cfg.metricsHost;
        metrics_port = cfg.metricsPort;
      };
    }
    // lib.optionalAttrs (cfg.rawLogDir != null) {
      raw_log_dir = cfg.rawLogDir;
    };
    worlds = lib.mapAttrsToList worldSettings cfg.worlds;
  };

  # Sink secrets arrive as systemd credentials; the agent reads them by file.
  # The list is handed over as JSON in the environment so the shared config
  # file never contains a per-unit credential path.
  sinkCredentials = lib.concatLists (
    lib.imap0 (
      i: sink:
      [ "sink-${toString i}-url:${sink.urlFile}" ]
      ++ lib.optional (sink.tokenFile != null) "sink-${toString i}-token:${sink.tokenFile}"
    ) cfg.sinks
  );
  sinksJson = builtins.toJSON (
    lib.imap0 (
      i: sink:
      {
        inherit (sink) kind;
        url_file = "\$CREDENTIALS_DIRECTORY/sink-${toString i}-url";
      }
      // lib.optionalAttrs (sink.tokenFile != null) {
        token_file = "\$CREDENTIALS_DIRECTORY/sink-${toString i}-token";
      }
    ) cfg.sinks
  );

  # A directory holding naust.toml, built like any other derivation. Reading
  # the generated file back at evaluation time would be import-from-derivation
  # and would break evaluating this host from another platform.
  configDir = pkgs.linkFarm "naust-config" [
    {
      name = "naust.toml";
      path = tomlFormat.generate "naust.toml" cfg.resolvedSettings;
    }
  ];

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
        ${lib.optionalString (cfg.sinks != [ ]) ''
          NAUST_AGENT__SINKS=${lib.escapeShellArg sinksJson}
          NAUST_AGENT__SINKS="''${NAUST_AGENT__SINKS//\$CREDENTIALS_DIRECTORY/$CREDENTIALS_DIRECTORY}"
          export NAUST_AGENT__SINKS
        ''}
        exec ${lib.getExe cfg.package} agent --world ${lib.escapeShellArg id}
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
        # Only a verified, successful drain (exit 0) may trigger the hooks.
        # The command runs first and to completion; the host is powered off
        # afterwards, never before, so a backup is never raced by shutdown.
        if [ "''${SERVICE_RESULT:-}" = success ] && [ "''${EXIT_STATUS:-1}" = 0 ]; then
          ${lib.optionalString (cfg.postDrainCommand != null) ''
            if ! ( ${cfg.postDrainCommand} ); then
              echo "naust: postDrainCommand failed for world ${id}" >&2
            fi
          ''}
          ${lib.optionalString (cfg.onDrained == "poweroff") "${pkgs.systemd}/bin/systemctl poweroff"}
        fi
      '';
      hookWanted = cfg.onDrained != "none" || cfg.postDrainCommand != null;
      preStart = pkgs.writeShellScript "naust-${id}-pre-start" ''
        set -euo pipefail
        ${cfg.preStartCommand}
      '';
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
        # naust sends READY=1 when the game accepts players, STATUS with the
        # player count, and extends the stop timeout while draining.
        Type = "notify";
        NotifyAccess = "main";
        User = cfg.user;
        Group = cfg.group;
        WorkingDirectory = configDir;
        RuntimeDirectory = "naust";
        RuntimeDirectoryPreserve = true;
        ExecStartPre =
          lib.optional (cfg.preStartCommand != null) "+${preStart}" ++ lib.optional cfg.updateOnStart update;
        ExecStart = start;
        ExecStopPost = lib.optional hookWanted "+${afterDrain}";
        LoadCredential =
          lib.optional (cfg.passwordFile != null) "password:${cfg.passwordFile}" ++ sinkCredentials;
        # naust drains the game on SIGTERM; give the whole sequence room.
        KillMode = "mixed";
        KillSignal = "SIGTERM";
        TimeoutStartSec = cfg.startTimeout;
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

    startTimeout = lib.mkOption {
      type = lib.types.int;
      default = 600;
      description = ''
        Seconds systemd waits for READY=1. A fresh world generates its
        locations on first start and can take several minutes.
      '';
    };

    metricsHost = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = "Address of the read-only listener for metrics and probes.";
    };

    metricsPort = lib.mkOption {
      type = lib.types.nullOr lib.types.port;
      default = 9701;
      description = ''
        Port of the read-only listener serving `/metrics`, `/v1/status`,
        `/readyz`, and `/healthz`. Commands are only accepted on
        `/run/naust/<world>.sock`. `null` disables the listener.
      '';
    };

    sinks = lib.mkOption {
      default = [ ];
      description = ''
        Where lifecycle events go. A `webhook` sink receives CloudEvents; a
        `discord` sink receives short messages such as the join code. URLs
        and tokens are read from files at start through systemd credentials.
      '';
      example = lib.literalExpression ''
        [
          { kind = "discord"; urlFile = "/run/secrets/discord-webhook"; }
          { kind = "webhook"; urlFile = "/run/secrets/worker-url"; tokenFile = "/run/secrets/worker-token"; }
        ]
      '';
      type = lib.types.listOf (
        lib.types.submodule {
          options = {
            kind = lib.mkOption {
              type = lib.types.enum [
                "webhook"
                "discord"
              ];
              description = "`webhook` for CloudEvents JSON, `discord` for a Discord webhook.";
            };
            urlFile = lib.mkOption {
              type = lib.types.path;
              description = "File containing the sink URL.";
            };
            tokenFile = lib.mkOption {
              type = lib.types.nullOr lib.types.path;
              default = null;
              description = "File containing a bearer token for a webhook sink.";
            };
          };
        }
      );
    };

    rawLogDir = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      example = "/var/lib/naust/logs";
      description = ''
        Keep a copy of the game's raw output, one file per session, under
        this directory. The journal carries only naust's events; raw output
        is what a new adapter pattern is written from. Files older than
        {option}`rawLogKeepDays` are removed.
      '';
    };

    rawLogKeepDays = lib.mkOption {
      type = lib.types.int;
      default = 14;
      description = "How long raw session logs are kept.";
    };

    preStartCommand = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "/run/current-system/sw/bin/restic restore latest --target /";
      description = ''
        Shell command run as root before every start, before the steamcmd
        update. The place for a restore from off-host storage. The agent
        still refuses to start on a half-present or shrunken world.
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
                type = lib.types.nullOr lib.types.str;
                default = "PT15M";
                description = ''
                  ISO 8601 duration the world may be empty before it drains.
                  `null` is orchestrator mode: the agent reports and obeys
                  (SIGTERM, `POST /v1/drain`) but never drains on its own.
                '';
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

    systemd.tmpfiles.rules = lib.optional (cfg.rawLogDir != null) (
      "d ${cfg.rawLogDir} 0750 ${cfg.user} ${cfg.group} ${toString cfg.rawLogKeepDays}d"
    );
  };
}
