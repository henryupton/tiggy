"""What `import --check` prints in each format, and the flags that shape it.

Text is for people, JSON for scripts and `github` for annotations on a pull request's diff. All three
carry the same differences, each located relative to the working directory, as validate's findings are.
Text marks each as applying the spec would make it (+ add, - remove, ~ change) and shows a structured
value below its line as the spec writes it.
"""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import (
    FIXTURES,
    Result,
    drift_reported,
    edit_spec,
    line_of,
    load_export,
    replace_in_spec,
    run_tiggy,
    write_export,
)
from tests.fixtures.cdm.builders import ALL_PAGES
from tiggy.gtm.resources import TagConsentSetting
from tiggy.spec import diff, yamlio

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
        *(
            f"{marker} {d['kind']} {d['name']!r}: {d['message']}  {d['location']}"
            for marker, d in zip("-~", differences, strict=True)
        ),
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


def _marked(result: Result) -> list[tuple[str, str]]:
    """Each difference as its marker and `kind 'name': message`, unlocated."""
    return [(d.marker, f"{d.kind} {d.name!r}: {d.detail.rsplit('  ', 1)[0]}") for d in drift_reported(result)]


def _named(version: dict[str, Any], collection: str, name: str) -> dict[str, Any]:
    (found,) = [item for item in version[collection] if item["name"] == name]
    return found


def _every_op(version: dict[str, Any]) -> None:
    """A tag only in GTM, one deleted there, and GA4 - sign_up edited every way a field can be."""
    login = copy.deepcopy(_tag(version, TAG))
    login.update(tagId="999", name="GA4 - login")
    version["tag"] = [t for t in version["tag"] if t["name"] != "GA4 - file_download"] + [login]
    tag = _tag(version, TAG)
    tag["paused"] = True
    del tag["tagFiringOption"]
    tag["firingTriggerId"].append(_named(version, "trigger", "CE - track_event")["triggerId"])
    parameters = {p["key"]: p for p in tag["parameter"]}
    parameters["eventName"]["value"] = "signup"
    parameters["eventSettingsTable"]["list"].reverse()


def test_each_difference_is_marked_plus_minus_or_tilde_as_applying_the_spec_would_make_it(spec, tmp_path):
    """+ where the spec has something the container lacks, - the other way round, ~ where both have it,
    for a whole object, a field and a member of a trigger set alike; JSON keeps the op as it was."""
    replace_in_spec(
        spec, TAGS, "      - PV - production hosts\n", "      - PV - production hosts\n      - CE - sign_up\n"
    )

    def every_op_and_retriggered(version):
        _every_op(version)
        _tag(version, "GA4 - track_event")["firingTriggerId"] = [
            _named(version, "trigger", "CE - sign_up")["triggerId"]
        ]

    live = _live(tmp_path, every_op_and_retriggered)

    result, as_json = _check(spec, live), _check(spec, live, "--format", "json")

    assert (result.code, result.err) == (1, ""), result
    assert _marked(result) == [
        ("+", "tags 'GA4 - file_download': only in the spec"),
        ("-", "tags 'GA4 - login': only in the container"),
        ("+", "tags 'GA4 - page_view': firing_triggers ('CE - sign_up' only in the spec)"),
        ("-", f"tags '{TAG}': firing_triggers ('CE - track_event' only in the container)"),
        ("-", f"tags '{TAG}': paused (missing from the spec)"),
        ("+", f"tags '{TAG}': tag_firing_option (missing from the container)"),
        ("~", f"tags '{TAG}': parameter.eventSettingsTable[0] (at [1] in the container, [0] in the spec)"),
        ("~", f"tags '{TAG}': parameter.eventName ('signup' in the container, 'sign_up' in the spec)"),
        (
            "~",
            "tags 'GA4 - track_event': firing_triggers "
            "('CE - sign_up' in the container, 'CE - track_event' in the spec)",
        ),
    ], result
    assert [d["op"] for d in json.loads(as_json.out)["differences"]] == [
        "only_in_spec",
        "only_in_container",
        "changed",
        "changed",
        "only_in_container",
        "only_in_spec",
        "moved",
        "changed",
        "changed",
    ]


