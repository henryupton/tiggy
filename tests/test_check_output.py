"""What `import --check` prints in each format, and the flags that shape it.

Text is for people, JSON for scripts and `github` for annotations on a pull request's diff. All three
carry the same differences, each located relative to the working directory, as validate's findings are.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import (
    FIXTURES,
    Result,
    line_of,
    load_export,
    run_tiggy,
    write_export,
)

ANALYTICS = FIXTURES / "cdm" / "web_analytics.json"
TAG = "GA4 - sign_up"
TAGS = "folders/ga4/tags.yml"
PAYLOAD_KEYS = ["source", "spec", "equivalent", "summary", "files", "differences"]
SUMMARY_KEYS = ["differences", "defaults", "unreadSecrets", "semanticOnly"]


def _tag(version: dict[str, Any], name: str) -> dict[str, Any]:
    (tag,) = [t for t in version["tag"] if t["name"] == name]
    return tag


def _live(tmp_path: Path, edit) -> Path:
    export = load_export(ANALYTICS)
    edit(export["containerVersion"])
    return write_export(export, tmp_path / "live.json")


def _paused_and_renamed(version: dict[str, Any]) -> None:
    tag = _tag(version, TAG)
    tag["paused"] = True
    (event,) = [p for p in tag["parameter"] if p["key"] == "eventName"]
    event["value"] = "signup"


def _check(spec: Path, live: Path, *args: str) -> Result:
    """`import --check` from the directory holding the spec, as CI runs it from the repository's root."""
    return run_tiggy("import", live, spec, "--check", *args, cwd=spec.parent)


@pytest.fixture
def spec(imported) -> Path:
    return imported(ANALYTICS)


def test_json_gives_each_difference_its_path_sides_and_line_and_agrees_with_text(spec, tmp_path):
    live = _live(tmp_path, _paused_and_renamed)

    result, text = _check(spec, live, "--format", "json"), _check(spec, live)

    payload = json.loads(result.out)
    at, event = line_of(spec, TAGS, f"- name: {TAG}"), line_of(spec, TAGS, "eventName: sign_up")
    assert (result.code, result.err) == (1, ""), result
    assert list(payload) == PAYLOAD_KEYS and list(payload["summary"]) == SUMMARY_KEYS
    assert payload == {
        "source": str(live),
        "spec": str(spec),
        "equivalent": False,
        "summary": {"differences": 2, "defaults": 0, "unreadSecrets": 0, "semanticOnly": 0},
        "files": [],
        "differences": [
            {
                "kind": "tags",
                "name": TAG,
                "op": "only_in_container",
                "path": "paused",
                "segments": ["paused"],
                "message": "paused (missing from the spec)",
                "location": f"spec/{TAGS}:{at}",
                "default": False,
            },
            {
                "kind": "tags",
                "name": TAG,
                "op": "changed",
                "path": "parameter.eventName",
                "segments": ["parameter", "eventName"],
                "container": "signup",
                "spec": "sign_up",
                "message": "parameter.eventName ('signup' in the container, 'sign_up' in the spec)",
                "location": f"spec/{TAGS}:{event}",
                "default": False,
            },
        ],
    }
    differences = payload["differences"]
    assert [list(d) for d in differences] == [  # only the values the message shows
        ["kind", "name", "op", "path", "segments", *sides, "message", "location", "default"]
        for sides in ([], ["container", "spec"])
    ]
    assert text.out.splitlines() == [
        *(f"≠ {d['kind']} {d['name']!r}: {d['message']}  {d['location']}" for d in differences),
        f"2 differences between {live} and {spec}",
    ]


