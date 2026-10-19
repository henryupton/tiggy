"""The CLI contract the adopting repo's CI jobs lean on.

Exit codes per command and input class (0 fine, 1 findings or drift, 2 can't run), which stream
carries what, atomic `export -o`, early-closing pipes, a closed stdout, an interrupted import, and
names that try to forge terminal output.
"""

import json
import os
import re
import select
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import (
    CDM_EXPORTS,
    FIXTURES,
    Result,
    _tiggy,
    base_env,
    export_id,
    fragment_of,
    load_export,
    replace_in_spec,
    run_tiggy,
    scrub,
    write_export,
)

ANALYTICS = FIXTURES / "cdm" / "web_analytics.json"
MARKETING = FIXTURES / "cdm" / "web_marketing.json"
LEGACY = FIXTURES / "cdm" / "web_legacy.json"
LOCK = ".tiggy-import.lock"
INTERRUPTED = "an import into this directory was interrupted, so the spec may be incomplete"
CLOSED_STDOUT = "Error: stdout is closed, so there's nowhere to write\n"

REPORT_KEYS = ["container", "counts", "summary", "files", "findings"]
CONTAINER_KEYS = ["name", "publicId", "version"]
ANALYTICS_HEADER = "Example - Analytics (GTM-TEST001): 11 tags, 6 triggers, 30 variables, 2 templates"
SUMMARY_KEYS = ["errors", "warnings"]
FINDING_KEYS = ["rule", "severity", "kind", "name", "id", "message", "location"]
EXPORT_KEYS = ["exportFormatVersion", "exportTime", "containerVersion"]

POSIX = pytest.mark.skipif(os.name == "nt", reason="POSIX signals, file modes and terminals")
PERMISSIONS = pytest.mark.skipif(
    os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions that bind (not root)"
)

Edit = Callable[[dict[str, Any]], None]


# Helpers


def _variant(tmp_path: Path, name: str, edit: Edit, source: Path = ANALYTICS) -> Path:
    """`source` with `edit` applied to its containerVersion, written as GTM writes an export."""
    export = load_export(source)
    edit(export["containerVersion"])
    return write_export(export, tmp_path / name)


def _tag(version: dict[str, Any], name: str) -> dict[str, Any]:
    (tag,) = [t for t in version["tag"] if t["name"] == name]
    return tag


def _paused_in_the_ui(version: dict[str, Any]) -> None:
    _tag(version, "GA4 - sign_up")["paused"] = True


def _with_bogus_key(spec: Path) -> tuple[str, int]:
    """Adds an unknown key to the `GA4 - sign_up` tag; returns the fragment and the key's line."""
    fragment = fragment_of(spec, "tags", "GA4 - sign_up")
    replace_in_spec(
        spec,
        fragment.relative_to(spec),
        "  - name: GA4 - sign_up\n",
        "  - name: GA4 - sign_up\n    bogus_key: 1\n",
    )
    lines = fragment.read_bytes().decode("utf-8").splitlines()
    return fragment.relative_to(spec).as_posix(), lines.index("    bogus_key: 1") + 1


def _with_stray_sidecar(spec: Path) -> None:
    (spec / "unfiled" / "old-snippet.html").write_bytes(b"<script>/* retired */</script>\n")


def _timeless(data: bytes) -> bytes:
    return re.sub(rb'"exportTime": "[^"]*"', b'"exportTime": ""', data)


def _run(*args: str | os.PathLike, close_stdout: bool = False, file_size_limit: int | None = None) -> Result:
    """run_tiggy, for what it can't arrange: a closed stdout, or a cap on file size (a full disk)."""

    def prepare() -> None:
        if file_size_limit is not None:
            import resource

            resource.setrlimit(resource.RLIMIT_FSIZE, (file_size_limit, file_size_limit))
        if close_stdout:
            os.close(1)

    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        completed = subprocess.run(
            [_tiggy(), *(os.fspath(a) for a in args)],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            env=base_env(),
            preexec_fn=prepare,
            timeout=120,
            check=False,
        )
        out.seek(0)
        err.seek(0)
        raw_out, raw_err = out.read(), err.read()
    return Result(completed.returncode, raw_out.decode(), raw_err.decode(), raw_out, raw_err)


