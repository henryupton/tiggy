"""Every bug the adversarial reviews found and Tiggy has since fixed, pinned so none comes back.

One small test per fixed behaviour, named for what the analytics team would see. Most run the CLI on
a small probe container (one folder, one Data Layer variable, the `track_event` trigger and the
custom HTML tag that POSTs the payload); the YAML ones call `tiggy.spec.yamlio` directly.
"""

import json
import os
import shutil
import subprocess
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from tests.conftest import (
    FIXTURES,
    fragment_of,
    load_export,
    problems_reported,
    replace_in_spec,
    run_tiggy,
    write_export,
)
from tests.fixtures.cdm import builders as b
from tiggy import checks, cli, report
from tiggy.checks import Severity
from tiggy.spec import importer
from tiggy.spec.yamlio import Flow, YamlError, load, render

SERVER = FIXTURES / "cdm" / "server.json"
MARKETING = FIXTURES / "cdm" / "web_marketing.json"
EXPORT_TIME = "2026-10-19 09:30:00"

TAGS = "folders/analytics/tags.yml"
TRIGGERS = "folders/analytics/triggers.yml"
VARIABLES = "folders/analytics/variables.yml"
SIDECAR = "folders/analytics/html-post-payload.html"
POST_HTML = "<script>\n  window.postPayload('https://collect.example.com/e');\n</script>\n"
LOCK = ".tiggy-import.lock"

POSIX = pytest.mark.skipif(os.name == "nt", reason="POSIX file modes, devices and file names")


# The probe container and helpers


def probe() -> dict[str, Any]:
    """IDs: folder 1, variable 2, trigger 3, tag 4."""
    c = b.Container("Regression probe", public_id="GTM-TEST001", container_id="101")
    c.built_ins("PAGE_URL", "EVENT")
    analytics = c.folder("Analytics")
    c.variable("DLV - event_name", b.data_layer("event_name"), folder=analytics)
    track = c.trigger("CE - track_event", b.custom_event("track_event"), folder=analytics)
    c.tag("HTML - post payload", b.custom_html(POST_HTML), firing=[track], folder=analytics)
    return c.export(EXPORT_TIME)


@pytest.fixture(scope="session")
def probe_export(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_export(probe(), tmp_path_factory.mktemp("probe") / "probe.json")


@pytest.fixture
def spec(imported: Callable[..., Path], probe_export: Path) -> Path:
    """A fresh copy of the probe container imported as a spec."""
    return imported(probe_export)


def changed(tmp_path: Path, change: Callable[[dict[str, Any]], None], name: str = "export.json") -> Path:
    """The probe export with `change` applied to its containerVersion, written to tmp_path."""
    export = probe()
    change(export["containerVersion"])
    return write_export(export, tmp_path / name)


def tag(version: dict[str, Any]) -> dict[str, Any]:
    return version["tag"][0]


def line_of(path: Path, needle: str) -> int:
    """The 1-based line holding `needle`, which must appear on exactly one line."""
    lines = [n for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if needle in line]
    assert len(lines) == 1, f"{needle!r} is on lines {lines} of {path}"
    return lines[0]


def symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("this system can't create symlinks")


def nested_lists(depth: int) -> dict[str, Any]:
    """A LIST `depth` deep whose every level carries isWeakReference, so none has a readable form."""
    p = b.text("leaf", "x")
    for level in range(depth):
        p = b.listing("nest" if level == depth - 1 else None, [p, b.text(None, f"v{level}")])
        p["isWeakReference"] = False
    return p


# Reading exports


MALFORMED = {
    "duplicate keys": (
        lambda text: text.replace(
            '"name": "HTML - post payload",',
            '"name": "HTML - post payload",\n                "name": "HTML - beacon",',
        ),
        'not valid JSON: "name" appears twice in one object',
    ),
    "NaN": (
        lambda text: text.replace('"exportFormatVersion": 2', '"exportFormatVersion": NaN'),
        "not valid JSON: NaN isn't JSON",
    ),
    "nested too deeply": (
        lambda text: text.replace('"exportFormatVersion": 2', '"x": ' + "[" * 100_000 + "]" * 100_000),
        "not valid JSON: nested too deeply",
    ),
    "not an object": (
        lambda text: "[" + text + "]",
        "not a GTM container export: the top level isn't an object",
    ),
}


@pytest.mark.parametrize("case", MALFORMED)
def test_malformed_export_json_is_refused_by_import_and_validate(tmp_path: Path, case: str):
    edit, message = MALFORMED[case]
    source = tmp_path / "export.json"
    source.write_bytes(edit(b.dumps(probe())).encode("utf-8"))

    imported, validated = run_tiggy("import", source, tmp_path / "spec"), run_tiggy("validate", source)

    for result in (imported, validated):
        assert result.code == 2, result
        assert message in result.err
        assert "Traceback" not in result.err
    assert not (tmp_path / "spec").exists()


def test_an_error_message_escapes_control_characters_from_the_export(tmp_path: Path):
    source = tmp_path / "export.json"
    text = b.dumps(probe()).replace(
        '"exportFormatVersion": 2', '"a\\u001bb": 1, "a\\u001bb": 2, "exportFormatVersion": 2'
    )
    source.write_text(text, encoding="utf-8")

    result = run_tiggy("validate", source)

    assert result.code == 2, result
    assert '"a\\x1bb" appears twice' in result.err
    assert "\x1b" not in result.err


def test_an_export_that_isnt_utf8_is_refused(tmp_path: Path):
    export = probe()
    export["containerVersion"]["container"]["notes"] = "Café tracking"
    source = tmp_path / "export.json"
    source.write_bytes(b.dumps(export).encode("latin-1"))

    result = run_tiggy("import", source, tmp_path / "spec")

    assert result.code == 2, result
    assert result.err.rstrip().endswith("export.json: not UTF-8 JSON")


@pytest.mark.parametrize("key", ["live_only", "tag_firing_option"])
def test_a_key_spelled_like_a_python_field_name_is_refused_by_import_and_validate(tmp_path: Path, key: str):
    source = changed(tmp_path, lambda v: tag(v).update({key: True}))

    imported, validated = run_tiggy("import", source, tmp_path / "spec"), run_tiggy("validate", source)

    assert imported.code == 2, imported
    assert (
        f"Tiggy reads a value differently from how the export writes it, at tag[0].{key} "
        "(missing from Tiggy's reading)"
    ) in imported.err
    assert validated.code == 2, validated
    assert f"Tiggy reads a value differently from how it's written, at tag[0].{key}" in validated.err
    assert not (tmp_path / "spec").exists()


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("paused", "true", "containerVersion.tag.0.paused: Input should be a valid boolean"),
        (
            "tagFiringOption",
            "once_per_event",
            "containerVersion.tag.0.tagFiringOption: 'once_per_event' isn't one of "
            "TAG_FIRING_OPTION_UNSPECIFIED, UNLIMITED, ONCE_PER_EVENT, ONCE_PER_LOAD",
        ),
    ],
    ids=["string for a boolean", "enum in the wrong case"],
)
def test_a_value_gtm_would_never_write_is_refused_not_coerced(
    tmp_path: Path, field: str, value: str, message: str
):
    source = changed(tmp_path, lambda v: tag(v).update({field: value}))

    result = run_tiggy("import", source, tmp_path / "spec")

    assert result.code == 2, result
    assert message in result.err


