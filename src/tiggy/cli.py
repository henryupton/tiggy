from pathlib import Path

import click

from tiggy import __version__, checks, report
from tiggy.checks import Severity
from tiggy.graph import ContainerGraph
from tiggy.io import export


class InputError(click.ClickException):
    """Exit 2: tiggy could not run (as opposed to 1: it ran and found problems)."""

    exit_code = 2


@click.group(no_args_is_help=True)
@click.version_option(__version__, prog_name="tiggy")
def cli() -> None:
    """Declarative Google Tag Manager."""


@cli.command()
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text", show_default=True)
@click.option("--strict", is_flag=True, help="Fail on warnings as well as errors.")
def validate(source: Path, fmt: str, strict: bool) -> None:
    """Check that everything in a container refers to something that exists.

    SOURCE is a container export (Admin > Export Container). Exits 0 when valid, 1 when
    not, 2 when the input can't be read.
    """
    try:
        container = export.load(source)
    except export.ExportError as e:
        raise InputError(str(e)) from e
    graph = ContainerGraph(container.container_version)
    findings = checks.run(graph)
    click.echo(report.as_json(graph, findings) if fmt == "json" else report.text(graph, findings))
    failing = {Severity.ERROR, Severity.WARNING} if strict else {Severity.ERROR}
    if any(f.severity in failing for f in findings):
        raise SystemExit(1)
