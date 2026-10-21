"""Change requests through the repo.

The analytics team's API tool maps their event dictionary into GTM: a cloned
`DLV - event_attributes.<property>` Data Layer variable per property, the property's key in the first-party
payload's `// Event` section, and sorted rows in GA4 event tags' eventSettingsTable and Amplitude tags'
eventProperties. Here each request is
made twice: as text edits to an imported spec, the way the adopting repo will make it, and directly to the
export JSON, the way the tool does in GTM today. `tiggy import <changed export> <spec> --check` exiting 0
proves the two land on the same container.
"""

import copy
import json
import re
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.conftest import (
    FIXTURES,
    drift_reported,
    edit_spec,
    fragment_of,
    line_of,
    load_export,
    problems_reported,
    replace_in_spec,
    run_tiggy,
    store_for,
    write_export,
)

ANALYTICS = FIXTURES / "cdm" / "web_analytics.json"
MARKETING = FIXTURES / "cdm" / "web_marketing.json"
SERVER = FIXTURES / "cdm" / "server.json"

PAYLOAD = "CJS - first-party payload"
AMPLITUDE = "Amplitude - track_event"
CLONED_DLV = "item_id"  # the tool clones a plain Data Layer variable: version 2, no default
ID_KEYS = {"variable": "variableId", "trigger": "triggerId", "tag": "tagId"}


def dlv(prop: str) -> str:
    return f"DLV - event_attributes.{prop}"


def braced(name: str) -> str:
    return "{{" + name + "}}"


# The payload's `// Event` section, the same text edit for the sidecar and the export's JSON string.


def with_payload_key(js: str, prop: str) -> str:
    """The payload code with `prop` in its `// Event` section, alphabetically, aligned with its neighbours."""
    lines = js.splitlines(keepends=True)
    head = lines.index("    // Event\n")
    stop = next(i for i in range(head + 1, len(lines)) if lines[i].lstrip().startswith("//"))
    section = lines[head + 1 : stop]
    column = section[0].index("{{")
    line = f"{f'    {prop}:'.ljust(column - 1)} {braced(dlv(prop))},\n"
    at = next((head + 1 + n for n, s in enumerate(section) if s.split(":")[0].strip() > prop), stop)
    lines[at:at] = [line]
    return "".join(lines)


# The API tool's change, made to an export as GTM would hold it afterwards.


def _named(items: list[dict[str, Any]], name: str) -> dict[str, Any]:
    (found,) = [item for item in items if item.get("name") == name]
    return found


def _parameter(holder: dict[str, Any], key: str, field: str = "parameter") -> dict[str, Any]:
    (found,) = [p for p in holder[field] if p.get("key") == key]
    return found


def _clone(version: dict[str, Any], alias: str, source: str, name: str) -> dict[str, Any]:
    """A copy of `source` named `name`, with the next free ID, as GTM's Copy does."""
    items, id_key = version[alias], ID_KEYS[alias]
    item = copy.deepcopy(_named(items, source))
    item.update({"name": name, id_key: str(max(int(i[id_key]) for i in items) + 1)})
    item["fingerprint"] = "1767225600000"
    items.append(item)
    return item


def _add_row(tag: dict[str, Any], table: str, row: dict[str, str]) -> None:
    """A MAP row in the tag's LIST parameter `table`, sorted by the row's first field."""
    rows = _parameter(tag, table)["list"]
    sort_key, sort_value = next(iter(row.items()))
    entry = {"type": "MAP", "map": [{"type": "TEMPLATE", "key": k, "value": v} for k, v in row.items()]}
    at = next(
        (i for i, r in enumerate(rows) if _parameter(r, sort_key, "map")["value"] > sort_value), len(rows)
    )
    rows.insert(at, entry)


def api_add_property(
    export: dict[str, Any],
    prop: str,
    ga4_tags: tuple[str, ...],
    amplitude_tags: tuple[str, ...] = (AMPLITUDE,),
) -> dict[str, Any]:
    version = export["containerVersion"]
    variable = _clone(version, "variable", dlv(CLONED_DLV), dlv(prop))
    _parameter(variable, "name")["value"] = f"event_attributes.{prop}"
    payload = _parameter(_named(version["variable"], PAYLOAD), "javascript")
    payload["value"] = with_payload_key(payload["value"], prop)
    for tag in ga4_tags:
        _add_row(_named(version["tag"], tag), "eventSettingsTable", ga4_row(prop))
    for tag in amplitude_tags:
        _add_row(_named(version["tag"], tag), "eventProperties", amplitude_row(prop))
    return export


def ga4_row(prop: str) -> dict[str, str]:
    return {"parameter": prop, "parameterValue": braced(dlv(prop))}


def amplitude_row(prop: str) -> dict[str, str]:
    return {"name": prop, "value": braced(dlv(prop))}


def _replace_strings(node: Any, old: str, new: str) -> Any:
    if isinstance(node, dict):
        return {k: _replace_strings(v, old, new) for k, v in node.items()}
    if isinstance(node, list):
        return [_replace_strings(v, old, new) for v in node]
    return node.replace(old, new) if isinstance(node, str) else node


def changed(export: Path, change: Callable[[dict[str, Any]], Any], path: Path) -> Path:
    """`export` with `change` made to its containerVersion, written to `path` the way GTM writes one."""
    data = load_export(export)
    change(data["containerVersion"])
    return write_export(data, path)


# The same change, made as text to the fragments `tiggy import` wrote.


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _span(lines: list[str], name: str) -> tuple[int, int]:
    """The lines of the top-level entry `  - name: <name>`, up to the next entry or top-level key."""
    (start,) = [i for i, line in enumerate(lines) if line.rstrip("\n") == f"  - name: {name}"]
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].strip() and _indent(lines[i]) < 4), len(lines)
    )
    return start, end