def test_fields_outside_the_container_are_named_on_import_and_check(tmp_path: Path, spec: Path):
    export = probe()
    export["exportNote"] = "exported for the audit"
    source = write_export(export, tmp_path / "export.json")
    note = "has fields outside the container, not imported: exportNote"

    imported = run_tiggy("import", source, tmp_path / "fresh")
    checked = run_tiggy("import", source, spec, "--check")

    assert imported.code == 0, imported
    assert imported.err.startswith("note: ") and imported.err.rstrip().endswith(note)
    assert checked.code == 0, checked
    assert checked.err.rstrip().endswith(note)


@pytest.mark.parametrize(
    "section, key",
    [("version", "name"), ("version", "description"), ("container", "features"), ("container", "tag_ids")],
)
def test_a_field_gtm_sets_is_refused_in_a_spec_saying_why(spec: Path, section: str, key: str):
    if section == "version":
        replace_in_spec(spec, "container.yml", "container:\n", f"version:\n  {key}: x\ncontainer:\n")
    else:
        replace_in_spec(
            spec, "container.yml", "  public_id: GTM-TEST001\n", f"  public_id: GTM-TEST001\n  {key}: x\n"
        )

    result = run_tiggy("validate", spec)

    assert result.code == 1, result
    assert f"{key} isn't written in a spec: GTM sets it, and Tiggy compares without it" in result.out


def test_a_null_collection_is_refused_cleanly(tmp_path: Path):
    source = changed(tmp_path, lambda v: v.update(folder=None))

    result = run_tiggy("import", source, tmp_path / "spec")

    assert result.code == 2, result
    assert (
        "can't import this container: folder is None, and a spec can only hold a list of folders"
        in result.err
    )


# Import's preconditions and encoding


def test_check_refuses_two_objects_with_one_name_as_import_does(tmp_path: Path, spec: Path):
    def twin(version: dict[str, Any]) -> None:
        other = json.loads(json.dumps(tag(version)))
        other.update(tagId="99", parameter=[b.text("html", "<p>a different tag</p>")])
        version["tag"].append(other)

    source = changed(tmp_path, twin)
    refusal = "can't import this container: 2 tags are named 'HTML - post payload'"

    imported = run_tiggy("import", source, tmp_path / "fresh")
    checked = run_tiggy("import", source, spec, "--check")

    for result in (imported, checked):
        assert result.code == 2, result
        assert refusal in result.err
    assert "equivalent" not in checked.out


def test_a_nameless_built_in_variable_gets_a_clear_refusal(tmp_path: Path, spec: Path):
    source = changed(tmp_path, lambda v: v["builtInVariable"][0].pop("name"))
    refusal = "can't import this container: built-in variable PAGE_URL has no name, which a spec needs"

    imported = run_tiggy("import", source, tmp_path / "fresh")
    checked = run_tiggy("import", source, spec, "--check")

    for result in (imported, checked):
        assert result.code == 2, result
        assert refusal in result.err


@pytest.mark.parametrize("where", ["tag type", "zone whitelist"])
def test_a_type_two_gallery_templates_could_spell_is_refused(tmp_path: Path, where: str):
    c = b.Container("Regression probe", public_id="GTM-TEST001", container_id="101")
    reference = b.gallery("example-dev", "data-tag", "a" * 40, "b" * 64, template_id="TESTGAL")
    data_tag = c.template("Data Tag", "___INFO___\n\n{}\n", gallery_reference=reference)
    c.template("Data Tag copy", "___INFO___\n\n{}\n", gallery_reference=dict(reference))
    purchase = c.trigger("CE - purchase", b.custom_event("purchase"))
    if where == "tag type":
        c.tag(
            "DT - purchase",
            b.template_tag(data_tag, [b.text("url", "https://collect.example.com")]),
            firing=[purchase],
        )
    export = c.export(EXPORT_TIME)
    if where == "zone whitelist":
        export["containerVersion"]["zone"] = [
            {
                "accountId": "1000",
                "containerId": "101",
                "zoneId": "1",
                "name": "Vendors",
                "typeRestriction": {"enable": True, "whitelistedTypeId": [data_tag, "html"]},
            }
        ]
    source = write_export(export, tmp_path / "export.json")

    result = run_tiggy("import", source, tmp_path / "spec")

    assert result.code == 2, result
    assert "cvt_TESTGAL could mean custom template 'Data Tag' or 'Data Tag copy'" in result.err


def test_a_trigger_group_imports_and_checks_equivalent(tmp_path: Path):
    group = {
        "accountId": "1000",
        "containerId": "101",
        "triggerId": "50",
        "name": "Group - sign up funnel",
        "type": "TRIGGER_GROUP",
        "parameter": [
            b.listing(
                "triggerIds",
                [
                    {"type": "TRIGGER_REFERENCE", "value": "3"},
                    {"type": "TRIGGER_REFERENCE", "value": b.ALL_PAGES},
                ],
            )
        ],
    }
    source = changed(tmp_path, lambda v: v["trigger"].append(group))
    spec = tmp_path / "spec"

    imported = run_tiggy("import", source, spec)
    checked = run_tiggy("import", source, spec, "--check")

    assert imported.code == 0, imported
    fragment = fragment_of(spec, "triggers", "Group - sign up funnel").read_text(encoding="utf-8")
    assert "        - {$type: TRIGGER_REFERENCE, $trigger: CE - track_event}\n" in fragment
    assert "        - {$type: TRIGGER_REFERENCE, $trigger: {built_in: All Pages}}\n" in fragment
    assert checked.code == 0, checked


def test_null_nested_and_reference_fields_import_and_check_equivalent(tmp_path: Path):
    source = changed(tmp_path, lambda v: tag(v).update(consentSettings=None, firingTriggerId=None))
    spec = tmp_path / "spec"

    imported = run_tiggy("import", source, spec)
    checked = run_tiggy("import", source, spec, "--check")

    assert imported.code == 0, imported
    fragment = (spec / TAGS).read_text(encoding="utf-8")
    assert "    firing_triggers: null\n" in fragment and "    consent_settings: null\n" in fragment
    assert checked.code == 0, checked
    assert "Traceback" not in checked.err


def test_a_trigger_id_of_unicode_digits_stays_a_dangling_reference(tmp_path: Path):
    source = changed(tmp_path, lambda v: tag(v).update(firingTriggerId=["²"]))
    spec = tmp_path / "spec"

    imported = run_tiggy("import", source, spec)
    validated = run_tiggy("validate", source)
    checked = run_tiggy("import", source, spec, "--check")

    assert imported.code == 0, imported
    assert "      - {trigger_id: ²}\n" in (spec / TAGS).read_text(encoding="utf-8")
    assert validated.code == 1, validated
    assert "    firing trigger ² does not exist  [unknown-trigger]" in validated.out
    assert checked.code == 0, checked


def test_a_parameter_keyed_like_a_merge_key_imports_in_list_form(tmp_path: Path):
    source = changed(tmp_path, lambda v: tag(v)["parameter"].append(b.text("<<", "merge?")))
    spec = tmp_path / "spec"

    imported = run_tiggy("import", source, spec)
    checked = run_tiggy("import", source, spec, "--check")

    assert imported.code == 0, imported
    assert "      - {$type: TEMPLATE, $key: '<<', $value: 'merge?'}\n" in (spec / TAGS).read_text(
        encoding="utf-8"
    )
    assert checked.code == 0, checked


