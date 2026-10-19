import functools
import io
import os
import re
import signal
import subprocess
import sys
import tempfile
import traceback
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click

from tiggy import __version__, checks, report
from tiggy.checks import Severity
from tiggy.graph import ContainerGraph, Kind, Node
from tiggy.gtm.resources import BuiltInVariable
from tiggy.io import export
from tiggy.report import plural, printable
from tiggy.secrets import Fetching, Offline, Resolve, SecretError, Store, is_placeholder, load_targets
from tiggy.spec import canon
from tiggy.spec.compiler import Compiled, check_spec, compile_spec
from tiggy.spec.importer import ImportError_, check, find_secrets, import_version, umask
from tiggy.spec.loader import load_spec, looks_like_spec
from tiggy.spec.problems import Origin, Problem, SpecError

CLOSED_STDOUT = "stdout is closed, so there's nowhere to write"
FORMATS = {"text": report.text, "json": report.as_json, "github": report.github}
DRIFT_FORMATS = {"text": report.drift_text, "json": report.drift_json, "github": report.drift_github}
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
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):  # so ✗ on a cp1252 pipe is \u2717, not a crash
            stream.reconfigure(errors="backslashreplace")
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


def _out(text: str | bytes, nl: bool = True) -> None:
    """Output on stdout, where failing to write is a reason Tiggy couldn't run, not a bug.

    Bytes go out exactly, without the line endings a Windows text stream would add.
    """
    if sys.stdout is None:
        raise InputError(CLOSED_STDOUT)
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
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Declarative Google Tag Manager."""
    if os.environ.get("NO_COLOR"):  # https://no-color.org
        ctx.color = False


@cli.command()
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--format",
    "fmt",
    type=click.Choice(sorted(FORMATS)),
    default="text",
    show_default=True,
    help="github writes workflow commands, so findings show on a pull request's diff.",
)
@click.option("--strict", is_flag=True, help="Fail on warnings as well as errors.")
@exits_2_on_crash
def validate(source: Path, fmt: str, strict: bool) -> None:
    """Check that everything in a container refers to something that exists.

    SOURCE is a spec directory, or a container export (Admin > Export Container). A spec's problems
    are reported with the errors the rest of it has (its warnings once it has no problems), at paths
    relative to the working directory when the spec is inside it. Exits 0 when valid, 1 when not, 2
    when SOURCE isn't a spec or export.
    """
    graph: ContainerGraph | None = None
    findings: list[checks.Finding] = []
    locations: dict[Node, str] = {}
    files: list[str] = []
    problems: list[Problem] = []
    if source.is_dir():
        _require_spec(source)
        prefix = _prefix(source)
        checked = check_spec(load_spec(source), Offline())
        problems = _relocated(checked.problems, prefix)
        files = [str(w) for w in _relocated(checked.warnings, prefix)]
        if checked.compiled is not None:
            compiled = checked.compiled
            labels = {(Kind.GTAG_CONFIG, i): label for i, label in compiled.labels.items()}
            unversioned = compiled.version.model_copy(
                update={"container_version_id": None}
            )  # a spec has none
            graph = ContainerGraph(unversioned, labels)
            locations = _locations(graph, compiled, prefix)
    else:
        read = _read_export(source)
        misread = read.misread()
        if misread:
            raise InputError(f"{source}: Tiggy reads a value differently from how it's written, at {misread}")
        graph = ContainerGraph(read.export.container_version)
    if graph is not None:
        findings = checks.run(graph, spec=source.is_dir())
    if problems:  # warnings wait for a spec that compiles: unused or never-fires may only echo a problem
        findings = [f for f in findings if f.severity is Severity.ERROR]
    _out(FORMATS[fmt](graph, findings, locations, files, problems))
    failing = {Severity.ERROR, Severity.WARNING} if strict else {Severity.ERROR}
    if problems or any(f.severity in failing for f in findings) or (strict and files):
        raise SystemExit(1)


TARGETS = click.option(
    "--targets",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    envvar="TIGGY_TARGETS",
    help="The targets file naming the secret store (or set TIGGY_TARGETS).",
)


@cli.command("import")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("spec_dir", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--check", "check_only", is_flag=True, help="Compare instead of writing; exit 1 if they differ."
)
@click.option(
    "--resolve-secrets", is_flag=True, help="With --check, fetch secrets and compare their values too."
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(sorted(DRIFT_FORMATS)),
    help="With --check (default text); github writes workflow commands, so differences show on a diff.",
)
@click.option(
    "--exact",
    is_flag=True,
    help="With --check, count objects that match only once order and empty lists are ignored.",
)
@TARGETS
@exits_2_on_crash
def import_(
    source: Path,
    spec_dir: Path,
    check_only: bool,
    resolve_secrets: bool,
    fmt: str | None,
    exact: bool,
    targets: Path | None,
) -> None:
    """Write a container export out as a YAML spec in SPEC_DIR.

    The spec is proven to compile back to the same container before it is written, and secrets
    (access tokens, API secrets) are written as {$secret: name} references, never their values.
    With --check, nothing is written: SOURCE is compared with the spec already in SPEC_DIR, every
    difference listed at the spec's file and line, and secret values only with --resolve-secrets.
    """
    if resolve_secrets and not (check_only and targets):
        raise InputError("--resolve-secrets is for --check, with --targets naming the secret store")
    for given, flag in ((fmt, "--format"), (exact, "--exact")):
        if given and not check_only:
            raise InputError(f"{flag} is for --check")
    if sys.stdout is None and not check_only:  # refused before anything is written, not after
        raise InputError(CLOSED_STDOUT)
    read = _read_export(source)
    ignored = read.outside_container
    if ignored:
        _err(
            printable(f"note: {source} has fields outside the container, not imported: {', '.join(ignored)}")
        )
    secrets = find_secrets(read.raw_version)  # an object named with a secret is shown without it
    for finding in checks.unmodelled_enums(ContainerGraph(read.export.container_version)):
        line = secrets.masked(f"{finding.node}: {finding.message}")
        _err(f"{click.style('!', fg='yellow')} {printable(line)}")
    if check_only:
        _require_spec(spec_dir)
        fetching = Fetching(_store(targets)) if resolve_secrets and targets else None
        try:
            comparison = check(read.raw_version, spec_dir, fetching, exact)
        except SpecError as e:
            raise InputError(f"the spec in {spec_dir} doesn't load:\n{_problems(e, spec_dir)}") from e
        except ImportError_ as e:
            raise InputError(str(e)) from e
        if fetching and fetching.failures:
            raise InputError("can't read the spec's secrets:\n" + "\n".join(fetching.failures))
        prefix = _prefix(spec_dir)
        warnings = _relocated(comparison.warnings, prefix)
        _print_warnings(warnings)
        diff = comparison.diff
        if diff.unread_secrets:
            unread = plural(diff.unread_secrets, "secret value")
            _err(f"note: {unread} not compared (--resolve-secrets to compare them)")
        if diff.semantic_only and not exact:
            n = diff.semantic_only
            match, them = ("matches", "it") if n == 1 else ("match", "them")
            _err(
                f"note: {plural(n, 'object')} {match} only once order and empty lists are ignored "
                f"(--exact counts {them})"
            )
        located = replace(diff, changes=[replace(c, origin=_within(c.origin, prefix)) for c in diff.changes])
        _out(DRIFT_FORMATS[fmt or "text"](located, str(source), str(spec_dir), [str(w) for w in warnings]))
        if diff.changes:
            raise SystemExit(1)
        return
    try:
        result = import_version(read.raw_version, spec_dir)
    except SpecError as e:
        raise InputError(_problems(e, spec_dir)) from e
    except ImportError_ as e:
        raise InputError(str(e)) from e
    counts = ", ".join(f"{n} {key.replace('_', ' ')}" for key, n in result.counts.items())
    container = read.export.container_version.container
    name = container.name if container and container.name else "container"
    into = printable(f"{spec_dir}: {counts or 'nothing'}")
    _out(f"Imported {click.style(printable(name), bold=True)} into {into}")
    _out(f"  {len(result.fragments)} YAML files, {len(result.sidecars)} code files")
    _out(click.style("  verified: the spec compiles back to the same container", fg="green"))
    if result.secrets:
        _out(
            f"\n{plural(len(result.secrets), 'secret')} written as {{$secret: name}}. Before exporting, put "
            "each value in the secret store your targets file names, under its name:"
        )
        for name, where in result.secrets.items():
            _out(f"  {printable(name)}  (from {printable(where)})")
    if result.shared:
        _out(
            "\nThese held another secret's value, and are named apart so a value that only matched by chance "
            "(a shared placeholder, say) never sends that secret somewhere else. Store the same value, or "
            "write the value back into the spec if it isn't a secret:"
        )
        for name, matched in result.shared.items():
            _out(f"  {printable(name)}  (the value of {printable(matched)})")
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
@TARGETS
@exits_2_on_crash
def export_(spec_dir: Path, output: str | None, targets: Path | None) -> None:
    """Compile a spec to a container export, the JSON GTM's Import Container reads.

    Each {$secret: name} is read from the store --targets names, since an export holds the value.
    """
    secrets = Fetching(_store(targets)) if targets else Offline()
    compiled = _compile(spec_dir, secrets)
    if isinstance(secrets, Offline) and secrets.refs:
        names = ", ".join(sorted({str(ref) for ref in secrets.refs}))
        raise InputError(f"the spec names secrets ({names}); --targets says where to read them from")
    if isinstance(secrets, Fetching) and secrets.failures:
        raise InputError("can't read the spec's secrets:\n" + "\n".join(secrets.failures))
    if _holds_placeholder(canon.dump(compiled.version)):  # unreachable unless the above has a hole
        raise InputError("a secret has no value, so the export would be wrong")
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    text = export.dumps(
        export.ContainerExport(exportFormatVersion=2, exportTime=stamp, containerVersion=compiled.version)
    )
    try:
        export.reads(text)
    except export.ExportError as e:
        raise InputError(f"the compiled container isn't one Tiggy can read back: {e}") from e
    data = text.encode("utf-8")
    if output and output != "-":
        _write(Path(output), data)
    else:
        _out(data, nl=False)  # the same bytes as -o, on every platform
    _print_warnings(compiled.warnings)


def _write(path: Path, data: bytes) -> None:
    """Write a file whole or not at all: a failed write leaves whatever was there before."""
    try:
        if _device(path):  # nothing to replace
            path.write_bytes(data)
            return
        path = path.resolve()  # through a link to its target, rather than replacing the link
        mode = path.stat().st_mode & 0o7777 if path.exists() else 0o666 & ~umask()
        fd, staging = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
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


def _device(path: Path) -> bool:
    """A FIFO, a device, or /dev/stdout and the like, which Linux resolves through /proc to a file."""
    if os.name != "nt" and path.absolute().parts[1:2] in (("dev",), ("proc",)):
        return True
    return path.exists() and not path.is_file()


def _problems(error: SpecError, spec_dir: Path) -> str:
    return "\n".join(printable(str(p)) for p in _relocated(error.problems, _prefix(spec_dir)))


def _compile(spec_dir: Path, secrets: Resolve) -> Compiled:
    """A spec compiled, or its problems on stderr and exit 1 (exit 2 when it isn't a spec at all)."""
    _require_spec(spec_dir)
    try:
        return compile_spec(load_spec(spec_dir), secrets)
    except SpecError as e:
        for problem in _relocated(e.problems, _prefix(spec_dir)):
            _err(f"{click.style('✗', fg='red')} {printable(str(problem))}")
        _err(click.style(f"{plural(len(e.problems), 'problem')} in the spec", fg="red"))
        raise SystemExit(1) from e


def _store(targets: Path) -> Store:
    try:
        return load_targets(targets)
    except SecretError as e:
        raise InputError(str(e)) from e


def _holds_placeholder(node: Any) -> bool:
    if isinstance(node, dict):
        return any(_holds_placeholder(v) for v in node.values())
    if isinstance(node, list):
        return any(_holds_placeholder(v) for v in node)
    return is_placeholder(node)


def _prefix(spec_dir: Path) -> str:
    """The spec's path from the working directory, so a location opens from wherever Tiggy ran."""
    try:
        relative = spec_dir.resolve().relative_to(Path.cwd().resolve())
    except (OSError, ValueError):  # outside it, so locations stay relative to the spec
        return ""
    return "" if relative == Path() else f"{relative.as_posix()}/"


def _relocated(problems: list[Problem], prefix: str) -> list[Problem]:
    return [Problem(_within(p.origin, prefix), p.message) for p in problems]


def _within(origin: Origin | None, prefix: str) -> Origin | None:
    return Origin(prefix + origin.file, origin.line) if origin else None


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


def _locations(graph: ContainerGraph, compiled: Compiled, prefix: str) -> dict[Node, str]:
    found = {}
    for node in graph.nodes:
        key = SPEC_KEYS.get(node.kind, "")
        origin = compiled.id_origins.get((key, node.id)) if node.id else None
        if origin is None and isinstance(node.resource, BuiltInVariable) and node.resource.type:
            origin = compiled.origins.get((key, node.resource.type.export_value))
        origin = origin or compiled.origins.get((key, node.name))
        if origin:
            found[node] = prefix + str(origin)
    return found


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
        out = subprocess.run(
            ["git", "-C", str(path.resolve()), "rev-parse", "--show-prefix"],
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return os.fsdecode(out).strip().rstrip("/") or "."  # git prints the path's bytes, not the locale's