def _scalar(text: str) -> str:
    return text.strip().strip("'")


def _quoted(value: str) -> str:
    return f"'{value}'" if value.startswith("{") else value


def insert_row(text: str, item: str, table: str, row: dict[str, str]) -> str:
    """Adds `row` to the list `table` under `item`'s parameters, sorted by the row's first field."""
    lines = text.splitlines(keepends=True)
    start, end = _span(lines, item)
    (head,) = [i for i in range(start, end) if lines[i].strip() == f"{table}:"]
    stop = next((i for i in range(head + 1, end) if _indent(lines[i]) <= _indent(lines[head])), end)
    dash = " " * (_indent(lines[head]) + 2) + "- "
    (sort_key, sort_value), *rest = row.items()
    rows = [i for i in range(head + 1, stop) if lines[i].startswith(dash)]
    assert all(lines[i].startswith(f"{dash}{sort_key}: ") for i in rows), (
        f"{item}: {table} isn't keyed by {sort_key}"
    )
    at = next((i for i in rows if _scalar(lines[i].split(": ", 1)[1]) > sort_value), stop)
    pad = " " * len(dash)
    new = [f"{dash}{sort_key}: {_quoted(sort_value)}\n", *(f"{pad}{k}: {_quoted(v)}\n" for k, v in rest)]
    lines[at:at] = new
    return "".join(lines)


def clone_entry(text: str, source: str, old: str, new: str) -> str:
    """Copies the entry named `source`, with `old` replaced by `new`, into name order among its siblings."""
    lines = text.splitlines(keepends=True)
    start, end = _span(lines, source)
    block = "".join(lines[start:end]).replace(old, new)
    name = block.splitlines()[0].removeprefix("  - name: ")
    entries = [
        (i, line.split(": ", 1)[1].strip()) for i, line in enumerate(lines) if line.startswith("  - name: ")
    ]
    at = next(
        (i for i, other in entries if other.casefold() > name.casefold()), _span(lines, entries[-1][1])[1]
    )
    lines[at:at] = [block]
    return "".join(lines)


def edit_entry(text: str, name: str, old: str, new: str) -> str:
    """Replaces `old`, which has to appear exactly once in the entry named `name`, with `new`."""
    lines = text.splitlines(keepends=True)
    start, end = _span(lines, name)
    block = "".join(lines[start:end])
    assert block.count(old) == 1, f"{name}: expected {old!r} once, found {block.count(old)} times"
    return "".join(lines[:start]) + block.replace(old, new) + "".join(lines[end:])


def cut_entry(spec: Path, kind: str, name: str) -> tuple[Path, str]:
    """Removes the entry named `name` from its fragment; returns the fragment and the entry's text.

    A fragment left with no entries loses the `kind` key too, leaving `tiggy: 1`."""
    fragment = fragment_of(spec, kind, name)
    lines = fragment.read_bytes().decode("utf-8").splitlines(keepends=True)
    start, end = _span(lines, name)
    block = "".join(lines[start:end])
    del lines[start:end]
    if not any(line.startswith("  - ") for line in lines):
        lines = [line for line in lines if line != f"{kind}:\n"]
    fragment.write_bytes("".join(lines).encode("utf-8"))
    return fragment, block


def code_files(block: str) -> list[str]:
    return re.findall(r"\{\$file: ([^}]+)\}", block)


def spec_add_property(spec: Path, prop: str, ga4_tags: tuple[str, ...], amplitude_tags=(AMPLITUDE,)) -> None:
    edit_spec(
        spec,
        fragment_of(spec, "variables", dlv(CLONED_DLV)),
        lambda text: clone_entry(
            text, dlv(CLONED_DLV), f"event_attributes.{CLONED_DLV}", f"event_attributes.{prop}"
        ),
    )
    spec_add_payload_key(spec, prop)
    for tag in ga4_tags:
        edit_spec(
            spec,
            fragment_of(spec, "tags", tag),
            lambda t, tag=tag: insert_row(t, tag, "eventSettingsTable", ga4_row(prop)),
        )
    for tag in amplitude_tags:
        edit_spec(
            spec,
            fragment_of(spec, "tags", tag),
            lambda t, tag=tag: insert_row(t, tag, "eventProperties", amplitude_row(prop)),
        )


def payload_file(spec: Path) -> str:
    tree = yaml.safe_load(fragment_of(spec, "variables", PAYLOAD).read_bytes().decode("utf-8"))
    return _named(tree["variables"], PAYLOAD)["parameter"]["javascript"]["$file"]


def spec_add_payload_key(spec: Path, prop: str) -> None:
    edit_spec(spec, payload_file(spec), lambda js: with_payload_key(js, prop))


def rename_in_spec(spec: Path, old: str, new: str, skip: tuple[str, ...] = ()) -> list[str]:
    """Renames a variable in every file of the spec, references in code files included, but `skip`."""
    touched = []
    for path in sorted(p for p in spec.rglob("*") if p.is_file()):
        relpath = path.relative_to(spec).as_posix()
        if relpath.startswith(".") or relpath in skip:
            continue
        text = path.read_bytes().decode("utf-8")
        after = text.replace(braced(old), braced(new)).replace(f"  - name: {old}\n", f"  - name: {new}\n")
        if after != text:
            path.write_bytes(after.encode("utf-8"))
            touched.append(relpath)
    return touched


# Running Tiggy on the result.


