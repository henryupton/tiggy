"""Where import puts things. Placement carries no meaning: any arrangement compiles to the same container."""

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from tiggy.spec.yamlio import Flow, Map

FOLDERED = ("variables", "triggers", "tags", "clients", "transformations")
SECTION_ORDER = (
    "container", "version", "built_in_variables", "folders", "custom_templates", "variables", "triggers",
    "tags", "clients", "transformations", "zones", "gtag_configs",
)  # fmt: skip


@dataclass
class SpecObject:
    kind: str  # the top-level spec key, e.g. "tags"
    node: Map
    code: list[tuple[str, str, str]] = field(
        default_factory=list
    )  # (parameter key or field, extension, body)

    @property
    def name(self) -> str:
        return str(self.node.get("name") or self.node.get("label") or self.node.get("type"))


@dataclass
class Plan:
    fragments: dict[str, dict[str, Any]] = field(default_factory=dict)  # path -> top-level mapping
    sidecars: dict[str, str] = field(default_factory=dict)  # path -> exact text


# Windows refuses these as file names whatever the extension, so `con.yml` can't be checked out there.
WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"{p}{n}" for p in ("com", "lpt") for n in range(1, 10))}


def slug(name: str, fallback: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    out = re.sub(r"[^a-z0-9]+", "-", ascii_).strip("-")[:80].strip("-")
    if out in WINDOWS_RESERVED:
        return f"{out}-{fallback}"
    return out or fallback


def _unique(names: list[str], fallback: str, taken: set[str] | None = None) -> dict[str, str]:
    """A slug per name, in name order, with -2, -3 for collisions."""
    used = set(taken or ())
    out = {}
    for name in sorted(names, key=lambda n: (n.casefold(), n)):
        base = slug(name, fallback)
        candidate, n = base, 2
        while candidate in used:
            candidate, n = f"{base}-{n}", n + 1
        used.add(candidate)
        out[name] = candidate
    return out


def plan(
    container: Map,
    version: Map | None,
    built_ins: list[Map] | None,
    objects: list[SpecObject],
    empty: list[str],
) -> Plan:
    result = Plan()
    sections: dict[str, dict[str, list[SpecObject]]] = defaultdict(lambda: defaultdict(list))

    folders = [o for o in objects if o.kind == "folders"]
    folder_dirs = {name: f"folders/{s}" for name, s in _unique([o.name for o in folders], "folder").items()}
    for folder in folders:
        sections[f"{folder_dirs[folder.name]}/folder.yml"]["folders"].append(folder)

    templates = [o for o in objects if o.kind == "custom_templates"]
    template_slugs = _unique([o.name for o in templates], "template")
    for template in templates:
        base = f"custom_templates/{template_slugs[template.name]}"
        sections[f"{base}.yml"]["custom_templates"].append(template)
        for field_, ext, body in template.code:
            path = f"{base}.{ext}"
            template.node[field_] = Flow({"$file": path})
            result.sidecars[path] = body

    for obj in objects:
        if obj.kind in FOLDERED:
            folder = obj.node.get("folder")
            directory = folder_dirs.get(folder, "unfiled") if isinstance(folder, str) else "unfiled"
            sections[f"{directory}/{obj.kind}.yml"][obj.kind].append(obj)
        elif obj.kind in ("zones", "gtag_configs"):
            sections[f"{obj.kind}.yml"][obj.kind].append(obj)

    for path, by_kind in sections.items():
        directory = path.rsplit("/", 1)[0] if "/" in path else ""
        for kind, items in by_kind.items():
            if kind != "gtag_configs":
                items.sort(key=lambda o: (o.name.casefold(), o.name))
        _place_code(
            directory,
            [o for items in by_kind.values() for o in items if o.kind != "custom_templates"],
            result,
        )
        fragment: dict[str, Any] = {"tiggy": 1}
        for kind in SECTION_ORDER:
            if kind in by_kind:
                fragment[kind] = [o.node for o in by_kind[kind]]
        result.fragments[path] = fragment

    main: dict[str, Any] = {"tiggy": 1, "container": container}
    if version:
        main["version"] = version
    if built_ins is not None:
        main["built_in_variables"] = built_ins
    for kind in SECTION_ORDER:
        if kind in empty:
            main[kind] = []
    result.fragments["container.yml"] = main
    return result


def _place_code(directory: str, objects: list[SpecObject], result: Plan) -> None:
    by_ext: dict[str, list[SpecObject]] = defaultdict(list)
    for obj in objects:
        for _, ext, _ in obj.code:
            by_ext[ext].append(obj)
    for ext, owners in by_ext.items():
        taken = {
            p.rsplit("/", 1)[-1].rsplit(".", 1)[0] for p in result.sidecars if p.startswith(f"{directory}/")
        }
        slugs = _unique([o.name for o in owners], "code", taken)
        for obj in owners:
            for key, code_ext, body in obj.code:
                if code_ext != ext:
                    continue
                path = f"{directory}/{slugs[obj.name]}.{ext}"
                obj.node["parameter"][key] = Flow({"$file": path})
                result.sidecars[path] = body
