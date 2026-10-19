"""Drift after the freeze: a hotfix made and published in the GTM UI shows on the next `import --check`.

Each case edits a synthetic export the way GTM would export it after the UI edit (new fingerprints on
what changed, a new version number), then checks it against the spec imported from the original. What
GTM changes without meaning anything (collection order, IDs, fingerprints) must stay a no-op, as must
what a spec writes in another order or leaves empty; anything else is drift, at the line to edit.
"""

import ast
import copy
import json
import random
import re
from collections import defaultdict
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import (
    ALL_EXPORTS,
    CDM_EXPORTS,
    FIXTURES,
    Result,
    edit_spec,
    export_id,
    line_of,
    load_export,
    replace_in_spec,
    run_tiggy,
    unread_note,
    write_export,
)
from tests.fixtures.cdm import builders as b
from tiggy.io import export as exports
from tiggy.secrets import Offline
from tiggy.spec import canon, diff, importer
from tiggy.spec.compiler import compile_spec
from tiggy.spec.loader import load_spec
from tiggy.spec.problems import same

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


def assert_drift(result: Result, expected: dict[tuple[str, str], list[str]], note: str = "") -> None:
    """Exit 1, exactly the `expected` objects named, and each with every one of its fragments."""
    assert result.code == 1, result
    assert result.err == note, result
    found = differences(result)
    assert sorted(found) == sorted(expected), result
    for subject, fragments in expected.items():
        details = "\n".join(found[subject])
        for fragment in fragments:
            assert fragment in details, f"{subject}: {fragment!r} isn't in {details!r}"


def assert_listed(result: Result, expected: dict[tuple[str, str], list[str]], note: str = "") -> None:
    """Exit 1 and exactly the `expected` lines, each object's in order, locations and all."""
    assert (result.code, result.err) == (1, note), result
    assert differences(result) == expected, result


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
        note=unread_note(SERVER),
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
    assert (result.out, result.err) == (f"{spec} is equivalent to {path}\n", unread_note(export)), result


# Known gaps: written as the behaviour the analytics team needs


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


def test_a_spec_without_a_public_id_is_compared_object_by_object(imported, tmp_path):
    """Written before specs recorded one, so there is nothing to assert, and the objects decide."""
    spec = imported(ANALYTICS)
    container_yml = spec / "container.yml"
    text = container_yml.read_bytes().decode("utf-8")
    container_yml.write_bytes(text.replace("  public_id: GTM-TEST001\n", "").encode("utf-8"))
    assert run_tiggy("import", ANALYTICS, spec, "--check").code == 0
    assert check(tmp_path, spec, load_export(MARKETING)).code == 1


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


@pytest.mark.parametrize("export", CDM_EXPORTS, ids=export_id)
def test_a_container_read_through_the_tag_manager_api_checks_clean(export, imported, tmp_path):
    spec = imported(export)
    live = load_export(export)
    _api_spelling(live["containerVersion"])
    assert live["containerVersion"]["container"]["usageContext"][0] in ("web", "server")
    result = check(tmp_path, spec, live)
    assert (result.code, result.err) == (0, unread_note(export)), result
    assert result.out.startswith(f"{spec} is equivalent to "), result


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


@pytest.mark.parametrize("case", SPEC_SPELLINGS)
def test_a_difference_names_the_field_as_the_spec_spells_it(case, imported, tmp_path):
    edit, subject, spec_spelling, export_spelling = SPEC_SPELLINGS[case]
    result = hotfix(imported, tmp_path, ANALYTICS, edit)
    assert result.code == 1, result
    (detail,) = differences(result)[subject]
    assert spec_spelling in detail and export_spelling not in detail, detail


def test_one_difference_is_counted_in_the_singular(imported, tmp_path):
    def pause(version):
        named(version, "tag", "Meta - Pixel - Purchase")["paused"] = True

    result = hotfix(imported, tmp_path, MARKETING, pause)
    assert result.code == 1, result
    assert result.out.splitlines()[-1].startswith("1 difference between "), result


def test_a_feature_google_turns_on_for_the_container_is_not_drift(imported, tmp_path):
    spec = imported(ANALYTICS)
    live = load_export(ANALYTICS)
    features = live["containerVersion"]["container"]["features"]
    assert features["supportGtagConfigs"] is False
    features["supportGtagConfigs"] = True
    result = check(tmp_path, spec, live)
    assert (result.code, result.err) == (0, ""), result


