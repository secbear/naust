"""Human-facing Typer CLI and composition boundary for Naust."""

from collections.abc import Callable
from typing import Annotated, Any

import structlog
import typer
from pydantic import BaseModel, ValidationError

from naust.agent.service import run_agent
from naust.control.service import run_control
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
    control_url: Annotated[
        str | None,
        typer.Option("--control-url", help="Override Control's HTTP URL."),
    ] = None,
) -> None:
    """Start the per-backend Agent stub."""

    override = {} if control_url is None else {"control_url": control_url}
    settings = _load_settings(ctx, agent=override) if override else _load_settings(ctx)
    _start_component("agent", settings, settings.agent, run_agent)


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


def main() -> None:
    app()
