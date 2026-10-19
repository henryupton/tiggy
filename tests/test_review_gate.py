"""What a reviewer and CI see when a pull request breaks a container spec.

Each case edits a fresh import of the synthetic Analytics container the way a hurried pull request in
the adopting repo would, runs `tiggy validate`, and holds the report to the object, the problem and
the file:line a reviewer needs to find the edit.
"""

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from tests.conftest import FIXTURES, Result, edit_spec, fragment_of, replace_in_spec, run_tiggy

ANALYTICS = FIXTURES / "cdm" / "web_analytics.json"
MARKS = ("✗", "⚠")


# Helpers


@dataclass(frozen=True)
class Broken:
    """What the report must say about one broken pull request: every finding line, and the summary."""

    lines: tuple[str | re.Pattern[str], ...]
    summary: str | re.Pattern[str]


def validate(spec: Path, *flags: str, cwd: Path | None = None) -> Result:
    return run_tiggy("validate", spec, *flags, cwd=cwd)


def rel(spec: Path, path: Path) -> str:
    return path.relative_to(spec).as_posix()


def line_of(path: Path, line: str) -> int:
    """The 1-based number of the one line in `path` that reads exactly `line`."""
    found = [n for n, text in enumerate(path.read_bytes().decode("utf-8").split("\n"), 1) if text == line]
    assert len(found) == 1, f"{path.name}: {line!r} is on lines {found}, not exactly one"
    return found[0]


def at(spec: Path, path: Path, line: str) -> str:
    """`file:line` as validate prints it, for the line reading exactly `line`."""
    return f"{rel(spec, path)}:{line_of(path, line)}"


def everywhere(spec: Path, line: str) -> list[str]:
    """`file:line` for every fragment line reading exactly `line`."""
    return [
        f"{rel(spec, path)}:{n}"
        for path in sorted(spec.rglob("*.yml"))
        for n, text in enumerate(path.read_bytes().decode("utf-8").split("\n"), 1)
        if text == line
    ]


def problems(n: int) -> re.Pattern[str]:
    """The summary line for `n` spec problems (its grammar is pinned by its own test)."""
    return re.compile(rf"{n} problems? in the spec")


def matches(expected: str | re.Pattern[str], line: str) -> bool:
    return line == expected if isinstance(expected, str) else expected.fullmatch(line) is not None


def assert_report(result: Result, broken: Broken) -> None:
    """Every expected line is in the report, nothing else is flagged, and the summary agrees."""
    out = result.out.splitlines()
    for expected in broken.lines:
        assert any(matches(expected, line) for line in out), f"no line matching {expected!r}\n{result}"
    flagged = [line for line in out if line.startswith(MARKS)]
    expected_flags = [e for e in broken.lines if (e if isinstance(e, str) else e.pattern).startswith(MARKS)]
    assert len(flagged) == len(expected_flags), f"expected {len(expected_flags)} flagged lines\n{result}"
    assert out and matches(broken.summary, out[-1]), f"summary is not {broken.summary!r}\n{result}"


def block(path: Path, name: str) -> str:
    """The YAML text of the list entry `  - name: <name>`, up to the next entry, as a clone would copy it."""
    lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)
    start = lines.index(f"  - name: {name}\n")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("  - ")), len(lines))
    return "".join(lines[start:end])


def append(spec: Path, path: Path, text: str) -> None:
    def edit(before: str) -> str:
        assert before.endswith("\n"), f"{path.name} doesn't end in a newline"
        return before + text

    edit_spec(spec, rel(spec, path), edit)


def sidecar(spec: Path, fragment: Path, kind: str, name: str, key: str) -> Path:
    """The code file a `{$file: ...}` parameter of one object names."""
    tree = yaml.safe_load(fragment.read_bytes().decode("utf-8"))
    (item,) = [i for i in tree[kind] if i["name"] == name]
    return spec / item["parameter"][key]["$file"]


# Broken pull requests. Each edits a spec and returns what validate must say about it.


