"""A loaded spec to a ContainerVersion, giving every object a fresh ID.

IDs are synthetic and avoid every raw ID a dangling reference keeps, so a broken reference can never
start resolving to something by accident.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from tiggy.gtm.resources import Container, ContainerVersion
from tiggy.spec.codec import Codec
from tiggy.spec.convert import from_spec
from tiggy.spec.loader import KINDS, LoadedSpec
from tiggy.spec.overlay import COLLECTIONS, OWN_ID, fields
from tiggy.spec.problems import Origin, Problem, SpecError
from tiggy.spec.refs import UNRESOLVED, CompileRefs, Template

ACCOUNT_ID = "0"
BY_SPEC_KEY = {key: (alias, model) for alias, (key, model, _) in COLLECTIONS.items()}


@dataclass
class Compiled:
    version: ContainerVersion
    origins: dict[tuple[str, str], Origin]  # (spec kind key, identity) -> where it is defined
    warnings: list[Problem] = field(default_factory=list)
    id_origins: dict[tuple[str, str], Origin] = field(default_factory=dict)  # (spec kind key, compiled ID)
    labels: dict[str, str] = field(default_factory=dict)  # gtag config compiled ID -> its spec label


@dataclass
class Checked:
    """A spec compiled as far as its problems allow, so validate reports everything in one run."""

    problems: list[Problem]
    compiled: Compiled | None = None  # None when the problems leave no container to check
    warnings: list[Problem] = field(default_factory=list)  # stray files, known once every $file is read


def compile_spec(spec: LoadedSpec) -> Compiled:
    checked = check_spec(spec)
    if checked.problems or checked.compiled is None:
        raise SpecError(checked.problems)
    return checked.compiled


def check_spec(spec: LoadedSpec) -> Checked:
    problems = spec.problems
    if problems.problems:
        # Resolving names against a spec missing a fragment would only add false "not defined" errors.
        return Checked(list(problems.problems))
    reserved = _reserved(spec)
    container_id = _container_id(reserved["cvt"] | _gallery_types(spec))
    refs = CompileRefs(problems, container_id)
    codec = Codec(trigger_id=refs.trigger_id, read_file=spec.read_file, problems=problems)

    ids: dict[tuple[str, str], str] = {}
    for key, entries in spec.collections.items():
        model = BY_SPEC_KEY[key][1]
        if model not in OWN_ID:
            continue
        taken, next_id = reserved.get(key, set()), 1
        for entry in entries:
            while str(next_id) in taken:
                next_id += 1
            ids[key, entry.node[KINDS[key]]] = str(next_id)
            next_id += 1
    refs.triggers = {name: i for (key, name), i in ids.items() if key == "triggers"}
    refs.folders = {name: i for (key, name), i in ids.items() if key == "folders"}
    for entry in spec.collections.get("custom_templates", []):
        gallery = entry.node.get("gallery_reference")
        gallery_id = gallery.get("gallery_template_id") if isinstance(gallery, dict) else None
        name = entry.node["name"]
        refs.templates[name] = Template(name, container_id, ids["custom_templates", name], gallery_id)

    stamp = {"accountId": ACCOUNT_ID, "containerId": container_id}
    version: dict[str, Any] = {**stamp, "containerVersionId": "0"}
    if "version" in spec.singletons:
        entry = spec.singletons["version"]
        version.update(from_spec(ContainerVersion, entry.node, entry.origin, refs, codec, problems))
    if "container" in spec.singletons:
        entry = spec.singletons["container"]
        version["container"] = {
            **stamp,
            **from_spec(Container, entry.node, entry.origin, refs, codec, problems),
        }

    origins: dict[tuple[str, str], Origin] = {}
    id_origins: dict[tuple[str, str], Origin] = {}
    for key, entries in spec.collections.items():
        alias, model = BY_SPEC_KEY[key]
        items = []
        for entry in entries:
            identity = entry.node[KINDS[key]]
            origins[key, identity] = entry.origin
            if (key, identity) in ids:
                id_origins[key, ids[key, identity]] = entry.origin
            if key == "built_in_variables" and not isinstance(entry.node.get("name"), str):
                problems.add(
                    entry.origin, "a built-in variable needs its GTM name too, such as `name: Page URL`"
                )
            item = {**stamp, **from_spec(model, entry.node, entry.origin, refs, codec, problems)}
            if model in OWN_ID:
                item[OWN_ID[model]] = ids[key, identity]
            items.append(item)
        version[alias] = items

    warnings = spec.unreferenced()
    found = list(problems.problems)
    try:
        # Strict, from JSON, exactly as an export is read: nothing in a spec is coerced on the way.
        compiled = ContainerVersion.model_validate_json(json.dumps(_resolved(version)), strict=True)
    except ValidationError as e:
        # Alongside other problems, most likely their consequences, so not reported twice.
        return Checked(found or [_located(spec, err) for err in e.errors()], None, warnings)
    labels = {i: label for (key, label), i in ids.items() if key == "gtag_configs"}
    return Checked(found, Compiled(compiled, origins, warnings, id_origins, labels), warnings)


def _resolved(node: Any) -> Any:
    """`node` without the references that failed, already reported, so checks don't report them again."""
    if isinstance(node, dict):
        return {k: _resolved(v) for k, v in node.items() if v != UNRESOLVED}
    if isinstance(node, list):
        return [_resolved(v) for v in node if v != UNRESOLVED]
    return node