def _on_a_terminal(*args: str | os.PathLike, env: dict[str, str | None] | None = None) -> tuple[int, bytes]:
    """Runs tiggy with stdout on a pseudo-terminal, where colour is on by default."""
    import pty

    environment = base_env()
    for key, value in (env or {}).items():
        if value is None:
            environment.pop(key, None)
        else:
            environment[key] = value
    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            [_tiggy(), *(os.fspath(a) for a in args)],
            stdin=subprocess.DEVNULL,
            stdout=slave,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
    finally:
        os.close(slave)
    chunks, deadline = [], time.monotonic() + 60
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 1)
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:  # EIO on Linux once the last writer has gone
                break
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(master)
    return proc.wait(timeout=60), b"".join(chunks)


def _unsafe(text: str) -> list[str]:
    """Characters a terminal acts on rather than shows: C0 and C1 controls, DEL, bidi overrides."""
    return [c for c in text if c != "\n" and not c.isprintable()]


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob("*") if p.is_file()}


# validate


@pytest.mark.parametrize("form", ["export", "spec"])
def test_validate_passes_a_clean_container(form: str, imported):
    source = ANALYTICS if form == "export" else imported(ANALYTICS)
    result = run_tiggy("validate", source)
    assert result.code == 0, str(result)
    assert result.out.splitlines()[-1] == "0 errors, 0 warnings"
    assert result.raw_err == b""


@pytest.mark.parametrize("form", ["export", "spec"])
def test_validate_exits_1_on_findings_and_reports_them_on_stdout(form: str, imported):
    source = LEGACY if form == "export" else imported(LEGACY)
    result = run_tiggy("validate", source)
    lines = result.out.splitlines()
    assert result.code == 1, str(result)
    assert (
        "    html: {{Click Text}} is a built-in variable that isn't enabled  [built-in-not-enabled]" in lines
    )
    assert lines[-1] == "1 error, 10 warnings"
    assert result.raw_err == b""


def test_validate_strict_fails_on_warnings_alone(imported):
    spec = imported(ANALYTICS)
    _with_stray_sidecar(spec)
    lenient, strict = run_tiggy("validate", spec), run_tiggy("validate", spec, "--strict")
    warning = "⚠ unfiled/old-snippet.html: no $file refers to this code file"
    assert (lenient.code, strict.code) == (0, 1), f"{lenient}\n{strict}"
    for result in (lenient, strict):
        assert warning in result.out.splitlines()
        assert result.out.splitlines()[-1] == "0 errors, 1 warning"


def test_validate_exits_1_when_a_spec_file_does_not_load(imported):
    spec = imported(ANALYTICS)
    relpath, line = _with_bogus_key(spec)
    result = run_tiggy("validate", spec)
    assert result.code == 1, str(result)
    assert result.out.splitlines() == [
        ANALYTICS_HEADER,
        "",
        f"✗ {relpath}:{line}: unknown key 'bogus_key' for a tag",
        "",
        "1 error, 0 warnings",
    ]


NOT_A_SOURCE = {  # input class: what stderr says after `Error: <path1>`
    "empty directory": " is not a spec directory: it holds no .yml fragments",
    "repo root before import": " is not a spec directory: it holds no .yml fragments",
    "API response, not an export": ": not a GTM container export:\n  exportFormatVersion: Field required\n"
    "  containerVersion: Field required\n",
    "JSON array": ": not a GTM container export: the top level isn't an object\n",
    "truncated download": ": not valid JSON: ",
    "PowerShell redirect": ": not UTF-8 JSON\n",
}


