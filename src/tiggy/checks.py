"""Rules run over a ContainerGraph. Each yields Findings; none reads GTM resources directly."""

from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from difflib import get_close_matches
from enum import StrEnum

from tiggy.graph import ContainerGraph, Kind, Node, Reference
from tiggy.gtm import builtins
from tiggy.gtm.base import GtmEnum, GtmModel
from tiggy.gtm.resources import Parameter, Tag, Variable
from tiggy.secrets import is_placeholder, secret_bearing


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: Severity
    node: Node
    message: str


Check = Callable[[ContainerGraph], Iterable[Finding]]
CHECKS: list[Check] = []
SPEC_CHECKS: list[Check] = []  # about how a spec is written, so not for an export
KIND_ORDER = list(Kind)


def check(fn: Check) -> Check:
    CHECKS.append(fn)
    return fn


def spec_check(fn: Check) -> Check:
    SPEC_CHECKS.append(fn)
    return fn


def run(graph: ContainerGraph, spec: bool = False) -> list[Finding]:
    findings = [f for check_ in CHECKS + (SPEC_CHECKS if spec else []) for f in check_(graph)]
    return sorted(
        findings,
        key=lambda f: (f.severity != Severity.ERROR, KIND_ORDER.index(f.node.kind), f.node.name, f.message),
    )


def _suggest(target: str, candidates: Iterable[str]) -> str:
    match = get_close_matches(target, list(candidates), n=1, cutoff=0.6)
    return f' (did you mean "{match[0]}"?)' if match else ""


def _names(graph: ContainerGraph, *kinds: Kind) -> list[str]:
    return [n.name for kind in kinds for n in graph.of_kind(kind)]


@check
def unresolved_references(graph: ContainerGraph) -> Iterator[Finding]:
    built_in_names = set(builtins.VARIABLE_NAMES.values())
    for ref in graph.references:
        if ref.resolved is not None:
            continue
        if ref.kind is Kind.VARIABLE and ref.target in built_in_names:
            yield _built_in_not_enabled(graph, ref)
        elif ref.kind is Kind.VARIABLE:
            hint = _suggest(ref.target, _names(graph, Kind.VARIABLE, Kind.BUILT_IN_VARIABLE))
            message = f"{ref.via}: {{{{{ref.target}}}}} is not a variable or an enabled built-in{hint}"
            yield Finding("unknown-variable", Severity.ERROR, ref.source, message)
        elif ref.kind is Kind.TAG:
            hint = _suggest(ref.target, _names(graph, Kind.TAG))
            message = f'{ref.via} "{ref.target}" does not exist{hint}'
            yield Finding("unknown-tag", Severity.ERROR, ref.source, message)
        elif ref.kind is Kind.TRIGGER:
            yield Finding(
                "unknown-trigger", Severity.ERROR, ref.source, f"{ref.via} {ref.target} does not exist"
            )
        elif ref.kind is Kind.TEMPLATE:
            message = f"type {ref.target} is not a custom template in this container"
            yield Finding("unknown-template", Severity.ERROR, ref.source, message)
        elif ref.kind is Kind.FOLDER:
            yield Finding("unknown-folder", Severity.ERROR, ref.source, f"folder {ref.target} does not exist")


def _built_in_not_enabled(graph: ContainerGraph, ref: Reference) -> Finding:
    if graph.built_ins_listed:
        message = f"{ref.via}: {{{{{ref.target}}}}} is a built-in variable that isn't enabled"
        return Finding("built-in-not-enabled", Severity.ERROR, ref.source, message)
    message = f"{ref.via}: {{{{{ref.target}}}}} is a built-in; the export doesn't say whether it's enabled"
    return Finding("built-in-not-enabled", Severity.WARNING, ref.source, message)


@check
def duplicates(graph: ContainerGraph) -> Iterator[Finding]:
    by_name: dict[tuple[Kind, str], list[Node]] = defaultdict(list)
    by_id: dict[tuple[Kind, str], list[Node]] = defaultdict(list)
    for node in graph.nodes:
        by_name[node.kind, node.name].append(node)
        if node.id:
            by_id[node.kind, node.id].append(node)
    for (kind, name), nodes in by_name.items():
        if len(nodes) > 1:
            yield Finding("duplicate-name", Severity.ERROR, nodes[0], f"{len(nodes)} {kind}s are named this")
    for (kind, node_id), nodes in by_id.items():
        if len(nodes) > 1:
            names = ", ".join(f'"{n.name}"' for n in nodes)
            yield Finding(
                "duplicate-id", Severity.ERROR, nodes[0], f"{kind} ID {node_id} is shared by {names}"
            )


@check
def cycles(graph: ContainerGraph) -> Iterator[Finding]:
    for kind, rule, what in (
        (Kind.VARIABLE, "variable-cycle", "variables reference each other in a loop"),
        (Kind.TAG, "sequencing-cycle", "setup and teardown tags loop"),
    ):
        edges: dict[Node, set[Node]] = defaultdict(set)
        for ref in graph.references:
            if ref.source.kind is kind and ref.resolved is not None and ref.resolved.kind is kind:
                edges[ref.source].add(ref.resolved)
        for component in _strongly_connected(edges):
            loop = " → ".join(f'"{n.name}"' for n in [*component, component[0]])
            for node in component:
                yield Finding(rule, Severity.ERROR, node, f"{what}: {loop}")


