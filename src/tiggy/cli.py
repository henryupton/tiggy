import functools
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import traceback
from datetime import UTC, datetime
from pathlib import Path

import click

from tiggy import __version__, checks, report
from tiggy.checks import Severity
from tiggy.graph import ContainerGraph, Kind, Node
from tiggy.gtm.resources import BuiltInVariable
from tiggy.io import export
from tiggy.report import printable
from tiggy.spec.compiler import Compiled, compile_spec
from tiggy.spec.importer import ImportError_, check, import_version, umask
from tiggy.spec.loader import load_spec, looks_like_spec
from tiggy.spec.problems import Problem, SpecError

SPEC_KEYS = {
    Kind.TAG: "tags",
    Kind.TRIGGER: "triggers",
    Kind.VARIABLE: "variables",
    Kind.FOLDER: "folders",
    Kind.TEMPLATE: "custom_templates",
    Kind.CLIENT: "clients",
    Kind.TRANSFORMATION: "transformations",
    Kind.ZONE: "zones",
    Kind.BUILT_IN_VARIABLE: "built_in_variables",
    Kind.GTAG_CONFIG: "gtag_configs",
}


class InputError(click.ClickException):
    """Exit 2: tiggy could not run (as opposed to 1: it ran and found problems)."""

    exit_code = 2

    def __init__(self, message: str):
        super().__init__("\n".join(printable(line) for line in message.split("\n")))


def exits_2_on_crash(command):
    """A bug in Tiggy exits 2 like any other failure to run, so it can't read as `1: they differ`."""

    @functools.wraps(command)
    def run(*args, **kwargs):
        try:
            return command(*args, **kwargs)
        except (click.ClickException, click.exceptions.Exit, click.Abort, SystemExit):
            raise
        except KeyboardInterrupt:
            raise SystemExit(130) from None
        except BrokenPipeError:
            # Whoever read the output stopped early (`| head`); nothing went wrong here.
            _silence(sys.stdout, sys.stderr)
            raise SystemExit(141) from None
        except Exception:
            try:
                traceback.print_exc()
            except OSError:
                _silence(sys.stderr)
            _err("tiggy hit a bug; the traceback above is worth reporting")
            raise SystemExit(2) from None

    return run


def main() -> None:
    """The `tiggy` command. A closed pipe ends it with 141, as for any Unix tool, click's own output too."""
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    try:
        cli.main(prog_name="tiggy")
    except BrokenPipeError:
        _silence(sys.stdout, sys.stderr)
        raise SystemExit(141) from None


def _silence(*streams) -> None:
    """Point streams at /dev/null, so nothing (exit-time flushes included) writes to a closed pipe."""
    devnull = os.open(os.devnull, os.O_WRONLY)
    for stream in streams:
        try:
            os.dup2(devnull, stream.fileno())
        except (AttributeError, OSError, ValueError):
            pass


def _out(text: str, nl: bool = True) -> None:
    """Output on stdout, where failing to write is a reason Tiggy couldn't run, not a bug."""
    if sys.stdout is None:
        raise InputError("stdout is closed, so there's nowhere to write")
    try:
        click.echo(text, nl=nl)
        sys.stdout.flush()
    except BrokenPipeError:
        raise
    except OSError as e:
        _silence(sys.stdout)  # or the exit-time flush fails the same way
        raise InputError(f"can't write the output: {e.strerror or e}") from e


def _err(message: str) -> None:
    """A diagnostic on stderr that never stops the command: a closed stderr only loses the message."""
    try:
        click.echo(message, err=True)
    except OSError:
        _silence(sys.stderr)


@click.group(no_args_is_help=True)
@click.version_option(__version__, prog_name="tiggy")
def cli() -> None:
    """Declarative Google Tag Manager."""