@pytest.mark.parametrize(
    "extra,message",
    [
        (
            {"vendorMeta": {"<<": "x"}},
            "tag 'HTML - post payload': unmodelled.vendorMeta.'<<': << can't be a key; "
            "YAML readers take it for a merge key",
        ),
        (
            {"vendorWeight": 1.5},
            "tag 'HTML - post payload': unmodelled.vendorWeight: "
            "float values such as 1.5 can't be written exactly yet",
        ),
    ],
    ids=["merge key", "float"],
)
def test_an_unmodelled_value_yaml_cant_hold_is_refused_naming_the_tag(
    tmp_path: Path, extra: dict, message: str
):
    source = changed(tmp_path, lambda v: tag(v).update(extra))

    result = run_tiggy("import", source, tmp_path / "spec")

    assert result.code == 2, result
    assert result.err.splitlines() == [f"Error: can't import this container: {message}"]
    assert not (tmp_path / "spec").exists()


def test_a_parameter_key_past_yamls_implicit_key_limit_imports_as_an_explicit_key(tmp_path: Path):
    key = "k" * 1100
    source = changed(tmp_path, lambda v: tag(v)["parameter"].append(b.text(key, "long")))
    spec = tmp_path / "spec"

    imported = run_tiggy("import", source, spec)
    checked = run_tiggy("import", source, spec, "--check")

    assert imported.code == 0, imported
    text = (spec / TAGS).read_text(encoding="utf-8")
    assert f"      ? {key}\n      : long\n" in text
    assert yaml.safe_load(text)["tags"][0]["parameter"][key] == "long"
    assert checked.code == 0, checked


def test_a_depth_20_nested_list_imports_in_under_2_seconds(tmp_path: Path):
    """Timed in process, so a slow runner's interpreter start-up doesn't count against it."""
    version = probe()["containerVersion"]
    tag(version)["parameter"].append(nested_lists(20))

    started = time.perf_counter()
    result = importer.import_version(version, tmp_path / "spec")
    elapsed = time.perf_counter() - started

    assert result.counts["tags"] == 1
    assert "nest:\n" in (tmp_path / "spec" / TAGS).read_text(encoding="utf-8")
    assert elapsed < 2, f"importing a depth-20 LIST took {elapsed:.1f}s"


def test_nesting_past_64_levels_is_refused_cleanly_on_import(tmp_path: Path):
    source = changed(tmp_path, lambda v: tag(v)["parameter"].append(nested_lists(70)))

    result = run_tiggy("import", source, tmp_path / "spec")

    assert result.code == 2, result
    assert result.err.splitlines() == [f"Error: can't write {TAGS}: it nests more than 64 levels deep"]


def test_windows_reserved_names_get_a_suffix_in_file_names(tmp_path: Path):
    c = b.Container("Regression probe", public_id="GTM-TEST001", container_id="101")
    con = c.folder("CON")
    every = c.trigger("CE - track_event", b.custom_event("track_event"))
    c.tag("NUL", b.custom_html("<p>one</p>"), firing=[every], folder=con)
    c.tag("aux", b.custom_html("<p>two</p>"), firing=[every])
    c.template("PRN", "___INFO___\n\n{}\n")
    source = write_export(c.export(EXPORT_TIME), tmp_path / "export.json")
    spec = tmp_path / "spec"

    result = run_tiggy("import", source, spec)

    assert result.code == 0, result
    written = sorted(p.relative_to(spec).as_posix() for p in spec.rglob("*") if p.is_file())
    assert written == [
        ".editorconfig",
        ".gitattributes",
        "container.yml",
        "custom_templates/prn-template.tpl",
        "custom_templates/prn-template.yml",
        "folders/con-folder/folder.yml",
        "folders/con-folder/nul-code.html",
        "folders/con-folder/tags.yml",
        "unfiled/aux-code.html",
        "unfiled/tags.yml",
        "unfiled/triggers.yml",
    ]


# Import's target directory


INTERRUPTED = f"{LOCK}: an import into this directory was interrupted, so the spec may be incomplete"


@pytest.mark.parametrize("command", ["import", "import --check", "validate", "export"])
def test_a_leftover_import_lock_makes_every_command_refuse(spec: Path, probe_export: Path, command: str):
    (spec / LOCK).touch()
    before = sorted(p.relative_to(spec).as_posix() for p in spec.rglob("*"))

    if command == "import":
        result = run_tiggy("import", probe_export, spec)
        assert result.code == 2, result
        assert "was interrupted, or is running now, and may have left part of a spec there" in result.err
    elif command == "import --check":
        result = run_tiggy("import", probe_export, spec, "--check")
        assert result.code == 2, result
        lines = result.err.splitlines()
        assert lines[-2].endswith("doesn't load:") and lines[-1] == INTERRUPTED
    elif command == "validate":
        result = run_tiggy("validate", spec)
        assert result.code == 1, result
        assert f"✗ {INTERRUPTED}" in result.out.splitlines()
    else:
        result = run_tiggy("export", spec)
        assert result.code == 1, result
        assert result.out == ""
        assert f"✗ {INTERRUPTED}" in result.err.splitlines()
    assert sorted(p.relative_to(spec).as_posix() for p in spec.rglob("*")) == before


def test_import_refuses_a_directory_that_isnt_empty(spec: Path, probe_export: Path):
    before = (spec / TAGS).read_bytes()

    result = run_tiggy("import", probe_export, spec)

    assert result.code == 2, result
    assert "already exists and isn't empty. Importing into an existing spec isn't supported yet" in result.err
    assert (spec / TAGS).read_bytes() == before


@POSIX
def test_import_into_an_existing_empty_directory_keeps_the_directory(tmp_path: Path, probe_export: Path):
    target = tmp_path / "spec"
    target.mkdir()
    target.chmod(0o750)
    inode = target.stat().st_ino

    result = run_tiggy("import", probe_export, target)

    assert result.code == 0, result
    assert target.stat().st_ino == inode
    assert target.stat().st_mode & 0o777 == 0o750
    assert sorted(os.listdir(target)) == [".editorconfig", ".gitattributes", "container.yml", "folders"]


@POSIX
def test_import_creates_the_spec_directory_by_the_umask_not_0700(tmp_path: Path, probe_export: Path):
    previous = os.umask(0o027)
    try:
        result = run_tiggy("import", probe_export, tmp_path / "spec")
    finally:
        os.umask(previous)

    assert result.code == 0, result
    assert (tmp_path / "spec").stat().st_mode & 0o777 == 0o750
    assert (tmp_path / "spec" / "container.yml").stat().st_mode & 0o777 == 0o640


def test_import_into_dot_fills_the_working_directory(tmp_path: Path, probe_export: Path):
    target = tmp_path / "spec"
    target.mkdir()

    result = run_tiggy("import", probe_export, ".", cwd=target)

    assert result.code == 0, result
    assert (target / "container.yml").is_file() and (target / TAGS).is_file()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".tiggy-import-")]


def test_import_through_a_link_fills_the_linked_directory(tmp_path: Path, probe_export: Path):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "spec"
    symlink(link, target)

    result = run_tiggy("import", probe_export, link)

    assert result.code == 0, result
    assert link.is_symlink()
    assert (target / "container.yml").is_file()


