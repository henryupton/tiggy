"""Generate src/tiggy/gtm/resources.py from the Tag Manager API v2 discovery document.

    uv run python scripts/generate_resources.py            # from the vendored document
    uv run python scripts/generate_resources.py --refresh  # fetch the latest first

Emits one Pydantic model per resource reachable from ContainerVersion, and one GtmEnum per
enumerated field. Never edit the output by hand; change this script or refresh the document.
"""

import argparse
import json
import keyword
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DISCOVERY = ROOT / "schema" / "tagmanager-v2.discovery.json"
OUTPUT = ROOT / "src" / "tiggy" / "gtm" / "resources.py"
DISCOVERY_URL = "https://tagmanager.googleapis.com/$discovery/rest?version=v2"
ROOTS = ["ContainerVersion"]
SHADOWED = {"list", "map", "dict", "set", "str", "int", "float", "bool"}


def snake(name: str) -> str:
    out = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    if keyword.iskeyword(out) or out in SHADOWED:
        out += "_"
    return out


def upper_snake(value: str) -> str:
    out = re.sub(r"(?<!^)(?=[A-Z])", "_", value).upper()
    return out if out.isidentifier() else f"V_{out}"


def cap(name: str) -> str:
    return name[0].upper() + name[1:]


def enum_name(schema: str, prop: str) -> str:
    if prop == "type":
        return f"{schema}Type"
    if prop.lower().startswith(schema.lower()):
        return cap(prop)
    return f"{schema}{cap(prop)}"


def closure(schemas: dict) -> list[str]:
    seen: list[str] = []
    todo = list(ROOTS)
    while todo:
        name = todo.pop(0)
        if name in seen:
            continue
        seen.append(name)
        for prop in schemas[name].get("properties", {}).values():
            for node in (prop, prop.get("items", {})):
                if "$ref" in node:
                    todo.append(node["$ref"])
    return seen


def doc(text: str | None) -> str:
    return " ".join((text or "").split())


class Generator:
    def __init__(self, discovery: dict):
        self.discovery = discovery
        self.schemas = discovery["schemas"]
        self.enums: dict[str, list[tuple[str, str]]] = {}

    def annotation(self, schema: str, prop: str, spec: dict) -> str:
        if "$ref" in spec:
            return spec["$ref"]
        kind = spec.get("type")
        if kind == "array":
            return f"list[{self.annotation(schema, prop, spec['items'])}]"
        if kind == "string" and "enum" in spec:
            name = enum_name(schema, prop)
            self.enums.setdefault(
                name, list(zip(spec["enum"], spec.get("enumDescriptions", [""] * len(spec["enum"]))))
            )
            return name
        if kind == "object" and isinstance(spec.get("additionalProperties"), dict):
            return f"dict[str, {self.annotation(schema, prop, spec['additionalProperties'])}]"
        return {"string": "str", "integer": "int", "boolean": "bool", "number": "float"}.get(kind, "Any")

    def model(self, name: str) -> str:
        spec = self.schemas[name]
        lines = [f"class {name}(GtmModel):", f"    {doc(spec.get('description')) or name!r}", ""]
        for prop, pspec in spec.get("properties", {}).items():
            ann = self.annotation(name, prop, pspec)
            lines.append(
                f"    {snake(prop)}: {ann} | None = Field(None, alias={prop!r}, "
                f"description={doc(pspec.get('description'))!r})"
            )
        return "\n".join(lines)

    def enum(self, name: str, members: list[tuple[str, str]]) -> str:
        lines = [f"class {name}(GtmEnum):"]
        for value, description in members:
            lines.append(f"    {upper_snake(value)} = {value!r}")
            if description:
                lines.append(f"    {doc(description)!r}")
        return "\n".join(lines)

    def render(self) -> str:
        names = closure(self.schemas)
        models = [self.model(n) for n in names]
        enums = [self.enum(n, m) for n, m in sorted(self.enums.items())]
        revision = self.discovery.get("revision")
        header = (
            f'"""GTM resource models, generated from the Tag Manager API v2 discovery document '
            f"(revision {revision}).\n\nDo not edit: run scripts/generate_resources.py.\n" + '"""'
        )
        uses_any = any(re.search(r"\bAny\b", m) for m in models)
        imports = (
            "from __future__ import annotations\n\n"
            + ("from typing import Any\n\n" if uses_any else "")
            + "from pydantic import Field\n\nfrom tiggy.gtm.base import GtmEnum, GtmModel"
        )
        rebuild = "\n".join(f"{n}.model_rebuild()" for n in names)
        return "\n\n\n".join([header, imports, *enums, *models, rebuild]) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="fetch the latest discovery document")
    args = parser.parse_args()
    if args.refresh:
        with urllib.request.urlopen(DISCOVERY_URL) as response:
            DISCOVERY.write_bytes(response.read())
    OUTPUT.write_text(Generator(json.loads(DISCOVERY.read_text())).render())
    subprocess.run([sys.executable, "-m", "ruff", "format", "--quiet", str(OUTPUT)], check=True)
    print(f"wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
