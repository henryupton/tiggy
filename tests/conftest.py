"""Shared fixtures and helpers. Scenario-specific helpers belong in their own test files.

Exports to test against, as lists of paths (parametrize with `ids=export_id`):
    CDM_EXPORTS          the synthetic Analytics, Marketing, server and legacy containers (tests/fixtures/cdm)
    THIRD_PARTY_EXPORTS  public exports committed byte for byte (tests/fixtures/third_party)
    EXTRA_EXPORTS        real exports from $TIGGY_EXTRA_EXPORTS, a directory outside the repo
    ALL_EXPORTS          all of the above

Helpers: run_tiggy, base_env, scrub, load_export, write_export, edit_spec, replace_in_spec, fragment_of,
line_of.
Fixtures: tiggy (run_tiggy), imported, golden (with --update-goldens).
"""

import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.fixtures.cdm.builders import dumps

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
FIXTURES = TESTS / "fixtures"
GOLDENS = TESTS / "goldens"

CDM_EXPORTS = sorted((FIXTURES / "cdm").glob("*.json"))
THIRD_PARTY_EXPORTS = sorted((FIXTURES / "third_party").glob("*.json"))


def _extra_exports() -> tuple[list[Path], str | None]:
    """Exports from $TIGGY_EXTRA_EXPORTS, or why they can't be used (reported once pytest starts)."""
    raw = os.environ.get("TIGGY_EXTRA_EXPORTS")
    if not raw:
        return [], None
    directory = Path(raw).expanduser().resolve()
    if not directory.is_dir():
        return [], f"TIGGY_EXTRA_EXPORTS={raw} is not a directory"
    if directory == ROOT or ROOT in directory.parents:
        return [], f"TIGGY_EXTRA_EXPORTS={raw} is inside the repository; keep real containers outside it"
    found = sorted(directory.glob("*.json"))
    if not found:
        return [], f"TIGGY_EXTRA_EXPORTS={raw} holds no *.json exports"
    return found, None


EXTRA_EXPORTS, _EXTRA_PROBLEM = _extra_exports()
ALL_EXPORTS = CDM_EXPORTS + THIRD_PARTY_EXPORTS + EXTRA_EXPORTS


def export_id(path: Path) -> str:
    """A parametrize id: `cdm/sgtm_server`, `third_party/...`, or `extra/<file stem>`."""
    if path.is_relative_to(FIXTURES):
        return path.relative_to(FIXTURES).with_suffix("").as_posix()
    return f"extra/{path.stem}"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-goldens",
        action="store_true",
        default=False,
        help="rewrite golden files from the current output instead of comparing with them",
    )


def pytest_configure(config: pytest.Config) -> None:
    if _EXTRA_PROBLEM:
        raise pytest.UsageError(_EXTRA_PROBLEM)


def pytest_report_header(config: pytest.Config) -> str | None:
    if EXTRA_EXPORTS:
        return f"TIGGY_EXTRA_EXPORTS: {len(EXTRA_EXPORTS)} exports from {EXTRA_EXPORTS[0].parent}"
    return None


@pytest.hookimpl(trylast=True)  # after -k and -m have deselected, so `items` is what will run
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """A run given real exports has to test them; otherwise it would pass without ever reading one."""
    if not EXTRA_EXPORTS:
        return
    if not any("extra/" in getattr(getattr(item, "callspec", None), "id", "") for item in items):
        raise pytest.UsageError(
            "TIGGY_EXTRA_EXPORTS is set, but no collected test runs against an extra/ export. "
            "Unset it, or select tests parametrized over ALL_EXPORTS."
        )


# Running the CLI


@dataclass(frozen=True)
class Result:
    code: int
    out: str
    err: str
    raw_out: bytes = field(repr=False, default=b"")
    raw_err: bytes = field(repr=False, default=b"")

    def __str__(self) -> str:
        """Everything, for an assertion message."""
        return f"exit {self.code}\n--- stdout\n{self.out}\n--- stderr\n{self.err}"


