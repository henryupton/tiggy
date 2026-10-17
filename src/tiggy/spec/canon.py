"""Container equivalence: equal unless something other than GTM-assigned identity differs.

`canon` drops identity and bookkeeping, rewrites every ID reference to what it refers to, and keys each
collection by name. Two containers are equivalent exactly when their canons are equal. Collections are
the only unordered part, because GTM orders them by the IDs this deliberately ignores.
"""

import json
from collections import Counter
from typing import Any

from tiggy.gtm import builtins
from tiggy.gtm.base import GtmModel
from tiggy.gtm.resources import Container, ContainerVersion
from tiggy.spec.overlay import COLLECTIONS, OWN_ID, Role, fields
from tiggy.spec.problems import difference
from tiggy.spec.refs import resolve_type, spellings, templates_of


def dump(version: ContainerVersion) -> dict[str, Any]:
    """A ContainerVersion as export-shaped JSON: only fields it has, enums in UPPER_SNAKE."""
    return version.model_dump(mode="json", by_alias=True, exclude_unset=True, context={"casing": "export"})


class CanonError(ValueError):
    """Two objects share an identity, so the container can't be compared by name."""


def canon(version: dict[str, Any]) -> dict[str, Any]:
    return _Canon(version).run()


def duplicates(version: dict[str, Any]) -> list[str]:
    """Objects that share a name (or type, for built-in variables), and IDs references can't tell apart."""
    found = []
    for alias, (key, _, identity) in COLLECTIONS.items():
        items = version.get(alias)
        if identity == "label" or not isinstance(items, list):
            continue
        field = "type" if identity == "type" else "name"
        counts = Counter(item.get(field) for item in items if isinstance(item, dict))
        found += [f"{counts[v]} {key} are {field}d {v!r}" for v in counts if counts[v] > 1]
    for alias, (key, model, _) in COLLECTIONS.items():
        id_field = OWN_ID.get(model)
        items = version.get(alias)
        if id_field is None or not isinstance(items, list):
            continue
        ids = Counter(
            i.get(id_field) for i in items if isinstance(i, dict) and isinstance(i.get(id_field), str)
        )
        found += [f"{ids[v]} {key} have {id_field} {v!r}" for v in ids if ids[v] > 1]
    for type_, matches in ambiguous_types(version).items():
        found.append(f"{type_} could mean custom template {' or '.join(repr(m) for m in matches)}")
    return found


def ambiguous_types(version: dict[str, Any]) -> dict[str, list[str]]:
    """`cvt_` types more than one custom template could be spelling, which no reader can untangle."""
    templates = templates_of(version)
    found = {}
    for alias, (_, model, _) in COLLECTIONS.items():
        for item in version.get(alias) or []:
            for type_ in _types(model, item):
                if isinstance(type_, str) and len(matches := spellings(type_, templates)) > 1:
                    found[type_] = sorted(t.name for t, _ in matches)
    return found


def _types(model: type[GtmModel], data: Any):
    """Every type string an object holds, nested ones (a zone's whitelist) included."""
    if not isinstance(data, dict):
        return
    for f in fields(model):
        value = data.get(f.alias)
        if f.role is Role.TYPE_REF:
            yield value
        elif f.role is Role.TYPE_REFS and isinstance(value, list):
            yield from value
        elif f.role is Role.NESTED:
            yield from _types(f.model, value)
        elif f.role is Role.NESTED_LIST and isinstance(value, list):
            for v in value:
                yield from _types(f.model, v)


def compare(
    a: dict[str, Any], b: dict[str, Any], sides: tuple[str, str] = ("the left", "the right")
) -> str | None:
    return difference(canon(a), canon(b), sides=sides)


class _Canon:
    def __init__(self, version: dict[str, Any]):
        problems = duplicates(version)
        if problems:
            raise CanonError("; ".join(problems))
        self.version = version
        self.triggers = {
            t["triggerId"]: t.get("name") for t in version.get("trigger") or [] if "triggerId" in t
        }
        self.folders = {f["folderId"]: f.get("name") for f in version.get("folder") or [] if "folderId" in f}
        self.templates = templates_of(version)

    def run(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in self.version.items():
            role = self._role(ContainerVersion, key)
            if role is Role.DROPPED:
                continue
            if role is Role.CONTAINER:
                out[key] = self.rewrite(Container, value)
            elif role is Role.COLLECTION:
                out[key] = self.collection(key, value)
            else:
                out[key] = value
        return out

    def collection(self, key: str, items: list[dict[str, Any]] | None) -> Any:
        if not isinstance(items, list):
            return items
        _, model, identity = COLLECTIONS[key]
        rewritten = [self.rewrite(model, item) for item in items]
        if identity == "label":
            return sorted(json.dumps(item, sort_keys=True) for item in rewritten)
        field = "type" if identity == "type" else "name"
        return {item.get(field) if isinstance(item, dict) else repr(item): item for item in rewritten}

    def rewrite(self, model: type[GtmModel], data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        roles = {f.alias: f for f in fields(model)}
        out: dict[str, Any] = {}
        for key, value in data.items():
            f = roles.get(key)
            if f is not None and f.role is Role.DROPPED:
                continue
            if f is None or value is None:
                out[key] = value
                continue
            match f.role:
                case Role.TRIGGER_REFS:
                    out[key] = _each(value, self.trigger)
                case Role.FOLDER_REF:
                    out[key] = (
                        ["folder", self.folders[value]]
                        if isinstance(value, str) and value in self.folders
                        else ["dangling", value]
                    )
                case Role.TYPE_REF:
                    out[key] = self.type_(value)
                case Role.TYPE_REFS:
                    out[key] = _each(value, self.type_)
                case Role.PARAMETER:
                    out[key] = self.parameter(value)
                case Role.PARAMETER_LIST:
                    out[key] = _each(value, self.parameter)
                case Role.NESTED:
                    out[key] = self.rewrite(f.model, value)
                case Role.NESTED_LIST:
                    out[key] = _each(value, lambda v: self.rewrite(f.model, v))
                case _:
                    out[key] = value
        return out

    def parameter(self, p: Any) -> Any:
        if not isinstance(p, dict):
            return p
        out = dict(p)
        if p.get("type") == "TRIGGER_REFERENCE" and isinstance(p.get("value"), str):
            out["value"] = self.trigger(p["value"])
        for field in ("list", "map"):
            if isinstance(p.get(field), list):
                out[field] = [self.parameter(c) for c in p[field]]
        return out

    def trigger(self, trigger_id: Any) -> Any:
        if not isinstance(trigger_id, str):
            return ["dangling", trigger_id]
        if trigger_id in self.triggers:
            return ["trigger", self.triggers[trigger_id]]
        if builtins.is_trigger_id(trigger_id):
            return ["built_in", trigger_id]
        return ["dangling", trigger_id]

    def type_(self, type_: Any) -> Any:
        if isinstance(type_, str) and type_.startswith("cvt_"):
            found = resolve_type(type_, self.templates)
            if found:
                template, form = found
                return ["template", template.name, form]
        return type_

    @staticmethod
    def _role(model: type[GtmModel], alias: str) -> Role | None:
        for f in fields(model):
            if f.alias == alias:
                return f.role
        return None


def _each(value: Any, fn) -> Any:
    return [fn(v) for v in value] if isinstance(value, list) else value