def test_an_import_beaten_to_a_new_directory_leaves_the_winners_spec(
    tmp_path: Path, monkeypatch, probe_export
):
    target = tmp_path / "spec"
    render_ = importer.render

    def rival_lands_first(tree: dict[str, Any]) -> str:
        if not target.exists():
            target.mkdir()
            (target / "container.yml").write_text("tiggy: 1\n", encoding="utf-8")
        return render_(tree)

    monkeypatch.setattr(importer, "render", rival_lands_first)

    with pytest.raises(importer.ImportError_, match="can't write"):
        importer.import_version(load_export(probe_export)["containerVersion"], target)

    assert os.listdir(target) == ["container.yml"]
    assert (target / "container.yml").read_text(encoding="utf-8") == "tiggy: 1\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["spec"]


def test_an_import_into_an_empty_directory_refuses_once_something_else_writes_there(
    tmp_path: Path, monkeypatch, probe_export
):
    target = tmp_path / "spec"
    target.mkdir()
    render_ = importer.render

    def someone_writes_meanwhile(tree: dict[str, Any]) -> str:
        (target / "notes.txt").write_text("mine", encoding="utf-8")
        return render_(tree)

    monkeypatch.setattr(importer, "render", someone_writes_meanwhile)

    with pytest.raises(importer.ImportError_, match="something else wrote into"):
        importer.import_version(load_export(probe_export)["containerVersion"], target)

    assert os.listdir(target) == ["notes.txt"]


def test_an_import_whose_spec_reads_back_differently_writes_nothing(
    tmp_path: Path, monkeypatch, probe_export
):
    target = tmp_path / "spec"
    compile_ = importer.compile_spec

    def lossy(spec, secrets=None):
        compiled = compile_(spec, secrets)
        compiled.version.tag[0].notes = "added on the way back"
        return compiled

    monkeypatch.setattr(importer, "compile_spec", lossy)

    with pytest.raises(
        importer.ImportError_, match=r"doesn't reproduce the container, at tag\.HTML - post payload\.notes"
    ):
        importer.import_version(load_export(probe_export)["containerVersion"], target)

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_the_pre_commit_hint_is_repo_relative_and_regex_escaped(tmp_path: Path, probe_export: Path):
    repo = tmp_path / "adopting-repo"
    (repo / "gtm.web").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    hint = "  exclude: '^gtm\\.web/{name}/.*\\.(tpl|html|js)$'"

    absolute = run_tiggy("import", probe_export, repo / "gtm.web" / "analytics")
    relative = run_tiggy("import", probe_export, "../gtm.web/marketing", cwd=repo / "gtm.web")

    assert absolute.code == 0, absolute
    assert hint.format(name="analytics") in absolute.out.splitlines()
    assert relative.code == 0, relative
    assert hint.format(name="marketing") in relative.out.splitlines()


# Loading a spec


@pytest.mark.parametrize("what", ["fragment", "sidecar", "directory"])
def test_links_are_refused(tmp_path: Path, spec: Path, what: str):
    outside = tmp_path / "outside"
    outside.mkdir()
    if what == "fragment":
        (outside / "extra.yml").write_text("tiggy: 1\n", encoding="utf-8")
        symlink(spec / "extra.yml", outside / "extra.yml")
        expected = "✗ extra.yml: a link, which Tiggy doesn't follow"
    elif what == "sidecar":
        line = line_of(spec / TAGS, "$file")
        shutil.move(spec / SIDECAR, outside / "post.html")
        symlink(spec / SIDECAR, outside / "post.html")
        expected = f"✗ {TAGS}:{line}: $file '{SIDECAR}' goes through a link, which Tiggy doesn't follow"
    else:
        shutil.move(spec / "folders" / "analytics", outside / "analytics")
        symlink(spec / "folders" / "analytics", outside / "analytics")
        expected = "✗ folders/analytics: a linked directory, which Tiggy doesn't follow"

    result = run_tiggy("validate", spec)

    assert result.code == 1, result
    assert expected in result.out.splitlines()


def test_a_file_ref_differing_only_in_case_is_reported(spec: Path):
    typed = SIDECAR.replace("html-post", "HTML-post")
    replace_in_spec(spec, TAGS, SIDECAR, typed)
    line = line_of(spec / TAGS, "$file")
    found = (spec / typed).exists()  # case-insensitive filesystems (macOS, Windows) find it anyway

    result = run_tiggy("validate", spec)

    assert result.code == 1, result
    reason = (
        f"is spelled '{SIDECAR}' on disk; Linux and other exact-name systems won't find it"
        if found
        else "does not exist"
    )
    assert f"✗ {TAGS}:{line}: $file '{typed}' {reason}" in result.out.splitlines()


def test_a_file_ref_in_another_unicode_form_is_reported(spec: Path):
    (spec / SIDECAR).rename(spec / "folders" / "analytics" / "café.html")
    (on_disk,) = [n for n in os.listdir(spec / "folders" / "analytics") if n.endswith(".html")]
    other = unicodedata.normalize("NFD" if unicodedata.is_normalized("NFC", on_disk) else "NFC", on_disk)
    typed = f"folders/analytics/{other}"
    replace_in_spec(spec, TAGS, SIDECAR, typed)
    line = line_of(spec / TAGS, "$file")
    found = (spec / typed).exists()  # macOS finds either form; Linux needs the exact bytes

    result = run_tiggy("validate", spec)

    assert result.code == 1, result
    reason = (
        "is written in a different Unicode form from the name on disk, "
        + ascii(f"folders/analytics/{on_disk}")
        if found
        else "does not exist"
    )
    assert f"✗ {TAGS}:{line}: $file '{typed}' {reason}" in result.out


@pytest.mark.parametrize(
    "ref,reason",
    [
        ("folders/analytics", "isn't a regular file"),
        pytest.param(
            "folders/analytics/tags.yml/post.html",
            "can't be read: Not a directory",
            marks=POSIX,
        ),
    ],
    ids=["a directory", "through a file"],
)
def test_a_file_ref_that_isnt_a_file_is_a_problem(spec: Path, ref: str, reason: str):
    replace_in_spec(spec, TAGS, SIDECAR, ref)
    line = line_of(spec / TAGS, "$file")

    result = run_tiggy("validate", spec)

    assert result.code == 1, result
    assert f"✗ {TAGS}:{line}: $file '{ref}' {reason}" in result.out.splitlines()


def test_a_sidecar_that_isnt_utf8_is_a_problem(spec: Path):
    (spec / SIDECAR).write_bytes("<p>Café</p>".encode("latin-1"))
    line = line_of(spec / TAGS, "$file")

    result = run_tiggy("validate", spec)

    assert result.code == 1, result
    assert f"✗ {TAGS}:{line}: $file '{SIDECAR}' is not UTF-8" in result.out.splitlines()


def test_a_fragment_with_an_upper_case_suffix_is_a_problem(spec: Path):
    (spec / "extra.YML").write_text("tiggy: 1\n", encoding="utf-8")

    result = run_tiggy("validate", spec)

    assert result.code == 1, result
    assert "✗ extra.YML: fragments end in lower-case .yml or .yaml" in result.out.splitlines()