VENV = ROOT / ".venv"
# What every run starts from: only what a process needs from the machine, plus a fixed locale and
# terminal, so output can't vary with the shell pytest was started from.
INHERITED = ("PATH", "HOME", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP")
PINNED = {"LC_ALL": "C.UTF-8", "PYTHONUTF8": "1", "COLUMNS": "200", "NO_COLOR": "1"}


def base_env() -> dict[str, str]:
    """The environment every `tiggy` run starts from, before a test's own overrides."""
    environment = {key: os.environ[key] for key in INHERITED if key in os.environ}
    environment.update(PINNED)
    return environment


@functools.cache
def _tiggy() -> str:
    """This checkout's console script, proven (once) to run this checkout's source, never one on PATH."""
    scripts, suffix = (VENV / "Scripts", ".exe") if os.name == "nt" else (VENV / "bin", "")
    executable, python = scripts / f"tiggy{suffix}", scripts / f"python{suffix}"
    if not executable.is_file():
        raise RuntimeError(f"{executable} is missing: run `uv sync` in {ROOT}")
    probe = subprocess.run(
        [python, "-c", "import tiggy; print(tiggy.__file__)"],
        capture_output=True,
        text=True,
        env=base_env(),
        cwd=tempfile.gettempdir(),  # so nothing in the working directory can shadow the install
        check=False,
    )
    expected = (ROOT / "src" / "tiggy" / "__init__.py").resolve()
    found = probe.stdout.strip()
    if probe.returncode != 0 or not found or Path(found).resolve() != expected:
        raise RuntimeError(
            f"{executable} doesn't run this checkout's source: its tiggy is {found or probe.stderr!r}, "
            f"not {expected}. Run `uv sync` in {ROOT}"
        )
    return str(executable)


def run_tiggy(
    *args: str | os.PathLike,
    input: str | bytes | None = None,
    env: Mapping[str, str | None] | None = None,
    cwd: str | os.PathLike | None = None,
    timeout: float = 120,
) -> Result:
    """Run this checkout's `tiggy` console script in its own process.

    stdout and stderr go to files, not pipes, so the exit code is the process's own and nothing
    stops reading early. The environment is base_env() with `env` on top; a None value unsets.
    """
    environment = base_env()
    for key, value in (env or {}).items():
        if value is None:
            environment.pop(key, None)
        else:
            environment[key] = value
    data = input.encode("utf-8") if isinstance(input, str) else input
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        completed = subprocess.run(
            [_tiggy(), *(os.fspath(a) for a in args)],
            input=data,
            stdin=subprocess.DEVNULL if data is None else None,
            stdout=out,
            stderr=err,
            env=environment,
            cwd=cwd,
            timeout=timeout,
            check=False,
        )
        out.seek(0)
        err.seek(0)
        raw_out, raw_err = out.read(), err.read()
    return Result(completed.returncode, _text(raw_out), _text(raw_err), raw_out, raw_err)


def _text(raw: bytes) -> str:
    """Output as text, with Windows' text-mode line endings read back as \\n, as `open()` would."""
    return raw.decode("utf-8", errors="replace").replace(os.linesep, "\n")


FAKES = TESTS / "fakes"  # a stand-in boto3, so specs naming secrets export offline


def secret_store(directory: Path, values: Mapping[str, str]) -> dict[str, str]:
    """The environment for a run that reads secrets from a fake AWS store holding `values`."""
    directory.mkdir(parents=True, exist_ok=True)
    store, targets = directory / "secrets.json", directory / "targets.yml"
    store.write_text(json.dumps(dict(values)), encoding="utf-8")
    targets.write_text("tiggy: 1\nsecrets:\n  aws:\n    region: test-region\n", encoding="utf-8")
    return {"TIGGY_TARGETS": str(targets), "TIGGY_FAKE_SECRETS": str(store), "PYTHONPATH": str(FAKES)}


def export_secrets(export: str | os.PathLike) -> dict[str, str]:
    """The secrets `tiggy import` finds in an export, by the name it writes for each."""
    from tiggy.spec.importer import find_secrets

    return find_secrets(load_export(export)["containerVersion"]).values


def store_for(export: str | os.PathLike, directory: Path) -> dict[str, str]:
    """A fake secret store holding what the spec imported from `export` names."""
    return secret_store(directory, export_secrets(export))


def unread_note(export: str | os.PathLike) -> str:
    """What `import --check` says on stderr about the secrets it didn't fetch for this export's spec."""
    from tiggy.spec.importer import find_secrets

    n = len(find_secrets(load_export(export)["containerVersion"]).held)
    return (
        f"note: {n} secret value{'' if n == 1 else 's'} not compared (--resolve-secrets to compare them)\n"
        if n
        else ""
    )


FINDING_LINE = re.compile(r'[✗⚠] [a-z][a-z -]* "')


def problems_reported(result: Result) -> list[str]:
    """validate's spec problems, then its tally: its report without the header or any findings."""
    lines = result.out.splitlines()
    return [line for line in lines if line.startswith("✗ ") and not FINDING_LINE.match(line)] + lines[-1:]


def scrub(text: str, *paths: str | os.PathLike) -> str:
    """`text` with each absolute path replaced by `<path1>`, `<path2>`, ... in argument order.

    For comparing CLI output with a golden. Each path is matched as given, resolved (macOS reaches
    /var through /private/var) and with forward slashes; longest first, so a path inside another
    keeps its own placeholder, and only where a path name ends, so /tmp/a never matches in /tmp/ab.
    """
    spellings: dict[str, str] = {}
    for n, path in enumerate(paths, 1):
        raw = os.fspath(path)
        if not os.path.isabs(raw):
            raise ValueError(f"scrub takes absolute paths, not {raw!r}")
        for form in (raw, os.path.realpath(raw)):
            for spelling in (form, Path(form).as_posix()):
                spellings.setdefault(spelling, f"<path{n}>")
    for spelling in sorted(spellings, key=len, reverse=True):
        text = re.sub(re.escape(spelling) + r"(?![\w.-])", lambda _, p=spellings[spelling]: p, text)
    return text


@pytest.fixture
def tiggy() -> Callable[..., Result]:
    return run_tiggy


# Exports as data


def load_export(path: str | os.PathLike) -> dict[str, Any]:
    return json.loads(Path(path).read_bytes().decode("utf-8"))


def write_export(export: dict[str, Any], path: str | os.PathLike) -> Path:
    """Writes an export the way GTM does (four-space JSON, no final newline); returns the path."""
    path = Path(path)
    path.write_bytes(dumps(export).encode("utf-8"))
    return path


# Specs


@pytest.fixture(scope="session")
def _imports() -> dict[tuple[Path, str], Path]:
    return {}


@pytest.fixture
def imported(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, _imports: dict[tuple[Path, str], Path]
) -> Callable[..., Path]:
    """`imported(export, name="spec")`: the export imported with `tiggy import`, as tmp_path/name.

    Each export is imported once per session (keyed by its content) and copied, so a test can edit
    its copy freely. Tests about import itself should call run_tiggy instead.
    """

    def make(export: str | os.PathLike, name: str = "spec") -> Path:
        source = Path(export).resolve()
        key = (source, hashlib.sha256(source.read_bytes()).hexdigest())
        if key not in _imports:
            spec = tmp_path_factory.mktemp("import") / "spec"
            result = run_tiggy("import", source, spec)
            assert result.code == 0, f"tiggy import {source} failed\n{result}"
            _imports[key] = spec
        target = tmp_path / name
        shutil.copytree(_imports[key], target, symlinks=True)
        return target

    return make


def edit_spec(spec_dir: Path, relpath: str | os.PathLike, edit: Callable[[str], str]) -> Path:
    """Rewrites one spec file as `edit(text)`, leaving every other byte as it was; returns its path.

    Text is read and written as UTF-8 bytes, so line endings survive. An edit that changes
    nothing fails, since it means the test didn't find what it meant to change.
    """
    path = spec_dir / relpath
    before = path.read_bytes().decode("utf-8")
    after = edit(before)
    assert after != before, f"editing {relpath} changed nothing"
    path.write_bytes(after.encode("utf-8"))
    return path


def replace_in_spec(spec_dir: Path, relpath: str | os.PathLike, old: str, new: str, count: int = 1) -> Path:
    """Replaces `old` with `new` in one spec file, failing unless `old` appears exactly `count` times."""

    def edit(text: str) -> str:
        found = text.count(old)
        assert found == count, f"{relpath}: expected {old!r} {count} times, found {found}"
        return text.replace(old, new)

    return edit_spec(spec_dir, relpath, edit)


IDENTITY = {"built_in_variables": "type", "gtag_configs": "label"}


def fragment_of(spec_dir: Path, kind: str, name: str) -> Path:
    """The fragment defining `name` under `kind` (`tags`, `variables`, ...), found with PyYAML."""
    identity = IDENTITY.get(kind, "name")
    found = []
    for path in sorted(spec_dir.rglob("*.yml")):
        if any(part.startswith(".") for part in path.relative_to(spec_dir).parts):
            continue
        tree = yaml.safe_load(path.read_bytes().decode("utf-8")) or {}
        items = tree.get(kind) if isinstance(tree, dict) else None
        if isinstance(items, list) and any(isinstance(i, dict) and i.get(identity) == name for i in items):
            found.append(path)
    assert len(found) == 1, f"{kind} {name!r} is defined in {len(found)} fragments: {found}"
    return found[0]


def line_of(spec: Path, relpath: str, text: str) -> int:
    """The 1-based line in a spec file holding `text`, which has to appear on exactly one line."""
    lines = (spec / relpath).read_bytes().decode("utf-8").splitlines()
    (number,) = [n for n, line in enumerate(lines, 1) if text in line]
    return number


# Goldens


@pytest.fixture
def update_goldens(request: pytest.FixtureRequest) -> bool:
    return request.config.getoption("--update-goldens")


def _tree(directory: Path) -> dict[str, bytes]:
    return {
        p.relative_to(directory).as_posix(): p.read_bytes()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def golden(update_goldens: bool) -> Callable[[str | os.PathLike, str | bytes | Path], None]:
    """`golden(relpath, actual)`: compare with tests/goldens/relpath, or rewrite it with --update-goldens.

    `actual` is text, bytes, or a directory, which is compared file by file (dotfiles included).
    """

    def check(relpath: str | os.PathLike, actual: str | bytes | Path) -> None:
        path = GOLDENS / relpath
        if update_goldens:
            if isinstance(actual, Path):
                shutil.rmtree(path, ignore_errors=True)
                shutil.copytree(actual, path)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(actual.encode("utf-8") if isinstance(actual, str) else actual)
            return
        assert path.exists(), f"no golden at {path.relative_to(ROOT)}; run pytest --update-goldens"
        if isinstance(actual, Path):
            expected, got = _tree(path), _tree(actual)
            assert sorted(got) == sorted(expected), f"files differ from {path.relative_to(ROOT)}"
            for name in expected:
                assert got[name].decode("utf-8", "replace") == expected[name].decode("utf-8", "replace"), name
                assert got[name] == expected[name], name
        elif isinstance(actual, str):
            assert actual == path.read_bytes().decode("utf-8")
        else:
            assert actual == path.read_bytes()

    return check