def lands(spec: Path, expected: Path, strict: bool = True) -> None:
    """The spec validates, exports, and is equivalent to `expected`, the same change made to the export."""
    validated = run_tiggy("validate", spec, *(["--strict"] if strict else []))
    assert validated.code == 0, str(validated)
    exported = run_tiggy(
        "export", spec, "-o", spec.parent / "exported.json", env=store_for(expected, spec.parent / "store")
    )
    assert exported.code == 0, str(exported)
    checked = run_tiggy("import", expected, spec, "--check")
    assert (checked.code, checked.out) == (0, f"{spec} is equivalent to {expected}\n"), str(checked)


def drift(spec: Path, export: Path) -> dict[tuple[str, str], list[str]]:
    """What `import --check` says differs between the export and the spec, by (kind, name), unlocated, each
    with its block's lines after it."""
    result = run_tiggy("import", export, spec, "--check")
    assert result.code == 1, str(result)
    found = defaultdict(list)
    for d in drift_reported(result):
        found[d.kind, d.name].append("\n".join([re.sub(r"  \S+:\d+$", "", d.detail), *d.block]))
    assert result.out.splitlines()[-1].endswith(f" between {export} and {spec}"), str(result)
    return dict(found)


def findings(source: Path) -> tuple[int, list[dict[str, Any]], list[str]]:
    """validate's exit code, findings and file warnings, from its JSON report."""
    result = run_tiggy("validate", source, "--format", "json")
    report = json.loads(result.out)
    return result.code, report["findings"], report["files"]


def refused(spec: Path, *problems: str) -> None:
    """validate and export both refuse the spec with exactly these problems, and export writes nothing."""
    validated = run_tiggy("validate", spec)
    assert validated.code == 1, str(validated)
    *reported, summary = problems_reported(validated)
    assert reported == [f"✗ {p}" for p in problems]
    assert re.fullmatch(rf"{len(problems)} errors?, 0 warnings", summary), summary
    target = spec.parent / "exported.json"
    exported = run_tiggy("export", spec, "-o", target)
    assert exported.code == 1, str(exported)
    assert [f"✗ {p}" for p in problems] == exported.err.splitlines()[: len(problems)]
    assert not target.exists()


# 1. Add a new property to an existing event.


def test_adding_a_property_to_an_existing_event_matches_the_same_change_in_gtm(imported, tmp_path: Path):
    spec = imported(ANALYTICS)
    ga4_tags = ("GA4 - file_download", "GA4 - track_event")
    spec_add_property(spec, "file_size", ga4_tags)
    expected = changed(
        ANALYTICS,
        lambda v: api_add_property({"containerVersion": v}, "file_size", ga4_tags),
        tmp_path / "expected.json",
    )

    lands(spec, expected)
    assert drift(spec, ANALYTICS).keys() == {
        ("variables", dlv("file_size")),
        ("variables", PAYLOAD),
        ("tags", "GA4 - file_download"),
        ("tags", "GA4 - track_event"),
        ("tags", AMPLITUDE),
    }


def test_the_added_property_reads_like_its_neighbours(imported):
    spec = imported(ANALYTICS)
    spec_add_property(spec, "file_size", ("GA4 - file_download",))

    payload = (spec / payload_file(spec)).read_bytes().decode("utf-8")
    assert (
        "    environment:          {{DLV - event_attributes.environment}},\n"
        "    file_size:            {{DLV - event_attributes.file_size}},\n"
        "    item_brand:           {{DLV - event_attributes.item_brand}},\n"
    ) in payload
    tree = yaml.safe_load(fragment_of(spec, "variables", dlv("file_size")).read_bytes().decode("utf-8"))
    assert _named(tree["variables"], dlv("file_size")) == {
        "name": dlv("file_size"),
        "type": "v",
        "folder": "Data Layer",
        "format_value": {},
        "parameter": {"dataLayerVersion": 2, "setDefaultValue": False, "name": "event_attributes.file_size"},
    }
    tags = yaml.safe_load(fragment_of(spec, "tags", "GA4 - file_download").read_bytes().decode("utf-8"))
    table = _named(tags["tags"], "GA4 - file_download")["parameter"]["eventSettingsTable"]
    assert [row["parameter"] for row in table] == [
        "content_id",
        "download_format",
        "file_size",
        "item_variant",
        "license_type",
    ]


def test_the_new_property_shows_in_the_payload_drift_at_its_line(imported):
    spec = imported(ANALYTICS)
    spec_add_payload_key(spec, "file_size")

    assert drift(spec, ANALYTICS) == {
        ("variables", PAYLOAD): [
            "parameter.javascript (from line 16, character 5: "
            "'.environment}},\\n    item_brand:           {{DLV - event_attr' in the container, "
            "'.environment}},\\n    file_size:            {{DLV - event_attr' in the spec)"
        ]
    }


def test_a_row_added_out_of_order_is_a_difference_from_gtm(imported, tmp_path: Path):
    """GTM keeps a table's rows in the order they were added, so appending instead of sorting shows up."""
    spec = imported(ANALYTICS)
    edit_spec(
        spec,
        "folders/ga4/tags.yml",
        lambda t: edit_entry(
            t,
            "GA4 - file_download",
            "      eventName: file_download\n",
            "        - parameter: file_size\n"
            "          parameterValue: '{{DLV - event_attributes.file_size}}'\n"
            "      eventName: file_download\n",
        ),
    )
    spec_add_payload_key(spec, "file_size")
    edit_spec(
        spec,
        fragment_of(spec, "variables", dlv(CLONED_DLV)),
        lambda t: clone_entry(
            t, dlv(CLONED_DLV), f"event_attributes.{CLONED_DLV}", "event_attributes.file_size"
        ),
    )
    expected = changed(
        ANALYTICS,
        lambda v: api_add_property({"containerVersion": v}, "file_size", ("GA4 - file_download",), ()),
        tmp_path / "expected.json",
    )

    assert drift(spec, expected) == {
        ("tags", "GA4 - file_download"): [
            "parameter.eventSettingsTable[4] (at [2] in the container, [4] in the spec)"
        ]
    }