@cli.command()
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text", show_default=True)
@click.option("--strict", is_flag=True, help="Fail on warnings as well as errors.")
@exits_2_on_crash
def validate(source: Path, fmt: str, strict: bool) -> None:
    """Check that everything in a container refers to something that exists.

    SOURCE is a spec directory, or a container export (Admin > Export Container). Exits 0 when
    valid, 1 when not (a spec file it can't read included), 2 when SOURCE isn't a spec or export.
    """
    locations: dict[Node, str] = {}
    files: list[str] = []
    if source.is_dir():
        compiled = _compile(source, fmt)
        files = [str(w) for w in compiled.warnings]
        labels = {(Kind.GTAG_CONFIG, i): label for i, label in compiled.labels.items()}
        graph = ContainerGraph(compiled.version, labels)
        locations = _locations(graph, compiled)
    else:
        read = _read_export(source)
        misread = read.misread()
        if misread:
            raise InputError(f"{source}: Tiggy reads a value differently from how it's written, at {misread}")
        graph = ContainerGraph(read.export.container_version)
    findings = checks.run(graph)
    output = (
        report.as_json(graph, findings, locations, files)
        if fmt == "json"
        else report.text(graph, findings, locations, files)
    )
    _out(output)
    failing = {Severity.ERROR, Severity.WARNING} if strict else {Severity.ERROR}
    if any(f.severity in failing for f in findings) or (strict and files):
        raise SystemExit(1)


@cli.command("import")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("spec_dir", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--check", "check_only", is_flag=True, help="Compare instead of writing; exit 1 if they differ."
)
@exits_2_on_crash
def import_(source: Path, spec_dir: Path, check_only: bool) -> None:
    """Write a container export out as a YAML spec in SPEC_DIR.

    The spec is proven to compile back to the same container before it is written. With
    --check, nothing is written: SOURCE is compared with the spec already in SPEC_DIR.
    """
    read = _read_export(source)
    ignored = read.outside_container
    if ignored:
        _err(
            printable(f"note: {source} has fields outside the container, not imported: {', '.join(ignored)}")
        )
    if check_only:
        _require_spec(spec_dir)
        try:
            comparison = check(read.raw_version, spec_dir)
        except SpecError as e:
            raise InputError(f"the spec in {spec_dir} doesn't load:\n{_problems(e)}") from e
        except ImportError_ as e:
            raise InputError(str(e)) from e
        _print_warnings(comparison.warnings)
        differences = comparison.differences
        if differences:
            for line in differences:
                _out(f"{click.style('≠', fg='yellow')} {printable(line)}")
            _out(click.style(f"{len(differences)} differences between {source} and {spec_dir}", fg="yellow"))
            raise SystemExit(1)
        _out(click.style(f"{spec_dir} is equivalent to {source}", fg="green"))
        return
    try:
        result = import_version(read.raw_version, spec_dir)
    except SpecError as e:
        raise InputError(_problems(e)) from e
    except ImportError_ as e:
        raise InputError(str(e)) from e
    counts = ", ".join(f"{n} {key.replace('_', ' ')}" for key, n in result.counts.items())
    container = read.export.container_version.container
    name = container.name if container and container.name else "container"
    _out(f"Imported {click.style(printable(name), bold=True)} into {spec_dir}: {counts or 'nothing'}")
    _out(f"  {len(result.fragments)} YAML files, {len(result.sidecars)} code files")
    _out(click.style("  verified: the spec compiles back to the same container", fg="green"))
    if result.sidecars:
        _out(f"\nCode files must stay byte-exact. {_precommit_hint(spec_dir)}")