def mistype_a_ga4_row(spec: Path) -> Broken:
    """A GA4 row reads licence_type; the dictionary property (and its variable) is license_type."""
    tags = fragment_of(spec, "tags", "GA4 - file_download")
    replace_in_spec(
        spec,
        rel(spec, tags),
        "'{{DLV - event_attributes.license_type}}'",
        "'{{DLV - event_attributes.licence_type}}'",
    )
    return Broken(
        (
            f'✗ tag "GA4 - file_download"  {at(spec, tags, "  - name: GA4 - file_download")}',
            "    eventSettingsTable[3].parameterValue: {{DLV - event_attributes.licence_type}} is not a "
            'variable or an enabled built-in (did you mean "DLV - event_attributes.license_type"?)  '
            "[unknown-variable]",
        ),
        "1 error, 0 warnings",
    )


def add_a_payload_key_without_its_variable(spec: Path) -> Broken:
    """coupon_code joins the payload's // Event section in order; nobody cloned its Data Layer variable."""
    variables = fragment_of(spec, "variables", "CJS - first-party payload")
    code = sidecar(spec, variables, "variables", "CJS - first-party payload", "javascript")
    build_number = "    build_number:         {{DLV - event_attributes.build_number}},\n"
    coupon_code = "    coupon_code:          {{DLV - event_attributes.coupon_code}},\n"
    replace_in_spec(spec, rel(spec, code), build_number, build_number + coupon_code)
    return Broken(
        (
            f'✗ variable "CJS - first-party payload"  '
            f"{at(spec, variables, '  - name: CJS - first-party payload')}",
            re.compile(
                r"    javascript: \{\{DLV - event_attributes\.coupon_code\}\} is not a variable or an "
                r"enabled built-in( \(did you mean .*\))?  \[unknown-variable\]"
            ),
        ),
        "1 error, 0 warnings",
    )


def use_a_built_in_that_is_not_enabled(spec: Path) -> Broken:
    """GA4 - track_event's link_url row switches to {{Click URL}}, which the container never enabled."""
    tags = fragment_of(spec, "tags", "GA4 - track_event")
    replace_in_spec(spec, rel(spec, tags), "'{{DLV - event_attributes.link_url}}'", "'{{Click URL}}'")
    return Broken(
        (
            f'✗ tag "GA4 - track_event"  {at(spec, tags, "  - name: GA4 - track_event")}',
            "    eventSettingsTable[8].parameterValue: {{Click URL}} is a built-in variable that isn't "
            "enabled  [built-in-not-enabled]",
        ),
        "1 error, 0 warnings",
    )


def rename_a_trigger_but_not_its_tags(spec: Path) -> Broken:
    triggers = fragment_of(spec, "triggers", "CE - track_event - file_download")
    replace_in_spec(
        spec,
        rel(spec, triggers),
        "  - name: CE - track_event - file_download\n",
        "  - name: CE - track_event - download\n",
    )
    firing = everywhere(spec, "      - CE - track_event - file_download")
    assert firing, "no tag fires on the renamed trigger"
    return Broken(
        tuple(
            f'✗ {where}: trigger "CE - track_event - file_download" is not defined in this spec'
            for where in firing
        ),
        problems(len(firing)),
    )


def delete_a_folder_still_in_use(spec: Path) -> Broken:
    folder = fragment_of(spec, "folders", "GA4")
    assert yaml.safe_load(folder.read_bytes().decode("utf-8"))["folders"] == [{"name": "GA4"}]
    folder.unlink()
    filed = everywhere(spec, "    folder: GA4")
    assert len(filed) > 1, "the GA4 folder should hold several objects"
    return Broken(
        tuple(f'✗ {where}: folder "GA4" is not declared under folders' for where in filed),
        problems(len(filed)),
    )


def define_a_variable_in_two_fragments(spec: Path) -> Broken:
    """A Data Layer variable cloned into the unfiled variables, where the original already lives elsewhere."""
    name = "DLV - event_attributes.wishlist_id"
    original = fragment_of(spec, "variables", name)
    unfiled = fragment_of(spec, "variables", "URL - utm_campaign")
    assert unfiled != original
    append(spec, unfiled, block(original, name))
    return Broken(
        (
            f"✗ {at(spec, unfiled, f'  - name: {name}')}: "
            f'variable "{name}" is already defined at {at(spec, original, f"  - name: {name}")}',
        ),
        problems(1),
    )


def quoting_hint(where: str) -> re.Pattern[str]:
    return re.compile(rf"✗ {re.escape(where)}: .*A \{{\{{variable\}}\}} needs quoting: '\{{\{{name\}}\}}'")


