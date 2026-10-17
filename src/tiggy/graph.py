"""How the objects in a container refer to each other.

The one place that knows GTM's reference rules: triggers by ID, sequenced tags and
`{{variables}}` by name, custom templates by type, folders by ID. Everything that reasons
about a container's structure (validation, apply ordering, code generation) reads this.
"""

import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from tiggy.gtm import builtins
from tiggy.gtm.base import GtmModel
from tiggy.gtm.resources import (
    BuiltInVariable,
    BuiltInVariableType,
    ContainerVersion,
    CustomTemplate,
    Parameter,
    ParameterType,
    Tag,
    Variable,
    Zone,
)

VARIABLE_REFERENCE = re.compile(r"\{\{([^{}]+)\}\}")


class Kind(StrEnum):
    TAG = "tag"
    TRIGGER = "trigger"
    VARIABLE = "variable"
    BUILT_IN_VARIABLE = "built-in variable"
    BUILT_IN_TRIGGER = "built-in trigger"
    FOLDER = "folder"
    TEMPLATE = "template"
    CLIENT = "client"
    TRANSFORMATION = "transformation"
    ZONE = "zone"
    GTAG_CONFIG = "gtag config"


# ContainerVersion field -> (kind, ID field). Order is display order.
COLLECTIONS: dict[str, tuple[Kind, str | None]] = {
    "tag": (Kind.TAG, "tag_id"),
    "trigger": (Kind.TRIGGER, "trigger_id"),
    "variable": (Kind.VARIABLE, "variable_id"),
    "built_in_variable": (Kind.BUILT_IN_VARIABLE, None),
    "custom_template": (Kind.TEMPLATE, "template_id"),
    "client": (Kind.CLIENT, "client_id"),
    "transformation": (Kind.TRANSFORMATION, "transformation_id"),
    "zone": (Kind.ZONE, "zone_id"),
    "gtag_config": (Kind.GTAG_CONFIG, "gtag_config_id"),
    "folder": (Kind.FOLDER, "folder_id"),
}

# Fields holding trigger IDs, per resource type.
TRIGGER_ID_FIELDS: dict[type[GtmModel], dict[str, str]] = {
    Tag: {"firing_trigger_id": "firing trigger", "blocking_trigger_id": "blocking trigger"},
    Variable: {"enabling_trigger_id": "enabling trigger", "disabling_trigger_id": "disabling trigger"},
}


@dataclass(frozen=True)
class Node:
    kind: Kind
    name: str
    id: str | None = None
    resource: GtmModel | None = field(default=None, compare=False, repr=False)

    def __str__(self) -> str:
        return f'{self.kind} "{self.name}"'


@dataclass(frozen=True)
class Reference:
    source: Node
    kind: Kind
    target: str
    by: Literal["id", "name", "type"]
    via: str
    resolved: Node | None


