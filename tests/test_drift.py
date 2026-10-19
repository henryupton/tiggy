"""Drift after the freeze: a hotfix made and published in the GTM UI shows on the next `import --check`.

Each case edits a synthetic export the way GTM would export it after the UI edit (new fingerprints on
what changed, a new version number), then checks it against the spec imported from the original. What
GTM changes without meaning anything (collection order, IDs, fingerprints) must stay a no-op.
"""

import ast
import copy
import re
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import (
    ALL_EXPORTS,
    CDM_EXPORTS,
    FIXTURES,
    Result,
    export_id,
    load_export,
    run_tiggy,
    write_export,
)

ANALYTICS = FIXTURES / "cdm" / "web_analytics.json"
MARKETING = FIXTURES / "cdm" / "web_marketing.json"
SERVER = FIXTURES / "cdm" / "server.json"
LEGACY = FIXTURES / "cdm" / "web_legacy.json"

OWN_IDS = {
    "tag": "tagId",
    "trigger": "triggerId",
    "variable": "variableId",
    "folder": "folderId",
    "customTemplate": "templateId",
    "client": "clientId",
    "transformation": "transformationId",
    "zone": "zoneId",
    "gtagConfig": "gtagConfigId",
}
TRIGGER_ID_LISTS = {
    "firingTriggerId",
    "blockingTriggerId",
    "enablingTriggerId",
    "disablingTriggerId",
    "customEvaluationTriggerId",
}
LATER = "1767225600000"  # a fingerprint GTM would stamp after the fixtures were exported
DIFFERENCE = re.compile(r"≠ (\w+) ('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"): (.*)")

Version = dict[str, Any]


# Exports as GTM writes them after a UI edit


def named(version: Version, collection: str, name: str) -> dict[str, Any]:
    (found,) = [item for item in version.get(collection) or [] if item.get("name") == name]
    return found


def entry(parameters: list[dict[str, Any]], key: str) -> dict[str, Any]:
    (found,) = [p for p in parameters if p.get("key") == key]
    return found


def parameter(item: dict[str, Any], key: str) -> dict[str, Any]:
    return entry(item["parameter"], key)


def next_id(version: Version, collection: str) -> str:
    return str(max(int(item[OWN_IDS[collection]]) for item in version[collection]) + 1)


def published(export: dict[str, Any], edit: Callable[[Version], None]) -> dict[str, Any]:
    """`export` after `edit` in the UI and a publish: what changed gets a fingerprint, the version a number.

    The version keeps its name and description, so a case is about the object alone; renaming the
    version has its own test.
    """
    live = copy.deepcopy(export)
    version = live["containerVersion"]
    edit(version)
    before = export["containerVersion"]
    for collection, key in OWN_IDS.items():
        old = {item[key]: item for item in before.get(collection) or []}
        for item in version.get(collection) or []:
            if item != old.get(item[key]):
                item["fingerprint"] = LATER
    number = str(int(version["containerVersionId"]) + 1)
    version["containerVersionId"] = number
    for key in ("path", "tagManagerUrl"):
        version[key] = re.sub(r"/versions/\d+", f"/versions/{number}", version[key])
    version["fingerprint"] = LATER
    live["exportTime"] = "2026-10-20 08:15:00"
    return live


def check(tmp_path: Path, spec: Path, live: dict[str, Any]) -> Result:
    return run_tiggy("import", write_export(live, tmp_path / "live.json"), spec, "--check")


def hotfix(imported, tmp_path: Path, fixture: Path, edit: Callable[[Version], None]) -> Result:
    """The spec frozen from `fixture`, checked against the container after `edit` was published."""
    return check(tmp_path, imported(fixture), published(load_export(fixture), edit))