def test_an_unreferenced_sidecar_is_a_warning_that_fails_strict(spec: Path):
    (spec / "unfiled").mkdir()
    (spec / "unfiled" / "orphan.html").write_text("<p>left behind</p>", encoding="utf-8")

    lenient, strict = run_tiggy("validate", spec), run_tiggy("validate", spec, "--strict")

    assert lenient.code == 0, lenient
    assert "⚠ unfiled/orphan.html: no $file refers to this code file" in lenient.out.splitlines()
    assert strict.code == 1, strict


def test_scalar_entries_in_a_collection_are_reported_at_their_line(spec: Path):
    replace_in_spec(spec, "container.yml", "  - type: EVENT\n    name: Event\n", "  - EVENT\n")
    line = line_of(spec / "container.yml", "  - EVENT")

    result = run_tiggy("validate", spec)

    assert result.code == 1, result
    assert (
        f"✗ container.yml:{line}: each entry under built_in_variables is a mapping, got 'EVENT'" in result.out
    )


def add_parameters(spec: Path, lines: str) -> int:
    """Adds parameter lines to the probe tag; returns the line the first one lands on."""
    replace_in_spec(
        spec, TAGS, "      supportDocumentWrite: false\n", "      supportDocumentWrite: false\n" + lines
    )
    return line_of(spec / TAGS, "supportDocumentWrite") + 1


def test_explicit_list_and_map_members_must_be_lists(spec: Path):
    first = add_parameters(
        spec,
        "      rows: {$type: LIST, $list: '{{DLV - rows}}'}\n"
        "      cells: {$type: MAP, $map: {a: b}}\n"
        "      count: {$type: LIST, $list: 5}\n",
    )

    result = run_tiggy("validate", spec)

    assert result.code == 1, result
    assert problems_reported(result) == [
        f"✗ {TAGS}:{first}: $list must be a list of parameters (or null), got '{{{{DLV - rows}}}}'",
        f'✗ {TAGS}:{first + 1}: $map must be a list of parameters (or null), got {{"a": "b"}}',
        f"✗ {TAGS}:{first + 2}: $list must be a list of parameters (or null), got 5",
        "3 errors, 0 warnings",
    ]


def test_explicit_members_are_type_checked_at_their_line(spec: Path):
    first = add_parameters(
        spec,
        "      a: {$type: TEMPLATE, $value: 2}\n"
        "      b: {$type: TAG_REFERENCE, $value: GA4 - page_view, $is_weak_reference: 'no'}\n"
        "      c: {$type: [TEMPLATE]}\n",
    )

    result = run_tiggy("validate", spec)

    assert result.code == 1, result
    assert result.out.splitlines()[:3] == [
        f"✗ {TAGS}:{first}: $value must be a string (or null), got 2",
        f"✗ {TAGS}:{first + 1}: $is_weak_reference must be true, false or null, got 'no'",
        f'✗ {TAGS}:{first + 2}: $type ["TEMPLATE"] is not a GTM parameter type (BOOLEAN, INTEGER, LIST, MAP, '
        "TAG_REFERENCE, TEMPLATE, TRIGGER_REFERENCE, TYPE_UNSPECIFIED)",
    ]


def test_a_trigger_reference_names_its_trigger_not_a_raw_id(spec: Path):
    first = add_parameters(
        spec,
        "      a: {$type: TRIGGER_REFERENCE, $value: '1'}\n"
        "      b: {$type: TEMPLATE, $trigger: CE - track_event}\n",
    )

    result = run_tiggy("validate", spec)

    assert result.code == 1, result
    assert problems_reported(result) == [
        f"✗ {TAGS}:{first}: a TRIGGER_REFERENCE names its trigger with $trigger, not a raw ID in $value",
        f"✗ {TAGS}:{first + 1}: $trigger is only for $type: TRIGGER_REFERENCE",
        "2 errors, 0 warnings",
    ]


def test_malformed_references_are_located_problems_not_tracebacks(spec: Path):
    replace_in_spec(spec, TAGS, "      - CE - track_event\n", "      - {name: [CE - track_event]}\n")
    replace_in_spec(spec, TAGS, "    type: html\n", "    type: {template: [Amplitude Event]}\n")

    result = run_tiggy("validate", spec)

    assert result.code == 1, result
    assert problems_reported(result) == [
        f'✗ {TAGS}:4: {{"template": ["Amplitude Event"]}} is not a type '
        "(a GTM type string or {template: name})",
        f'✗ {TAGS}:7: {{"name": ["CE - track_event"]}} is not a trigger reference '
        "(a name, {built_in: ...}, or {trigger_id: ...})",
        "2 errors, 0 warnings",
    ]


def test_tiggy_true_is_not_spec_version_1(spec: Path):
    replace_in_spec(spec, TRIGGERS, "tiggy: 1\n", "tiggy: true\n")

    result = run_tiggy("validate", spec)

    assert result.code == 1, result
    assert (
        f"✗ {TRIGGERS}:1: tiggy: true is not a spec version this Tiggy reads (1)" in result.out.splitlines()
    )


def test_validate_exits_2_on_a_directory_with_no_fragments(tmp_path: Path):
    result = run_tiggy("validate", tmp_path)

    assert result.code == 2, result
    assert result.err.rstrip().endswith("is not a spec directory: it holds no .yml fragments")


def test_an_out_of_range_int32_priority_is_reported_with_the_other_problems(imported: Callable[..., Path]):
    spec = imported(SERVER)
    clients = fragment_of(spec, "clients", "GA4")
    tags = fragment_of(spec, "tags", "GA4")
    replace_in_spec(
        spec,
        clients.relative_to(spec),
        "    folder: Clients\n",
        "    folder: Clients\n    priority: 2147483648\n",
    )
    replace_in_spec(
        spec,
        tags.relative_to(spec),
        "      - Event - page_view_anon\n",
        "      - Event - page_view_anonymous\n",
    )

    result = run_tiggy("validate", spec)

    assert result.code == 1, result
    assert problems_reported(result) == [
        f"✗ {clients.relative_to(spec).as_posix()}:{line_of(clients, 'priority')}: "
        "priority must be at most 2147483647, got 2147483648",
        f"✗ {tags.relative_to(spec).as_posix()}:{line_of(tags, 'page_view_anonymous')}: "
        'trigger "Event - page_view_anonymous" is not defined in this spec '
        '(did you mean "Event - page_view_anon"?)',
        "2 errors, 0 warnings",
    ]


def test_unmodelled_cant_set_a_modelled_field_by_either_spelling(spec: Path):
    replace_in_spec(
        spec,
        TAGS,
        "    tag_firing_option: ONCE_PER_EVENT\n",
        "    tag_firing_option: ONCE_PER_EVENT\n"
        "    unmodelled: {liveOnly: true, live_only: true, tagId: '5'}\n",
    )
    line = line_of(spec / TAGS, "unmodelled")

    result = run_tiggy("validate", spec)

    assert result.code == 1, result
    prefix = f"✗ {TAGS}:{line}: unmodelled is for fields Tiggy doesn't know, and"
    assert problems_reported(result) == [
        f"{prefix} 'liveOnly' is written as `live_only`",
        f"{prefix} 'live_only' is written as `live_only`",
        f"{prefix} 'tagId' is assigned by Tiggy, not written in a spec",
        "3 errors, 0 warnings",
    ]