class ContainerGraph:
    def __init__(self, version: ContainerVersion, labels: dict[tuple[Kind, str], str] | None = None):
        """`labels` names objects GTM leaves unnamed (gtag configs), keyed by kind and ID."""
        self.version = version
        self.labels = labels or {}
        self.nodes: list[Node] = []
        self.references: list[Reference] = []
        self._by_id: dict[tuple[Kind, str], Node] = {}
        self._by_name: dict[tuple[Kind, str], Node] = {}
        self._templates: dict[str, Node] = {}
        self._incoming: dict[Node, list[Reference]] = defaultdict(list)
        self.built_ins_listed = version.built_in_variable is not None

        for attr, (kind, id_attr) in COLLECTIONS.items():
            for resource in getattr(version, attr) or []:
                self._add(kind, resource, id_attr)
        self._add_legacy_built_ins()
        for node in list(self.nodes):
            for ref in self._extract(node):
                self.references.append(ref)
                if ref.resolved:
                    self._incoming[ref.resolved].append(ref)

    def of_kind(self, kind: Kind) -> list[Node]:
        return [n for n in self.nodes if n.kind is kind]

    def find(self, kind: Kind, name: str) -> Node | None:
        return self._by_name.get((kind, name))

    def incoming(self, node: Node) -> list[Reference]:
        return self._incoming.get(node, [])

    def outgoing(self, node: Node) -> list[Reference]:
        return [r for r in self.references if r.source == node]

    def _add(self, kind: Kind, resource: GtmModel, id_attr: str | None) -> Node:
        node_id = getattr(resource, id_attr) if id_attr else None
        name = getattr(resource, "name", None)
        name = name if isinstance(name, str) else None  # unmodelled on some kinds, so any JSON value
        name = name or self.labels.get((kind, node_id or "")) or node_id
        if not name and isinstance(resource, BuiltInVariable) and resource.type:
            name = builtins.VARIABLE_NAMES.get(resource.type, resource.type.export_value)
        name = name or "(unnamed)"
        node = Node(kind, name, node_id, resource)
        self.nodes.append(node)
        if node_id:
            self._by_id.setdefault((kind, node_id), node)
        self._by_name.setdefault((kind, name), node)
        if isinstance(resource, CustomTemplate):
            self._templates[f"cvt_{resource.container_id}_{resource.template_id}"] = node
            gallery = resource.gallery_reference
            if gallery and gallery.gallery_template_id:
                self._templates[f"cvt_{gallery.gallery_template_id}"] = node
        return node

    def _add_legacy_built_ins(self) -> None:
        """Exports before format 2 list enabled built-ins as types on the container."""
        container = self.version.container
        legacy = container.unmodelled_fields.get("enabledBuiltInVariable") if container else None
        if legacy is None:
            return
        self.built_ins_listed = True
        for raw in legacy if isinstance(legacy, list) else [legacy]:
            try:
                built_in = BuiltInVariableType(raw)
            except ValueError:
                name, resource = str(raw), BuiltInVariable(name=str(raw))
            else:
                name = builtins.VARIABLE_NAMES.get(built_in, built_in.value)
                resource = BuiltInVariable(type=built_in, name=name)
            if (Kind.BUILT_IN_VARIABLE, name) not in self._by_name:
                self._add(Kind.BUILT_IN_VARIABLE, resource, None)

    def _extract(self, node: Node) -> Iterator[Reference]:
        resource = node.resource
        if resource is None:
            return
        for attr, via in TRIGGER_ID_FIELDS.get(type(resource), {}).items():
            for trigger_id in getattr(resource, attr) or []:
                yield self._ref(node, Kind.TRIGGER, trigger_id, "id", via)
        if isinstance(resource, Zone) and resource.boundary:
            for trigger_id in resource.boundary.custom_evaluation_trigger_id or []:
                yield self._ref(node, Kind.TRIGGER, trigger_id, "id", "boundary trigger")
        if isinstance(resource, Tag):
            for step, via in ((resource.setup_tag, "setup tag"), (resource.teardown_tag, "teardown tag")):
                for sequenced in step or []:
                    if sequenced.tag_name:
                        yield self._ref(node, Kind.TAG, sequenced.tag_name, "name", via)
        folder_id = getattr(resource, "parent_folder_id", None)
        if folder_id:
            yield self._ref(node, Kind.FOLDER, folder_id, "id", "folder")
        type_ = getattr(resource, "type", None)
        if isinstance(type_, str) and type_.startswith("cvt_"):
            yield self._ref(node, Kind.TEMPLATE, type_, "type", "type")
        for path, parameter in parameters(resource):
            yield from self._parameter_refs(node, path, parameter)

    def _parameter_refs(self, node: Node, path: str, parameter: Parameter) -> Iterator[Reference]:
        value = parameter.value
        if value is None:
            return
        if parameter.type is ParameterType.TRIGGER_REFERENCE:
            yield self._ref(node, Kind.TRIGGER, value, "id", path)
        elif parameter.type is ParameterType.TAG_REFERENCE:
            yield self._ref(node, Kind.TAG, value, "name", path)
        else:
            for name in VARIABLE_REFERENCE.findall(value):
                if not name.startswith("_"):
                    yield self._ref(node, Kind.VARIABLE, name, "name", path)

    def _ref(self, source: Node, kind: Kind, target: str, by: str, via: str) -> Reference:
        return Reference(source, kind, target, by, via, self._resolve(kind, target, by))

    def _resolve(self, kind: Kind, target: str, by: str) -> Node | None:
        if by == "type":
            return self._templates.get(target)
        if by == "id":
            found = self._by_id.get((kind, target))
            if found is None and kind is Kind.TRIGGER and builtins.is_trigger_id(target):
                found = self._built_in_trigger(target)
            return found
        if kind is Kind.VARIABLE:
            return self._by_name.get((Kind.VARIABLE, target)) or self._by_name.get(
                (Kind.BUILT_IN_VARIABLE, target)
            )
        return self._by_name.get((kind, target))

    def _built_in_trigger(self, trigger_id: str) -> Node:
        key = (Kind.BUILT_IN_TRIGGER, trigger_id)
        if key not in self._by_id:
            node = Node(Kind.BUILT_IN_TRIGGER, builtins.trigger_name(trigger_id), trigger_id)
            self.nodes.append(node)
            self._by_id[key] = node
        return self._by_id[key]


def parameters(model: GtmModel, path: str = "") -> Iterator[tuple[str, Parameter]]:
    """Every Parameter in a resource, wherever GTM puts one, with a readable path to it."""
    for name, info in type(model).model_fields.items():
        value = getattr(model, name)
        if value is None:
            continue
        here = f"{path}.{info.alias}" if path else info.alias
        if isinstance(value, Parameter):
            yield from _walk(value, here)
        elif isinstance(value, GtmModel):
            yield from parameters(value, here)
        elif isinstance(value, list):
            # A resource's own `parameter` list reads better as its keys alone.
            base = path if info.alias == "parameter" else here
            for i, item in enumerate(value):
                if isinstance(item, Parameter):
                    yield from _walk(item, _join(base, item.key, i))
                elif isinstance(item, GtmModel):
                    yield from parameters(item, f"{here}[{i}]")


def _walk(parameter: Parameter, path: str) -> Iterator[tuple[str, Parameter]]:
    yield path, parameter
    for i, child in enumerate(parameter.list_ or []):
        yield from _walk(child, f"{path}[{i}]")
    for i, child in enumerate(parameter.map_ or []):
        yield from _walk(child, _join(path, child.key, i))


def _join(base: str, key: str | None, index: int) -> str:
    if key:
        return f"{base}.{key}" if base else key
    return f"{base}[{index}]"
