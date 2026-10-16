"""Rendering findings for people (text) and machines (JSON)."""

import json
from collections import defaultdict

import click

from tiggy.checks import Finding, Severity
from tiggy.graph import ContainerGraph, Kind, Node

MARKS = {Severity.ERROR: ("✗", "red"), Severity.WARNING: ("⚠", "yellow")}
SUMMARY_KINDS = (Kind.TAG, Kind.TRIGGER, Kind.VARIABLE, Kind.TEMPLATE, Kind.CLIENT, Kind.TRANSFORMATION)


def _container(graph: ContainerGraph) -> dict[str, str | None]:
    container = graph.version.container
    return {
        "name": container.name if container else None,
        "publicId": container.public_id if container else None,
        "version": graph.version.container_version_id,
    }


def _counts(graph: ContainerGraph) -> dict[str, int]:
    return {str(k): len(graph.of_kind(k)) for k in SUMMARY_KINDS if graph.of_kind(k)}


def _tally(findings: list[Finding]) -> dict[str, int]:
    return {
        "errors": sum(f.severity is Severity.ERROR for f in findings),
        "warnings": sum(f.severity is Severity.WARNING for f in findings),
    }


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def text(graph: ContainerGraph, findings: list[Finding]) -> str:
    meta = _container(graph)
    counts = ", ".join(_plural(n, k) for k, n in _counts(graph).items())
    title = f"{meta['name'] or 'Container'} ({meta['publicId'] or 'no public ID'})"
    lines = [click.style(title, bold=True) + f", version {meta['version']}: {counts}", ""]
    by_node: dict[Node, list[Finding]] = defaultdict(list)
    for f in findings:
        by_node[f.node].append(f)
    for node, group in by_node.items():
        worst = Severity.ERROR if any(f.severity is Severity.ERROR for f in group) else Severity.WARNING
        mark, colour = MARKS[worst]
        lines.append(f"{click.style(mark, fg=colour)} {node}")
        for f in group:
            lines.append(f"    {f.message}  {click.style(f'[{f.rule}]', dim=True)}")
    tally = _tally(findings)
    if findings:
        lines.append("")
    summary = f"{_plural(tally['errors'], 'error')}, {_plural(tally['warnings'], 'warning')}"
    lines.append(
        click.style(summary, fg="red" if tally["errors"] else "yellow" if tally["warnings"] else "green")
    )
    return "\n".join(lines)


def as_json(graph: ContainerGraph, findings: list[Finding]) -> str:
    payload = {
        "container": _container(graph),
        "counts": _counts(graph),
        "summary": _tally(findings),
        "findings": [
            {
                "rule": f.rule,
                "severity": str(f.severity),
                "kind": str(f.node.kind),
                "name": f.node.name,
                "id": f.node.id,
                "message": f.message,
            }
            for f in findings
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)