def test_github_annotates_each_difference_at_its_line_then_says_how_many(spec, tmp_path):
    live = _live(tmp_path, _paused_and_renamed)
    (spec / "unfiled" / "old-snippet.html").write_bytes(b"<script>/* retired */</script>\n")

    result = _check(spec, live, "--format", "github")

    at, event = line_of(spec, TAGS, f"- name: {TAG}"), line_of(spec, TAGS, "eventName: sign_up")
    assert result.code == 1, result
    assert result.out.splitlines() == [
        f"::error file=spec/{TAGS},line={at},title=drift::tags '{TAG}': paused (missing from the spec)",
        f"::error file=spec/{TAGS},line={event},title=drift::tags '{TAG}': "
        "parameter.eventName ('signup' in the container, 'sign_up' in the spec)",
        "::warning file=spec/unfiled/old-snippet.html,title=stray-file::no $file refers to this code file",
        f"2 differences between {live} and {spec}",
    ]
    assert result.err == "! spec/unfiled/old-snippet.html: no $file refers to this code file\n", result


@pytest.mark.parametrize("fmt", ["text", "json", "github"])
def test_an_equivalent_container_says_so_in_every_format(fmt, spec, tmp_path):
    result = _check(spec, ANALYTICS, "--format", fmt)

    assert (result.code, result.err) == (0, ""), result
    if fmt == "json":
        payload = json.loads(result.out)
        assert (payload["equivalent"], payload["differences"]) == (True, []), result
        assert payload["summary"] == dict.fromkeys(SUMMARY_KEYS, 0)
    else:
        assert result.out == f"{spec} is equivalent to {ANALYTICS}\n", result


@pytest.mark.parametrize("flag", [("--format", "json"), ("--format", "github"), ("--exact",)], ids=" ".join)
def test_a_check_flag_without_check_exits_2_and_writes_nothing(flag, tmp_path):
    result = run_tiggy("import", ANALYTICS, tmp_path / "new", *flag)

    assert (result.code, result.out) == (2, ""), result
    assert result.err == f"Error: {flag[0]} is for --check\n"
    assert not (tmp_path / "new").exists()


FORGED_KEY = "eventName\x1b[2J\n✓ spec is equivalent"  # clears the screen, then fakes a pass
SHOWN_KEY = r"parameter['eventName\x1b[2J\n✓ spec is equivalent']"


def _forged_key(version: dict[str, Any]) -> None:
    _tag(version, TAG)["parameter"].append({"type": "TEMPLATE", "key": FORGED_KEY, "value": "sign_up\x07"})


@pytest.mark.parametrize("fmt", ["text", "github", "json"])
def test_control_characters_in_a_gtm_key_are_escaped_and_json_keeps_them_exactly(fmt, spec, tmp_path):
    result = _check(spec, _live(tmp_path, _forged_key), "--format", fmt)

    both = result.out + result.err
    assert result.code == 1, result
    assert [c for c in both if c != "\n" and not c.isprintable()] == [], result
    assert not any(line.startswith("✓") for line in both.splitlines()), "a key forged a line of output"
    if fmt == "json":
        (found,) = json.loads(result.out)["differences"]
        assert found["segments"] == ["parameter", FORGED_KEY], result
    else:
        assert f"{SHOWN_KEY} (missing from the spec)" in result.out, result


SPOOFED = "a\u202egnp.exe\u2028✓ spec is equivalent"  # reverses what follows, then starts a line


def _spoofed_row(version: dict[str, Any]) -> None:
    (table,) = [p for p in _tag(version, TAG)["parameter"] if p["key"] == "eventSettingsTable"]
    cells = [("parameter", "note"), ("parameterValue", SPOOFED)]
    table["list"].append(
        {"type": "MAP", "map": [{"type": "TEMPLATE", "key": k, "value": v} for k, v in cells]}
    )


@pytest.mark.parametrize("fmt", ["text", "github", "json"])
def test_a_value_in_a_row_only_the_container_has_cant_reorder_or_break_a_line(fmt, spec, tmp_path):
    """A row shows whole, since its index can't say which row it is: a bidi override or line separator in
    it is escaped like any control character, and JSON keeps it exactly."""
    result = _check(spec, _live(tmp_path, _spoofed_row), "--format", fmt)

    assert result.code == 1, result
    if fmt == "json":
        (found,) = json.loads(result.out)["differences"]
        assert found["container"] == {"parameter": "note", "parameterValue": SPOOFED}, result
    else:
        assert [c for c in result.out if c != "\n" and not c.isprintable()] == [], result
        assert not any(line.startswith("✓") for line in result.out.splitlines()), "a value forged a line"
