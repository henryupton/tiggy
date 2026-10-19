"""Rendering findings and drift for people (text), machines (JSON) and GitHub Actions (annotations).

A spec problem (something that stops a spec compiling, such as a name that doesn't resolve) is
reported beside the findings, as an error with rule `spec-problem` and no object.
"""

import json
from collections import defaultdict
from typing import Any

import click

from tiggy.checks import Finding, Severity
from tiggy.graph import ContainerGraph, Kind, Node
from tiggy.spec.diff import ABSENT, SIDES, Change, Diff, Op, Secret, shown, where
from tiggy.spec.problems import Problem, describe

MARKS = {Severity.ERROR: ("✗", "red"), Severity.WARNING: ("⚠", "yellow")}
SPEC_PROBLEM = "spec-problem"
SUMMARY_KINDS = (
    Kind.TAG, Kind.TRIGGER, Kind.VARIABLE, Kind.TEMPLATE, Kind.CLIENT, Kind.TRANSFORMATION, Kind.ZONE,
    Kind.GTAG_CONFIG,
)  # fmt: skip


def _container(graph: ContainerGraph | None) -> dict[str, str | None]:
    if graph is None:
        return {"name": None, "publicId": None, "version": None}
    container = graph.version.container
    return {
        "name": container.name if container else None,
        "publicId": container.public_id if container else None,
        "version": graph.version.container_version_id,
    }


def _counts(graph: ContainerGraph | None) -> dict[str, int]:
    if graph is None:
        return {}
    return {str(k): len(graph.of_kind(k)) for k in SUMMARY_KINDS if graph.of_kind(k)}


def _tally(findings: list[Finding], files: list[str], problems: list[Problem]) -> dict[str, int]:
    return {
        "errors": sum(f.severity is Severity.ERROR for f in findings) + len(problems),
        "warnings": sum(f.severity is Severity.WARNING for f in findings) + len(files),
    }


def printable(text: str) -> str:
    """Text safe for a terminal: control characters, newlines too, escaped so a name can't forge output."""
    return "".join(c if c.isprintable() else c.encode("unicode_escape").decode() for c in text)


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def text(
    graph: ContainerGraph | None,
    findings: list[Finding],
    locations: dict[Node, str] | None = None,
    files: list[str] | None = None,
    problems: list[Problem] | None = None,
) -> str:
    """`files` are warnings about the spec's files rather than its objects, already located."""
    files, problems, locations = files or [], problems or [], locations or {}
    lines = []
    if graph is not None:
        meta = _container(graph)
        counts = ", ".join(plural(n, k) for k, n in _counts(graph).items())
        title = meta["name"] or "Container"
        if meta["publicId"]:
            title += f" ({meta['publicId']})"
        if meta["version"]:
            title += f", version {meta['version']}"
        lines += [click.style(printable(title), bold=True) + (f": {counts}" if counts else ""), ""]
    mark, colour = MARKS[Severity.ERROR]
    lines.extend(f"{click.style(mark, fg=colour)} {printable(str(problem))}" for problem in problems)
    by_node: dict[Node, list[Finding]] = defaultdict(list)
    for f in findings:
        by_node[f.node].append(f)
    for node, group in by_node.items():
        worst = Severity.ERROR if any(f.severity is Severity.ERROR for f in group) else Severity.WARNING
        mark, colour = MARKS[worst]
        where = f"  {click.style(printable(locations[node]), dim=True)}" if node in locations else ""
        lines.append(f"{click.style(mark, fg=colour)} {printable(str(node))}{where}")
        for f in group:
            lines.append(f"    {printable(f.message)}  {click.style(f'[{f.rule}]', dim=True)}")
    mark, colour = MARKS[Severity.WARNING]
    lines.extend(f"{click.style(mark, fg=colour)} {printable(warning)}" for warning in files)
    tally = _tally(findings, files, problems)
    if findings or files or problems:
        lines.append("")
    summary = f"{plural(tally['errors'], 'error')}, {plural(tally['warnings'], 'warning')}"
    lines.append(
        click.style(summary, fg="red" if tally["errors"] else "yellow" if tally["warnings"] else "green")
    )
    return "\n".join(lines)


