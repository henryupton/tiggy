"""One GTM object between its export-shaped JSON and its spec mapping, driven by overlay.py."""

import re
from difflib import get_close_matches
from typing import Any, get_args, get_origin

from tiggy.gtm.base import UNKNOWN, GtmEnum, GtmModel
from tiggy.spec.codec import Codec, Unwritable
from tiggy.spec.overlay import SPEC_ONLY_KEYS, UNMODELLED, Role, fields
from tiggy.spec.problems import Collector, Origin, at, at_item, describe
from tiggy.spec.refs import CompileRefs, ImportRefs
from tiggy.spec.yamlio import Map

SKIPPED = (Role.DROPPED, Role.COLLECTION, Role.CONTAINER)
EXPECTED = {str: "a string", int: "an integer", bool: "true or false", float: "a number"}


def to_spec(model: type[GtmModel], data: dict[str, Any], refs: ImportRefs, codec: Codec) -> Map:
    out = Map()
    for f in fields(model):
        if f.role in SKIPPED or f.alias not in data:
            continue
        value = data[f.alias]
        try:
            if value is None and f.role is Role.PARAMETER:
                # YAML null is already the shorthand for a TEMPLATE parameter with no value.
                raise Unwritable("it is null, which the spec can't express yet")
            out[f.key] = None if value is None else _to_spec_value(f, value, refs, codec)
        except Unwritable as e:
            raise e.within(f.key) from None
    known = {f.alias for f in fields(model)}
    extras = {k: v for k, v in data.items() if k not in known}
    if extras:
        out[UNMODELLED] = extras
    return out


def _to_spec_value(f, value: Any, refs: ImportRefs, codec: Codec) -> Any:
    match f.role:
        case Role.TRIGGER_REFS:
            return [refs.trigger_ref(i) for i in value]
        case Role.FOLDER_REF:
            return refs.folder_ref(value)
        case Role.TYPE_REF:
            return refs.type_ref(value)
        case Role.TYPE_REFS:
            return [refs.type_ref(v) for v in value]
        case Role.PARAMETER:
            return codec.encode(value, keyed=False)
        case Role.PARAMETER_LIST:
            return codec.encode_list(value)
        case Role.NESTED:
            return to_spec(f.model, value, refs, codec)
        case Role.NESTED_LIST:
            return [_nested(f.model, i, item, refs, codec) for i, item in enumerate(value)]
    return value


def _nested(model: type[GtmModel], index: int, item: Any, refs: ImportRefs, codec: Codec) -> Map:
    try:
        return to_spec(model, item, refs, codec)
    except Unwritable as e:
        raise e.within(index) from None


def from_spec(
    model: type[GtmModel], node: Any, origin: Origin, refs: CompileRefs, codec: Codec, problems: Collector
) -> dict[str, Any]:
    if not isinstance(node, dict):
        problems.add(at(origin, node), f"a {_noun(model)} is a mapping of fields, got {describe(node)}")
        return {}
    known = {f.key: f for f in fields(model) if f.role not in SKIPPED}
    out: dict[str, Any] = {}
    for key, value in node.items():
        here = at(origin, node, key)
        if key.startswith("x-") or key in SPEC_ONLY_KEYS.get(model, ()):
            continue
        if key == UNMODELLED:
            if not isinstance(value, dict):
                problems.add(here, "unmodelled holds a mapping of GTM fields")
                continue
            for extra, item in value.items():
                owner = _owner(model, extra)
                if owner:
                    problems.add(
                        at(here, value, extra),
                        f"unmodelled is for fields Tiggy doesn't know, and {extra!r} is {owner}",
                    )
                else:
                    out[extra] = item
            continue
        f = known.get(key)
        if f is None and key in _gtm_owned(model):
            problems.add(here, f"{key} isn't written in a spec: GTM sets it, and Tiggy compares without it")
            continue
        if f is None:
            hint = get_close_matches(key, list(known), n=1)
            problems.add(
                here,
                f"unknown key {key!r} for a {_noun(model)}"
                + (f' (did you mean "{hint[0]}"?)' if hint else ""),
            )
            continue
        out[f.alias] = _from_spec_value(f, value, here, refs, codec, problems)
    return out