def _not_a_source(case: str, tmp_path: Path) -> Path:
    if case == "empty directory":
        (tmp_path / "spec").mkdir()
        return tmp_path / "spec"
    if case == "repo root before import":
        (tmp_path / "README.md").write_text("# GTM\n", encoding="utf-8")
        return tmp_path
    if case == "API response, not an export":
        return write_export(load_export(ANALYTICS)["containerVersion"], tmp_path / "version.json")
    path = tmp_path / "container.json"
    if case == "JSON array":
        path.write_bytes(b"[]")
    elif case == "truncated download":
        path.write_bytes(ANALYTICS.read_bytes()[:4096])
    else:  # Windows PowerShell 5.1's `>` writes utf-16 with a byte order mark
        path.write_bytes(ANALYTICS.read_bytes().decode("utf-8").encode("utf-16"))
    return path


@pytest.mark.parametrize("case", NOT_A_SOURCE)
def test_validate_exits_2_on_what_is_neither_a_spec_nor_an_export(case: str, tmp_path: Path):
    source = _not_a_source(case, tmp_path)
    result = run_tiggy("validate", source, "--format", "json")
    assert result.code == 2, str(result)
    assert result.raw_out == b"", "nothing half-reported for a JSON reader to choke on"
    assert scrub(result.err, source).startswith(f"Error: <path1>{NOT_A_SOURCE[case]}"), str(result)


@PERMISSIONS
def test_validate_exits_2_on_an_export_it_cannot_read(tmp_path: Path):
    export = tmp_path / "container.json"
    shutil.copyfile(ANALYTICS, export)
    export.chmod(0)
    try:
        result = run_tiggy("validate", export)
    finally:
        export.chmod(0o644)
    assert result.code == 2, str(result)
    assert result.raw_out == b""
    assert "Error: Invalid value for 'SOURCE': Path " in result.err and "is not readable." in result.err


def test_validate_exits_2_on_an_export_it_would_misread(tmp_path: Path):
    """A key spelled like a field's Python name isn't GTM's, and pydantic would silently drop it."""
    export = _variant(tmp_path, "pasted.json", _python_field_name)
    result = run_tiggy("validate", export)
    assert result.code == 2, str(result)
    assert scrub(result.err, export) == (
        "Error: <path1>: Tiggy reads a value differently from how it's written, at tag[0].live_only "
        "(missing from Tiggy's reading)\n"
    )


def test_validate_reads_an_export_in_the_apis_spelling(tmp_path: Path):
    """The team's API tool speaks the API's lowerCamel enums, which read as the export's UPPER_SNAKE."""
    export = _variant(tmp_path, "pasted.json", _api_spelled_enum)
    as_api, as_exported = run_tiggy("validate", export), run_tiggy("validate", ANALYTICS)
    assert (as_api.code, as_api.out, as_api.err) == (as_exported.code, as_exported.out, as_exported.err)


# import


@pytest.mark.parametrize("target", ["new directory", "existing empty directory"])
def test_import_exits_0_and_says_what_it_wrote(target: str, tmp_path: Path):
    spec = tmp_path / "gtm" / "analytics"
    if target == "existing empty directory":
        spec.mkdir(parents=True)
    result = run_tiggy("import", ANALYTICS, spec)
    assert result.code == 0, str(result)
    assert scrub(result.out, spec).splitlines()[:3] == [
        "Imported Example - Analytics into <path1>: "
        "6 folders, 2 custom templates, 30 variables, 6 triggers, 11 tags",
        "  19 YAML files, 5 code files",
        "  verified: the spec compiles back to the same container",
    ]
    assert result.raw_err == b""
    assert (spec / "container.yml").is_file() and not (spec / LOCK).exists()


@pytest.mark.parametrize("occupant", ["README.md", "an earlier import"])
def test_import_exits_2_on_a_directory_that_is_not_empty(occupant: str, tmp_path: Path, imported):
    if occupant == "README.md":
        spec = tmp_path / "spec"
        spec.mkdir()
        (spec / "README.md").write_text("Analytics container\n")
    else:
        spec = imported(MARKETING)
    before = _snapshot(spec)
    result = run_tiggy("import", ANALYTICS, spec)
    assert result.code == 2, str(result)
    assert scrub(result.err, spec) == (
        "Error: <path1> already exists and isn't empty. Importing into an existing spec isn't supported "
        "yet; use --check to compare instead\n"
    )
    assert _snapshot(spec) == before