def as_json(
    graph: ContainerGraph | None,
    findings: list[Finding],
    locations: dict[Node, str] | None = None,
    files: list[str] | None = None,
    problems: list[Problem] | None = None,
) -> str:
    files, problems, locations = files or [], problems or [], locations or {}
    payload = {
        "container": _container(graph),
        "counts": _counts(graph),
        "summary": _tally(findings, files, problems),
        "files": files,
        "findings": [
            {
                "rule": SPEC_PROBLEM,
                "severity": str(Severity.ERROR),
                "kind": None,
                "name": None,
                "id": None,
                "message": p.message,
                "location": str(p.origin) if p.origin else None,
            }
            for p in problems
        ]
        + [
            {
                "rule": f.rule,
                "severity": str(f.severity),
                "kind": str(f.node.kind),
                "name": f.node.name,
                "id": f.node.id,
                "message": f.message,
                "location": locations.get(f.node),
            }
            for f in findings
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def github(
    graph: ContainerGraph | None,
    findings: list[Finding],
    locations: dict[Node, str] | None = None,
    files: list[str] | None = None,
    problems: list[Problem] | None = None,
) -> str:
    """One workflow command per finding, which GitHub shows on the pull request's diff, then the tally."""
    files, problems, locations = files or [], problems or [], locations or {}
    lines = [
        _annotation(Severity.ERROR, SPEC_PROBLEM, p.message, str(p.origin) if p.origin else None)
        for p in problems
    ]
    lines += [
        _annotation(f.severity, f.rule, f"{f.node}: {f.message}", locations.get(f.node)) for f in findings
    ]
    lines += _stray(files)
    tally = _tally(findings, files, problems)
    lines.append(f"{plural(tally['errors'], 'error')}, {plural(tally['warnings'], 'warning')}")
    return "\n".join(lines)


def _stray(files: list[str]) -> list[str]:
    return [
        _annotation(Severity.WARNING, "stray-file", message, location)
        for location, _, message in (warning.partition(": ") for warning in files)
    ]


# Drift, from `import --check`

DRIFT_OPS = {
    Op.ONLY_LEFT: "only_in_container",
    Op.ONLY_RIGHT: "only_in_spec",
    Op.CHANGED: "changed",
    Op.MOVED: "moved",
}


def message(change: Change, sides: tuple[str, str] = SIDES) -> str:
    """A Change after its object's name: `parameter.eventName ('a' in the container, 'b' in the spec)`.

    A key on one side only reads `missing from`; a list item shows its value, since an index can't say
    which item it is.
    """
    left, right = sides
    if change.unexplained:
        return change.unexplained
    if not change.path:
        return f"only in {left if change.op is Op.ONLY_LEFT else right}"
    item = _item(change)
    if change.op is Op.MOVED:
        detail = f"at [{change.left}] in {left}, [{change.right}] in {right}"
    elif change.window:
        w = change.window
        detail = f"from line {w.line}, character {w.column}: {w.left!r} in {left}, {w.right!r} in {right}"
    elif change.members:
        gone, new = (", ".join(map(_spelled, members)) for members in (change.left, change.right))
        if gone and new:
            detail = f"{gone} in {left}, {new} in {right}"
        else:
            detail = f"{gone} only in {left}" if gone else f"{new} only in {right}"
    elif change.op is Op.ONLY_LEFT:
        detail = f"{_spelled(change.left)} only in {left}" if item else f"missing from {right}"
    elif change.op is Op.ONLY_RIGHT:
        detail = f"{_spelled(change.right)} only in {right}" if item else f"missing from {left}"
    else:
        detail = f"{_spelled(change.left)} in {left}, {_spelled(change.right)} in {right}"
    if change.default:
        detail += "; GTM's default, unverified"
    return f"{where(change.path)} ({detail})"


def _item(change: Change) -> bool:
    return bool(change.path) and isinstance(change.path[-1], int)


def _spelled(value: Any) -> str:
    found = shown(value)
    return str(found) if isinstance(found, Secret) else describe(found)


def _subject(change: Change) -> str:
    return change.collection if change.identity is None else f"{change.collection} {change.identity!r}"


def _location(change: Change) -> str | None:
    return str(change.origin) if change.origin else None


def _verdict(diff: Diff, source: str, spec: str) -> str:
    if not diff.changes:
        return f"{spec} is equivalent to {source}"
    return f"{plural(len(diff.changes), 'difference')} between {source} and {spec}"


def drift_text(diff: Diff, source: str, spec: str, files: list[str]) -> str:
    """Each difference with its location dimmed after it. `files` are warned about on stderr instead."""
    lines = []
    for change in diff.changes:
        location = _location(change)
        where_ = f"  {click.style(printable(location), dim=True)}" if location else ""
        line = printable(f"{_subject(change)}: {message(change)}")
        lines.append(f"{click.style('≠', fg='yellow')} {line}{where_}")
    lines.append(
        click.style(printable(_verdict(diff, source, spec)), fg="yellow" if diff.changes else "green")
    )
    return "\n".join(lines)


def drift_json(diff: Diff, source: str, spec: str, files: list[str]) -> str:
    payload = {
        "source": source,
        "spec": spec,
        "equivalent": not diff.changes,
        "summary": {
            "differences": len(diff.changes),
            "defaults": sum(c.default for c in diff.changes),
            "unreadSecrets": diff.unread_secrets,
            "semanticOnly": diff.semantic_only,
        },
        "files": files,
        "differences": [_difference(c) for c in diff.changes],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, default=_secret)


def _difference(change: Change) -> dict[str, Any]:
    """A Change as JSON, with the values its message shows and no more: so neither side of a key only one
    side has, which may be where a secret went. A secret is `{"$secret": name}`."""
    sides = (("container", change.left), ("spec", change.right))
    shows = change.op not in (Op.ONLY_LEFT, Op.ONLY_RIGHT) or _item(change)
    return {
        "kind": change.collection,
        "name": change.identity,
        "op": DRIFT_OPS[change.op],
        "path": where(change.path),
        "segments": list(change.path),
        **{side: shown(value) for side, value in sides if shows and value is not ABSENT},
        "message": message(change),
        "location": _location(change),
        "default": change.default,
    }


def _secret(value: Any) -> Any:
    if isinstance(value, Secret):
        return {"$secret": value.name, **({} if value.stored else {"stored": False})}
    raise TypeError(f"{type(value).__name__} isn't JSON")


def drift_github(diff: Diff, source: str, spec: str, files: list[str]) -> str:
    """One annotation per difference, a warning where it is only GTM's default, then the verdict."""
    lines = [
        _annotation(
            Severity.WARNING if c.default else Severity.ERROR,
            "drift-default" if c.default else "drift",
            f"{_subject(c)}: {message(c)}",
            _location(c),
        )
        for c in diff.changes
    ]
    lines += _stray(files)
    lines.append(printable(_verdict(diff, source, spec)))
    return "\n".join(lines)


def _annotation(severity: Severity, title: str, message: str, location: str | None) -> str:
    """`::error file=..,line=..,title=..::message`, escaped as GitHub's workflow commands require."""
    properties = []
    if location:
        file, _, line = location.rpartition(":")
        if not line.isdigit():
            file, line = location, ""
        properties.append(f"file={_escape(file, property_=True)}")
        if line:
            properties.append(f"line={line}")
    properties.append(f"title={_escape(title, property_=True)}")
    return f"::{severity} {','.join(properties)}::{_escape(printable(message))}"


def _escape(text: str, property_: bool = False) -> str:
    text = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return text.replace(":", "%3A").replace(",", "%2C") if property_ else text
