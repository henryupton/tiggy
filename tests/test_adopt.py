"""Adoption: the analytics team codifies a live container as-is, and a fresh plan must be a no-op.

They export each container read-only from the GTM UI, `tiggy import` it into the adopting repo, and from
then on `tiggy import --check` is the drift gate. Adopt, never recreate: whatever import writes has to
check clean against the export it came from, validate the way the export does, and survive an export and
re-import byte for byte. Parametrised over ALL_EXPORTS, so $TIGGY_EXTRA_EXPORTS gets the same treatment.
"""

import hashlib
import json
import os
import stat
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import (
    ALL_EXPORTS,
    CDM_EXPORTS,
    FIXTURES,
    THIRD_PARTY_EXPORTS,
    export_id,
    load_export,
    run_tiggy,
    scrub,
    write_export,
)

COMMITTED = CDM_EXPORTS + THIRD_PARTY_EXPORTS
WEB_ANALYTICS = FIXTURES / "cdm" / "web_analytics.json"
WEB_MARKETING = FIXTURES / "cdm" / "web_marketing.json"

# `tiggy validate` on each committed export: its exit code and the rules of the errors behind it.
VALIDATE_EXIT = {
    "cdm/server": (0, []),
    "cdm/web_analytics": (0, []),
    "cdm/web_legacy": (1, ["built-in-not-enabled"]),  # a tag reads {{Click Text}}, which is off
    "cdm/web_marketing": (0, []),
    "third_party/bounteous_youtube_tracking": (1, ["unknown-variable"]),  # {{YOUR_GA_TRACKING_ID}}
    "third_party/stape_magento_meta_web": (0, []),
    "third_party/stape_shopify_meta_ga4_ads_server": (0, []),
    "third_party/stape_woocommerce_meta_web": (0, []),
}

# How import's summary names each collection of the export.
COLLECTION_LABELS = {
    "folder": "folders",
    "customTemplate": "custom templates",
    "variable": "variables",
    "trigger": "triggers",
    "tag": "tags",
    "client": "clients",
    "transformation": "transformations",
    "zone": "zones",
    "gtagConfig": "gtag configs",
}

GITATTRIBUTES = "*.tpl -text\n*.html -text\n*.js -text\n"
EDITORCONFIG = "[*.{tpl,html,js}]\ntrim_trailing_whitespace = false\ninsert_final_newline = false\n"
NOT_EMPTY = (
    "Error: <path1> already exists and isn't empty. Importing into an existing spec isn't supported yet; "
    "use --check to compare instead\n"
)
INTERRUPTED = (
    "Error: an import into <path1> was interrupted, or is running now, and may have left part of a spec "
    "there. If it isn't running, empty the directory and import again\n"
)