# 2. Add a brand-new event end to end.

VIDEO_PROGRESS = """\
# video_progress from the event dictionary: its properties, its trigger and its GA4 event tag.
tiggy: 1
variables:
  - name: DLV - event_attributes.video_percent
    type: v
    folder: Data Layer
    format_value: {}
    parameter:
      dataLayerVersion: 2
      setDefaultValue: false
      name: event_attributes.video_percent
  - name: DLV - event_attributes.video_title
    type: v
    folder: Data Layer
    format_value: {}
    parameter:
      dataLayerVersion: 2
      setDefaultValue: false
      name: event_attributes.video_title
triggers:
  - name: CE - track_event - video_progress
    type: CUSTOM_EVENT
    folder: Triggers
    custom_event_filter:
      - type: EQUALS
        parameter:
          arg0: '{{_event}}'
          arg1: track_event
    filter:
      - type: EQUALS
        parameter:
          arg0: '{{DLV - event_name}}'
          arg1: video_progress
tags:
  - name: GA4 - video_progress
    type: gaawe
    folder: GA4
    firing_triggers:
      - CE - track_event - video_progress
    tag_firing_option: ONCE_PER_EVENT
    consent_settings:
      consent_status: NOT_SET
    monitoring_metadata: {$type: MAP}
    parameter:
      sendEcommerceData: false
      enhancedUserId: false
      eventSettingsTable:
        - parameter: video_percent
          parameterValue: '{{DLV - event_attributes.video_percent}}'
        - parameter: video_title
          parameterValue: "{{DLV - event_attributes.video_title}}"
      eventName: video_progress
      measurementIdOverride: '{{C - GA4 measurement ID}}'
"""
VIDEO_PROPERTIES = ("video_percent", "video_title")


def api_add_event(version: dict[str, Any], event: str, props: tuple[str, ...]) -> None:
    """A new event as the tool builds it: copies of an event's trigger and GA4 tag, then its properties."""
    trigger = _clone(version, "trigger", "CE - track_event - file_download", f"CE - track_event - {event}")
    _parameter(trigger["filter"][0], "arg1")["value"] = event
    tag = _clone(version, "tag", "GA4 - file_download", f"GA4 - {event}")
    tag["firingTriggerId"] = [trigger["triggerId"]]
    _parameter(tag, "eventName")["value"] = event
    _parameter(tag, "eventSettingsTable")["list"] = []
    for prop in props:
        api_add_property({"containerVersion": version}, prop, (f"GA4 - {event}",))


def add_video_progress(spec: Path, fragment: str = VIDEO_PROGRESS) -> None:
    (spec / "events").mkdir()
    (spec / "events" / "video_progress.yml").write_bytes(fragment.encode("utf-8"))
    for prop in VIDEO_PROPERTIES:
        spec_add_payload_key(spec, prop)
        edit_spec(
            spec,
            fragment_of(spec, "tags", AMPLITUDE),
            lambda t, p=prop: insert_row(t, AMPLITUDE, "eventProperties", amplitude_row(p)),
        )


def test_adding_a_brand_new_event_end_to_end(imported, tmp_path: Path):
    spec = imported(ANALYTICS)
    add_video_progress(spec)
    expected = changed(
        ANALYTICS, lambda v: api_add_event(v, "video_progress", VIDEO_PROPERTIES), tmp_path / "expected.json"
    )

    lands(spec, expected)
    differences = drift(spec, ANALYTICS)
    (payload,) = differences.pop(("variables", PAYLOAD))
    assert payload.startswith("parameter.javascript (from line ")
    assert differences == {
        ("variables", dlv("video_percent")): ["only in the spec"],
        ("variables", dlv("video_title")): ["only in the spec"],
        ("triggers", "CE - track_event - video_progress"): ["only in the spec"],
        ("tags", "GA4 - video_progress"): ["only in the spec"],
        ("tags", AMPLITUDE): [
            f"parameter.eventProperties[{i}] (only in the spec)\n"
            f"    + name: {prop}\n"
            f"    + value: '{amplitude_row(prop)['value']}'"
            for i, prop in enumerate(VIDEO_PROPERTIES, 24)
        ],
    }


def test_the_new_event_lands_in_its_folders_when_the_container_is_reimported(imported, tmp_path: Path):
    spec = imported(ANALYTICS)
    add_video_progress(spec)
    exported, again = tmp_path / "exported.json", tmp_path / "again"
    assert run_tiggy("export", spec, "-o", exported).code == 0
    assert run_tiggy("import", exported, again).code == 0

    assert fragment_of(again, "variables", dlv("video_title")) == again / "folders/data-layer/variables.yml"
    assert (
        fragment_of(again, "triggers", "CE - track_event - video_progress")
        == again / "folders/triggers/triggers.yml"
    )
    assert fragment_of(again, "tags", "GA4 - video_progress") == again / "folders/ga4/tags.yml"


def test_a_new_event_with_a_misspelt_trigger_is_refused_where_it_is_written(imported):
    spec = imported(ANALYTICS)
    add_video_progress(
        spec,
        VIDEO_PROGRESS.replace(
            "      - CE - track_event - video_progress\n", "      - CE - track_event - video_progres\n"
        ),
    )

    event = "events/video_progress.yml"
    line = line_of(spec, event, "- CE - track_event - video_progres")
    refused(
        spec,
        f'{event}:{line}: trigger "CE - track_event - video_progres" is not defined in this spec'
        ' (did you mean "CE - track_event - video_progress"?)',
    )