def _strongly_connected(edges: dict[Node, set[Node]]) -> list[list[Node]]:
    """Tarjan's algorithm; returns components that form a loop, including self-loops."""
    index: dict[Node, int] = {}
    low: dict[Node, int] = {}
    stack: list[Node] = []
    on_stack: set[Node] = set()
    found: list[list[Node]] = []

    def visit(node: Node) -> None:
        index[node] = low[node] = len(index)
        stack.append(node)
        on_stack.add(node)
        for nxt in edges.get(node, ()):
            if nxt not in index:
                visit(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in on_stack:
                low[node] = min(low[node], index[nxt])
        if low[node] == index[node]:
            component = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1 or node in edges.get(node, ()):
                found.append(sorted(component, key=lambda n: n.name))

    for node in sorted(edges, key=lambda n: n.name):
        if node not in index:
            visit(node)
    return found


@check
def unused(graph: ContainerGraph) -> Iterator[Finding]:
    for kind, rule, message in (
        (Kind.VARIABLE, "unused-variable", "nothing references it"),
        (Kind.TRIGGER, "unused-trigger", "nothing fires, blocks or groups on it"),
        (Kind.TEMPLATE, "unused-template", "no tag, variable or client uses it"),
    ):
        for node in graph.of_kind(kind):
            if not graph.incoming(node):
                yield Finding(rule, Severity.WARNING, node, message)


@check
def tags_that_never_fire(graph: ContainerGraph) -> Iterator[Finding]:
    for node in graph.of_kind(Kind.TAG):
        tag = node.resource
        if not isinstance(tag, Tag):
            continue
        sequenced = any(r.via in ("setup tag", "teardown tag") for r in graph.incoming(node))
        if not tag.firing_trigger_id and not sequenced:
            yield Finding("tag-never-fires", Severity.WARNING, node, "it has no firing trigger")


@check
def unmodelled_fields(graph: ContainerGraph) -> Iterator[Finding]:
    for node in graph.nodes:
        if node.resource is None:
            continue
        for path in _extras(node.resource):
            message = f"{path} is not in GTM's API schema; it is kept as-is but not checked"
            yield Finding("unmodelled-field", Severity.WARNING, node, message)


def _extras(model: GtmModel, path: str = "") -> Iterator[str]:
    for key in model.unmodelled_fields:
        yield f"{path}.{key}" if path else key
    for name, info in type(model).model_fields.items():
        value = getattr(model, name)
        here = f"{path}.{info.alias}" if path else info.alias
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, GtmModel):
                yield from _extras(item, here)


@check
def unmodelled_enums(graph: ContainerGraph) -> Iterator[Finding]:
    for node in graph.nodes:
        if node.resource is None:
            continue
        for path, value in _unknown_values(node.resource):
            hint = _suggest(value.value, (m.export_value for m in type(value)))
            message = (
                f"{path} {value.value} isn't in Tiggy's copy of GTM's API schema{hint}; it is kept as-is"
            )
            yield Finding("unmodelled-enum", Severity.WARNING, node, message)


def _unknown_values(model: GtmModel, path: str = "") -> Iterator[tuple[str, GtmEnum]]:
    for name, info in type(model).model_fields.items():
        value = getattr(model, name)
        here = f"{path}.{info.alias}" if path else info.alias
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, GtmEnum) and not item.known:
                yield here, item
            elif isinstance(item, GtmModel):
                yield from _unknown_values(item, here)


@spec_check
def literal_secrets(graph: ContainerGraph) -> Iterator[Finding]:
    """A credential written into the spec, where every clone and fork of the repository keeps it."""
    constants = {n.name: n for n in graph.of_kind(Kind.VARIABLE) if isinstance(n.resource, Variable)}
    flagged = set()
    for node in graph.nodes:
        for param in _params(node.resource):
            value = param.value
            if (
                not secret_bearing(param.key)
                or not isinstance(value, str)
                or not value
                or is_placeholder(value)
            ):
                continue
            whole = value[2:-2] if value.startswith("{{") and value.endswith("}}") else None
            if whole in constants and constants[whole] not in flagged and _literal(constants[whole].resource):
                flagged.add(constants[whole])
                message = (
                    f'its value is the secret {node.kind} "{node.name}" sends as {param.key}, in plain text'
                )
                yield Finding("literal-secret", Severity.WARNING, constants[whole], f"{message}; {HOW}")
            elif "{{" not in value:
                yield Finding(
                    "literal-secret", Severity.WARNING, node, f"{param.key} is in plain text; {HOW}"
                )


HOW = "reference it as {$secret: name} and keep the value in a secret store"


def _literal(variable: Variable) -> bool:
    return variable.type == "c" and any(
        p.key == "value" and isinstance(p.value, str) and p.value and not is_placeholder(p.value)
        for p in variable.parameter or []
    )


def _params(model: GtmModel | None) -> Iterator[Parameter]:
    if model is None:
        return
    for name in type(model).model_fields:
        value = getattr(model, name)
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, Parameter):
                yield item
            if isinstance(item, GtmModel):
                yield from _params(item)