ROW = "        - parameter: plan_name\n          parameterValue: '{{DLV - event_attributes.plan_name}}'\n"
SIGNUP_SOURCE = (
    "        - parameter: signup_source\n"
    "          parameterValue: '{{DLV - event_attributes.signup_source}}'\n"
)
SIGNUP_SOURCE_BLOCK = [
    "    + parameter: signup_source",
    "    + parameterValue: '{{DLV - event_attributes.signup_source}}'",
]


def _with_unmodelled(spec: Path, field: str) -> None:
    """GA4 - sign_up in the spec with `field` written under unmodelled, before its parameters."""

    def edit(text: str) -> str:
        at = text.index("    parameter:\n", text.index(f"  - name: {TAG}\n"))
        return f"{text[:at]}    unmodelled:\n{field}{text[at:]}"

    edit_spec(spec, TAGS, edit)


def test_a_row_or_nested_object_one_side_has_and_a_structured_change_show_as_the_spec_writes_them(
    spec, tmp_path
):
    """Below its line, a block of YAML in the spec's spelling: the container's lines marked -, the spec's +,
    and for a value both have, the container's before the spec's."""

    def edited_in_gtm(version):
        tag = _tag(version, TAG)
        (table,) = [p for p in tag["parameter"] if p["key"] == "eventSettingsTable"]
        cells = [("parameter", "note"), ("parameterValue", "{{Page URL}}")]
        table["list"].append(
            {"type": "MAP", "map": [{"type": "TEMPLATE", "key": k, "value": v} for k, v in cells]}
        )
        tag["vendorList"] = ["a", "b"]  # hypothetical: a field newer than the schema
        teardown = {"tagName": "GA4 - page_view", "stopTeardownOnFailure": True}
        _tag(version, "HTML - first-party POST")["teardownTag"].append(teardown)

    replace_in_spec(
        spec, TAGS, "      eventName: file_download\n", f"{SIGNUP_SOURCE}      eventName: file_download\n"
    )
    _with_unmodelled(spec, "      vendorList:\n        - a\n")

    result = _check(spec, _live(tmp_path, edited_in_gtm))

    added, vendor = line_of(spec, TAGS, "- parameter: signup_source"), line_of(spec, TAGS, "vendorList:")
    lines = (spec / TAGS).read_bytes().decode("utf-8").splitlines()
    table = lines.index("      eventSettingsTable:", lines.index(f"  - name: {TAG}")) + 1  # 1-based
    teardown = line_of(spec, "folders/first-party/tags.yml", "teardown_tag:")
    assert (result.code, result.err) == (1, ""), result
    assert result.out.splitlines() == [
        "+ tags 'GA4 - file_download': parameter.eventSettingsTable[4] (only in the spec)  "
        f"spec/{TAGS}:{added}",
        *SIGNUP_SOURCE_BLOCK,
        f"- tags '{TAG}': parameter.eventSettingsTable[2] (only in the container)  spec/{TAGS}:{table}",
        "    - parameter: note",
        "    - parameterValue: '{{Page URL}}'",
        f"~ tags '{TAG}': unmodelled.vendorList (changed)  spec/{TAGS}:{vendor}",
        "    - - a",
        "    - - b",
        "    + - a",
        "- tags 'HTML - first-party POST': teardown_tag[1] (only in the container)  "
        f"spec/folders/first-party/tags.yml:{teardown}",
        "    - tag_name: GA4 - page_view",
        "    - stop_teardown_on_failure: true",
        f"4 differences between {tmp_path / 'live.json'} and {spec}",
    ]