def test_a_new_event_reading_a_property_nobody_created_is_caught(imported):
    spec = imported(ANALYTICS)
    misnamed = "  - name: DLV - event_attributes.video_name\n"
    add_video_progress(
        spec, VIDEO_PROGRESS.replace("  - name: DLV - event_attributes.video_title\n", misnamed)
    )

    event = "events/video_progress.yml"
    code, found, _ = findings(spec)
    assert code == 1
    assert [(f["rule"], f["kind"], f["name"], f["location"]) for f in found] == [
        ("unknown-variable", "tag", AMPLITUDE, "folders/amplitude/tags.yml:3"),
        (
            "unknown-variable",
            "tag",
            "GA4 - video_progress",
            f"{event}:{line_of(spec, event, 'GA4 - video_progress')}",
        ),
        ("unknown-variable", "variable", PAYLOAD, "folders/first-party/variables.yml:3"),
        (
            "unused-variable",
            "variable",
            dlv("video_name"),
            f"{event}:{line_of(spec, event, misnamed.strip())}",
        ),
    ]
    assert found[1]["message"] == (
        "eventSettingsTable[1].parameterValue: {{DLV - event_attributes.video_title}} is not a variable "
        'or an enabled built-in (did you mean "DLV - event_attributes.video_name"?)'
    )


# 3. Put a marketing tag behind ad_storage consent.


def ad_storage_consent(version: dict[str, Any]) -> None:
    _named(version["tag"], "Google Ads - Conversion Linker")["consentSettings"] = {
        "consentStatus": "NEEDED",
        "consentType": {"type": "LIST", "list": [{"type": "TEMPLATE", "value": "ad_storage"}]},
    }


@pytest.mark.parametrize(
    "consent",
    [
        "      consent_status: NEEDED\n      consent_type:\n        - ad_storage\n",
        "      consent_status: NEEDED\n      consent_type: [ad_storage]  # same as every other ad tag\n",
    ],
    ids=["block-list", "flow-list"],
)
def test_putting_a_marketing_tag_behind_ad_storage_consent(imported, tmp_path: Path, consent: str):
    spec = imported(MARKETING)
    tag = "Google Ads - Conversion Linker"
    edit_spec(
        spec,
        fragment_of(spec, "tags", tag),
        lambda t: edit_entry(t, tag, "      consent_status: NOT_SET\n", consent),
    )

    lands(spec, changed(MARKETING, ad_storage_consent, tmp_path / "expected.json"))
    assert drift(spec, MARKETING) == {
        ("tags", tag): [
            "consent_settings.consent_status ('NOT_SET' in the container, 'NEEDED' in the spec)",
            "consent_settings.consent_type (missing from the container)",
        ]
    }


def test_a_misspelt_consent_status_is_refused_where_it_is_written(imported):
    spec = imported(MARKETING)
    tag = "Google Ads - Conversion Linker"
    path = edit_spec(
        spec,
        fragment_of(spec, "tags", tag),
        lambda t: edit_entry(t, tag, "consent_status: NOT_SET", "consent_status: needed"),
    )
    relpath = path.relative_to(spec).as_posix()

    line = line_of(spec, relpath, "consent_status: needed")
    refused(spec, f"{relpath}:{line}: consent_status: needed is the API's spelling; a spec writes NEEDED")


def test_an_upper_case_consent_status_typo_warns_and_fails_strict(imported):
    """Shaped like a value GTM added after Tiggy's schema, so it passes through, but never quietly."""
    spec = imported(MARKETING)
    tag = "Google Ads - Conversion Linker"
    edit_spec(
        spec,
        fragment_of(spec, "tags", tag),
        lambda t: edit_entry(t, tag, "consent_status: NOT_SET", "consent_status: NEDED"),
    )

    code, found, _ = findings(spec)
    strict = run_tiggy("validate", spec, "--strict")

    assert code == 0
    assert [(f["rule"], f["name"], f["message"]) for f in found if f["rule"] == "unmodelled-enum"] == [
        (
            "unmodelled-enum",
            tag,
            "consentSettings.consentStatus NEDED isn't in Tiggy's copy of GTM's API schema (did you mean "
            '"NEEDED"?); it is kept as-is',
        )
    ]
    assert strict.code == 1, str(strict)


# 4. Rename a variable everywhere it is referenced.

OLD_NAME, NEW_NAME = "DLV - event_name", "Data Layer - event_name"
REFERRERS = {  # the files holding OLD_NAME, and the objects in each that read it
    "folders/amplitude/tags.yml": [("tag", AMPLITUDE)],
    "folders/first-party/cjs-first-party-payload.js": [("variable", PAYLOAD)],
    "folders/ga4/tags.yml": [("tag", "GA4 - track_event"), ("tag", "GA4 - wishlist")],
    "folders/triggers/triggers.yml": [
        ("trigger", "CE - track_event - file_download"),
        ("trigger", "CE - track_event - wishlist"),
    ],
}


def test_renaming_a_variable_everywhere_it_is_read(imported, tmp_path: Path):
    spec = imported(ANALYTICS)
    touched = rename_in_spec(spec, OLD_NAME, NEW_NAME)

    def rename(version: dict[str, Any]) -> None:
        _named(version["variable"], OLD_NAME)["name"] = NEW_NAME
        version.update(_replace_strings(version, braced(OLD_NAME), braced(NEW_NAME)))

    assert touched == sorted([*REFERRERS, "folders/data-layer/variables.yml"])
    lands(spec, changed(ANALYTICS, rename, tmp_path / "expected.json"))


