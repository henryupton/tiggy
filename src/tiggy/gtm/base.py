"""Foundations shared by every generated GTM resource model."""

import re
import types
from enum import StrEnum
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, GetCoreSchemaHandler, SerializationInfo
from pydantic_core import core_schema

Casing = Literal["api", "export"]
Int32 = Annotated[int, Field(ge=-(2**31), le=2**31 - 1)]
UNKNOWN = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*")  # how an export would spell a value Tiggy doesn't know

# Exports keep the underscore lowerCamel drops before a digit only sometimes, so every enum value with a
# digit is spelled out, from Google's 2019 discovery doc (which still used the export spelling).
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

    Either exact spelling parses to a member. Any other UPPER_SNAKE value is one newer than the vendored
    schema: it parses to an unknown pseudo-member, kept and written back exactly as spelled. Serialising
    with `context={"casing": "export"}` writes UPPER_SNAKE.
    """

    @classmethod
    def _missing_(cls, value: object) -> "GtmEnum | None":
        found = next((m for m in cls if m.export_value == value), None)
        if found is not None or not isinstance(value, str) or not UNKNOWN.fullmatch(value):
            return found
        unknown = str.__new__(cls, value)
        unknown._name_ = unknown._value_ = value
        return unknown

    @property
    def known(self) -> bool:
        return self._name_ in type(self)._member_map_

    @property
    def export_value(self) -> str:
        return export_spelling(self.value) if self.known else self.value

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


def export_spelled(model: type[GtmModel], data: Any) -> Any:
    """`data` with every enum value the API spells lowerCamel respelled UPPER_SNAKE, as exports write it."""
    if not isinstance(data, dict):
        return data
    out = dict(data)
    for info in model.model_fields.values():
        if info.alias in data:
            out[info.alias] = _respelled(unwrap(info.annotation), data[info.alias])
    return out


def _respelled(annotation: Any, value: Any) -> Any:
    if get_origin(annotation) is list:
        inner = unwrap(get_args(annotation)[0])
        return [_respelled(inner, v) for v in value] if isinstance(value, list) else value
    if isinstance(annotation, type) and issubclass(annotation, GtmModel):
        return export_spelled(annotation, value)
    if isinstance(annotation, type) and issubclass(annotation, GtmEnum) and isinstance(value, str):
        return next((m.export_value for m in annotation if m.value == value), value)
    return value


def unwrap(annotation: Any) -> Any:
    """The type inside Optional and Annotated; bounds such as Int32's are pydantic's to check."""
    if get_origin(annotation) is Annotated:
        return unwrap(get_args(annotation)[0])
    if get_origin(annotation) in (Union, types.UnionType):
        args = [a for a in get_args(annotation) if a is not type(None)]
        return unwrap(args[0]) if len(args) == 1 else annotation
    return annotation