def test_a_built_in_variable_in_a_spec_needs_its_name(spec: Path):
    replace_in_spec(spec, "container.yml", "  - type: EVENT\n    name: Event\n", "  - type: EVENT\n")
    line = line_of(spec / "container.yml", "type: EVENT")

    result = run_tiggy("validate", spec)

    assert result.code == 1, result
    assert problems_reported(result) == [
        f"✗ container.yml:{line}: a built-in variable needs its GTM name too, such as `name: Page URL`",
        "1 error, 0 warnings",
    ]


@pytest.mark.parametrize(
    "fragment,broken,expected",
    [
        (TRIGGERS, "tiggy: 1\nbroken: [\n", f"✗ {TRIGGERS}:4: not valid YAML"),
        ("container.yml", "tiggy: 1\n\tbad\n", "✗ container.yml:2: not valid YAML"),
    ],
    ids=["triggers fragment", "container fragment"],
)
def test_a_broken_fragment_doesnt_cascade_into_false_problems(
    spec: Path, fragment: str, broken: str, expected: str
):
    replace_in_spec(spec, fragment, "tiggy: 1\n", broken)

    result = run_tiggy("validate", spec)

    assert result.code == 1, result
    lines = result.out.splitlines()
    assert len(lines) == 3 and lines[0].startswith(expected), result
    assert lines[1:] == ["", "1 error, 0 warnings"]


def test_a_dangling_trigger_id_never_starts_resolving(spec: Path):
    replace_in_spec(spec, TAGS, "      - CE - track_event\n", "      - {trigger_id: '1'}\n")

    result = run_tiggy("validate", spec)

    assert result.code == 1, result
    assert "    firing trigger 1 does not exist  [unknown-trigger]" in result.out.splitlines()


def test_a_dangling_template_type_never_starts_resolving(spec: Path):
    (spec / "custom_templates").mkdir()
    (spec / "custom_templates" / "vendor.yml").write_text(
        "tiggy: 1\ncustom_templates:\n"
        "  - name: Vendor\n    template_data: {$file: custom_templates/vendor.tpl}\n",
        encoding="utf-8",
    )
    (spec / "custom_templates" / "vendor.tpl").write_text("___INFO___\n\n{}\n", encoding="utf-8")
    replace_in_spec(spec, TAGS, "    type: html\n", "    type: cvt_1_1\n")

    result = run_tiggy("validate", spec)

    assert result.code == 1, result
    assert (
        "    type cvt_1_1 is not a custom template in this container  [unknown-template]"
        in result.out.splitlines()
    )


def test_twin_gallery_templates_in_a_spec_are_refused(imported: Callable[..., Path]):
    spec = imported(MARKETING)
    original = fragment_of(spec, "custom_templates", "Meta Pixel")
    twin = original.with_name("meta-pixel-copy.yml")
    twin.write_bytes(original.read_bytes().replace(b"  - name: Meta Pixel\n", b"  - name: Meta Pixel copy\n"))

    result = run_tiggy("validate", spec)

    assert result.code == 1, result
    message = (
        'custom template "Meta Pixel" shares its gallery template with "Meta Pixel copy", '
        "so GTM can't tell which this means; use form: container"
    )
    problems = [line for line in result.out.splitlines() if line.startswith("✗ ")]
    assert problems and all(line.endswith(message) for line in problems), result


# YAML the readers disagree about


REFUSED_YAML = {
    "plain merge key": (
        "tiggy: 1\nx-a: 1\n<<: {b: 2}\n",
        3,
        "<< can't be a key: YAML readers take it for a merge key",
    ),
    "quoted merge key": (
        "tiggy: 1\nx-a: 1\n'<<': b\n",
        3,
        "<< can't be a key: YAML readers take it for a merge key",
    ),
    "escaped slash": (
        'tiggy: 1\nx-a: 1\nx-b: "a\\/b"\n',
        3,
        "write / rather than \\/; some YAML readers refuse",
    ),
    "tab in block scalar indentation": (
        "tiggy: 1\nx-a: |\n \tb\n",
        3,
        "a tab in a block scalar's indentation",
    ),
    "raw U+2028": ("tiggy: 1\nx-a:\n  - a\u2028  - b\n", 3, "raw U+2028 isn't allowed"),
    "raw U+2029": ("tiggy: 1\nx-a: 1\nx-b: a\u2029b\n", 3, "raw U+2029 isn't allowed"),
    "raw NEL": ("tiggy: 1\nx-a: 1\nx-b: a\x85b\n", 3, "raw U+0085 isn't allowed"),
    "BOM mid-file": ("tiggy: 1\nx-a: 1\nx-b: a\ufeffb\n", 3, "raw U+FEFF isn't allowed"),
    "bare CR": ("tiggy: 1\nx-a: 1\rx-b: 2\n", 2, "a bare CR line ending"),
    "tab inside flow": ("tiggy: 1\nx-a: [b,\tc]\n", 2, "a tab inside [] or {}"),
    "? inside flow": (
        "tiggy: 1\nx-a: {b: c?d}\n",
        2,
        "some YAML readers can't read ? or : unquoted inside {} or []",
    ),
    "signed underscore number": ("tiggy: 1\nx-a: +_1\n", 2, "+_1 isn't a string to every YAML reader"),
    "upper-case hex": ("tiggy: 1\nx-a: 0X1F\n", 2, "0X1F isn't a string to every YAML reader"),
    "upper-case octal": ("tiggy: 1\nx-a: 0O17\n", 2, "0O17 isn't a string to every YAML reader"),
    "upper-case binary": ("tiggy: 1\nx-a: 0B11\n", 2, "0B11 isn't a string to every YAML reader"),
    "integer past 64 bits": (
        "tiggy: 1\nx-a: 99999999999999999999\n",
        2,
        "too big an integer for some YAML readers",
    ),
    "surrogate escapes": (
        'tiggy: 1\nx-a: "\\ud83d\\ude00"\n',
        2,
        "surrogate escapes such as \\ud83d aren't characters",
    ),
    "escape past U+10FFFF": ('tiggy: 1\nx-a: 1\nx-b: "\\U00110000"\n', 3, "an escape past U+10FFFF"),
    "control character": ("tiggy: 1\nx-a: a\x01b\n", 2, "not valid YAML"),
    "unquoted variable in flow": ("tiggy: 1\nx-a: {{DLV - x}}\n", 2, "A {{variable}} needs quoting"),
}


@pytest.mark.parametrize("case", REFUSED_YAML)
def test_yaml_readers_disagree_about_is_refused_at_its_line(case: str):
    text, line, message = REFUSED_YAML[case]

    with pytest.raises(YamlError) as refused:
        load(text)

    assert refused.value.line == line
    assert message in str(refused.value)


@pytest.mark.parametrize("value", [".", "-.", "+.", "2024-01-01 note", "2024-01-01T"])
def test_plain_scalars_every_reader_reads_as_strings_load_as_strings(value: str):
    text = f"tiggy: 1\nx-a: {value}\n"

    assert load(text) == yaml.safe_load(text) == {"tiggy": 1, "x-a": value}


@pytest.mark.parametrize(
    "text", ["notes: >\n  Fires the payload\n  on every page.\n", "notes: >-\n  a\n\n  b\n"]
)
def test_a_folded_block_scalar_reads_as_every_reader_reads_it(text: str):
    assert load(text) == yaml.safe_load(text)
    assert "\a" not in load(text)["notes"]


def test_yaml_nesting_past_64_levels_is_a_clean_error_however_deep():
    started = time.perf_counter()
    with pytest.raises(YamlError) as refused:
        load("x-a: " + "[" * 5000 + "]" * 5000 + "\n")

    assert (refused.value.line, str(refused.value)) == (1, "nested more than 64 levels deep")
    assert time.perf_counter() - started < 2