# Order and emptiness: what a spec may write its own way and still match


ALPHABETISED = (
    "      pixelId: '{{C - Meta pixel ID}}'\n"
    "      eventName: standard\n"
    "      standardEventName: CompleteRegistration\n"
    "      consent: true\n"
    "      advancedMatching: false\n",
    "      advancedMatching: false\n"
    "      consent: true\n"
    "      eventName: standard\n"
    "      pixelId: '{{C - Meta pixel ID}}'\n"
    "      standardEventName: CompleteRegistration\n",
)
LOOSE = "differs only in order or empty lists"


def test_alphabetised_parameters_and_reordered_triggers_match_with_a_note(imported, tmp_path):
    """A parameter's key is unique and any one firing trigger fires the tag, so neither order means
    anything. The note says the match leans on that; --exact counts it as a difference."""
    tag = "Meta - Pixel - CompleteRegistration"

    def also_on_purchase(version):
        named(version, "tag", tag)["firingTriggerId"].append(
            named(version, "trigger", "CE - purchase")["triggerId"]
        )

    spec = imported(MARKETING)
    tags = "folders/meta/tags.yml"
    replace_in_spec(spec, tags, "      - CE - sign_up\n", "      - CE - purchase\n      - CE - sign_up\n")
    replace_in_spec(spec, tags, *ALPHABETISED)
    live = write_export(published(load_export(MARKETING), also_on_purchase), tmp_path / "live.json")

    result = run_tiggy("import", live, spec, "--check")
    exact = run_tiggy("import", live, spec, "--check", "--exact")

    assert (result.code, result.out) == (0, f"{spec} is equivalent to {live}\n"), result
    assert (
        result.err
        == "note: 1 object matches only once order and empty lists are ignored (--exact counts it)\n"
    )
    assert_listed(exact, {("tags", tag): [f"{LOOSE}  {tags}:{line_of(spec, tags, f'- name: {tag}')}"]})


def test_empty_blocks_written_by_hand_match_what_gtm_leaves_out(imported):
    """GTM's JSON never holds an empty list, so `[]` (and a parameter written `{}`) is the same as none."""
    spec = imported(ANALYTICS)
    replace_in_spec(
        spec,
        "folders/ga4/tags.yml",
        "      - CE - sign_up\n    tag_firing_option: ONCE_PER_EVENT\n"
        "    consent_settings:\n      consent_status: NOT_SET\n    monitoring_metadata: {$type: MAP}\n",
        "      - CE - sign_up\n    blocking_triggers: []\n    tag_firing_option: ONCE_PER_EVENT\n"
        "    consent_settings:\n      consent_status: NOT_SET\n    monitoring_metadata: {}\n",
    )
    replace_in_spec(
        spec,
        "folders/triggers/triggers.yml",
        "  - name: CE - sign_up\n    type: CUSTOM_EVENT\n    folder: Triggers\n",
        "  - name: CE - sign_up\n    type: CUSTOM_EVENT\n    folder: Triggers\n    parameter: {}\n",
    )

    result = run_tiggy("import", ANALYTICS, spec, "--check")

    assert (result.code, result.out) == (0, f"{spec} is equivalent to {ANALYTICS}\n"), result
    assert (
        result.err
        == "note: 2 objects match only once order and empty lists are ignored (--exact counts them)\n"
    )


def test_two_table_rows_swapped_in_the_ui_are_drift_on_one_line(imported, tmp_path):
    """A table's rows keep the order GTM sends them in, and a swap reads as one row moved, not two changed.
    (A row added out of order is one moved row too: test_change_requests.)"""

    def swap(version):
        parameter(named(version, "tag", "GA4 - sign_up"), "eventSettingsTable")["list"].reverse()

    spec = imported(ANALYTICS)
    result = check(tmp_path, spec, published(load_export(ANALYTICS), swap))

    row = line_of(spec, "folders/ga4/tags.yml", "- parameter: method")
    assert_listed(
        result,
        {
            ("tags", "GA4 - sign_up"): [
                f"parameter.eventSettingsTable[0] (at [1] in the container, [0] in the spec)  "
                f"folders/ga4/tags.yml:{row}"
            ]
        },
    )