def differences(result: Result) -> dict[tuple[str, str], list[str]]:
    """What `import --check` listed, as {(kind, name): [details]}; a line about no one object fails."""
    *listed, summary = result.out.splitlines()
    found = defaultdict(list)
    for line in listed:
        match = DIFFERENCE.fullmatch(line)
        assert match, f"not a difference in one object: {line!r}\n{result}"
        found[match[1], ast.literal_eval(match[2])].append(match[3])
    assert re.fullmatch(rf"{len(listed)} differences? between .+ and .+", summary), result
    return dict(found)


def assert_drift(result: Result, expected: dict[tuple[str, str], list[str]]) -> None:
    """Exit 1, exactly the `expected` objects named, and each with every one of its fragments."""
    assert result.code == 1, result
    assert result.err == "", result
    found = differences(result)
    assert sorted(found) == sorted(expected), result
    for subject, fragments in expected.items():
        details = "\n".join(found[subject])
        for fragment in fragments:
            assert fragment in details, f"{subject}: {fragment!r} isn't in {details!r}"


# Hotfixes in the GTM UI, published: each is drift, naming what changed


def test_pausing_a_tag_in_the_ui_is_drift(imported, tmp_path):
    def pause(version):
        named(version, "tag", "Meta - Pixel - Purchase")["paused"] = True

    result = hotfix(imported, tmp_path, MARKETING, pause)
    assert_drift(result, {("tags", "Meta - Pixel - Purchase"): ["paused", "missing from the spec"]})


def test_changing_a_ga4_event_parameter_value_is_drift(imported, tmp_path):
    def point_plan_name_elsewhere(version):
        table = parameter(named(version, "tag", "GA4 - sign_up"), "eventSettingsTable")
        (row,) = [r["map"] for r in table["list"] if entry(r["map"], "parameter")["value"] == "plan_name"]
        entry(row, "parameterValue")["value"] = "{{DLV - event_attributes.license_type}}"

    result = hotfix(imported, tmp_path, ANALYTICS, point_plan_name_elsewhere)
    assert_drift(
        result,
        {
            ("tags", "GA4 - sign_up"): [
                "'{{DLV - event_attributes.license_type}}' in the container",
                "'{{DLV - event_attributes.plan_name}}' in the spec",
            ]
        },
    )


def test_adding_a_tag_and_its_trigger_in_the_ui_is_drift(imported, tmp_path):
    def track_login(version):
        trigger = copy.deepcopy(named(version, "trigger", "CE - sign_up"))
        trigger.update(triggerId=next_id(version, "trigger"), name="CE - login")
        trigger["customEventFilter"][0]["parameter"][1]["value"] = "login"
        tag = copy.deepcopy(named(version, "tag", "GA4 - sign_up"))
        tag.update(tagId=next_id(version, "tag"), name="GA4 - login", firingTriggerId=[trigger["triggerId"]])
        parameter(tag, "eventName")["value"] = "login"
        version["trigger"].append(trigger)
        version["tag"].append(tag)

    result = hotfix(imported, tmp_path, ANALYTICS, track_login)
    assert_drift(
        result,
        {
            ("tags", "GA4 - login"): ["only in the container"],
            ("triggers", "CE - login"): ["only in the container"],
        },
    )


def test_deleting_the_trigger_that_keeps_anonymous_page_views_from_ga4_is_drift(imported, tmp_path):
    def delete_block(version):
        trigger = named(version, "trigger", "Event - page_view_anon")
        version["trigger"].remove(trigger)
        ga4 = named(version, "tag", "GA4")
        ga4["blockingTriggerId"].remove(trigger["triggerId"])
        if not ga4["blockingTriggerId"]:
            del ga4["blockingTriggerId"]

    result = hotfix(imported, tmp_path, SERVER, delete_block)
    assert_drift(
        result,
        {
            ("triggers", "Event - page_view_anon"): ["only in the spec"],
            ("tags", "GA4"): ["locking", "missing from the container"],
        },
    )