def leave_a_variable_unquoted_in_a_flow_sequence(spec: Path) -> Broken:
    tags = fragment_of(spec, "tags", "GA4 - track_event")
    flow = "      userProperties: [{name: user_logged_in, value: {{DLV - event_attributes.user_logged_in}}}]"
    replace_in_spec(
        spec,
        rel(spec, tags),
        "      userProperties:\n"
        "        - name: user_logged_in\n"
        "          value: '{{DLV - event_attributes.user_logged_in}}'\n",
        flow + "\n",
    )
    return Broken((quoting_hint(at(spec, tags, flow)),), problems(1))


def leave_a_variable_unquoted_in_a_ga4_row(spec: Path) -> Broken:
    tags = fragment_of(spec, "tags", "GA4 - wishlist")
    row = "          parameterValue: {{DLV - event_attributes.wishlist_id}}"
    replace_in_spec(
        spec, rel(spec, tags), "          parameterValue: '{{DLV - event_attributes.wishlist_id}}'", row
    )
    return Broken((quoting_hint(at(spec, tags, row)),), problems(1))


def mistype_a_key(spec: Path) -> Broken:
    tags = fragment_of(spec, "tags", "GA4 - sign_up")
    replace_in_spec(
        spec,
        rel(spec, tags),
        "    firing_triggers:\n      - CE - sign_up\n",
        "    firing_trigger:\n      - CE - sign_up\n",
    )
    return Broken(
        (
            f"✗ {at(spec, tags, '    firing_trigger:')}: "
            "unknown key 'firing_trigger' for a tag (did you mean \"firing_triggers\"?)",
        ),
        problems(1),
    )


def add_a_data_layer_variable_nothing_uses(spec: Path) -> Broken:
    """The API tool's habit: clone a Data Layer variable for a new property before anything reads it."""
    original = fragment_of(spec, "variables", "DLV - event_attributes.wishlist_id")
    clone = block(original, "DLV - event_attributes.wishlist_id").replace("wishlist_id", "coupon_code")
    append(spec, original, clone)
    return Broken(
        (
            '⚠ variable "DLV - event_attributes.coupon_code"  '
            f"{at(spec, original, '  - name: DLV - event_attributes.coupon_code')}",
            "    nothing references it  [unused-variable]",
        ),
        "0 errors, 1 warning",
    )


BREAKERS: dict[str, Callable[[Path], Broken]] = {
    "ga4-row-typo": mistype_a_ga4_row,
    "payload-key-without-variable": add_a_payload_key_without_its_variable,
    "built-in-not-enabled": use_a_built_in_that_is_not_enabled,
    "trigger-renamed": rename_a_trigger_but_not_its_tags,
    "folder-deleted": delete_a_folder_still_in_use,
    "variable-in-two-fragments": define_a_variable_in_two_fragments,
    "unquoted-in-flow-sequence": leave_a_variable_unquoted_in_a_flow_sequence,
    "unquoted-in-ga4-row": leave_a_variable_unquoted_in_a_ga4_row,
    "mistyped-key": mistype_a_key,
    "unused-variable": add_a_data_layer_variable_nothing_uses,
}


def broken_spec(
    imported: Callable[..., Path], *breakers: Callable[[Path], Broken]
) -> tuple[Path, list[Broken]]:
    spec = imported(ANALYTICS)
    return spec, [breaker(spec) for breaker in breakers]


# What the reviewer reads


def test_a_typo_in_a_ga4_row_names_the_tag_and_the_variable_it_meant(imported):
    spec, (broken,) = broken_spec(imported, mistype_a_ga4_row)
    result = validate(spec)
    assert result.code == 1, result
    assert_report(result, broken)


def test_a_payload_key_without_its_data_layer_variable_names_the_payload_variable(imported):
    spec, (broken,) = broken_spec(imported, add_a_payload_key_without_its_variable)
    result = validate(spec)
    assert result.code == 1, result
    assert_report(result, broken)


def test_a_built_in_the_container_never_enabled_names_the_tag_and_the_built_in(imported):
    spec, (broken,) = broken_spec(imported, use_a_built_in_that_is_not_enabled)
    result = validate(spec)
    assert result.code == 1, result
    assert_report(result, broken)


def test_renaming_a_trigger_points_at_every_tag_still_firing_on_the_old_name(imported):
    spec, (broken,) = broken_spec(imported, rename_a_trigger_but_not_its_tags)
    result = validate(spec)
    assert result.code == 1, result
    assert_report(result, broken)