# What a difference says, and where


FROM_SCRATCH = """\
  - name: DLV - event_attributes.file_size
    type: v
    folder: Data Layer
    parameter:
      dataLayerVersion: 2
      name: event_attributes.file_size
"""
DEFAULT = "GTM's default, unverified"


def test_a_variable_written_from_scratch_differs_by_what_gtm_fills_in_labelled_as_such(imported, tmp_path):
    """The API tool clones a variable in GTM, carrying format_value and setDefaultValue; the analyst writes it
    by hand without either. Still drift, since nobody has seen GTM fill them in, but labelled, and only a
    warning on the pull request."""
    name = "DLV - event_attributes.file_size"

    def cloned_by_the_tool(version):
        variable = copy.deepcopy(named(version, "variable", "DLV - event_attributes.item_id"))
        variable.update(variableId=next_id(version, "variable"), name=name)
        parameter(variable, "name")["value"] = "event_attributes.file_size"
        version["variable"].append(variable)

    spec = imported(ANALYTICS)
    fragment = "folders/data-layer/variables.yml"
    edit_spec(spec, fragment, lambda text: text + FROM_SCRATCH)
    live = write_export(published(load_export(ANALYTICS), cloned_by_the_tool), tmp_path / "live.json")

    text = run_tiggy("import", live, spec, "--check")
    github = run_tiggy("import", live, spec, "--check", "--format", "github")
    as_json = run_tiggy("import", live, spec, "--check", "--format", "json")

    at = line_of(spec, fragment, f"- name: {name}")
    missing = [
        (f"format_value (missing from the spec; {DEFAULT})", at),
        (f"parameter.setDefaultValue (missing from the spec; {DEFAULT})", at + 3),
    ]
    assert_listed(text, {("variables", name): [f"{m}  {fragment}:{line}" for m, line in missing]})
    assert github.code == 1, github
    assert github.out.splitlines() == [
        *(
            f"::warning file={fragment},line={line},title=drift-default::variables '{name}': {m}"
            for m, line in missing
        ),
        f"2 differences between {live} and {spec}",
    ]
    payload = json.loads(as_json.out)
    assert (as_json.code, payload["summary"]["defaults"]) == (1, 2), as_json
    assert [(d["message"], d["default"]) for d in payload["differences"]] == [(m, True) for m, _ in missing]


@pytest.mark.parametrize("roundabout", [False, True], ids=["as_imported", "by_a_roundabout_path"])
def test_an_edit_to_a_custom_html_file_is_located_at_its_line_in_that_file(roundabout, imported):
    spec = imported(ANALYTICS)
    sidecar = "folders/first-party/html-first-party-post.html"
    if roundabout:  # the location names the file as GitHub does, so an annotation lands on its diff
        ref = "./" + sidecar.replace("/first-party/", "/first-party//")
        replace_in_spec(spec, "folders/first-party/tags.yml", f"$file: {sidecar}", f"$file: {ref}")
    replace_in_spec(spec, sidecar, "example.com/v1/", "example.com/v2/")

    result = run_tiggy("import", ANALYTICS, spec, "--check")

    line = line_of(spec, sidecar, "example.com/v2/")
    assert result.code == 1, result
    (detail,) = differences(result).pop(("tags", "HTML - first-party POST"))
    assert detail.startswith(f"parameter.html (from line {line}, character "), detail
    assert detail.endswith(f"  {sidecar}:{line}"), detail


def test_a_tag_moved_to_its_templates_other_form_names_the_form_on_both_sides(imported, tmp_path):
    """A gallery template is a tag type by its gallery ID or by the container's own; the spec writes the form
    only where it isn't the usual one, so where nothing else differs, both sides spell it out."""

    def retyped_by_the_container_copy(version):
        template = named(version, "customTemplate", "Meta Pixel")["templateId"]
        container = version["container"]["containerId"]
        named(version, "tag", "Meta - Pixel - Base")["type"] = f"cvt_{container}_{template}"

    spec = imported(MARKETING)
    result = check(tmp_path, spec, published(load_export(MARKETING), retyped_by_the_container_copy))

    line = line_of(spec, "folders/meta/tags.yml", "- name: Meta - Pixel - Base") + 1
    assert_listed(
        result,
        {
            ("tags", "Meta - Pixel - Base"): [
                'type ({"template": "Meta Pixel", "form": "container"} in the container, '
                f'{{"template": "Meta Pixel", "form": "gallery"}} in the spec)  folders/meta/tags.yml:{line}'
            ]
        },
    )


