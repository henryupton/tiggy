"""GTM container exports: the JSON from Admin > Export Container."""

import json
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from tiggy.gtm.base import GtmModel
from tiggy.gtm.resources import ContainerVersion


class ExportError(Exception):
    pass


class ContainerExport(GtmModel):
    export_format_version: int | float = Field(alias="exportFormatVersion")
    export_time: str | None = Field(None, alias="exportTime")
    container_version: ContainerVersion = Field(alias="containerVersion")


def load(path: Path) -> ContainerExport:
    try:
        return loads(path.read_text())
    except OSError as e:
        raise ExportError(f"{path}: {e.strerror}") from e
    except ExportError as e:
        raise ExportError(f"{path}: {e}") from e


def loads(text: str) -> ContainerExport:
    try:
        return ContainerExport.model_validate_json(text)
    except ValidationError as e:
        problems = "\n".join(f"  {'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())
        raise ExportError(f"not a GTM container export:\n{problems}") from e


def dump(export: ContainerExport) -> dict[str, Any]:
    """The export as GTM writes it: only the fields it had, enums in UPPER_SNAKE."""
    return export.model_dump(mode="json", by_alias=True, exclude_unset=True, context={"casing": "export"})


def dumps(export: ContainerExport) -> str:
    return json.dumps(dump(export), indent=4, ensure_ascii=False) + "\n"