def _files(directory: Path) -> dict[str, bytes]:
    """Every file under `directory`, dotfiles included, by POSIX relative path."""
    return {
        p.relative_to(directory).as_posix(): p.read_bytes()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def _entries(directory: Path) -> list[str]:
    """Every file and directory under `directory`, so an empty directory left behind shows up too."""
    return sorted(p.relative_to(directory).as_posix() for p in directory.rglob("*"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate(source: Path) -> tuple[int, dict[str, Any]]:
    result = run_tiggy("validate", source, "--format", "json")
    assert result.code in (0, 1), str(result)
    return result.code, json.loads(result.out)


def _findings(report: dict[str, Any]) -> Counter:
    """Findings as validate states them, without the IDs and locations that differ between sources."""
    return Counter((f["rule"], f["severity"], f["kind"], f["name"], f["message"]) for f in report["findings"])


def _as_workspace_export(published: dict[str, Any]) -> dict[str, Any]:
    """The same container exported from its workspace (Admin > Export Container) rather than as a version."""
    export = json.loads(json.dumps(published))
    version = export["containerVersion"]
    old = version["containerVersionId"]
    version["containerVersionId"] = "0"
    version["path"] = version["path"].removesuffix(f"/versions/{old}") + "/versions/0"
    version["tagManagerUrl"] = version["tagManagerUrl"].replace(f"/versions/{old}?", "/versions/0?")
    del version["name"], version["description"]
    return export


# Adopting: import, check, export, validate


@pytest.mark.parametrize("export", ALL_EXPORTS, ids=export_id)
def test_adopting_a_container_writes_its_spec_and_says_what_it_wrote(export: Path, tmp_path: Path):
    """Import reads the export without touching it, writes every object, and reports what it wrote."""
    before = _sha(export)
    spec = tmp_path / "spec"
    result = run_tiggy("import", export, spec)
    assert result.code == 0, str(result)
    assert _sha(export) == before, "import changed the export it read"

    version = load_export(export)["containerVersion"]
    name = version["container"].get("name") or "container"
    headline, files_line, verified, *_ = scrub(result.out, spec).split("\n")
    prefix = f"Imported {name} into <path1>: "
    assert headline.startswith(prefix), headline
    counts = headline.removeprefix(prefix)
    reported = (
        {}
        if counts == "nothing"
        else {label: int(n) for n, label in (part.split(" ", 1) for part in counts.split(", "))}
    )
    expected = {
        COLLECTION_LABELS[alias]: len(version[alias]) for alias in COLLECTION_LABELS if version.get(alias)
    }
    assert reported == expected

    written = _files(spec)
    fragments = [p for p in written if p.endswith(".yml")]
    code = [p for p in written if not p.endswith(".yml") and p not in (".gitattributes", ".editorconfig")]
    assert files_line == f"  {len(fragments)} YAML files, {len(code)} code files"
    assert verified == "  verified: the spec compiles back to the same container"
    assert written[".gitattributes"].decode() == GITATTRIBUTES
    assert written[".editorconfig"].decode() == EDITORCONFIG
    if export in COMMITTED:
        assert result.err == ""


@pytest.mark.parametrize("export", ALL_EXPORTS, ids=export_id)
def test_adopting_the_same_export_twice_writes_the_same_bytes(export: Path, tmp_path: Path):
    first, second = tmp_path / "first", tmp_path / "second"
    for spec in (first, second):
        result = run_tiggy("import", export, spec)
        assert result.code == 0, str(result)
    assert _files(second) == _files(first)


@pytest.mark.parametrize("export", ALL_EXPORTS, ids=export_id)
def test_a_fresh_plan_after_adopting_is_a_no_op(export: Path, imported):
    spec = imported(export)
    result = run_tiggy("import", export, spec, "--check")
    assert result.code == 0, str(result)
    assert scrub(result.out, spec, export) == "<path1> is equivalent to <path2>\n"
    assert result.err == ""


@pytest.mark.parametrize("export", ALL_EXPORTS, ids=export_id)
def test_exporting_the_adopted_spec_and_adopting_that_reproduces_it_byte_for_byte(
    export: Path, imported, tmp_path: Path
):
    spec = imported(export)
    compiled, again = tmp_path / "compiled.json", tmp_path / "again"
    for args in (("export", spec, "-o", compiled), ("import", compiled, again)):
        result = run_tiggy(*args)
        assert result.code == 0, f"tiggy {' '.join(map(str, args))}\n{result}"
    assert _files(again) == _files(spec)

    plan = run_tiggy("import", compiled, spec, "--check")
    assert plan.code == 0, f"the compiled export doesn't check clean against the spec it came from\n{plan}"


@pytest.mark.parametrize("export", ALL_EXPORTS, ids=export_id)
def test_validate_finds_the_same_problems_in_the_export_and_the_adopted_spec(export: Path, imported):
    spec = imported(export)
    export_code, from_export = _validate(export)
    spec_code, from_spec = _validate(spec)

    assert spec_code == export_code
    assert _findings(from_spec) == _findings(from_export)
    assert from_spec["summary"] == from_export["summary"]
    assert from_spec["files"] == [], "the adopted spec has code files no $file names"
    for finding in from_spec["findings"]:
        assert finding["location"], f"{finding['kind']} {finding['name']!r} has no location in the spec"
        relpath, line = finding["location"].rsplit(":", 1)
        lines = (spec / relpath).read_bytes().decode("utf-8").split("\n")
        assert 1 <= int(line) <= len(lines), finding["location"]


@pytest.mark.parametrize("export", COMMITTED, ids=export_id)
def test_validate_exits_as_pinned_for_each_committed_container(export: Path):
    code, report = _validate(export)
    errors = sorted({f["rule"] for f in report["findings"] if f["severity"] == "error"})
    assert (code, errors) == VALIDATE_EXIT[export_id(export)]


def test_every_committed_container_has_its_validate_exit_pinned():
    assert sorted(VALIDATE_EXIT) == sorted(export_id(p) for p in COMMITTED)


@pytest.mark.parametrize("export", CDM_EXPORTS, ids=export_id)
def test_adopted_spec_matches_its_reviewed_golden(export: Path, imported, golden):
    """Each synthetic container's adopted spec, so a layout or emitter change shows as a reviewable diff."""
    golden(f"adopt/{export.stem}", imported(export))


# Where the spec lands


@pytest.mark.parametrize(
    ("occupant", "message"),
    [("an-earlier-adoption", NOT_EMPTY), ("a-gitkeep", NOT_EMPTY), ("an-interrupted-import", INTERRUPTED)],
    ids=["an-earlier-adoption", "a-gitkeep", "an-interrupted-import"],
)
def test_adopting_refuses_a_directory_that_is_not_empty_and_leaves_it_untouched(
    occupant: str, message: str, imported, tmp_path: Path
):
    if occupant == "an-earlier-adoption":
        spec = imported(WEB_ANALYTICS)
    else:
        spec = tmp_path / "spec"
        spec.mkdir()
        (spec / {"a-gitkeep": ".gitkeep", "an-interrupted-import": ".tiggy-import.lock"}[occupant]).touch()
    entries, files, mtime = _entries(spec), _files(spec), spec.stat().st_mtime_ns

    result = run_tiggy("import", WEB_ANALYTICS, spec)
    assert result.code == 2, str(result)
    assert result.out == ""
    assert scrub(result.err, spec) == message
    assert (_entries(spec), _files(spec), spec.stat().st_mtime_ns) == (entries, files, mtime)
    assert _entries(tmp_path) == ["spec", *(f"spec/{e}" for e in entries)]


@pytest.mark.parametrize("named_as", ["its-path", "dot-from-inside-it"])
def test_adopting_into_an_existing_empty_directory_fills_it_in_place(named_as: str, imported, tmp_path: Path):
    """The adopting repo's directory keeps its identity and permissions; only its contents arrive."""
    spec = tmp_path / "containers" / "analytics"
    spec.mkdir(parents=True)
    if os.name != "nt":
        spec.chmod(0o750)
    identity, mode = spec.stat().st_ino, stat.S_IMODE(spec.stat().st_mode)

    if named_as == "its-path":
        result = run_tiggy("import", WEB_ANALYTICS, spec)
    else:
        result = run_tiggy("import", WEB_ANALYTICS, ".", cwd=spec)
    assert result.code == 0, str(result)
    assert _files(spec) == _files(imported(WEB_ANALYTICS, name="fresh"))
    assert spec.stat().st_ino == identity
    assert stat.S_IMODE(spec.stat().st_mode) == mode
    assert not (spec / ".tiggy-import.lock").exists()


def _duplicate_tag_name(export: dict[str, Any]) -> str:
    tags = export["containerVersion"]["tag"]
    tags[1]["name"] = tags[0]["name"]
    return f"Error: can't import this container: 2 tags are named {tags[0]['name']!r}\n"


def _not_a_container(export: dict[str, Any]) -> str:
    export["containerVersion"] = []
    return "Error: <path2>: not a GTM container export:\n  containerVersion: Input should be an object\n"


@pytest.mark.parametrize("target", ["new-directory-under-missing-parents", "existing-empty-directory"])
@pytest.mark.parametrize(
    "broken", [_duplicate_tag_name, _not_a_container], ids=["duplicate-tag", "not-export"]
)
def test_a_refused_adoption_leaves_nothing_behind(target: str, broken, tmp_path: Path):
    export = load_export(WEB_ANALYTICS)
    message = broken(export)
    source = write_export(export, tmp_path / "export.json")
    work = tmp_path / "repo"
    work.mkdir()
    spec = work / "containers" / "analytics" if target.startswith("new") else work / "analytics"
    if target == "existing-empty-directory":
        spec.mkdir()
    before = _entries(work)

    result = run_tiggy("import", source, spec)
    assert result.code == 2, str(result)
    assert scrub(result.err, spec, source) == message
    assert result.out == ""
    assert _entries(work) == before


@pytest.mark.parametrize("fault", ["refusal", "interrupt"])
@pytest.mark.parametrize(
    "target",
    [
        "existing-empty-directory",
        "new-directory",
        "new-directory-under-missing-parents",
    ],
)
def test_an_adoption_stopped_after_staging_leaves_nothing_behind(
    target: str, fault: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Import's last defence refuses after the spec is staged on disk, when it doesn't reproduce the
    container; that, or Ctrl-C at the same moment, has to clean up as thoroughly as an early refusal."""
    from tiggy.io import export as exports
    from tiggy.spec import importer

    def stop(*_: Any, **__: Any) -> str:
        if fault == "interrupt":
            raise KeyboardInterrupt
        return "tag 'Google tag'"

    monkeypatch.setattr(importer.canon, "compare", stop)
    work = tmp_path / "repo"
    work.mkdir()
    spec = {
        "existing-empty-directory": work / "analytics",
        "new-directory": work / "analytics",
        "new-directory-under-missing-parents": work / "containers" / "web" / "analytics",
    }[target]
    if target == "existing-empty-directory":
        spec.mkdir()
    before = _entries(work)

    try:
        importer.import_version(exports.read(WEB_ANALYTICS).raw_version, spec)
    except KeyboardInterrupt:
        assert fault == "interrupt"
    except importer.ImportError_ as e:
        assert fault == "refusal"
        assert str(e) == "the written spec doesn't reproduce the container, at tag 'Google tag'"
    else:
        pytest.fail("import went ahead although the staged spec didn't reproduce the container")
    assert _entries(work) == before


# Published version or workspace export


def test_a_published_version_and_a_workspace_export_adopt_the_same_way(tmp_path: Path):
    """Versions > Export and Admin > Export Container give the same objects. Only a published version has
    a name and description, which label the publish rather than configure the container."""
    published = load_export(WEB_ANALYTICS)
    workspace = _as_workspace_export(published)
    collections = set(COLLECTION_LABELS) | {"builtInVariable", "container"}
    marketing = load_export(WEB_MARKETING)["containerVersion"]
    assert set(workspace["containerVersion"]) - collections == set(marketing) - collections, (
        "not shaped like the committed workspace export"
    )
    assert {"name", "description"} <= set(published["containerVersion"])
    sources = {"published": WEB_ANALYTICS, "workspace": write_export(workspace, tmp_path / "workspace.json")}

    specs = {}
    for kind, source in sources.items():
        specs[kind] = tmp_path / kind
        result = run_tiggy("import", source, specs[kind])
        assert result.code == 0, str(result)
        plan = run_tiggy("import", source, specs[kind], "--check")
        assert plan.code == 0, str(plan)

    assert _files(specs["published"]) == _files(specs["workspace"])
    (code_a, report_a), (code_b, report_b) = _validate(specs["published"]), _validate(specs["workspace"])
    assert (code_a, _findings(report_a)) == (code_b, _findings(report_b)) == (0, Counter())


def test_switching_the_drift_gate_to_workspace_exports_changes_nothing(imported, tmp_path: Path):
    """A spec adopted from a published version, checked against a workspace export of the same container."""
    spec = imported(WEB_ANALYTICS)
    workspace = write_export(_as_workspace_export(load_export(WEB_ANALYTICS)), tmp_path / "workspace.json")
    result = run_tiggy("import", workspace, spec, "--check")
    assert (result.code, result.err) == (0, ""), str(result)
    assert scrub(result.out, workspace, spec) == "<path2> is equivalent to <path1>\n"
