"""GTM Parameters between their JSON shape and the spec's YAML shape, exact in both directions.

A Parameter is written in its readable form only when that form decodes back to exactly the same
Parameter; anything else uses the explicit `$type` form, which can express every Parameter. So
`decode(encode(p)) == p` holds for every input, checked at each node rather than argued.
"""

import re
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from tiggy.gtm.resources import Parameter, ParameterType
from tiggy.spec.problems import Collector, Origin, at, at_item, describe, difference, same
from tiggy.spec.yamlio import Flow, Map, is_canonical_int

FIELDS = {"type", "key", "value", "list", "map", "isWeakReference"}
TYPES = {t.export_value for t in ParameterType}
EXPLICIT_KEYS = {
    "$type",
    "$key",
    "$value",
    "$file",
    "$trigger",
    "$list",
    "$map",
    "$is_weak_reference",
    "$unmodelled",
}
_NO_SHORTHAND = object()


class Unwritable(ValueError):
    """A parameter the spec can't express exactly, with the path of fields, keys and indexes to it."""

    def __init__(self, detail: str, path: tuple[str | int, ...] = ()):
        self.detail, self.path = detail, path
        where = "".join(_segment(s) for s in path).lstrip(".") or "a parameter"
        super().__init__(f"{where} can't be written exactly yet: {detail}")

    def within(self, segment: str | int) -> "Unwritable":
        return Unwritable(self.detail, (segment, *self.path))


def _segment(s: str | int) -> str:
    if isinstance(s, int):
        return f"[{s}]"
    return f".{s}" if re.fullmatch(r"[\w-]+", s) else f"[{s!r}]"