def test_deleting_a_folder_points_at_every_object_still_filed_in_it(imported):
    spec, (broken,) = broken_spec(imported, delete_a_folder_still_in_use)
    result = validate(spec)
    assert result.code == 1, result
    assert_report(result, broken)


def test_a_variable_defined_in_two_fragments_names_both_places(imported):
    spec, (broken,) = broken_spec(imported, define_a_variable_in_two_fragments)
    result = validate(spec)
    assert result.code == 1, result
    assert_report(result, broken)


@pytest.mark.parametrize(
    "breaker",
    [leave_a_variable_unquoted_in_a_flow_sequence, leave_a_variable_unquoted_in_a_ga4_row],
    ids=["flow-sequence", "ga4-row"],
)
def test_an_unquoted_variable_gets_the_quoting_hint_at_its_line(imported, breaker):
    spec, (broken,) = broken_spec(imported, breaker)
    result = validate(spec)
    assert result.code == 1, result
    assert_report(result, broken)


def test_a_mistyped_key_suggests_the_real_one(imported):
    spec, (broken,) = broken_spec(imported, mistype_a_key)
    result = validate(spec)
    assert result.code == 1, result
    assert_report(result, broken)


def test_a_reviewer_reads_this_report_for_a_broken_dictionary_change(imported, golden):
    """One pull request mapping a dictionary change badly three ways: the exact bytes CI prints."""
    spec, broken = broken_spec(
        imported,
        mistype_a_ga4_row,
        add_a_payload_key_without_its_variable,
        use_a_built_in_that_is_not_enabled,
    )
    text, data = validate(spec), validate(spec, "--format", "json")
    assert (text.code, data.code) == (1, 1), f"{text}\n{data}"
    assert_report(text, Broken(tuple(line for each in broken for line in each.lines), "3 errors, 0 warnings"))
    assert str(spec) not in text.out + data.out
    golden("review_gate/broken_dictionary_change.txt", text.out)
    golden("review_gate/broken_dictionary_change.json", data.out)


# What CI parses


def report_lines(payload: dict) -> list[str]:
    """The finding lines of a text report, rebuilt from validate's JSON."""
    nodes: dict[tuple[str, str, str | None], list[dict]] = {}
    for finding in payload["findings"]:
        nodes.setdefault((finding["kind"], finding["name"], finding["id"]), []).append(finding)
    lines = []
    for (kind, name, _), group in nodes.items():
        mark = "✗" if any(f["severity"] == "error" for f in group) else "⚠"
        location = group[0]["location"]
        lines.append(f'{mark} {kind} "{name}"' + (f"  {location}" if location else ""))
        lines.extend(f"    {f['message']}  [{f['rule']}]" for f in group)
    return lines


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


@pytest.mark.parametrize("breaker", BREAKERS.values(), ids=BREAKERS.keys())
def test_json_carries_the_same_findings_as_the_text_report(imported, breaker):
    spec, (broken,) = broken_spec(imported, breaker)
    text, data = validate(spec), validate(spec, "--format", "json")
    assert data.code == text.code, f"{text}\n{data}"
    payload = json.loads(data.out)
    out = text.out.splitlines()
    if "problems" in payload:
        assert set(payload) == {"problems"}, payload
        assert [f"✗ {p}" for p in payload["problems"]] == out[:-1], f"{text}\n{data}"
        assert matches(broken.summary, out[-1]), text
        return
    assert payload["container"]["name"] == "Example - Analytics"
    assert out[0].startswith("Example - Analytics (GTM-TEST001): "), text
    assert report_lines(payload) == out[2:-2], f"{text}\n{data}"
    summary = payload["summary"]
    assert out[-1] == f"{plural(summary['errors'], 'error')}, {plural(summary['warnings'], 'warning')}"
    assert matches(broken.summary, out[-1]), text