def test_a_renamed_row_cell_pairs_with_its_row_so_it_reads_as_one_change(spec, tmp_path):
    """A table that gained a row and had a cell renamed: the rows are paired by what they share (at least
    half), so the rename is one ~ at the cell and the new row one +. Only the explanation changes."""
    renamed = ROW.replace("parameter: plan_name", "parameter: plan")
    replace_in_spec(spec, TAGS, ROW, renamed + SIGNUP_SOURCE)

    result, as_json = _check(spec, ANALYTICS), _check(spec, ANALYTICS, "--format", "json")

    rename, added = (
        line_of(spec, TAGS, "- parameter: plan"),
        line_of(spec, TAGS, "- parameter: signup_source"),
    )
    assert (result.code, result.err) == (1, ""), result
    assert result.out.splitlines() == [
        f"~ tags '{TAG}': parameter.eventSettingsTable[1].parameter ('plan_name' in the container, "
        f"'plan' in the spec)  spec/{TAGS}:{rename}",
        f"+ tags '{TAG}': parameter.eventSettingsTable[2] (only in the spec)  spec/{TAGS}:{added}",
        *SIGNUP_SOURCE_BLOCK,
        f"2 differences between {ANALYTICS} and {spec}",
    ]
    payload = json.loads(as_json.out)
    assert (as_json.code, payload["equivalent"]) == (1, False)
    assert [(d["op"], d["path"], d["message"]) for d in payload["differences"]] == [  # values on the line
        (
            "changed",
            "parameter.eventSettingsTable[1].parameter",
            "parameter.eventSettingsTable[1].parameter ('plan_name' in the container, 'plan' in the spec)",
        ),
        (
            "only_in_spec",
            "parameter.eventSettingsTable[2]",
            'parameter.eventSettingsTable[2] ({"parameter": "signup_source", '
            '"parameterValue": "{{DLV - event_a... (92 chars) only in the spec)',
        ),
    ]


def test_a_row_alike_two_new_ones_pairs_with_the_nearer(spec, tmp_path):
    """plan_name shares its value with both plan and tier, so the nearer, plan, is read as it renamed."""
    tier = "        - parameter: tier\n          parameterValue: '{{DLV - event_attributes.plan_name}}'\n"
    replace_in_spec(spec, TAGS, ROW, ROW.replace("parameter: plan_name", "parameter: plan") + tier)

    result = _check(spec, ANALYTICS)

    assert result.code == 1, result
    assert [(d.marker, d.detail.split("  ", 1)[0]) for d in drift_reported(result)] == [
        ("~", "parameter.eventSettingsTable[1].parameter ('plan_name' in the container, 'plan' in the spec)"),
        ("+", "parameter.eventSettingsTable[2] (only in the spec)"),
    ], result


def test_rows_pair_only_in_order_so_a_reordered_rename_still_shows_the_row_gone(spec, tmp_path):
    """method and plan_name renamed, and method moved to the end: pairing method too would cross plan_name's
    pair and hide the move, so it stays one row gone and another new."""
    method = "        - parameter: method\n          parameterValue: '{{DLV - event_attributes.method}}'\n"
    plan = ROW.replace("parameter: plan_name", "parameter: plan")
    replace_in_spec(
        spec, TAGS, method + ROW, plan + SIGNUP_SOURCE + method.replace(": method", ": sign_up_method")
    )

    result = _check(spec, ANALYTICS)

    assert result.code == 1, result
    assert [(d.marker, d.detail.split("  ", 1)[0]) for d in drift_reported(result)] == [
        ("-", "parameter.eventSettingsTable[0] (only in the container)"),
        ("~", "parameter.eventSettingsTable[0].parameter ('plan_name' in the container, 'plan' in the spec)"),
        ("+", "parameter.eventSettingsTable[1] (only in the spec)"),
        ("+", "parameter.eventSettingsTable[2] (only in the spec)"),
    ], result


