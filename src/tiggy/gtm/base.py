"""Foundations shared by every generated GTM resource model."""

import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, GetCoreSchemaHandler, SerializationInfo
from pydantic_core import core_schema

Casing = Literal["api", "export"]


class GtmModel(BaseModel):
    """A GTM resource.

    Unknown fields are kept rather than rejected, so nothing a newer GTM sends is lost on a
    round trip; `unmodelled_fields` surfaces them.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True, validate_assignment=True)

    @property
    def unmodelled_fields(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


def _fold(value: str) -> str:
    return value.replace("_", "").upper()


class GtmEnum(StrEnum):
    """A GTM enum. Values are the API's lowerCamel; container exports use UPPER_SNAKE.

    Either spelling parses. Serialising with `context={"casing": "export"}` writes UPPER_SNAKE.
    """

    @classmethod
    def _missing_(cls, value: object) -> "GtmEnum | None":
        if isinstance(value, str):
            folded = _fold(value)
            for member in cls:
                if _fold(member.value) == folded:
                    return member
        return None

    @property
    def export_value(self) -> str:
        return re.sub(r"(?<!^)(?=[A-Z])", "_", self.value).upper()

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: type[Any], handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        schema = handler(source)
        schema["serialization"] = core_schema.plain_serializer_function_ser_schema(
            _serialise_enum, info_arg=True
        )
        return schema


def _serialise_enum(member: GtmEnum, info: SerializationInfo) -> str:
    if (info.context or {}).get("casing") == "export":
        return member.export_value
    return member.value