def test_strict_fails_ci_on_a_data_layer_variable_nothing_uses(imported):
    spec, (broken,) = broken_spec(imported, add_a_data_layer_variable_nothing_uses)
    lenient = validate(spec)
    assert lenient.code == 0, lenient
    assert_report(lenient, broken)
    strict = validate(spec, "--strict")
    assert strict.code == 1, strict
    assert strict.out == lenient.out
    data = validate(spec, "--strict", "--format", "json")
    assert data.code == 1, data
    payload = json.loads(data.out)
    assert payload["summary"] == {"errors": 0, "warnings": 1}
    assert [(f["rule"], f["severity"], f["name"]) for f in payload["findings"]] == [
        ("unused-variable", "warning", "DLV - event_attributes.coupon_code")
    ]


# Known gaps and bugs: written as the behaviour the gate should have


@pytest.mark.xfail(
    strict=True,
    reason="known gap: one spec problem (here a renamed trigger) stops validate before the reference "
    "checks, so the reviewer fixes it and only then learns of the mistyped variable",
)
def test_one_run_reports_spec_problems_and_reference_findings_together(imported):
    spec, (renamed, mistyped) = broken_spec(imported, rename_a_trigger_but_not_its_tags, mistype_a_ga4_row)
    result = validate(spec)
    assert result.code == 1, result
    for expected in renamed.lines + mistyped.lines[1:]:
        assert any(matches(expected, line) for line in result.out.splitlines()), (
            f"no line matching {expected!r}\n{result}"
        )


TYPOS = {
    "trigger": (
        "GA4 - file_download",
        "tags",
        "      - CE - track_event - file_download",
        "      - CE - track_event - file_downlaod",
        "CE - track_event - file_download",
    ),
    "folder": (
        "CJS - first-party payload",
        "variables",
        "    folder: First-party",
        "    folder: First party",
        "First-party",
    ),
    "template": (
        "Amplitude - track_event",
        "tags",
        "    type: {template: Amplitude Event}",
        "    type: {template: Amplitude event}",
        "Amplitude Event",
    ),
}


@pytest.mark.xfail(
    strict=True,
    reason="known gap: did-you-mean covers variables, tags and keys, not trigger, folder or template names",
)
@pytest.mark.parametrize("owner, kind, old, new, meant", TYPOS.values(), ids=TYPOS.keys())
def test_a_mistyped_trigger_folder_or_template_name_suggests_the_real_one(
    imported, owner, kind, old, new, meant
):
    spec = imported(ANALYTICS)
    fragment = fragment_of(spec, kind, owner)
    replace_in_spec(spec, rel(spec, fragment), f"{old}\n", f"{new}\n")
    result = validate(spec)
    assert result.code == 1, result
    flagged = [line for line in result.out.splitlines() if line.startswith("✗ ")]
    assert flagged and flagged[0].startswith(f"✗ {at(spec, fragment, new)}: "), result
    assert f'did you mean "{meant}"?' in flagged[0], result


@pytest.mark.xfail(
    strict=True,
    reason="known gap: locations are relative to the spec directory, so CI run from the adopting "
    "repo's root prints paths that neither the diff nor an editor can open",
)
@pytest.mark.parametrize(
    "breaker", [mistype_a_ga4_row, rename_a_trigger_but_not_its_tags], ids=["finding", "spec-problem"]
)
def test_locations_are_relative_to_the_repository_root_when_run_from_it(imported, tmp_path, breaker):
    repo = tmp_path / "adopting-repo"
    spec = imported(ANALYTICS, name="adopting-repo/gtm/web-analytics")
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    broken = breaker(spec)
    prefix = "gtm/web-analytics/"
    expected = []
    for line in broken.lines:
        if isinstance(line, str) and line.startswith("✗ "):
            expected.append(re.sub(r"(✗ (?:\w+ \"[^\"]*\"  )?)", rf"\g<1>{prefix}", line, count=1))
    assert expected, broken
    result = validate(Path("gtm/web-analytics"), cwd=repo)
    assert result.code == 1, result
    for line in expected:
        assert line in result.out.splitlines(), f"no line {line!r}\n{result}"
    data = validate(Path("gtm/web-analytics"), "--format", "json", cwd=repo)
    payload = json.loads(data.out)
    located = [f["location"] for f in payload.get("findings", [])] + payload.get("problems", [])
    assert located and all(where.startswith(prefix) for where in located), data


def test_a_single_spec_problem_is_counted_in_the_singular(imported):
    spec, _ = broken_spec(imported, rename_a_trigger_but_not_its_tags)
    result = validate(spec)
    assert result.code == 1, result
    assert result.out.splitlines()[-1] == "1 problem in the spec", result