def _api_spelled_enum(version: dict[str, Any]) -> None:
    assert version["trigger"][0]["type"] == "CUSTOM_EVENT"
    version["trigger"][0]["type"] = "customEvent"


def _python_field_name(version: dict[str, Any]) -> None:
    version["tag"][0]["live_only"] = True


def _cloned_without_renaming(version: dict[str, Any]) -> None:
    version["tag"][1]["name"] = version["tag"][0]["name"]


@pytest.mark.parametrize(
    "edit, says",
    [
        (
            _python_field_name,
            "Tiggy reads a value differently from how the export writes it, at tag[0].live_only "
            "(missing from Tiggy's reading)",
        ),
        (_cloned_without_renaming, "2 tags are named 'Google tag'"),
    ],
    ids=["Python field name", "clone left with its name"],
)
def test_import_exits_2_on_an_export_it_would_misread_and_writes_nothing(
    edit: Edit, says: str, tmp_path: Path
):
    export = _variant(tmp_path, "container.json", edit)
    spec = tmp_path / "spec"
    result = run_tiggy("import", export, spec)
    assert result.code == 2, str(result)
    assert result.err == f"Error: can't import this container: {says}\n"
    assert not spec.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["container.json"], "no staging left behind"


# import --check


def test_check_exits_0_when_the_live_container_matches_the_repo(imported):
    spec = imported(ANALYTICS)
    result = run_tiggy("import", ANALYTICS, spec, "--check")
    assert result.code == 0, str(result)
    assert scrub(result.out, spec, ANALYTICS) == "<path1> is equivalent to <path2>\n"
    assert result.raw_err == b""


def test_check_exits_1_and_names_what_drifted(tmp_path: Path, imported):
    spec = imported(ANALYTICS)
    live = _variant(tmp_path, "live.json", _paused_in_the_ui)
    before = _snapshot(spec)
    result = run_tiggy("import", live, spec, "--check")
    assert result.code == 1, str(result)
    assert scrub(result.out, live, spec) == (
        "≠ tags 'GA4 - sign_up': paused (missing from the spec)  folders/ga4/tags.yml:45\n"
        "1 difference between <path1> and <path2>\n"
    )
    assert _snapshot(spec) == before, "--check writes nothing"


@pytest.mark.parametrize("breakage", ["unknown key", "not a spec directory"])
def test_check_exits_2_when_the_spec_does_not_load(breakage: str, tmp_path: Path, imported):
    """Exit 1 would read as drift; a spec that doesn't load means the gate couldn't run."""
    if breakage == "unknown key":
        spec = imported(ANALYTICS)
        relpath, line = _with_bogus_key(spec)
        says = (
            f"Error: the spec in <path1> doesn't load:\n{relpath}:{line}: unknown key 'bogus_key' for a tag\n"
        )
    else:
        spec = tmp_path / "spec"
        spec.mkdir()
        says = "Error: <path1> is not a spec directory: it holds no .yml fragments\n"
    result = run_tiggy("import", ANALYTICS, spec, "--check")
    assert result.code == 2, str(result)
    assert result.raw_out == b""
    assert scrub(result.err, spec) == says


# export


def test_export_writes_only_the_export_to_stdout(imported):
    result = run_tiggy("export", imported(ANALYTICS))
    assert result.code == 0, str(result)
    assert list(json.loads(result.out)) == EXPORT_KEYS
    assert result.out.endswith("}\n")
    assert result.raw_err == b""