def test_a_parse_error_names_its_problem_and_line():
    with pytest.raises(YamlError) as refused:
        load("tiggy: 1\nx-a: |\n  \n   b\n")

    assert refused.value.line == 4
    assert str(refused.value).startswith("not valid YAML: ") and "None" not in str(refused.value)


def test_a_bom_at_the_start_of_a_fragment_is_allowed(spec: Path):
    (spec / TRIGGERS).write_bytes(b"\xef\xbb\xbf" + (spec / TRIGGERS).read_bytes())

    result = run_tiggy("validate", spec)

    assert result.code == 0, result


def test_yaml_a_fragment_cant_read_reaches_validate_as_problems_at_file_and_line(spec: Path):
    replace_in_spec(spec, TRIGGERS, "arg1: track_event", "arg1: track\x01event")
    (spec / VARIABLES).write_bytes(
        (spec / VARIABLES).read_bytes().replace(b"event_name\n", b"\xe9vent_name\n")
    )

    result = run_tiggy("validate", spec)

    assert result.code == 1, result
    assert "Traceback" not in result.out + result.err
    lines = result.out.splitlines()
    assert lines[0].startswith(f"✗ {TRIGGERS}:{line_of(spec / TRIGGERS, 'arg1')}: not valid YAML")
    assert lines[1].startswith(f"✗ {VARIABLES}: not UTF-8 (byte ")
    assert lines[2:] == ["", "2 errors, 0 warnings"]


def test_render_quotes_question_marks_and_colons_inside_flow():
    tree = {
        "tiggy": 1,
        "type": Flow(template="Data Tag (sGTM?)"),
        "p": Flow({"$type": "TEMPLATE", "$value": "https://collect.example.com/p?a=1"}),
    }

    text = render(tree)

    assert "type: {template: 'Data Tag (sGTM?)'}\n" in text
    assert yaml.safe_load(text) == load(text) == tree


def test_render_writes_a_multiline_string_with_a_leading_space_as_an_indented_literal():
    tree = {"tiggy": 1, "notes": "  indented first line\nsecond line\n"}

    text = render(tree)

    assert text == "tiggy: 1\nnotes: |2\n    indented first line\n  second line\n"
    assert yaml.safe_load(text) == tree


# Output


def control_export() -> dict[str, Any]:
    c = b.Container("Example\x1b[2J\nforged", public_id="GTM-TEST001", container_id="101")
    c.trigger("CE - a\x07b", b.custom_event("track_event"))
    c.tag("HTML\x1b[31m - x", b.custom_html("<p>x</p>"), firing=["999"])
    export = c.export(EXPORT_TIME)
    export["note\x1b"] = "outside the container"
    return export


def escaped_in_validate_export(tmp_path: Path, imported) -> tuple[Any, list[str]]:
    source = write_export(control_export(), tmp_path / "export.json")
    return run_tiggy("validate", source), [
        "Example\\x1b[2J\\nforged",
        'tag "HTML\\x1b[31m - x"',
        'trigger "CE - a\\x07b"',
    ]


def escaped_in_import(tmp_path: Path, imported) -> tuple[Any, list[str]]:
    source = write_export(control_export(), tmp_path / "export.json")
    return run_tiggy("import", source, tmp_path / "spec"), [
        "Imported Example\\x1b[2J\\nforged into",
        "note\\x1b",
    ]


def escaped_in_validate_spec(tmp_path: Path, imported) -> tuple[Any, list[str]]:
    spec = imported(write_export(control_export(), tmp_path / "export.json"))
    return run_tiggy("validate", spec), [
        'tag "HTML\\x1b[31m - x"  unfiled/tags.yml:3',
        'trigger "CE - a\\x07b"',
    ]


def escaped_in_check(tmp_path: Path, imported) -> tuple[Any, list[str]]:
    spec = imported(write_export(control_export(), tmp_path / "export.json"))
    renamed = control_export()
    tag(renamed["containerVersion"])["name"] = "HTML\x1b[31m - y"
    source = write_export(renamed, tmp_path / "renamed.json")
    return run_tiggy("import", source, spec, "--check"), [
        "≠ tags 'HTML\\x1b[31m - x': only in the spec",
        "≠ tags 'HTML\\x1b[31m - y': only in the container",
    ]


def escaped_in_export_problems(tmp_path: Path, imported) -> tuple[Any, list[str]]:
    spec = imported(write_export(control_export(), tmp_path / "export.json"))
    replace_in_spec(spec, "unfiled/tags.yml", "      - {trigger_id: '999'}\n", '      - "CE - \\a"\n')
    return run_tiggy("export", spec), ['trigger "CE - \\x07" is not defined in this spec']


@pytest.mark.parametrize(
    "scenario",
    [
        escaped_in_validate_export,
        escaped_in_import,
        escaped_in_validate_spec,
        escaped_in_check,
        escaped_in_export_problems,
    ],
    ids=["validate an export", "import", "validate a spec", "import --check", "export's problems"],
)
def test_control_characters_in_names_are_escaped_in_every_output_path(tmp_path: Path, imported, scenario):
    result, expected = scenario(tmp_path, imported)

    raw = result.raw_out + result.raw_err
    assert b"\x1b" not in raw and b"\x07" not in raw, result
    assert b"\nforged" not in raw
    for text in expected:
        assert text in result.out + result.err, result


@POSIX
def test_control_characters_in_paths_are_escaped_in_import_and_check_summaries(tmp_path: Path, probe_export):
    spec = tmp_path / "spec\a\nforged"

    imported = run_tiggy("import", probe_export, spec)
    checked = run_tiggy("import", probe_export, spec, "--check")

    for result in (imported, checked):
        assert result.code == 0, result
        assert b"\x07" not in result.raw_out and b"\nforged" not in result.raw_out, result
        assert "spec\\x07\\nforged" in result.out


def test_check_shows_a_window_into_a_long_value_not_the_whole_value(tmp_path: Path):
    c = b.Container("Regression probe", public_id="GTM-TEST001", container_id="101")
    every = c.trigger("CE - track_event", b.custom_event("track_event"))
    lines = "".join(f"  window.dataLayer.push({{event: 'line_{n}'}});\n" for n in range(400))
    c.tag("HTML - big", b.custom_html(f"<script>\n{lines}</script>\n"), firing=[every])
    source = write_export(c.export(EXPORT_TIME), tmp_path / "export.json")
    spec = tmp_path / "spec"
    assert run_tiggy("import", source, spec).code == 0
    replace_in_spec(spec, "unfiled/html-big.html", "'line_200'", "'line_two_hundred'")

    result = run_tiggy("import", source, spec, "--check")

    assert result.code == 1, result
    difference = result.out.splitlines()[0]
    assert difference.startswith("≠ tags 'HTML - big': parameter.html (from line 202, character 39: ")
    assert difference.endswith("  unfiled/html-big.html:202") and len(difference) < 300


def test_github_annotations_escape_what_workflow_commands_reserve():
    annotation = report._annotation(Severity.WARNING, "a,b", "100%\nsure\x1b", "dir:1/x,y.yml:7")
    assert annotation == "::warning file=dir%3A1/x%2Cy.yml,line=7,title=a%2Cb::100%25\\nsure\\x1b"