def test_a_field_newer_than_tiggy_is_compared_as_typed_so_true_is_not_1(imported, tmp_path):
    def flagged(version):
        named(version, "tag", "GA4 - sign_up")["vendorFlag"] = True  # hypothetical: newer than the schema

    live = write_export(published(load_export(ANALYTICS), flagged), tmp_path / "live.json")
    spec = imported(live)
    replace_in_spec(spec, "folders/ga4/tags.yml", "vendorFlag: true", "vendorFlag: 1")

    result = run_tiggy("import", live, spec, "--check")

    line = line_of(spec, "folders/ga4/tags.yml", "vendorFlag: 1")
    assert_listed(
        result,
        {
            ("tags", "GA4 - sign_up"): [
                f"unmodelled.vendorFlag (true in the container, 1 in the spec)  folders/ga4/tags.yml:{line}"
            ]
        },
    )


def _line_after(spec: Path, relpath: str, start: str, text: str) -> int:
    """The 1-based line of the first `text` at or after the line `start`, for text that repeats."""
    lines = (spec / relpath).read_bytes().decode("utf-8").splitlines()
    first = lines.index(start)
    return next(n for n, line in enumerate(lines[first:], first + 1) if text in line)


def test_an_edit_to_a_short_code_file_is_located_at_its_line_too(imported, tmp_path):
    before, after = "function() {\n  return 1;\n}", "function() {\n  return 2;\n}"

    def shortened(version):
        parameter(named(version, "variable", "CJS - first-party payload"), "javascript")["value"] = before

    live = write_export(published(load_export(ANALYTICS), shortened), tmp_path / "live.json")
    spec = imported(live)
    sidecar = "folders/first-party/cjs-first-party-payload.js"
    replace_in_spec(spec, sidecar, "return 1;", "return 2;")

    result = run_tiggy("import", live, spec, "--check")

    detail = f"parameter.javascript ({before!r} in the container, {after!r} in the spec)  {sidecar}:2"
    assert_listed(result, {("variables", "CJS - first-party payload"): [detail]})


def test_a_table_gtm_emptied_or_a_mapping_it_filled_names_each_row_and_key(imported, tmp_path):
    """GTM leaves an emptied table's list out, and a spec writes an empty mapping as {$type: MAP}; either
    way the difference names the rows and keys, at their lines, rather than a $list or $map missing whole."""

    def emptied_and_filled(version):
        tag = named(version, "tag", "GA4 - sign_up")
        parameter(tag, "eventSettingsTable")["list"] = []
        tag["monitoringMetadata"]["map"] = [{"type": "TEMPLATE", "key": "team", "value": "growth"}]

    spec = imported(ANALYTICS)
    result = check(tmp_path, spec, published(load_export(ANALYTICS), emptied_and_filled))

    tags = "folders/ga4/tags.yml"
    metadata = _line_after(spec, tags, "  - name: GA4 - sign_up", "monitoring_metadata:")
    rows = [line_of(spec, tags, f"- parameter: {name}") for name in ("method", "plan_name")]
    assert_listed(
        result,
        {
            ("tags", "GA4 - sign_up"): [
                f"monitoring_metadata.team (missing from the spec)  {tags}:{metadata}",
                'parameter.eventSettingsTable[0] ({"parameter": "method", "parameterValue": '
                f'"{{{{DLV - event_attributes.method}}}}"}} only in the spec)  {tags}:{rows[0]}',
                'parameter.eventSettingsTable[1] ({"parameter": "plan_name", "parameterValue": '
                f'"{{{{DLV - event_attri... (84 chars) only in the spec)  {tags}:{rows[1]}',
            ]
        },
    )