def test_export_sends_warnings_to_stderr_and_keeps_stdout_pure(imported):
    spec = imported(ANALYTICS)
    _with_stray_sidecar(spec)
    result = run_tiggy("export", spec)
    assert result.code == 0, str(result)
    assert list(json.loads(result.out)) == EXPORT_KEYS
    assert result.err == "! unfiled/old-snippet.html: no $file refers to this code file\n"


def test_export_of_a_broken_spec_exits_1_with_problems_on_stderr_only(tmp_path: Path, imported):
    spec = imported(ANALYTICS)
    relpath, line = _with_bogus_key(spec)
    previous = tmp_path / "container.json"
    previous.write_bytes(b'{"previous": true}')
    for args in ((), ("-o", previous)):
        result = run_tiggy("export", spec, *args)
        assert result.code == 1, str(result)
        assert result.raw_out == b""
        assert result.err == f"✗ {relpath}:{line}: unknown key 'bogus_key' for a tag\n1 problem in the spec\n"
    assert previous.read_bytes() == b'{"previous": true}'


def test_export_to_a_file_writes_what_stdout_would(tmp_path: Path, imported):
    spec = imported(ANALYTICS)
    target = tmp_path / "build" / "container.json"
    target.parent.mkdir()
    target.write_bytes(b'{"previous": true}')
    target.chmod(0o640)
    on_stdout = run_tiggy("export", spec)
    to_file = run_tiggy("export", spec, "-o", target)
    to_dash = run_tiggy("export", spec, "-o", "-")
    assert (on_stdout.code, to_file.code, to_dash.code) == (0, 0, 0)
    assert (to_file.raw_out, to_file.raw_err) == (b"", b"")
    assert _timeless(target.read_bytes()) == _timeless(on_stdout.raw_out) == _timeless(to_dash.raw_out)
    assert [p.name for p in target.parent.iterdir()] == ["container.json"], "no staging file left behind"
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o640, "replacing the file keeps its mode"


@PERMISSIONS
@pytest.mark.parametrize(
    "case", ["read-only directory and file", "read-only directory, new file", "disk full"]
)
def test_a_failed_export_write_leaves_the_previous_file(case: str, tmp_path: Path, imported):
    spec = imported(ANALYTICS)
    out = tmp_path / "build"
    out.mkdir()
    target = out / "container.json"
    previous = b'{"previous": true}'
    if case != "read-only directory, new file":
        target.write_bytes(previous)
    limit = None
    if case == "disk full":
        limit = 32 * 1024
        assert ANALYTICS.stat().st_size > 2 * limit, "the export has to outgrow the limit"
        says = "File too large"
    else:
        says = "Permission denied"
        if target.exists():
            target.chmod(0o444)
        out.chmod(0o555)
    try:
        result = _run("export", spec, "-o", target, file_size_limit=limit)
    finally:
        out.chmod(0o755)
    assert result.code == 2, str(result)
    assert result.raw_out == b""
    assert scrub(result.err, target) == f"Error: can't write <path1>: {says}\n"
    if case == "read-only directory, new file":
        assert not target.exists()
    else:
        assert target.read_bytes() == previous
        assert [p.name for p in out.iterdir()] == ["container.json"], "no staging file left behind"


@PERMISSIONS
def test_a_failed_export_write_in_a_read_only_directory_leaves_the_previous_file(tmp_path: Path, imported):
    spec = imported(ANALYTICS)
    out = tmp_path / "build"
    out.mkdir()
    target = out / "container.json"
    previous = b'{"previous": true}'
    target.write_bytes(previous)
    out.chmod(0o555)
    try:
        result = _run("export", spec, "-o", target, file_size_limit=32 * 1024)
    finally:
        out.chmod(0o755)
    assert result.code == 2, str(result)
    assert target.read_bytes() == previous, f"left {target.stat().st_size} bytes of a partial export"


# --format json


def _json_sources() -> list[Any]:
    return [pytest.param(path, id=export_id(path)) for path in CDM_EXPORTS] + [
        pytest.param("legacy spec", id="cdm/web_legacy-spec"),
        pytest.param("spec with a stray file", id="cdm/web_analytics-stray"),
    ]