def _located(spec: LoadedSpec, err: dict[str, Any]) -> Problem:
    """A model error at the spec object it came from, with the path spelled as the spec spells it."""
    loc = list(err["loc"])
    origin, model = None, None
    if loc and loc[0] in COLLECTIONS and len(loc) > 1 and isinstance(loc[1], int):
        key, model, _ = COLLECTIONS[loc[0]]
        entries = spec.collections.get(key, [])
        if loc[1] < len(entries):
            origin, loc = entries[loc[1]].origin, loc[2:]
    elif loc and loc[0] == "container" and "container" in spec.singletons:
        origin, model, loc = spec.singletons["container"].origin, Container, loc[1:]
    if model and loc and isinstance(loc[0], str):
        loc[0] = next((f.key for f in fields(model) if f.alias == loc[0]), loc[0])
    path = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in loc).lstrip(".")
    return Problem(origin, f"{path}: {_message(err)}" if path else _message(err))


def _message(err: dict[str, Any]) -> str:
    ctx = err.get("ctx") or {}
    if err["type"] == "less_than_equal":
        return f"must be at most {ctx['le']}, got {err['input']!r}"
    if err["type"] == "greater_than_equal":
        return f"must be at least {ctx['ge']}, got {err['input']!r}"
    return err["msg"]


def _reserved(spec: LoadedSpec) -> dict[str, set[str]]:
    """Raw IDs that dangling references keep, per kind, and every raw `cvt_` type string."""
    found: dict[str, set[str]] = {"triggers": set(), "folders": set(), "cvt": set()}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if set(node) == {"trigger_id"} and isinstance(node["trigger_id"], str):
                found["triggers"].add(node["trigger_id"])
            if set(node) == {"folder_id"} and isinstance(node["folder_id"], str):
                found["folders"].add(node["folder_id"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str) and node.startswith("cvt_"):
            found["cvt"].add(node)

    for entries in spec.collections.values():
        for entry in entries:
            walk(entry.node)
    return found


def _gallery_types(spec: LoadedSpec) -> set[str]:
    """`cvt_<gallery id>` for every template, so no container-form type can spell one of them."""
    found = set()
    for entry in spec.collections.get("custom_templates", []):
        gallery = entry.node.get("gallery_reference")
        gallery_id = gallery.get("gallery_template_id") if isinstance(gallery, dict) else None
        if isinstance(gallery_id, str):
            found.add(f"cvt_{gallery_id}")
    return found


def _container_id(raw_types: set[str]) -> str:
    candidate = 1
    while any(t.startswith(f"cvt_{candidate}_") for t in raw_types):
        candidate += 1
    return str(candidate)