FILE_DOWNLOAD_ROWS = (
    "      eventSettingsTable:\n"
    "        - parameter: content_id\n"
    "          parameterValue: '{{DLV - event_attributes.item_id}}'\n"
    "        - parameter: download_format\n"
)
LICENSE_ROW = "          parameterValue: '{{DLV - event_attributes.license_type}}'\n"


def test_a_row_moved_and_renamed_is_one_gone_and_one_new_so_no_move_hides(spec, tmp_path):
    """Rows pair only within the run of rows that changed, in order: content_id moved from first to last
    and renamed item_id is gone from [0] and new at [3], since a cell change at [3] would hide the move."""
    replace_in_spec(
        spec, TAGS, FILE_DOWNLOAD_ROWS, "      eventSettingsTable:\n        - parameter: download_format\n"
    )
    item_id = "        - parameter: item_id\n          parameterValue: '{{DLV - event_attributes.item_id}}'\n"
    replace_in_spec(spec, TAGS, LICENSE_ROW, LICENSE_ROW + item_id)

    result, as_json = _check(spec, ANALYTICS), _check(spec, ANALYTICS, "--format", "json")

    lines = (spec / TAGS).read_bytes().decode("utf-8").splitlines()
    table = lines.index("      eventSettingsTable:") + 1  # 1-based: GA4 - file_download's comes first
    added = line_of(spec, TAGS, "- parameter: item_id")
    assert result.code == 1, result
    assert result.out.splitlines() == [
        "- tags 'GA4 - file_download': parameter.eventSettingsTable[0] (only in the container)  "
        f"spec/{TAGS}:{table}",
        "    - parameter: content_id",
        "    - parameterValue: '{{DLV - event_attributes.item_id}}'",
        "+ tags 'GA4 - file_download': parameter.eventSettingsTable[3] (only in the spec)  "
        f"spec/{TAGS}:{added}",
        "    + parameter: item_id",
        "    + parameterValue: '{{DLV - event_attributes.item_id}}'",
        f"2 differences between {ANALYTICS} and {spec}",
    ]
    assert [(d["op"], d["path"]) for d in json.loads(as_json.out)["differences"]] == [
        ("only_in_container", "parameter.eventSettingsTable[0]"),
        ("only_in_spec", "parameter.eventSettingsTable[3]"),
    ]


def test_a_tables_differences_read_in_row_order(spec, tmp_path):
    """A row the spec adds before one it changes: the new row first, then the change paired after it."""
    consent = "folders/consent/tags.yml"
    region = "        - region: ''\n          ad_storage: denied\n"
    us = "        - region: US\n" + "".join(
        f"          {kind}: granted\n"
        for kind in ("ad_storage", "analytics_storage", "ad_user_data", "ad_personalization")
    )
    replace_in_spec(spec, consent, region, us + region.replace("denied", "granted"))

    result = _check(spec, ANALYTICS)

    assert result.code == 1, result
    assert [(d.marker, d.detail.split(" (", 1)[0]) for d in drift_reported(result)] == [
        ("+", "parameter.defaultConsent[1]"),
        ("~", "parameter.defaultConsent[2].ad_storage"),
    ], result


def test_rows_sharing_less_than_half_stay_one_gone_and_others_new(spec, tmp_path):
    tier = "        - parameter: tier\n          parameterValue: '{{DLV - event_attributes.tier}}'\n"
    replace_in_spec(spec, TAGS, ROW, tier + SIGNUP_SOURCE)

    result = _check(spec, ANALYTICS)

    assert result.code == 1, result
    assert [(d.marker, d.detail.split(" (", 1)[0]) for d in drift_reported(result)] == [
        ("-", "parameter.eventSettingsTable[1]"),
        ("+", "parameter.eventSettingsTable[1]"),
        ("+", "parameter.eventSettingsTable[2]"),
    ], result


