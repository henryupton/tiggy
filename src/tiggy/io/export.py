"""GTM container exports: the JSON from Admin > Export Container."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from tiggy.gtm.base import GtmModel, export_spelled, export_spelling
from tiggy.gtm.resources import ContainerVersion
from tiggy.spec.problems import difference


class ExportError(Exception):
    pass


class ContainerExport(GtmModel):
    export_format_version: int | float = Field(alias="exportFormatVersion")
    export_time: str | None = Field(None, alias="exportTime")
    container_version: ContainerVersion = Field(alias="containerVersion")


ENVELOPE = {"exportFormatVersion", "exportTime", "containerVersion"}


@dataclass(frozen=True)
class Source:
    """An export both as Tiggy reads it and as written, so nothing read can pass unnoticed.

    `raw` is the JSON exactly as written, bar enum values the Tag Manager API spells lowerCamel, which
    read as the UPPER_SNAKE an export from the GTM UI uses.
    """

    export: ContainerExport
    raw: dict[str, Any]

    @property
    def outside_container(self) -> list[str]:
        """Envelope fields that aren't GTM's own, read from the raw JSON since pydantic drops some."""
        return sorted(set(self.raw) - ENVELOPE)

    @property
    def raw_version(self) -> dict[str, Any]:
        return self.raw["containerVersion"]

    def misread(self) -> str | None:
        """Where Tiggy's reading differs from the JSON as written, such as a key pydantic drops."""
        read = dump(self.export)["containerVersion"]
        return difference(self.raw_version, read, sides=("the export", "Tiggy's reading"))


def load(path: Path) -> ContainerExport:
    return read(path).export


def read(path: Path) -> Source:
    try:
        return reads(path.read_bytes().decode("utf-8"))
    except OSError as e:
        raise ExportError(f"{path}: {e.strerror}") from e
    except UnicodeDecodeError as e:
        raise ExportError(f"{path}: not UTF-8 JSON") from e
    except ExportError as e:
        raise ExportError(f"{path}: {e}") from e


def loads(text: str) -> ContainerExport:
    return reads(text).export


def reads(text: str) -> Source:
    try:
        raw = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
    except ValueError as e:
        raise ExportError(f"not valid JSON: {e}") from e
    except RecursionError as e:
        raise ExportError("not valid JSON: nested too deeply") from e
    if not isinstance(raw, dict):
        raise ExportError("not a GTM container export: the top level isn't an object")
    if isinstance(raw.get("containerVersion"), dict):
        raw["containerVersion"] = export_spelled(ContainerVersion, raw["containerVersion"])
    try:
        # Strict: a value GTM would never write ("true" for true, 5.0 for 5) is refused, not coerced.
        export = ContainerExport.model_validate_json(text, strict=True)
    except ValidationError as e:
        problems = "\n".join(
            f"  {'.'.join(str(p) for p in err['loc'])}: {_message(err)}" for err in e.errors()
        )
        raise ExportError(f"not a GTM container export:\n{problems}") from e
    return Source(export, raw)


def dump(export: ContainerExport) -> dict[str, Any]:
    """The export as GTM writes it: only the fields it had, enums in UPPER_SNAKE."""
    return export.model_dump(mode="json", by_alias=True, exclude_unset=True, context={"casing": "export"})


def dumps(export: ContainerExport) -> str:
    return json.dumps(dump(export), indent=4, ensure_ascii=False) + "\n"


def _message(err: dict[str, Any]) -> str:
    """Pydantic's message, with enum choices spelled the way exports spell them."""
    if err["type"] == "enum":
        choices = re.findall(r"'([^']*)'", str(err.get("ctx", {}).get("expected", "")))
        return f"{err['input']!r} isn't one of {', '.join(export_spelling(c) for c in choices)}"
    return err["msg"]


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f'"{key}" appears twice in one object')
        out[key] = value
    return out


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} isn't JSON")