def test_renaming_a_trigger_in_the_ui_is_drift(imported, tmp_path):
    def rename(version):
        named(version, "trigger", "CE - sign_up")["name"] = "CE - sign_up (all methods)"

    result = hotfix(imported, tmp_path, ANALYTICS, rename)
    assert_drift(
        result,
        {
            ("triggers", "CE - sign_up"): ["only in the spec"],
            ("triggers", "CE - sign_up (all methods)"): ["only in the container"],
            ("tags", "GA4 - sign_up"): ["'CE - sign_up (all methods)' in the container"],
        },
    )


def test_dropping_a_tags_consent_requirement_is_drift(imported, tmp_path):
    def no_consent_needed(version):
        named(version, "tag", "Google Ads - sign_up")["consentSettings"] = {"consentStatus": "NOT_NEEDED"}

    result = hotfix(imported, tmp_path, MARKETING, no_consent_needed)
    assert_drift(result, {("tags", "Google Ads - sign_up"): ["'NOT_NEEDED' in the container"]})


def test_editing_a_custom_html_body_is_drift_at_its_line(imported, tmp_path):
    version = load_export(ANALYTICS)["containerVersion"]
    html = parameter(named(version, "tag", "HTML - first-party POST"), "html")["value"]
    line = 1 + html[: html.index("example.com/v1/")].count("\n")

    def new_endpoint(version):
        html = parameter(named(version, "tag", "HTML - first-party POST"), "html")
        html["value"] = html["value"].replace("example.com/v1/", "example.com/v2/")

    result = hotfix(imported, tmp_path, ANALYTICS, new_endpoint)
    assert_drift(result, {("tags", "HTML - first-party POST"): [f"from line {line},", "v2/events"]})


def test_editing_the_payload_custom_javascript_is_drift_at_its_line(imported, tmp_path):
    version = load_export(ANALYTICS)["containerVersion"]
    code = parameter(named(version, "variable", "CJS - first-party payload"), "javascript")["value"]
    line = 1 + code[: code.index("element_text:")].count("\n")

    def stop_sending_element_text(version):
        javascript = parameter(named(version, "variable", "CJS - first-party payload"), "javascript")
        lines = javascript["value"].split("\n")
        javascript["value"] = "\n".join(t for t in lines if not t.lstrip().startswith("element_text:"))

    result = hotfix(imported, tmp_path, ANALYTICS, stop_sending_element_text)
    assert_drift(result, {("variables", "CJS - first-party payload"): [f"from line {line},", "element_text"]})


def test_enabling_a_built_in_variable_in_the_ui_is_drift(imported, tmp_path):
    def enable_click_text(version):
        container = version["container"]
        version["builtInVariable"].append(
            {
                "accountId": container["accountId"],
                "containerId": container["containerId"],
                "type": "CLICK_TEXT",
                "name": "Click Text",
            }
        )

    result = hotfix(imported, tmp_path, LEGACY, enable_click_text)
    assert_drift(result, {("built_in_variables", "CLICK_TEXT"): ["only in the container"]})


def test_updating_a_gallery_template_in_the_ui_is_drift(imported, tmp_path):
    signature = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809"

    def update_from_gallery(version):
        template = named(version, "customTemplate", "Cookiebot CMP")
        template["galleryReference"].update(version="a" * 40, signature=signature)
        template["templateData"] = template["templateData"].replace("/uc.js?", "/v2/uc.js?")

    result = hotfix(imported, tmp_path, ANALYTICS, update_from_gallery)
    assert_drift(result, {("custom_templates", "Cookiebot CMP"): [signature]})


# What GTM changes without meaning anything: never drift


def _reorder(version: Version) -> None:
    for collection in (*OWN_IDS, "builtInVariable"):
        if isinstance(version.get(collection), list):
            version[collection].reverse()


def _refingerprint(node: Any) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "fingerprint":
                node[key] = str(int(value) + 1) if value.isdigit() else f"{value}1"
            else:
                _refingerprint(value)
    elif isinstance(node, list):
        for value in node:
            _refingerprint(value)