def test_a_teardown_tag_only_the_container_has_is_spelled_as_the_spec_writes_one(imported, tmp_path):
    def second_teardown(version):
        teardown = {"tagName": "GA4 - page_view", "stopTeardownOnFailure": True}
        named(version, "tag", "HTML - first-party POST")["teardownTag"].append(teardown)

    spec = imported(ANALYTICS)
    result = check(tmp_path, spec, published(load_export(ANALYTICS), second_teardown))

    tags = "folders/first-party/tags.yml"
    line = _line_after(spec, tags, "  - name: HTML - first-party POST", "teardown_tag:")
    assert_listed(
        result,
        {
            ("tags", "HTML - first-party POST"): [
                'teardown_tag[1] ({"tag_name": "GA4 - page_view", "stop_teardown_on_failure": true} '
                f"only in the container)  {tags}:{line}"
            ]
        },
    )


def test_a_boolean_gtm_holds_as_neither_true_nor_false_shows_the_spec_side_as_written(imported, tmp_path):
    name = "DLV - event_attributes.build_number"

    def yes(version):
        parameter(named(version, "variable", name), "setDefaultValue")["value"] = "yes"

    spec = imported(ANALYTICS)
    result = check(tmp_path, spec, published(load_export(ANALYTICS), yes))

    fragment = "folders/data-layer/variables.yml"
    line = _line_after(spec, fragment, f"  - name: {name}", "setDefaultValue:")
    assert_listed(
        result,
        {
            ("variables", name): [
                f"parameter.setDefaultValue ('yes' in the container, false in the spec)  {fragment}:{line}"
            ]
        },
    )


def test_a_null_value_against_none_at_all_is_the_value_missing(imported, tmp_path):
    """Both read as null in shorthand, so it is the value itself that one side lacks."""

    def noted(version):
        named(version, "tag", "GA4 - sign_up")["parameter"].append(
            {"type": "TEMPLATE", "key": "vendorNote", "value": None}
        )

    live = published(load_export(ANALYTICS), noted)
    spec = imported(write_export(live, tmp_path / "before.json"))
    del parameter(named(live["containerVersion"], "tag", "GA4 - sign_up"), "vendorNote")["value"]

    result = check(tmp_path, spec, live)

    line = line_of(spec, "folders/ga4/tags.yml", "vendorNote")
    assert_listed(
        result,
        {
            ("tags", "GA4 - sign_up"): [
                f"parameter.vendorNote.$value (missing from the container)  folders/ga4/tags.yml:{line}"
            ]
        },
    )


# Gtag configs: no name, so paired by the tag ID each configures


def _google_tags(*configs: tuple[str | None, bool]) -> dict[str, Any]:
    """A container of Google tag configs, each (its tag ID or None, whether it sends a page view)."""
    c = b.Container("Example - Google tags", public_id="GTM-TEST006", container_id="106")
    for tag_id, page_view in configs:
        tagged = [b.text("tagId", tag_id)] if tag_id else []
        c.gtag_config("googtag", [*tagged, b.boolean("sendPageView", page_view)])
    return c.export("2026-10-01 09:00:00")


def test_gtag_configs_pair_by_tag_id_whatever_order_gtm_lists_them_in(imported, tmp_path):
    spec = imported(
        write_export(_google_tags(("G-TEST1", True), ("G-TEST2", True)), tmp_path / "before.json")
    )
    replace_in_spec(spec, "gtag_configs.yml", "label: googtag 2\n", "label: GA4 - app\n")
    live = write_export(_google_tags(("G-TEST2", False), ("G-TEST1", True)), tmp_path / "live.json")

    result = run_tiggy("import", live, spec, "--check")

    lines = (spec / "gtag_configs.yml").read_bytes().decode("utf-8").splitlines()
    line = lines.index("      sendPageView: true", lines.index("  - label: GA4 - app")) + 1  # 1-based
    assert_listed(
        result,
        {
            ("gtag_configs", "GA4 - app"): [
                f"parameter.sendPageView (false in the container, true in the spec)  gtag_configs.yml:{line}"
            ]
        },
    )