def test_a_crash_exits_2_and_says_it_is_a_bug(monkeypatch):
    def crash(graph):
        raise RuntimeError("planted")

    monkeypatch.setattr(checks, "run", crash)

    result = CliRunner().invoke(cli.cli, ["validate", str(SERVER)])

    assert result.exit_code == 2
    assert result.output.rstrip().endswith("tiggy hit a bug; the traceback above is worth reporting")


def test_an_interrupt_exits_130(monkeypatch):
    def interrupt(graph, spec=False):
        raise KeyboardInterrupt

    monkeypatch.setattr(checks, "run", interrupt)

    result = CliRunner().invoke(cli.cli, ["validate", str(SERVER)])

    assert result.exit_code == 130
    assert "Traceback" not in result.output


# export -o


def test_export_o_writes_through_a_link_to_its_target(tmp_path: Path, spec: Path):
    real = tmp_path / "out" / "container.json"
    real.parent.mkdir()
    real.write_text("previous", encoding="utf-8")
    if os.name != "nt":
        real.chmod(0o640)
    link = real.with_name("link.json")
    symlink(link, real)

    result = run_tiggy("export", spec, "-o", link)

    assert result.code == 0, result
    assert link.is_symlink()
    assert (
        json.loads(real.read_text(encoding="utf-8"))["containerVersion"]["container"]["name"]
        == "Regression probe"
    )
    if os.name != "nt":
        assert real.stat().st_mode & 0o777 == 0o640
    assert sorted(os.listdir(real.parent)) == ["container.json", "link.json"]


@POSIX
def test_export_o_dev_stdout_writes_to_stdout(spec: Path):
    result = run_tiggy("export", spec, "-o", "/dev/stdout")

    assert result.code == 0, result
    assert json.loads(result.out)["containerVersion"]["container"]["name"] == "Regression probe"


def test_export_o_into_a_missing_directory_exits_2(tmp_path: Path, spec: Path):
    result = run_tiggy("export", spec, "-o", tmp_path / "missing" / "container.json")

    assert result.code == 2, result
    assert result.err.startswith("Error: can't write ") and "Traceback" not in result.err
    assert not (tmp_path / "missing").exists()


def test_export_o_leaves_the_previous_file_when_the_spec_is_broken(tmp_path: Path, spec: Path):
    out = tmp_path / "container.json"
    out.write_text("previous export", encoding="utf-8")
    replace_in_spec(spec, TRIGGERS, "tiggy: 1\n", "tiggy: 2\n")

    result = run_tiggy("export", spec, "-o", out)

    assert result.code == 1, result
    assert f"✗ {TRIGGERS}:1: tiggy: 2 is not a spec version this Tiggy reads (1)" in result.err.splitlines()
    assert out.read_text(encoding="utf-8") == "previous export"


def test_export_o_replaces_the_file_whole_or_not_at_all(tmp_path: Path, monkeypatch):
    out = tmp_path / "container.json"
    out.write_text("previous export", encoding="utf-8")

    def fail(source, destination):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(cli.os, "replace", fail)

    with pytest.raises(cli.InputError, match="No space left on device"):
        cli._write(out, b'{"exportFormatVersion": 2}')

    assert out.read_text(encoding="utf-8") == "previous export"
    assert os.listdir(tmp_path) == ["container.json"]


# Scale


def large_container() -> dict[str, Any]:
    """A long-lived Analytics container at the scale of a large real one: 400 tags, 300 triggers,
    700 variables. One Data Layer variable per event attribute, payload Custom JavaScript variables
    listing them, GA4 event tags with eventSettingsTable rows and custom HTML beacons, in 20 folders."""
    c = b.Container("Example - Analytics at scale", public_id="GTM-TEST001", container_id="101")
    c.built_ins("PAGE_URL", "PAGE_HOSTNAME", "PAGE_PATH", "REFERRER", "EVENT")
    folders = [c.folder(f"Area {n:02d}") for n in range(20)]
    properties = [f"property_{n:03d}" for n in range(640)]
    for n, prop in enumerate(properties):
        c.variable(
            f"DLV - event_attributes.{prop}", b.data_layer(f"event_attributes.{prop}"), folder=folders[n % 20]
        )
    for n in range(30):
        keys = "".join(
            f"    {p}: {{{{DLV - event_attributes.{p}}}}},\n" for p in properties[n * 20 : (n + 1) * 20]
        )
        c.variable(f"CJS - payload {n:02d}", b.custom_js(f"function() {{\n  return {{\n{keys}  }};\n}}"))
    for n in range(20):
        c.variable(f"C - measurement ID {n:02d}", b.constant("G-TEST000001"))
    for n in range(10):
        rows = [(f"www{k}.example.com", f"https://sgtm.example.com/{k}") for k in range(8)]
        c.variable(f"LT - server URL {n:02d}", b.lookup_table("{{Page Hostname}}", rows, default="none"))
    triggers = [
        c.trigger(f"CE - event_{n:03d}", b.custom_event(f"event_{n:03d}"), folder=folders[n % 20])
        for n in range(300)
    ]
    for n in range(400):
        firing, folder = [triggers[n % 300]], folders[n % 20]
        if n % 4 == 3:
            c.tag(
                f"HTML - beacon {n:03d}",
                b.custom_html(f"<script>\n  beacon({n});\n</script>\n"),
                firing=firing,
                folder=folder,
            )
            continue
        attributes = [properties[(n * 7 + k) % 640] for k in range(8)]
        rows = [(a, f"{{{{DLV - event_attributes.{a}}}}}") for a in attributes]
        body = b.ga4_event(
            f"event_{n % 300:03d}", measurement_id=f"{{{{C - measurement ID {n % 20:02d}}}}}", settings=rows
        )
        c.tag(f"GA4 - event_{n:03d}", body, firing=firing, folder=folder)
    return c.export(EXPORT_TIME)


@pytest.mark.slow
@pytest.mark.parametrize("text", ["k: {?a: v}\n", "k: {? a: v}\n", "k: {a: v, ?b: w}\n"])
def test_an_explicit_key_inside_a_flow_mapping_is_refused(text: str):
    """ruamel and yq 4.48 read {?a: v} as the key a; later yq, following the spec, reads ?a."""
    with pytest.raises(YamlError, match=r"drop the \? before a key inside \{\}"):
        load(text)


def test_a_container_at_real_scale_imports_in_under_20_seconds_and_validates_in_under_10(tmp_path: Path):
    source = write_export(large_container(), tmp_path / "export.json")
    spec = tmp_path / "spec"

    started = time.perf_counter()
    imported = run_tiggy("import", source, spec)
    import_seconds = time.perf_counter() - started
    started = time.perf_counter()
    validated = run_tiggy("validate", spec)
    validate_seconds = time.perf_counter() - started

    assert imported.code == 0, imported
    assert "20 folders, 700 variables, 300 triggers, 400 tags" in imported.out
    assert import_seconds < 20, f"import took {import_seconds:.1f}s"
    assert validated.code == 0, validated
    assert (
        validated.out.splitlines()[0]
        == "Example - Analytics at scale (GTM-TEST001): 400 tags, 300 triggers, 700 variables"
    )
    assert validated.out.splitlines()[-1].startswith("0 errors, ")
    assert validate_seconds < 10, f"validate took {validate_seconds:.1f}s"
