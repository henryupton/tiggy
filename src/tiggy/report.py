"""Rendering findings for people (text), machines (JSON) and GitHub Actions (workflow annotations).

A spec problem (something that stops a spec compiling, such as a name that doesn't resolve) is
reported beside the findings, as an error with rule `spec-problem` and no object.
"""

import json
from collections import defaultdict

import click

from tiggy.checks import Finding, Severity
from tiggy.graph import ContainerGraph, Kind, Node
from tiggy.spec.problems import Problem

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
    for warning in files:
        location, _, message = warning.partition(": ")
        lines.append(_annotation(Severity.WARNING, "stray-file", message, location))
    tally = _tally(findings, files, problems)
    lines.append(f"{plural(tally['errors'], 'error')}, {plural(tally['warnings'], 'warning')}")
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