@pytest.mark.parametrize("source", _json_sources())
def test_validate_json_has_stable_keys_and_agrees_with_text(source: Path | str, imported):
    if source == "legacy spec":
        source = imported(LEGACY)
    elif source == "spec with a stray file":
        source = imported(ANALYTICS)
        _with_stray_sidecar(source)
    as_json, as_text = run_tiggy("validate", source, "--format", "json"), run_tiggy("validate", source)
    payload = json.loads(as_json.out)
    assert list(payload) == REPORT_KEYS
    assert list(payload["container"]) == CONTAINER_KEYS
    assert list(payload["summary"]) == SUMMARY_KEYS
    assert all(list(finding) == FINDING_KEYS for finding in payload["findings"])
    severities = [finding["severity"] for finding in payload["findings"]]
    assert payload["summary"] == {
        "errors": severities.count("error"),
        "warnings": severities.count("warning") + len(payload["files"]),
    }
    assert as_json.code == as_text.code == (1 if payload["summary"]["errors"] else 0)
    errors, warnings = payload["summary"]["errors"], payload["summary"]["warnings"]
    tally = f"{errors} error{'' if errors == 1 else 's'}, {warnings} warning{'' if warnings == 1 else 's'}"
    assert as_text.out.splitlines()[-1] == tally


def test_validate_json_keeps_its_keys_when_the_spec_does_not_load(imported):
    spec = imported(ANALYTICS)
    _with_bogus_key(spec)
    result = run_tiggy("validate", spec, "--format", "json")
    assert result.code == 1, str(result)
    payload = json.loads(result.out)
    assert set(REPORT_KEYS) <= set(payload), sorted(payload)
    assert payload["summary"]["errors"] == 1


# Pipes and closed streams


@POSIX
def test_a_reader_stopping_early_ends_export_with_sigpipe_and_no_traceback(imported):
    spec = imported(ANALYTICS)
    assert ANALYTICS.stat().st_size > 1 << 16, "the export has to outgrow a pipe buffer"
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(
            [_tiggy(), "export", spec],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=err,
            env=base_env(),
        )
        first = os.read(proc.stdout.fileno(), 16)
        proc.stdout.close()
        code = proc.wait(timeout=60)
        err.seek(0)
        stderr = err.read()
    assert first.startswith(b"{")
    assert code == -signal.SIGPIPE
    assert stderr == b""


@POSIX
def test_a_reader_gone_before_any_output_ends_validate_with_sigpipe():
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(
            [_tiggy(), "validate", LEGACY],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=err,
            env=base_env(),
        )
        proc.stdout.close()
        code = proc.wait(timeout=60)
        err.seek(0)
        stderr = err.read()
    assert code == -signal.SIGPIPE, "a signal, not 1 for the findings it never got to report"
    assert stderr == b""


@POSIX
@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_a_shell_pipeline_into_head_sees_141(tmp_path: Path, imported):
    spec = imported(ANALYTICS)
    err = tmp_path / "stderr.txt"
    script = 'set -o pipefail; "$0" export "$1" 2>"$2" | head -c 16 >/dev/null'
    completed = subprocess.run(
        ["bash", "-c", script, _tiggy(), spec, err], env=base_env(), stdin=subprocess.DEVNULL, check=False
    )
    assert completed.returncode == 141
    assert err.read_bytes() == b""


@POSIX
@pytest.mark.parametrize(
    "command",
    ["validate clean", "validate with findings", "export", "check"],
)
def test_a_closed_stdout_exits_2_with_a_message(command: str, imported):
    spec = imported(ANALYTICS)
    args = {
        "validate clean": ("validate", spec),
        "validate with findings": ("validate", LEGACY),
        "export": ("export", spec),
        "check": ("import", ANALYTICS, spec, "--check"),
    }[command]
    result = _run(*args, close_stdout=True)
    assert result.code == 2, str(result)
    assert result.err == CLOSED_STDOUT