@dataclass
class Codec:
    """Encoding needs trigger names for TRIGGER_REFERENCE values; decoding needs the reverse and files."""

    trigger_ref: Callable[[str], Any] = lambda trigger_id: {"trigger_id": trigger_id}
    trigger_id: Callable[[Any, Origin], str] | None = None
    read_file: Callable[[Any, Origin], str] | None = None
    problems: Collector | None = None
    # Each node is encoded once per call, though the readable and explicit forms both need its children.
    _memo: dict[tuple[int, bool], Any] | None = field(default=None, init=False, repr=False)

    # Encoding: GTM JSON to YAML

    def encode(self, p: dict[str, Any], keyed: bool) -> Any:
        with self._encoding():
            return self._keyed(p) if keyed else self._once(p, keyed=False)

    def encode_list(self, params: list[dict[str, Any]] | None) -> Any:
        """A `parameter:` list: a mapping by GTM key when every key is usable, else a sequence."""
        if params is None:
            return None
        with self._encoding():
            for i, p in enumerate(params):
                if not isinstance(p.get("key"), str):
                    raise Unwritable("it has no key", (i,))
            if _usable_keys(params):
                return Map((p["key"], self._keyed(p)) for p in params)
            return self._items(params)

    @contextmanager
    def _encoding(self):
        top = self._memo is None
        if top:
            self._memo = {}
        try:
            yield
        finally:
            if top:
                self._memo = None

    def _keyed(self, p: dict[str, Any]) -> Any:
        """A parameter written under its own key, in a mapping."""
        try:
            return self._once(p, keyed=True)
        except Unwritable as e:
            raise e.within(p["key"]) from None

    def _items(self, items: list[Any], by_key: bool = False) -> list[Any]:
        """A sequence of parameters, written in list form; a failure names the item."""
        out = []
        for i, item in enumerate(items):
            try:
                out.append(self._once(item, keyed=False))
            except Unwritable as e:
                key = item.get("key") if isinstance(item, dict) else None
                raise e.within(key if by_key and isinstance(key, str) else i) from None
        return out

    def _once(self, p: dict[str, Any], keyed: bool) -> Any:
        memo = self._memo if self._memo is not None else {}
        slot = (id(p), keyed)
        if slot not in memo:
            try:
                memo[slot] = self._encode(p, keyed)
            except Unwritable as e:
                memo[slot] = e
        found = memo[slot]
        if isinstance(found, Unwritable):
            raise found
        return found

    def _encode(self, p: dict[str, Any], keyed: bool) -> Any:
        key = p.get("key") if keyed else None
        try:
            candidate = self._shorthand(p, keyed)
        except ValueError:
            candidate = _NO_SHORTHAND
        if candidate is not _NO_SHORTHAND:
            try:
                if same(self.decode(candidate, key), p):
                    return candidate
            except ValueError:
                pass
        entries = p.get("map")
        if isinstance(entries, list):
            for i, entry in enumerate(entries):
                if not isinstance(entry, dict) or not isinstance(entry.get("key"), str):
                    raise Unwritable("a map entry with no key", (i,))
        explicit = self._explicit(p, keyed)
        try:
            found = difference(p, self.decode(explicit, key), sides=("GTM", "the spec"))
        except ValueError as e:
            found = str(e)
        if found:
            raise Unwritable(found)
        return explicit

    def _shorthand(self, p: dict[str, Any], keyed: bool) -> Any:
        if not keyed and "key" in p:
            return _NO_SHORTHAND
        kind, value = p.get("type"), p.get("value")
        if kind == "TEMPLATE" and "list" not in p and "map" not in p:
            return value if "value" in p else None
        if kind == "BOOLEAN" and value in ("true", "false"):
            return value == "true"
        if kind == "INTEGER" and isinstance(value, str) and is_canonical_int(value):
            return int(value)
        if kind == "LIST" and isinstance(p.get("list"), list) and "value" not in p:
            return self._items(p["list"])
        if kind == "MAP" and isinstance(p.get("map"), list) and "value" not in p and _usable_keys(p["map"]):
            return Map((c["key"], self._keyed(c)) for c in p["map"])
        return _NO_SHORTHAND

    def _explicit(self, p: dict[str, Any], keyed: bool) -> dict[str, Any]:
        node: dict[str, Any] = {"$type": p.get("type")}
        if "key" in p and not keyed:
            node["$key"] = p["key"]
        if "value" in p:
            if p.get("type") == "TRIGGER_REFERENCE" and isinstance(p["value"], str):
                node["$trigger"] = self.trigger_ref(p["value"])
            else:
                node["$value"] = p["value"]
        for member, key in (("list", "$list"), ("map", "$map")):
            if member in p:
                items = p[member]
                node[key] = None if items is None else self._items(items, by_key=member == "map")
        if "isWeakReference" in p:
            node["$is_weak_reference"] = p["isWeakReference"]
        extras = {k: v for k, v in p.items() if k not in FIELDS}
        if extras:
            node["$unmodelled"] = extras
        flat = all(not isinstance(v, dict | list) or isinstance(v, Flow) for v in node.values())
        return Flow(node) if flat else Map(node)

    # Decoding: YAML to GTM JSON

    def decode(self, node: Any, key: str | None, origin: Origin | None = None) -> dict[str, Any]:
        origin = origin or Origin("(spec)")
        p: dict[str, Any] = {} if key is None else {"key": key}
        if isinstance(node, bool):
            p.update(type="BOOLEAN", value="true" if node else "false")
        elif isinstance(node, int):
            p.update(type="INTEGER", value=str(node))
        elif isinstance(node, str):
            p.update(type="TEMPLATE", value=node)
        elif node is None:
            p.update(type="TEMPLATE")
        elif isinstance(node, list):
            p.update(
                type="LIST", list=[self.decode(x, None, at_item(origin, node, i)) for i, x in enumerate(node)]
            )
        elif isinstance(node, dict) and "$type" in node:
            return self._decode_explicit(node, key, at(origin, node))
        elif isinstance(node, dict) and set(node) == {"$file"}:
            p.update(type="TEMPLATE", value=self.read(node, at(origin, node)))
        elif isinstance(node, dict):
            for k in node:
                if k.startswith("$"):
                    self._problem(
                        at(origin, node, k), f"{k} needs $type alongside it (or use {{$file: path}} alone)"
                    )
            p.update(type="MAP", map=[self.decode(v, k, at(origin, node, k)) for k, v in node.items()])
        return p

    def decode_list(self, node: Any, origin: Origin) -> list[dict[str, Any]] | None:
        if node is None:
            return None
        if isinstance(node, dict):
            for k in node:
                if k.startswith("$"):
                    self._problem(
                        at(origin, node, k),
                        f"{k}: a GTM key can't start with $ here; write the parameters as a list with $key",
                    )
            return [self.decode(v, k, at(origin, node, k)) for k, v in node.items()]
        if isinstance(node, list):
            return self._keyed_items(node, origin, "a parameter in the list form needs $key")
        self._problem(origin, "parameter must be a mapping of GTM keys, or a list of explicit parameters")
        return []

    def _keyed_items(self, items: list[Any], origin: Origin, message: str) -> list[dict[str, Any]]:
        out = []
        for i, item in enumerate(items):
            here = at_item(origin, items, i)
            p = self.decode(item, None, here)
            if "key" not in p:
                self._problem(here, f"{message}; or did you mean a mapping of GTM keys?")
            out.append(p)
        return out

    def _decode_explicit(self, node: dict[str, Any], key: str | None, origin: Origin) -> dict[str, Any]:
        def problem(member: str, message: str) -> None:
            self._problem(at(origin, node, member), message)

        unknown = set(node) - EXPLICIT_KEYS
        if unknown:
            self._problem(
                origin, f"unknown explicit keys {sorted(unknown)}; allowed: {sorted(EXPLICIT_KEYS)}"
            )
        p: dict[str, Any] = {}
        kind = node["$type"]
        if kind is not None and (not isinstance(kind, str) or kind not in TYPES):
            problem(
                "$type", f"$type {describe(kind)} is not a GTM parameter type ({', '.join(sorted(TYPES))})"
            )
        elif kind is not None:
            p["type"] = kind
        if "$key" in node:
            if key is not None:
                problem("$key", "$key is only for parameters in a list; this one is keyed by its mapping")
            elif node["$key"] is not None and not isinstance(node["$key"], str):
                problem("$key", f"$key must be a string (or null), got {describe(node['$key'])}")
            p["key"] = node["$key"]
        elif key is not None:
            p["key"] = key
        sources = [k for k in ("$value", "$file", "$trigger") if k in node]
        if len(sources) > 1:
            self._problem(origin, f"use one of {sources}, not several")
        if "$value" in node:
            value = node["$value"]
            if value is not None and not isinstance(value, str):
                problem("$value", f"$value must be a string (or null), got {describe(value)}")
            elif kind == "TRIGGER_REFERENCE" and value is not None:
                problem(
                    "$value", "a TRIGGER_REFERENCE names its trigger with $trigger, not a raw ID in $value"
                )
            p["value"] = value
        elif "$file" in node:
            if kind == "TRIGGER_REFERENCE":
                problem("$file", "a TRIGGER_REFERENCE names its trigger with $trigger, not a file")
            p["value"] = self.read({"$file": node["$file"]}, at(origin, node, "$file"))
        elif "$trigger" in node:
            if kind != "TRIGGER_REFERENCE":
                problem("$trigger", "$trigger is only for $type: TRIGGER_REFERENCE")
            p["value"] = self._trigger(node["$trigger"], at(origin, node, "$trigger"))
        for member, name in (("$list", "list"), ("$map", "map")):
            if member not in node:
                continue
            items = node[member]
            if items is None:
                p[name] = None
            elif not isinstance(items, list):
                problem(member, f"{member} must be a list of parameters (or null), got {describe(items)}")
            elif member == "$map":
                p[name] = self._keyed_items(items, at(origin, node, member), "a $map entry needs $key")
            else:
                here = at(origin, node, member)
                p[name] = [self.decode(item, None, at_item(here, items, i)) for i, item in enumerate(items)]
        if "$is_weak_reference" in node:
            weak = node["$is_weak_reference"]
            if weak is not None and not isinstance(weak, bool):
                problem(
                    "$is_weak_reference",
                    f"$is_weak_reference must be true, false or null, got {describe(weak)}",
                )
            p["isWeakReference"] = weak
        if "$unmodelled" in node:
            extras = node["$unmodelled"]
            if not isinstance(extras, dict):
                problem("$unmodelled", "$unmodelled holds a mapping of GTM fields")
            else:
                clashes = sorted(set(extras) & (FIELDS | set(Parameter.model_fields)))
                if clashes:
                    problem("$unmodelled", f"$unmodelled can't set {clashes}; use the $ members")
                p.update({k: v for k, v in extras.items() if k not in FIELDS})
        return p

    def read(self, node: dict[str, Any], origin: Origin) -> str:
        """The text of a `{$file: path}` node."""
        if self.read_file is None:
            self._problem(origin, "$file can't be read here")
            return ""
        return self.read_file(node["$file"], origin)

    def _trigger(self, ref: Any, origin: Origin) -> str:
        if self.trigger_id is None:
            self._problem(origin, "trigger references can't be resolved here")
            return ""
        return self.trigger_id(ref, origin)

    def _problem(self, origin: Origin, message: str) -> None:
        if self.problems is None:  # encoding checks a node built in memory, so there's no file to name
            raise ValueError(message)
        self.problems.add(origin, message)


def _usable_keys(params: list[Any]) -> bool:
    """Whether parameters can be a mapping by GTM key: distinct strings, no `$` prefix, no merge key."""
    keys = [p.get("key") if isinstance(p, dict) else None for p in params]
    usable = all(isinstance(k, str) and not k.startswith("$") and k != "<<" for k in keys)
    return usable and len(set(keys)) == len(keys)