def _renumber(version: Version) -> None:
    """Every object ID changed, by rotating each collection's IDs, so each ID now means another object."""
    moves: dict[str, dict[str, str]] = {}
    for collection, key in OWN_IDS.items():
        ids = sorted((item[key] for item in version.get(collection) or []), key=lambda i: (len(i), i))
        rotated = ids[1:] + ids[:1] if len(ids) > 1 else [f"{i}000" for i in ids]
        moves[collection] = dict(zip(ids, rotated))
    container_id = version["container"]["containerId"]
    custom_type = re.compile(rf"cvt_{re.escape(container_id)}_(\w+)")

    def retype(type_: Any) -> Any:
        found = isinstance(type_, str) and custom_type.fullmatch(type_)
        return f"cvt_{container_id}_{moves['customTemplate'].get(found[1], found[1])}" if found else type_

    def walk(node: Any, collection: str | None = None) -> None:
        if isinstance(node, list):
            for value in node:
                walk(value, collection)
            return
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if collection and key == OWN_IDS[collection]:
                node[key] = moves[collection][value]
            elif key in TRIGGER_ID_LISTS and isinstance(value, list):
                node[key] = [moves["trigger"].get(t, t) for t in value]
            elif key == "parentFolderId":
                node[key] = moves["folder"].get(value, value)
            elif key == "type" and isinstance(value, str):
                node[key] = retype(value)
            elif key == "whitelistedTypeId" and isinstance(value, list):
                node[key] = [retype(t) for t in value]
            elif key == "value" and node.get("type") == "TRIGGER_REFERENCE":
                node[key] = moves["trigger"].get(value, value)
            else:
                walk(value)

    for collection in OWN_IDS:
        for item in version.get(collection) or []:
            walk(item, collection)


def _fresh_export(version: Version) -> None:
    _renumber(version)
    _reorder(version)
    _refingerprint(version)


NOISE = {
    "untouched": lambda version: None,
    "reordered": _reorder,
    "refingerprinted": _refingerprint,
    "renumbered": _renumber,
    "all_at_once": _fresh_export,
}


@pytest.mark.parametrize("noise", NOISE)
@pytest.mark.parametrize("export", ALL_EXPORTS, ids=export_id)
def test_what_gtm_changes_without_meaning_anything_is_not_drift(export, noise, imported, tmp_path):
    original = load_export(export)
    live = copy.deepcopy(original)
    NOISE[noise](live["containerVersion"])
    if noise != "untouched":
        assert live != original, f"{noise} changed nothing in {export.name}"
    if noise in ("renumbered", "all_at_once"):
        for collection, key in OWN_IDS.items():
            before, after = (
                {item.get("name"): item[key] for item in v["containerVersion"].get(collection) or []}
                for v in (original, live)
            )
            assert all(after[name] != before[name] for name in before), f"a {collection} kept its ID"
    spec = imported(export)
    path = write_export(live, tmp_path / "live.json")
    result = run_tiggy("import", path, spec, "--check")
    assert result.code == 0, result
    assert (result.out, result.err) == (f"{spec} is equivalent to {path}\n", ""), result


# Known gaps: written as the behaviour the analytics team needs


@pytest.mark.xfail(
    strict=True, reason="known gap: a version's name and description are compared as container content"
)
@pytest.mark.parametrize("label", ["renamed", "unnamed"])
def test_publishing_with_only_a_new_version_name_is_not_drift(label, imported, tmp_path):
    def relabel(version):
        if label == "renamed":
            version["name"] = "Hotfix - republish"
            version["description"] = "Published to clear a stuck preview; nothing changed."
        else:
            del version["name"], version["description"]

    spec = imported(ANALYTICS)
    result = check(tmp_path, spec, published(load_export(ANALYTICS), relabel))
    assert (result.code, result.err) == (0, ""), result
    assert result.out.startswith(f"{spec} is equivalent to "), result