@POSIX
def test_export_to_a_file_does_not_need_stdout(tmp_path: Path, imported):
    target = tmp_path / "container.json"
    result = _run("export", imported(ANALYTICS), "-o", target, close_stdout=True)
    assert result.code == 0, str(result)
    assert result.raw_err == b""
    assert list(json.loads(target.read_bytes())) == EXPORT_KEYS


@POSIX
def test_import_with_a_closed_stdout_writes_nothing(tmp_path: Path):
    spec = tmp_path / "spec"
    result = _run("import", ANALYTICS, spec, close_stdout=True)
    assert result.code == 2, str(result)
    assert result.err == CLOSED_STDOUT
    assert not spec.exists(), "exit 2 means nothing was imported"


# An interrupted import


@pytest.mark.parametrize("command", ["validate", "export", "check", "import"])
def test_a_spec_left_by_an_interrupted_import_is_refused(command: str, tmp_path: Path, imported):
    spec = imported(ANALYTICS)
    (spec / LOCK).touch()
    previous = tmp_path / "container.json"
    previous.write_bytes(b'{"previous": true}')
    if command == "validate":
        result = run_tiggy("validate", spec)
        assert result.code == 1, str(result)
        assert result.out == f"✗ {LOCK}: {INTERRUPTED}\n\n1 error, 0 warnings\n"
    elif command == "export":
        result = run_tiggy("export", spec, "-o", previous)
        assert (result.code, result.raw_out) == (1, b""), str(result)
        assert result.err == f"✗ {LOCK}: {INTERRUPTED}\n1 problem in the spec\n"
        assert previous.read_bytes() == b'{"previous": true}'
    elif command == "check":
        result = run_tiggy("import", ANALYTICS, spec, "--check")
        assert result.code == 2, str(result)
        assert scrub(result.err, spec) == f"Error: the spec in <path1> doesn't load:\n{LOCK}: {INTERRUPTED}\n"
    else:
        result = run_tiggy("import", ANALYTICS, spec)
        assert result.code == 2, str(result)
        assert scrub(result.err, spec) == (
            "Error: an import into <path1> was interrupted, or is running now, and may have left part of a "
            "spec there. If it isn't running, empty the directory and import again\n"
        )


# Names that try to drive the terminal

FORGED_TAG = "GA4 - sign_up\x1b[2J\n✓ 0 errors, 0 warnings"  # clears the screen, then fakes a pass
TITLED_CONTAINER = "Analytics\x1b]0;pwned\x07"  # sets the terminal's title
SHOWN_TAG = r"GA4 - sign_up\x1b[2J\n✓ 0 errors, 0 warnings"
SHOWN_CONTAINER = r"Analytics\x1b]0;pwned\x07"


def _forged(version: dict[str, Any]) -> None:
    version["container"]["name"] = TITLED_CONTAINER
    tag = _tag(version, "GA4 - sign_up")
    tag["name"] = FORGED_TAG
    tag["firingTriggerId"] = ["999"]  # so validate has a finding to name it in


def _forged_and_paused(version: dict[str, Any]) -> None:
    _forged(version)
    _tag(version, FORGED_TAG)["paused"] = True


def _forged_twice(version: dict[str, Any]) -> None:
    _forged(version)
    version["tag"][0]["name"] = FORGED_TAG