@cli.command("export")
@click.argument("spec_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "-o",
    "--output",
    type=click.Path(dir_okay=False, allow_dash=True, readable=False),
    help="Write here, not stdout (- is stdout too).",
)
@exits_2_on_crash
def export_(spec_dir: Path, output: str | None) -> None:
    """Compile a spec to a container export, the JSON GTM's Import Container reads."""
    compiled = _compile(spec_dir, "text", err=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    text = export.dumps(
        export.ContainerExport(exportFormatVersion=2, exportTime=stamp, containerVersion=compiled.version)
    )
    try:
        export.reads(text)
    except export.ExportError as e:
        raise InputError(f"the compiled container isn't one Tiggy can read back: {e}") from e
    if output and output != "-":
        _write(Path(output), text.encode("utf-8"))
    else:
        _out(text, nl=False)
    _print_warnings(compiled.warnings)


def _write(path: Path, data: bytes) -> None:
    """Write a file whole or not at all: a failed write leaves whatever was there before."""
    try:
        path = path.resolve()  # through a link to its target, rather than replacing the link
        if path.exists() and not path.is_file():  # a FIFO or device: nothing to replace
            path.write_bytes(data)
            return
        mode = path.stat().st_mode & 0o7777 if path.exists() else 0o666 & ~umask()
        try:
            fd, staging = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        except OSError:  # nowhere to stage it, such as /dev/fd for /dev/stdout
            path.write_bytes(data)
            return
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.chmod(staging, mode)
            os.replace(staging, path)
        except BaseException:
            os.unlink(staging)
            raise
    except OSError as e:
        raise InputError(f"can't write {path}: {e.strerror or e}") from e


def _problems(error: SpecError) -> str:
    return "\n".join(printable(str(p)) for p in error.problems)


def _compile(spec_dir: Path, fmt: str, err: bool = False) -> Compiled:
    """A spec compiled, or its problems printed and exit 1 (exit 2 when it isn't a spec at all)."""
    _require_spec(spec_dir)
    try:
        return compile_spec(load_spec(spec_dir))
    except SpecError as e:
        _print_problems(e, fmt, err)
        raise SystemExit(1) from e


def _require_spec(spec_dir: Path) -> None:
    try:
        candidate = spec_dir.is_dir() and looks_like_spec(spec_dir)
    except (OSError, RuntimeError) as e:
        raise InputError(f"can't read {spec_dir}: {getattr(e, 'strerror', None) or e}") from e
    if not candidate:
        raise InputError(f"{spec_dir} is not a spec directory: it holds no .yml fragments")


def _read_export(source: Path) -> export.Source:
    try:
        return export.read(source)
    except export.ExportError as e:
        raise InputError(str(e)) from e


def _locations(graph: ContainerGraph, compiled: Compiled) -> dict[Node, str]:
    found = {}
    for node in graph.nodes:
        key = SPEC_KEYS.get(node.kind, "")
        origin = compiled.id_origins.get((key, node.id)) if node.id else None
        if origin is None and isinstance(node.resource, BuiltInVariable) and node.resource.type:
            origin = compiled.origins.get((key, node.resource.type.export_value))
        origin = origin or compiled.origins.get((key, node.name))
        if origin:
            found[node] = str(origin)
    return found


def _print_problems(error: SpecError, fmt: str, err: bool = False) -> None:
    """Spec problems, on stdout as validate's report, or on stderr where stdout is the data."""
    say = _err if err else _out
    if fmt == "json":
        say(json.dumps({"problems": [str(p) for p in error.problems]}, indent=2))
        return
    for problem in error.problems:
        say(f"{click.style('✗', fg='red')} {printable(str(problem))}")
    say(click.style(f"{len(error.problems)} problems in the spec", fg="red"))


def _print_warnings(warnings: list[Problem]) -> None:
    for warning in warnings:
        _err(f"{click.style('!', fg='yellow')} {printable(str(warning))}")


def _precommit_hint(spec_dir: Path) -> str:
    """How to keep pre-commit's whitespace fixers off the code files, with a path that will match."""
    relative = _repo_relative(spec_dir)
    if relative is None:
        return "Keep whitespace fixers (such as pre-commit's) away from its .tpl, .html and .js files."
    prefix = "" if relative == "." else f"{re.escape(relative)}/"
    pattern = f"^{prefix}.*\\.(tpl|html|js)$".replace("'", "''")
    return f"If pre-commit runs whitespace fixers, exclude them:\n  exclude: '{pattern}'"


def _repo_relative(path: Path) -> str | None:
    """The spec's path within its git repository, spelled the way git spells it."""
    try:
        prefix = subprocess.run(
            ["git", "-C", str(path.resolve()), "rev-parse", "--show-prefix"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return prefix.rstrip("/") or "."