def _moved_to(export: dict[str, Any], container_id: str, public_id: str) -> dict[str, Any]:
    """The same container imported into another one, as a staging copy is made."""
    old = export["containerVersion"]["container"]["containerId"]
    moved = copy.deepcopy(export)

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, dict):
            for key, value in node.items():
                if key == "containerId" and value == old:
                    node[key] = container_id
                elif key in ("path", "tagManagerUrl"):
                    node[key] = value.replace(f"containers/{old}", f"containers/{container_id}")
                elif key == "type" and isinstance(value, str) and value.startswith(f"cvt_{old}_"):
                    node[key] = f"cvt_{container_id}_{value.removeprefix(f'cvt_{old}_')}"
                elif key == "publicId":
                    node[key] = public_id
                elif key == "tagIds":
                    node[key] = [public_id]
                else:
                    walk(value)

    walk(moved)
    return moved


@pytest.mark.xfail(
    strict=True,
    reason="known gap: the spec doesn't record the container's public ID, so another container is "
    "compared object by object (and an identical copy passes)",
)
@pytest.mark.parametrize("case", ["the_marketing_container", "a_staging_copy"])
def test_checking_the_wrong_container_says_so(case, imported, tmp_path):
    spec = imported(ANALYTICS)
    if case == "the_marketing_container":
        live, public_id = load_export(MARKETING), "GTM-TEST002"
    else:
        live, public_id = _moved_to(load_export(ANALYTICS), "105", "GTM-TEST005"), "GTM-TEST005"
    result = check(tmp_path, spec, live)
    assert result.code == 2, result
    assert "GTM-TEST001" in result.err and public_id in result.err, result
    assert "≠" not in result.out, result


@pytest.mark.xfail(strict=True, reason="known gap: only the first difference in each object is listed")
def test_every_difference_in_a_tag_is_listed(imported, tmp_path):
    def pause_and_rename_the_event(version):
        tag = named(version, "tag", "GA4 - sign_up")
        tag["paused"] = True
        parameter(tag, "eventName")["value"] = "signup"

    result = hotfix(imported, tmp_path, ANALYTICS, pause_and_rename_the_event)
    assert result.code == 1, result
    details = differences(result)[("tags", "GA4 - sign_up")]
    assert len(details) == 2, result
    assert any("paused" in d for d in details) and any("'signup' in the container" in d for d in details)


ENUM_FIELDS = {
    "type",
    "usageContext",
    "tagFiringOption",
    "consentStatus",
    "caseConversionType",
    "convertToNumber",
}


def _api_spelling(node: Any) -> None:
    """Enum values as the Tag Manager API returns them: lowerCamel (`customEvent`), not `CUSTOM_EVENT`."""

    def camel(value: str) -> str:
        first, *rest = value.lower().split("_")
        return first + "".join(part.capitalize() for part in rest)

    def is_enum(value: Any) -> bool:
        return isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*", value) is not None

    if isinstance(node, list):
        for value in node:
            _api_spelling(value)
    elif isinstance(node, dict):
        for key, value in node.items():
            if key in ENUM_FIELDS and is_enum(value):
                node[key] = camel(value)
            elif key in ENUM_FIELDS and isinstance(value, list) and value and all(map(is_enum, value)):
                node[key] = [camel(v) for v in value]
            else:
                _api_spelling(value)


@pytest.mark.xfail(
    strict=True,
    reason="known gap: the Tag Manager API's lowerCamel enum values are refused as a misreading (exit 2)",
)
@pytest.mark.parametrize("export", CDM_EXPORTS, ids=export_id)
def test_a_container_read_through_the_tag_manager_api_checks_clean(export, imported, tmp_path):
    spec = imported(export)
    live = load_export(export)
    _api_spelling(live["containerVersion"])
    assert live["containerVersion"]["container"]["usageContext"][0] in ("web", "server")
    result = check(tmp_path, spec, live)
    assert (result.code, result.err) == (0, ""), result
    assert result.out.startswith(f"{spec} is equivalent to "), result


