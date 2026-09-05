"""Human-facing Typer CLI and composition boundary for Naust."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import structlog
import typer
from pydantic import BaseModel, ValidationError

from naust.agent.presence import PresenceTracker
from naust.agent.replay import ReplayEvent, replay
from naust.agent.service import run_agent, run_world
from naust.control.service import run_control
from naust.games.facts import BackendReady, BackendVersion, JoinInfo, SaveCompleted
from naust.games.registry import get_profile
from naust.gateway.service import run_gateway
from naust.log import LogLevel, setup_logging
from naust.settings import NaustSettings

app = typer.Typer(no_args_is_help=True)


@app.callback()
def root(
    ctx: typer.Context,
    log_level: Annotated[
        LogLevel | None,
        typer.Option("--log-level", help="Override the configured log level."),
    ] = None,
) -> None:
    """Run one Naust component with CLI > env > TOML > default precedence."""

    ctx.obj = {}
    if log_level is not None:
        ctx.obj["log_level"] = log_level


def _load_settings(ctx: typer.Context, **component_override: Any) -> NaustSettings:
    overrides = dict(ctx.obj or {})
    overrides.update(component_override)
    try:
        return NaustSettings(**overrides)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
            for item in error.errors(include_url=False)
        )
        raise typer.BadParameter(f"invalid configuration: {details}") from None


def _start_component[ComponentConfig: BaseModel](
    name: str,
    settings: NaustSettings,
    config: ComponentConfig,
    runner: Callable[[ComponentConfig], None],
) -> None:
    setup_logging(settings.log_level)
    structlog.get_logger("naust").info(
        "component.starting",
        component=name,
        config=settings.resolved_config(),
    )
    runner(config)


@app.command()
def agent(
    ctx: typer.Context,
    world: Annotated[
        str | None,
        typer.Option("--world", help="Supervise this configured world until it drains."),
    ] = None,
    control_url: Annotated[
        str | None,
        typer.Option("--control-url", help="Override Control's HTTP URL."),
    ] = None,
) -> None:
    """Start the per-backend Agent.

    With --world, launch that world's backend, track presence, and drain it
    on idle timeout, SIGTERM, or SIGINT. Exit status 0 means the world was
    saved, verified, and stopped; 1 means it needs a human.
    """

    override = {} if control_url is None else {"control_url": control_url}
    settings = _load_settings(ctx, agent=override) if override else _load_settings(ctx)
    if world is None:
        _start_component("agent", settings, settings.agent, run_agent)
        return
    selected = next((w for w in settings.worlds if w.id == world), None)
    if selected is None:
        known = ", ".join(w.id for w in settings.worlds) or "none"
        raise typer.BadParameter(f"unknown world {world!r}; configured worlds: {known}")
    if settings.agent.backend.executable is None:
        raise typer.BadParameter("agent.backend.executable is required to run a world")
    try:
        get_profile(selected.game)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from None
    setup_logging(settings.log_level)
    structlog.get_logger("naust").info(
        "component.starting", component="agent", world=world, config=settings.resolved_config()
    )
    raise typer.Exit(code=asyncio.run(run_world(selected, settings.agent)))


@app.command()
def control(
    ctx: typer.Context,
    port: Annotated[
        int | None,
        typer.Option("--port", help="Override Control's HTTP listen port."),
    ] = None,
) -> None:
    """Start the authoritative Control stub."""

    override = {} if port is None else {"bind_port": port}
    settings = _load_settings(ctx, control=override) if override else _load_settings(ctx)
    _start_component("control", settings, settings.control, run_control)


@app.command()
def gateway(
    ctx: typer.Context,
    control_url: Annotated[
        str | None,
        typer.Option("--control-url", help="Override Control's HTTP URL."),
    ] = None,
) -> None:
    """Start the always-on Gateway stub."""

    override = {} if control_url is None else {"control_url": control_url}
    settings = _load_settings(ctx, gateway=override) if override else _load_settings(ctx)
    _start_component("gateway", settings, settings.gateway, run_gateway)


def _render_replay_event(event: ReplayEvent) -> list[str]:
    """One stable line per fact worth reading; presence lines only on change."""

    prefix = f"L{event.line_number:>6}"
    lines: list[str] = []
    for fact in event.facts:
        match fact:
            case BackendReady():
                lines.append(f"{prefix}  ready")
            case BackendVersion(version=version):
                lines.append(f"{prefix}  version {version}")
            case SaveCompleted(duration_ms=duration_ms):
                shown = "?" if duration_ms is None else f"{duration_ms:.3f}ms"
                lines.append(f"{prefix}  saved {shown}")
            case JoinInfo(code=code) if code is not None:
                lines.append(f"{prefix}  join-code {code}")
            case _:
                pass
    for transition in event.transitions:
        names = ", ".join(sorted(transition.after.players)) or "-"
        if transition.joined:
            lines.append(f"{prefix}  join  {' '.join(sorted(transition.joined))} -> {names}")
        elif transition.left:
            lines.append(f"{prefix}  leave {' '.join(sorted(transition.left))} -> {names}")
        else:
            lines.append(f"{prefix}  count {transition.count}")
    return lines


@app.command()
def parse(
    logfile: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            help="A Valheim dedicated-server log to replay.",
        ),
    ],
    max_players: Annotated[
        int,
        typer.Option("--max-players", min=1, help="The server's player limit."),
    ] = 10,
    game: Annotated[
        str,
        typer.Option("--game", help="Which game's observer and resolver to use."),
    ] = "valheim",
) -> None:
    """Replay a server log and print the presence timeline.

    Undecodable bytes are replaced rather than rejected: a capture with one
    bad byte is still evidence.
    """

    try:
        profile = get_profile(game)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from None
    tracker = PresenceTracker(max_players=max_players)
    with logfile.open(encoding="utf-8", errors="replace") as lines:
        for event in replay(lines, profile.observer(), profile.resolver(), tracker):
            for rendered in _render_replay_event(event):
                typer.echo(rendered)
    present = ", ".join(sorted(tracker.snapshot.players)) or "-"
    typer.echo(f"present: {tracker.count} [{present}]")
    if tracker.rejected_joins:
        typer.echo(
            f"warning: {tracker.rejected_joins} join(s) exceeded --max-players {max_players}",
            err=True,
        )


def main() -> None:
    app()