@pytest.mark.parametrize("missed", sorted(REFERRERS))
def test_a_rename_that_misses_a_file_is_caught(imported, missed: str):
    spec = imported(ANALYTICS)
    rename_in_spec(spec, OLD_NAME, NEW_NAME, skip=(missed,))

    code, found, _ = findings(spec)
    assert code == 1
    assert sorted((f["kind"], f["name"]) for f in found) == REFERRERS[missed]
    for finding in found:
        assert finding["rule"] == "unknown-variable"
        assert finding["message"].endswith(
            f'{braced(OLD_NAME)} is not a variable or an enabled built-in (did you mean "{NEW_NAME}"?)'
        )


def test_renaming_a_trigger_follows_every_tag_that_fires_on_it(imported, tmp_path: Path):
    spec = imported(ANALYTICS)
    replace_in_spec(
        spec, "folders/triggers/triggers.yml", "  - name: CE - sign_up\n", "  - name: CE - sign_up (all)\n"
    )
    replace_in_spec(spec, "folders/ga4/tags.yml", "      - CE - sign_up\n", "      - CE - sign_up (all)\n")

    lands(
        spec,
        changed(
            ANALYTICS,
            lambda v: _named(v["trigger"], "CE - sign_up").update(name="CE - sign_up (all)"),
            tmp_path / "e.json",
        ),
    )
    assert drift(spec, ANALYTICS) == {
        ("triggers", "CE - sign_up"): ["only in the container"],
        ("triggers", "CE - sign_up (all)"): ["only in the spec"],
        ("tags", "GA4 - sign_up"): [
            "firing_triggers ('CE - sign_up' in the container, 'CE - sign_up (all)' in the spec)"
        ],
    }


def test_a_trigger_rename_that_misses_a_tag_is_refused(imported):
    spec = imported(ANALYTICS)
    replace_in_spec(
        spec, "folders/triggers/triggers.yml", "  - name: CE - sign_up\n", "  - name: CE - sign_up (all)\n"
    )

    line = line_of(spec, "folders/ga4/tags.yml", "      - CE - sign_up")
    refused(
        spec,
        f'folders/ga4/tags.yml:{line}: trigger "CE - sign_up" is not defined in this spec'
        ' (did you mean "CE - sign_up (all)"?)',
    )


# 5. Move an object to another file or folder fragment.


def _move_tag_to_new_file(spec: Path) -> None:
    _, block = cut_entry(spec, "tags", "GA4 - page_view")
    (spec / "pages").mkdir()
    (spec / "pages" / "page_view.yml").write_bytes(f"tiggy: 1\ntags:\n{block}".encode("utf-8"))


def _move_unfiled_variable_beside_its_reader(spec: Path) -> None:
    _, block = cut_entry(spec, "variables", "URL - utm_campaign")
    edit_spec(spec, "folders/first-party/variables.yml", lambda t: t + block)


def _reverse_a_file(spec: Path) -> None:
    def reverse(text: str) -> str:
        lines = text.splitlines(keepends=True)
        starts = [i for i, line in enumerate(lines) if line.startswith("  - name: ")]
        blocks = ["".join(lines[a:b]) for a, b in zip(starts, [*starts[1:], len(lines)])]
        return "".join(lines[: starts[0]]) + "".join(reversed(blocks))

    edit_spec(spec, "folders/triggers/triggers.yml", reverse)


def _folder_beside_its_tags(spec: Path) -> None:
    folder = (spec / "folders/ga4/folder.yml").read_bytes().decode("utf-8").removeprefix("tiggy: 1\n")
    edit_spec(spec, "folders/ga4/tags.yml", lambda t: t.replace("tiggy: 1\n", f"tiggy: 1\n{folder}", 1))
    (spec / "folders/ga4/folder.yml").unlink()


def _rename_a_fragment(spec: Path) -> None:
    (spec / "folders/ga4/tags.yml").rename(spec / "folders/ga4/event-tags.yaml")


def _move_the_payload_code(spec: Path) -> None:
    old = payload_file(spec)
    (spec / "code").mkdir()
    (spec / old).rename(spec / "code" / "payload.js")
    replace_in_spec(
        spec, "folders/first-party/variables.yml", f"{{$file: {old}}}", "{$file: code/payload.js}"
    )


def _comment_a_fragment(spec: Path) -> None:
    edit_spec(
        spec,
        "folders/ga4/tags.yml",
        lambda t: t.replace("tags:\n", "# GA4 event tags; rows stay sorted.\ntags:\n\n", 1).replace(
            "      eventName: file_download\n",
            "      eventName: file_download  # a GA4 recommended event\n",
            1,
        ),
    )


def _split_out_the_container(spec: Path) -> None:
    text = (spec / "container.yml").read_bytes().decode("utf-8")
    start, end = text.index("container:\n"), text.index("built_in_variables:\n")
    (spec / "container.yml").write_bytes((text[:start] + text[end:]).encode("utf-8"))
    (spec / "settings.yml").write_bytes(f"tiggy: 1\n{text[start:end]}".encode("utf-8"))


def _split_built_ins_across_files(spec: Path) -> None:
    text = (spec / "container.yml").read_bytes().decode("utf-8")
    start, later = text.index("built_in_variables:\n"), text.index("  - type: EVENT\n")
    (spec / "container.yml").write_bytes(text[:start].encode("utf-8"))
    (spec / "built-ins").mkdir()
    (spec / "built-ins" / "page.yml").write_bytes(f"tiggy: 1\n{text[start:later]}".encode("utf-8"))
    (spec / "built-ins" / "event.yml").write_bytes(
        f"tiggy: 1\nbuilt_in_variables:\n{text[later:]}".encode("utf-8")
    )


MOVES = {
    "tag-into-a-new-file": _move_tag_to_new_file,
    "unfiled-variable-beside-its-reader": _move_unfiled_variable_beside_its_reader,
    "file-reordered": _reverse_a_file,
    "folder-beside-its-tags": _folder_beside_its_tags,
    "fragment-renamed-yaml": _rename_a_fragment,
    "payload-code-moved": _move_the_payload_code,
    "fragment-commented": _comment_a_fragment,
    "container-split-out": _split_out_the_container,
    "built-ins-split-across-files": _split_built_ins_across_files,
}