def test_gtag_configs_without_a_tag_id_each_pair_by_content(imported, tmp_path):
    """One config with no tag ID means none can pair by it, so a changed config is one only in the spec,
    named and located by its label, and one only in the container, named by its type."""
    spec = imported(write_export(_google_tags((None, True), ("G-TEST2", True)), tmp_path / "before.json"))
    live = write_export(_google_tags((None, True), ("G-TEST2", False)), tmp_path / "live.json")

    result = run_tiggy("import", live, spec, "--check")

    label = line_of(spec, "gtag_configs.yml", "label: googtag 2")
    assert_listed(
        result,
        {
            ("gtag_configs", "googtag 2"): [f"only in the spec  gtag_configs.yml:{label}"],
            ("gtag_configs", "googtag"): ["only in the container"],
        },
    )


def test_gtag_configs_holding_secrets_pair_by_content_with_the_secrets_unread(imported, tmp_path):
    """Import writes each apiSecret as {$secret} while GTM still holds the value, so content alone never
    matches; a config pairs with the one equal to it once its secrets are masked, as offline compares."""
    c = b.Container("Example - Google tags", public_id="GTM-TEST006", container_id="106")
    c.gtag_config("googtag", [b.text("measurementId", "G-TEST1"), b.text("apiSecret", "TEST_GTAG_SECRET")])
    c.gtag_config("googtag", [b.text("tagId", "G-TEST2"), b.text("apiSecret", "TEST_GTAG_SECRET_2")])
    export = write_export(c.export("2026-10-01 09:00:00"), tmp_path / "google_tags.json")

    result = run_tiggy("import", export, imported(export), "--check")

    assert (result.code, result.err) == (0, unread_note(export)), result
    assert "TEST_GTAG_SECRET" not in result.out, result


# Every other change is drift, at a line: one value flipped or deleted anywhere, sampled


SAMPLED = 25  # values per export, each flipped then deleted; about 2 s across the committed exports


def _leaves(node: Any, path: tuple[str | int, ...] = ()) -> Iterator[tuple[str | int, ...]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaves(value, (*path, key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _leaves(value, (*path, i))
    else:
        yield path


def _changed(version: Version, path: tuple[str | int, ...], kind: str) -> tuple[Version, Any]:
    """`version` with the value at `path` flipped or deleted, and the value it held."""
    live = copy.deepcopy(version)
    *steps, last = path
    holder: Any = live
    for step in steps:
        holder = holder[step]
    value = holder[last]
    if kind == "deleted":
        del holder[last]
    elif isinstance(value, bool):
        holder[last] = not value
    elif isinstance(value, int | float):
        holder[last] = value + 1
    else:
        holder[last] = f"{value}X"
    return live, value


def _placed(change: diff.Change) -> bool:
    """Explained, and at a line, unless it is a whole object the spec doesn't have."""
    whole_and_live_only = change.op is diff.Op.ONLY_LEFT and not change.path
    return not change.unexplained and (change.origin is not None or whole_and_live_only)


def _refused(version: Version) -> bool:
    """Whether import --check refuses the export outright (exit 2), so says nothing about drift."""
    try:
        importer._preconditions(version)
    except importer.ImportError_:
        return True
    return False


@pytest.mark.parametrize("export", ALL_EXPORTS, ids=export_id)
def test_any_one_value_changed_is_drift_with_every_difference_placed(export, imported):
    """Equivalent only where canon is unchanged too, or the value was a secret offline can't read; and every
    difference is explained and placed, so the fallback never fires."""
    spec = load_spec(imported(export))
    compiled = compile_spec(spec, Offline())
    version = exports.read(export).raw_version
    original, secrets = canon.canon(version), set(importer.find_secrets(version).values.values())
    leaves = list(_leaves(version))
    for path in random.Random(export_id(export)).sample(leaves, min(SAMPLED, len(leaves))):
        for kind in ("flipped", "deleted"):
            live, value = _changed(version, path, kind)
            try:
                found = diff.compare(
                    live, compiled, spec, known=tuple(importer.find_secrets(live).values.values())
                )
            except canon.CanonError:  # import --check exits 2
                continue
            where = f"{'/'.join(map(str, path))} {kind}"
            if not found.changes:
                secret = kind == "flipped" and value in secrets  # offline, any value stands in for one
                assert secret or same(canon.canon(live), original) or _refused(live), f"{where}: equivalent"
            unplaced = [c for c in found.changes if not _placed(c)]
            assert not unplaced or _refused(live), f"{where}: {unplaced}"