@pytest.mark.xfail(
    strict=True,
    reason="known gap: an enum value newer than Tiggy's vendored API schema makes the export unreadable "
    "(exit 2)",
)
def test_a_trigger_of_a_type_newer_than_tiggy_is_drift_with_a_warning(imported, tmp_path):
    def add_new_kind_of_trigger(version):
        version["trigger"].append(
            {
                "accountId": "1000",
                "containerId": "101",
                "triggerId": next_id(version, "trigger"),
                "name": "Consent - updated",
                "type": "CONSENT_UPDATE",  # hypothetical: a type GTM ships after the vendored schema
                "parentFolderId": named(version, "folder", "Triggers")["folderId"],
            }
        )

    result = hotfix(imported, tmp_path, ANALYTICS, add_new_kind_of_trigger)
    assert result.code == 1, result
    assert "CONSENT_UPDATE" in result.err, result
    assert differences(result) == {("triggers", "Consent - updated"): ["only in the container"]}


# Bugs found while writing these


SPEC_SPELLINGS = {  # hotfix, the object, how the spec spells the field, how the export does
    "event_name": (
        lambda v: parameter(named(v, "tag", "GA4 - sign_up"), "eventName").update(value="signup"),
        ("tags", "GA4 - sign_up"),
        "parameter.eventName",
        "parameter[",
    ),
    "firing_trigger": (
        lambda v: named(v, "trigger", "CE - sign_up").update(name="CE - sign_up (all methods)"),
        ("tags", "GA4 - sign_up"),
        "firing_triggers",
        "firingTriggerId",
    ),
    "consent": (
        lambda v: named(v, "tag", "GA4 - sign_up").update(consentSettings={"consentStatus": "NEEDED"}),
        ("tags", "GA4 - sign_up"),
        "consent_settings.consent_status",
        "consentSettings",
    ),
}


@pytest.mark.xfail(
    strict=True,
    reason="bug: --check names a field by the export's JSON path and canon's internals "
    "(parameter[3].value, firingTriggerId[0][1]), which the spec's YAML never shows",
)
@pytest.mark.parametrize("case", SPEC_SPELLINGS)
def test_a_difference_names_the_field_as_the_spec_spells_it(case, imported, tmp_path):
    edit, subject, spec_spelling, export_spelling = SPEC_SPELLINGS[case]
    result = hotfix(imported, tmp_path, ANALYTICS, edit)
    assert result.code == 1, result
    (detail,) = differences(result)[subject]
    assert spec_spelling in detail and export_spelling not in detail, detail


@pytest.mark.xfail(strict=True, reason="bug: one difference is summed up as '1 differences'")
def test_one_difference_is_counted_in_the_singular(imported, tmp_path):
    def pause(version):
        named(version, "tag", "Meta - Pixel - Purchase")["paused"] = True

    result = hotfix(imported, tmp_path, MARKETING, pause)
    assert result.code == 1, result
    assert result.out.splitlines()[-1].startswith("1 difference between "), result


@pytest.mark.xfail(
    strict=True,
    reason="bug: container features are read-only in the Tag Manager API, yet a flag Google flips fails "
    "the drift gate with a difference the repo can't apply",
)
def test_a_feature_google_turns_on_for_the_container_is_not_drift(imported, tmp_path):
    spec = imported(ANALYTICS)
    live = load_export(ANALYTICS)
    features = live["containerVersion"]["container"]["features"]
    assert features["supportGtagConfigs"] is False
    features["supportGtagConfigs"] = True
    result = check(tmp_path, spec, live)
    assert (result.code, result.err) == (0, ""), result