@pytest.mark.parametrize("move", MOVES.values(), ids=MOVES.keys())
def test_moving_objects_between_files_is_not_a_change(imported, move: Callable[[Path], None]):
    spec = imported(ANALYTICS)
    move(spec)

    validated = run_tiggy("validate", spec, "--strict")
    assert validated.code == 0, str(validated)
    checked = run_tiggy("import", ANALYTICS, spec, "--check")
    assert (checked.code, checked.err) == (0, ""), str(checked)


@pytest.mark.parametrize("move", MOVES.values(), ids=MOVES.keys())
def test_a_rearranged_spec_reimports_as_it_was_imported(
    imported, tmp_path: Path, move: Callable[[Path], None]
):
    """Layout carries no meaning, so exporting and importing again regenerates the original layout exactly."""
    original, spec = imported(ANALYTICS, "original"), imported(ANALYTICS)
    move(spec)
    exported, again = tmp_path / "exported.json", tmp_path / "again"
    assert run_tiggy("export", spec, "-o", exported).code == 0
    assert run_tiggy("import", exported, again).code == 0

    assert _files(again) == _files(original)


def _files(directory: Path) -> dict[str, bytes]:
    return {
        p.relative_to(directory).as_posix(): p.read_bytes()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


@pytest.mark.parametrize(
    ("edit", "folder_id", "difference", "lands_in"),
    [
        (
            ("    folder: Data Layer\n", "    folder: GA4\n"),
            "GA4",
            "folder ('Data Layer' in the container, 'GA4' in the spec)",
            "folders/ga4/variables.yml",
        ),
        (
            ("    folder: Data Layer\n", ""),
            None,
            "folder (missing from the spec)",
            "unfiled/variables.yml",
        ),
    ],
    ids=["to-another-folder", "out-of-any-folder"],
)
def test_changing_the_folder_field_changes_only_folder_membership(
    imported, tmp_path: Path, edit, folder_id, difference, lands_in
):
    spec = imported(ANALYTICS)
    name = dlv("user_logged_in")
    edit_spec(spec, fragment_of(spec, "variables", name), lambda t: edit_entry(t, name, *edit))

    def refolder(version: dict[str, Any]) -> None:
        variable = _named(version["variable"], name)
        if folder_id is None:
            del variable["parentFolderId"]
        else:
            variable["parentFolderId"] = _named(version["folder"], folder_id)["folderId"]

    lands(spec, changed(ANALYTICS, refolder, tmp_path / "expected.json"))
    assert drift(spec, ANALYTICS) == {("variables", name): [difference]}
    exported, again = tmp_path / "exported.json", tmp_path / "again"
    assert run_tiggy("export", spec, "-o", exported).code == 0
    assert run_tiggy("import", exported, again).code == 0
    assert fragment_of(again, "variables", name) == again / lands_in


def test_a_folder_field_naming_no_folder_is_refused(imported):
    spec = imported(ANALYTICS)
    name = dlv("user_logged_in")
    edit_spec(
        spec,
        "folders/data-layer/variables.yml",
        lambda t: edit_entry(t, name, "folder: Data Layer", "folder: Data layer"),
    )

    line = line_of(spec, "folders/data-layer/variables.yml", "folder: Data layer")
    refused(
        spec,
        f'folders/data-layer/variables.yml:{line}: folder "Data layer" is not declared under folders'
        ' (did you mean "Data Layer"?)',
    )


def test_a_tag_copied_instead_of_moved_names_both_places(imported):
    spec = imported(ANALYTICS)
    lines = (spec / "folders/ga4/tags.yml").read_bytes().decode("utf-8").splitlines(keepends=True)
    start, end = _span(lines, "GA4 - page_view")
    (spec / "pages").mkdir()
    (spec / "pages" / "page_view.yml").write_bytes(("tiggy: 1\ntags:\n" + "".join(lines[start:end])).encode())

    original = f"folders/ga4/tags.yml:{start + 1}"
    refused(spec, f'pages/page_view.yml:3: tag "GA4 - page_view" is already defined at {original}')


def test_code_copied_instead_of_moved_is_flagged(imported):
    spec = imported(ANALYTICS)
    old = payload_file(spec)
    (spec / "code").mkdir()
    (spec / "code" / "payload.js").write_bytes((spec / old).read_bytes())
    replace_in_spec(
        spec, "folders/first-party/variables.yml", f"{{$file: {old}}}", "{$file: code/payload.js}"
    )

    assert findings(spec) == (0, [], [f"{old}: no $file refers to this code file"])
    strict = run_tiggy("validate", spec, "--strict")
    assert strict.code == 1, str(strict)
    assert f"⚠ {old}: no $file refers to this code file" in strict.out.splitlines()
    assert run_tiggy("import", ANALYTICS, spec, "--check").code == 0


# 6. sGTM: keep page_view_anon away from GA4.

PAGE_VIEW_ANON = """\
  - name: Event - page_view_anon
    type: ALWAYS
    folder: Triggers
    filter:
      - type: EQUALS
        parameter:
          arg0: '{{Event Name}}'
          arg1: page_view_anon
    notes: 'Consentless page views: counted by the first-party collector, never sent to GA4.'
"""


def unblocked_server(path: Path) -> Path:
    """The server container before the request: GA4 fires on every GA4 client event, page_view_anon too."""

    def unblock(version: dict[str, Any]) -> None:
        version["trigger"].remove(_named(version["trigger"], "Event - page_view_anon"))
        del _named(version["tag"], "GA4")["blockingTriggerId"]

    return changed(SERVER, unblock, path)


def block_page_view_anon(spec: Path, trigger: str = "Event - page_view_anon") -> None:
    edit_spec(
        spec,
        fragment_of(spec, "triggers", "Client - GA4"),
        lambda t: t.replace("  - name: GA4 - purchase\n", PAGE_VIEW_ANON + "  - name: GA4 - purchase\n", 1),
    )
    edit_spec(
        spec,
        fragment_of(spec, "tags", "GA4"),
        lambda t: edit_entry(
            t,
            "GA4",
            "      - Client - GA4\n",
            f"      - Client - GA4\n    blocking_triggers:\n      - {trigger}\n",
        ),
    )


def test_keeping_page_view_anon_away_from_ga4(imported, tmp_path: Path):
    spec = imported(unblocked_server(tmp_path / "before.json"))
    assert drift(spec, SERVER) == {
        ("tags", "GA4"): ["blocking_triggers (missing from the spec)"],
        ("triggers", "Event - page_view_anon"): ["only in the container"],
    }

    block_page_view_anon(spec)

    lands(spec, SERVER)


def test_a_misspelt_blocking_trigger_is_refused_rather_than_blocking_nothing(imported, tmp_path: Path):
    spec = imported(unblocked_server(tmp_path / "before.json"))
    block_page_view_anon(spec, trigger="Event - page_view_anonymous")

    relpath = fragment_of(spec, "tags", "GA4").relative_to(spec).as_posix()
    line = line_of(spec, relpath, "- Event - page_view_anonymous")
    refused(
        spec,
        f'{relpath}:{line}: trigger "Event - page_view_anonymous" is not defined in this spec'
        ' (did you mean "Event - page_view_anon"?)',
    )


# 7. Delete a tag.


def delete_tag(spec: Path, name: str, keep_code: bool = False) -> list[str]:
    """Deletes a tag's entry, and its code files unless `keep_code`; returns the code files' paths."""
    _, block = cut_entry(spec, "tags", name)
    code = code_files(block)
    if not keep_code:
        for relpath in code:
            (spec / relpath).unlink()
    return code


def without_tag(name: str) -> Callable[[dict[str, Any]], None]:
    return lambda version: version["tag"].remove(_named(version["tag"], name))


def test_deleting_a_tag_removes_it_and_nothing_else(imported, tmp_path: Path):
    spec = imported(ANALYTICS)
    delete_tag(spec, "GA4 - view_search_results")

    lands(
        spec,
        changed(ANALYTICS, without_tag("GA4 - view_search_results"), tmp_path / "expected.json"),
        strict=False,
    )
    assert drift(spec, ANALYTICS) == {("tags", "GA4 - view_search_results"): ["only in the container"]}
    code, found, files = findings(spec)
    assert (code, files) == (0, [])
    assert [(f["rule"], f["kind"], f["name"]) for f in found] == [
        ("unused-trigger", "trigger", "CE - view_search_results")
    ]


def test_deleting_a_custom_html_tag_takes_its_code_file_with_it(imported, tmp_path: Path):
    spec = imported(MARKETING)
    code = delete_tag(spec, "Microsoft Ads - UET purchase")

    assert code == ["folders/microsoft-advertising/microsoft-ads-uet-purchase.html"]
    lands(spec, changed(MARKETING, without_tag("Microsoft Ads - UET purchase"), tmp_path / "expected.json"))
    assert drift(spec, MARKETING) == {("tags", "Microsoft Ads - UET purchase"): ["only in the container"]}


def test_a_deleted_tags_code_file_left_behind_is_flagged(imported):
    spec = imported(MARKETING)
    (code,) = delete_tag(spec, "Microsoft Ads - UET purchase", keep_code=True)

    assert findings(spec) == (0, [], [f"{code}: no $file refers to this code file"])
    assert run_tiggy("validate", spec, "--strict").code == 1


@pytest.mark.parametrize(
    ("export", "tag", "referrer", "message"),
    [
        (
            ANALYTICS,
            "HTML - reset event_attributes",
            ("tag", "HTML - first-party POST"),
            'teardown tag "HTML - reset event_attributes" does not exist',
        ),
        (
            SERVER,
            "GA4",
            ("transformation", "Exclude phone number from GA4"),
            'affectedTags[0].tagReference "GA4" does not exist',
        ),
    ],
    ids=["teardown-tag", "transformation-target"],
)
def test_deleting_a_tag_something_else_names_is_caught(
    imported, export: Path, tag: str, referrer, message: str
):
    spec = imported(export)
    delete_tag(spec, tag)

    code, found, files = findings(spec)
    assert (code, files) == (1, [])
    errors = [(f["rule"], f["kind"], f["name"], f["message"]) for f in found if f["severity"] == "error"]
    assert errors == [("unknown-tag", *referrer, message)]


# Output wording seen on these requests.


def test_one_change_reads_as_one_difference(imported, tmp_path: Path):
    spec = imported(MARKETING)
    delete_tag(spec, "Microsoft Ads - UET purchase")

    result = run_tiggy("import", MARKETING, spec, "--check")
    assert result.out.splitlines()[-1] == f"1 difference between {MARKETING} and {spec}"


def test_one_problem_reads_as_one_problem(imported):
    spec = imported(ANALYTICS)
    replace_in_spec(
        spec, "folders/triggers/triggers.yml", "  - name: CE - sign_up\n", "  - name: CE - sign_up (all)\n"
    )

    validated, exported = run_tiggy("validate", spec), run_tiggy("export", spec)
    assert validated.out.splitlines()[-1] == "1 error, 0 warnings"
    assert exported.err.splitlines()[-1] == "1 problem in the spec"
