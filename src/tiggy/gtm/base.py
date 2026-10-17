"""Foundations shared by every generated GTM resource model."""

import re
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, GetCoreSchemaHandler, SerializationInfo
from pydantic_core import core_schema

Casing = Literal["api", "export"]

# Exports keep the underscore lowerCamel drops before a digit only sometimes, so every enum value with a
# digit is spelled out, from Google's 2019 discovery doc (which still used the export spelling).
Int32 = Annotated[int, Field(ge=-(2**31), le=2**31 - 1)]

DIGIT_SPELLINGS = {
    "androidSdk5": "ANDROID_SDK_5",
    "iosSdk5": "IOS_SDK_5",
    "firebaseEventParameterCampaignCp1": "FIREBASE_EVENT_PARAMETER_CAMPAIGN_CP1",
}


class GtmModel(BaseModel):
    """A GTM resource.

    Unknown fields are kept rather than rejected, so nothing a newer GTM sends is lost on a
    round trip; `unmodelled_fields` surfaces them.
    """

    model_config = ConfigDict(extra="allow", validate_assignment=True)

    @property
    def unmodelled_fields(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


def export_spelling(value: str) -> str:
    """The UPPER_SNAKE spelling container exports use for an API lowerCamel enum value."""
    return DIGIT_SPELLINGS.get(value) or re.sub(r"(?<!^)(?=[A-Z])", "_", value).upper()


class GtmEnum(StrEnum):
    """A GTM enum. Values are the API's lowerCamel; container exports use UPPER_SNAKE.

    Either exact spelling parses; nothing else does. Serialising with `context={"casing": "export"}`
    writes UPPER_SNAKE.
    """

    @classmethod
    def _missing_(cls, value: object) -> "GtmEnum | None":
        return next((m for m in cls if m.export_value == value), None)

    @property
    def export_value(self) -> str:
        return export_spelling(self.value)

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