def _from_spec_value(
    f, value: Any, origin: Origin, refs: CompileRefs, codec: Codec, problems: Collector
) -> Any:
    if value is None and f.role is not Role.PARAMETER:
        return None
    match f.role:
        case Role.TRIGGER_REFS:
            return [refs.trigger_id(v, at_item(origin, value, i)) for i, v in _items(value, origin, problems)]
        case Role.FOLDER_REF:
            return refs.folder_id(value, origin)
        case Role.TYPE_REF:
            return refs.type_string(value, origin)
        case Role.TYPE_REFS:
            return [
                refs.type_string(v, at_item(origin, value, i)) for i, v in _items(value, origin, problems)
            ]
        case Role.PARAMETER:
            return codec.decode(value, None, origin)
        case Role.PARAMETER_LIST:
            return codec.decode_list(value, origin)
        case Role.NESTED:
            return from_spec(f.model, value, origin, refs, codec, problems)
        case Role.NESTED_LIST:
            return [
                from_spec(f.model, v, at_item(origin, value, i), refs, codec, problems)
                for i, v in _items(value, origin, problems)
            ]
        case Role.CODE if isinstance(value, dict) and set(value) == {"$file"}:
            return codec.read(value, origin)
    _check_scalar(value, f.annotation, f.key, origin, problems)
    _check_bounds(value, f.bounds, f.key, origin, problems)
    return value


def _check_bounds(value: Any, bounds: tuple[Any, Any], key: str, origin: Origin, problems: Collector) -> None:
    """Caught here, not by pydantic after compiling, so it's reported alongside every other problem."""
    if type(value) is not int:
        return
    ge, le = bounds
    if ge is not None and value < ge:
        problems.add(origin, f"{key} must be at least {ge}, got {value}")
    if le is not None and value > le:
        problems.add(origin, f"{key} must be at most {le}, got {value}")


def _items(value: Any, origin: Origin, problems: Collector) -> list[tuple[int, Any]]:
    if isinstance(value, list):
        return list(enumerate(value))
    problems.add(origin, f"expected a list, got {describe(value)}")
    return []


def _check_scalar(value: Any, annotation: Any, key: str, origin: Origin, problems: Collector) -> None:
    if value is None or annotation is Any:
        return
    if get_origin(annotation) is list:
        if not isinstance(value, list):
            problems.add(origin, f"{key} must be a list")
            return
        for i, item in enumerate(value):
            here = at_item(origin, value, i)
            if item is None:
                problems.add(here, f"{key} items can't be null")
            else:
                _check_scalar(item, get_args(annotation)[0], key, here, problems)
        return
    if isinstance(annotation, type) and issubclass(annotation, GtmEnum):
        # Any other UPPER_SNAKE value is one newer than the vendored schema; validate warns about it.
        api = next((m for m in annotation if m.value == value and m.value != m.export_value), None)
        if api is not None:
            problems.add(origin, f"{key}: {value} is the API's spelling; a spec writes {api.export_value}")
        elif not isinstance(value, str) or not UNKNOWN.fullmatch(value):
            allowed = ", ".join(m.export_value for m in annotation)
            problems.add(origin, f"{key}: {describe(value)} is not one of {allowed}")
        return
    expected = {str: str, int: int, bool: bool, float: (int, float)}.get(annotation)
    if expected is None:
        return
    if isinstance(value, bool) and annotation is not bool or not isinstance(value, expected):
        problems.add(origin, f"{key} must be {EXPECTED[annotation]}, got {describe(value)}")


def _gtm_owned(model: type[GtmModel]) -> set[str]:
    return {f.key for f in fields(model) if f.role is Role.DROPPED}


def _noun(model: type[GtmModel]) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", model.__name__).lower()


def _owner(model: type[GtmModel], name: str) -> str | None:
    """Who sets a field `unmodelled` names, when it's one Tiggy models (by GTM or Python name)."""
    aliases = {python: info.alias for python, info in model.model_fields.items()}
    alias = name if name in aliases.values() else aliases.get(name)
    f = next((f for f in fields(model) if f.alias == alias), None)
    if f is None:
        return None
    if f.role is Role.DROPPED:
        return "assigned by Tiggy, not written in a spec"
    if f.role in (Role.COLLECTION, Role.CONTAINER):
        return f"a top-level section, `{f.key}`"
    return f"written as `{f.key}`"