def test_github_carries_a_block_in_its_annotation_escaped_as_workflow_commands_require(spec, tmp_path):
    replace_in_spec(spec, TAGS, ROW, ROW + SIGNUP_SOURCE.replace("'{{DLV", "'100% {{DLV"))

    result = _check(spec, ANALYTICS, "--format", "github")

    line = line_of(spec, TAGS, "- parameter: signup_source")
    assert result.code == 1, result
    assert result.out.splitlines() == [
        f"::error file=spec/{TAGS},line={line},title=drift::tags '{TAG}': "
        "parameter.eventSettingsTable[2] (only in the spec)"
        "%0A    + parameter: signup_source"
        "%0A    + parameterValue: '100%25 {{DLV - event_attributes.signup_source}}'",
        f"1 difference between {ANALYTICS} and {spec}",
    ]


@pytest.mark.parametrize("fmt", ["text", "github"])
def test_a_tab_or_escape_in_a_block_cant_reach_the_terminal(fmt, spec, tmp_path):
    """The emitter quotes and escapes what YAML can't hold plainly, but a literal block keeps a tab, so each
    block line is escaped as any other line is."""

    def noted(version):
        (table,) = [p for p in _tag(version, TAG)["parameter"] if p["key"] == "eventSettingsTable"]
        cells = [("parameter", "note\x1b[2J"), ("parameterValue", "first\n\tsecond")]
        table["list"].append(
            {"type": "MAP", "map": [{"type": "TEMPLATE", "key": k, "value": v} for k, v in cells]}
        )

    result = _check(spec, _live(tmp_path, noted), "--format", fmt)

    assert result.code == 1, result
    assert [c for c in result.out if c != "\n" and not c.isprintable()] == [], result
    shown = ['parameter: "note\\x1b[2J"', "parameterValue: |-", "  first", "  \\tsecond"]
    block = drift_reported(result)[0].block if fmt == "text" else result.out.splitlines()[0].split("%0A")[1:]
    assert block == [f"    - {line}" for line in shown], result


def _text(key: str, value: str) -> dict[str, Any]:
    return {"type": "TEMPLATE", "key": key, "value": value}


@pytest.mark.parametrize("fmt", ["text", "github"])
@pytest.mark.parametrize(
    ("cell", "shown"),
    [
        (_text("parameterValue", "first\n\nthird"), ["parameterValue: |-", "  first", "", "  third"]),
        ({"type": "LIST", "key": "parameterValue", "list": []}, ["parameterValue: []"]),
    ],
    ids=["blank-line", "empty-list"],
)
def test_each_cell_of_a_row_one_side_has_reads_as_the_spec_writes_it(cell, shown, fmt, spec, tmp_path):
    """An empty list σ dropped is written back as one, and a literal block's blank line is its marker alone,
    with no space after it."""

    def noted(version):
        (table,) = [p for p in _tag(version, TAG)["parameter"] if p["key"] == "eventSettingsTable"]
        table["list"].append({"type": "MAP", "map": [_text("parameter", "note"), cell]})

    result = _check(spec, _live(tmp_path, noted), "--format", fmt)

    assert result.code == 1, result
    block = drift_reported(result)[0].block if fmt == "text" else result.out.splitlines()[0].split("%0A")[1:]
    assert block == [f"    - {line}" if line else "    -" for line in ["parameter: note", *shown]], result


def test_a_value_no_spec_could_write_shows_on_one_line_as_json(spec, tmp_path):
    """A float in a field the schema doesn't know is beyond the spec's YAML, so that side shows as JSON."""
    _with_unmodelled(spec, "      vendorList:\n        - a: 1\n")

    result = _check(spec, _live(tmp_path, lambda version: _tag(version, TAG).update(vendorList=[{"a": 1.5}])))

    vendor = line_of(spec, TAGS, "vendorList:")
    assert result.out.splitlines() == [
        f"~ tags '{TAG}': unmodelled.vendorList (changed)  spec/{TAGS}:{vendor}",
        '    - [{"a": 1.5}]',
        "    + - a: 1",
        f"1 difference between {tmp_path / 'live.json'} and {spec}",
    ], result