@pytest.mark.parametrize(
    "command, code",
    [("validate export", 1), ("validate spec", 1), ("import", 0), ("import --check", 1), ("import error", 2)],
)
def test_control_characters_in_names_are_escaped_in_text_output(command: str, code: int, tmp_path: Path):
    export = _variant(tmp_path, "forged.json", _forged)
    spec = tmp_path / "spec"
    if command != "import":
        assert run_tiggy("import", export, spec).code == 0
    if command == "validate export":
        result, shown = run_tiggy("validate", export), [SHOWN_CONTAINER, f'✗ tag "{SHOWN_TAG}"']
    elif command == "validate spec":
        result, shown = run_tiggy("validate", spec), [SHOWN_CONTAINER, f'✗ tag "{SHOWN_TAG}"']
    elif command == "import":
        result, shown = run_tiggy("import", export, spec), [f"Imported {SHOWN_CONTAINER} into "]
    elif command == "import --check":
        live = _variant(tmp_path, "live.json", _forged_and_paused)
        result, shown = run_tiggy("import", live, spec, "--check"), [f"≠ tags '{SHOWN_TAG}': paused"]
    else:
        twice = _variant(tmp_path, "twice.json", _forged_twice)
        result, shown = run_tiggy("import", twice, tmp_path / "other"), [f"2 tags are named '{SHOWN_TAG}'"]
    both = result.out + result.err
    assert result.code == code, str(result)
    assert _unsafe(both) == [], str(result)
    for text in shown:
        assert text in both, str(result)
    assert not any(line.startswith("✓") for line in both.splitlines()), "a name forged a line of output"


def test_json_output_keeps_names_exactly(tmp_path: Path):
    export = _variant(tmp_path, "forged.json", _forged)
    result = run_tiggy("validate", export, "--format", "json")
    assert result.code == 1, str(result)
    assert "\x1b" not in result.out and "\x07" not in result.out
    payload = json.loads(result.out)
    assert payload["container"]["name"] == TITLED_CONTAINER
    assert [f["name"] for f in payload["findings"] if f["rule"] == "unknown-trigger"] == [FORGED_TAG]


# Encodings and colour


@pytest.mark.parametrize("command", ["validate with findings", "check with drift"])
def test_findings_still_exit_1_on_a_non_utf8_stdout(command: str, tmp_path: Path, imported):
    if command == "validate with findings":
        args, shows = ("validate", LEGACY), "[built-in-not-enabled]"
    else:
        args, shows = (
            ("import", _variant(tmp_path, "live.json", _paused_in_the_ui), imported(ANALYTICS), "--check"),
            "paused (missing from the spec)",
        )
    result = run_tiggy(*args, env={"PYTHONIOENCODING": "cp1252"})
    assert "Traceback" not in result.err, str(result)
    assert result.code == 1, str(result)
    assert shows in result.out


def test_output_to_a_file_or_pipe_is_never_coloured(imported):
    spec = imported(ANALYTICS)
    _with_bogus_key(spec)
    for args in (("validate", LEGACY), ("export", spec)):
        result = run_tiggy(*args, env={"NO_COLOR": None})
        assert result.code == 1, str(result)
        assert "\x1b[" not in result.out + result.err, str(result)


@POSIX
def test_a_terminal_gets_colour_by_default():
    code, out = _on_a_terminal("validate", LEGACY, env={"NO_COLOR": None})
    assert code == 1
    assert "\x1b[31m✗\x1b[0m".encode() in out


@POSIX
def test_no_color_turns_colour_off_on_a_terminal():
    code, out = _on_a_terminal("validate", LEGACY, env={"NO_COLOR": "1"})
    assert code == 1
    assert b"\x1b[" not in out, out[:200]


@POSIX
@pytest.mark.parametrize("no_color", [None, "1"], ids=["colour", "NO_COLOR"])
def test_check_marks_drift_and_dims_its_line_on_a_terminal_unless_no_color(no_color, tmp_path, imported):
    spec = imported(ANALYTICS)
    live = _variant(tmp_path, "live.json", _paused_in_the_ui)

    code, out = _on_a_terminal("import", live, spec, "--check", env={"NO_COLOR": no_color})

    assert code == 1
    if no_color:
        assert b"\x1b[" not in out, out[:200]
        assert (
            "≠ tags 'GA4 - sign_up': paused (missing from the spec)  folders/ga4/tags.yml:45".encode() in out
        )
    else:
        assert "\x1b[33m≠\x1b[0m tags 'GA4 - sign_up': paused".encode() in out, out[:200]
        assert "(missing from the spec)  \x1b[2mfolders/ga4/tags.yml:45\x1b[0m".encode() in out, out[:200]