def test_a_key_gtm_repeats_is_named_and_the_rest_still_compare_by_key(spec, tmp_path):
    """A repeated key stops σ keying GTM's parameters, but they still pair with the spec's by key: the
    repeated one is named, without values, and a code body beside it keeps its window."""

    def repeated(version):
        tag = _tag(version, "HTML - first-party POST")
        tag["parameter"].append({"type": "BOOLEAN", "key": "supportDocumentWrite", "value": "false"})
        (html,) = [p for p in tag["parameter"] if p["key"] == "html"]
        html["value"] = html["value"].replace("keepalive: true", "keepalive: false")

    live = _live(tmp_path, repeated)
    result, as_json = _check(spec, live), _check(spec, live, "--format", "json")

    tags, code = "folders/first-party/tags.yml", "folders/first-party/html-first-party-post.html"
    lines = (spec / tags).read_bytes().decode("utf-8").splitlines()
    repeated = lines.index("      supportDocumentWrite: false") + 1  # 1-based: the POST tag's comes first
    html, again = drift_reported(result)
    assert result.code == 1, result
    assert (
        html.detail.startswith(f"parameter.html (from line {line_of(spec, code, 'keepalive')}, ")
        and not html.block
    )
    assert (again.marker, again.detail, again.block) == (
        "~",
        f"parameter.supportDocumentWrite (repeated in the container)  spec/{tags}:{repeated}",
        [],
    ), result
    *_, found = json.loads(as_json.out)["differences"]
    assert found == {
        "kind": "tags",
        "name": "HTML - first-party POST",
        "op": "changed",
        "path": "parameter.supportDocumentWrite",
        "segments": ["parameter", "supportDocumentWrite"],
        "message": "parameter.supportDocumentWrite (repeated in the container)",
        "location": f"spec/{tags}:{repeated}",
        "default": False,
    }


GROUP = "TG - sign up funnel"


@pytest.mark.parametrize("fmt", ["text", "github"])
def test_a_trigger_reference_in_a_block_names_its_trigger_as_the_spec_does(fmt, imported, tmp_path):
    export = load_export(ANALYTICS)
    refs = [{"type": "TRIGGER_REFERENCE", "value": "41"}]
    group = {
        "accountId": "1000",
        "containerId": "101",
        "triggerId": "50",
        "name": GROUP,
        "type": "TRIGGER_GROUP",
    }
    group["parameter"] = [{"type": "LIST", "key": "triggerIds", "list": refs}]
    export["containerVersion"]["trigger"].append(group)
    spec = imported(write_export(export, tmp_path / "grouped.json"))
    refs[:] = [
        {"type": "TRIGGER_REFERENCE", "value": ALL_PAGES},
        *refs,
        {"type": "TRIGGER_REFERENCE", "value": "39"},
    ]

    result = _check(spec, write_export(export, tmp_path / "live.json"), "--format", fmt)

    assert result.code == 1, result
    blocks = (
        [d.block for d in drift_reported(result)]
        if fmt == "text"
        else [line.split("%0A")[1:] for line in result.out.splitlines()[:-1]]
    )
    assert blocks == [
        ["    - {$type: TRIGGER_REFERENCE, $trigger: {built_in: All Pages}}"],
        ["    - {$type: TRIGGER_REFERENCE, $trigger: CE - track_event}"],
    ], result


def test_a_list_items_parameters_are_written_as_the_spec_writes_them():
    """No list item has a parameter field yet, but one written whole would spell it as a spec does."""
    consent = {
        "consentStatus": "NEEDED",
        "consentType": {"type": "LIST", "list": [{"type": "TEMPLATE", "value": "ad_storage"}]},
    }

    written = diff.written(diff._spec_keyed(TagConsentSetting, consent), TagConsentSetting, str)

    assert yamlio.lines(written) == ["consent_status: NEEDED", "consent_type:", "  - ad_storage"]
