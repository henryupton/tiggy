"""The hand-written tables that turn GTM's resource model into the spec format.

Everything else in `tiggy.spec` is driven by these tables and the generated models. Every model field
gets exactly one Role; `classify` refuses a field it can't place, so a regenerated model with a new
field fails loudly instead of being dropped, and a new ID-like field can't leak into a spec.
"""

import re
import types
from dataclasses import dataclass
from enum import Enum, auto
from functools import cache
from typing import Annotated, Any, Union, get_args, get_origin

from tiggy.gtm import resources as r
from tiggy.gtm.base import GtmModel, unwrap


class Role(Enum):
    DROPPED = auto()  # GTM-assigned identity, bookkeeping or read-only state
    ASSERTED = auto()  # GTM-assigned identity a spec records, to check it is compared with its own container
    COLLECTION = auto()  # a ContainerVersion list that becomes a top-level spec key
    CONTAINER = auto()  # ContainerVersion.container, the top-level `container` key
    TRIGGER_REFS = auto()  # trigger IDs, written as trigger refs
    FOLDER_REF = auto()  # parentFolderId, written as a folder ref
    TYPE_REF = auto()  # a type string that may name a custom template
    TYPE_REFS = auto()  # a list of those
    PARAMETER = auto()  # a single Parameter, positional
    PARAMETER_LIST = auto()  # a list of Parameters, keyed by their GTM key where possible
    NESTED = auto()  # another model
    NESTED_LIST = auto()  # a list of another model
    CODE = auto()  # a code body, written to a sidecar file
    COPIED = auto()  # a scalar, enum or list of them, copied verbatim


@dataclass(frozen=True)
class Field:
    alias: str  # GTM's name
    key: str  # the spec's name
    role: Role
    annotation: Any  # the model's type with Optional removed
    model: type[GtmModel] | None = None  # for NESTED and NESTED_LIST
    bounds: tuple[int | None, int | None] = (None, None)  # (ge, le), such as Int32's


BOOKKEEPING = {"accountId", "containerId", "workspaceId", "path", "fingerprint", "tagManagerUrl"}
OWN_ID = {
    r.Tag: "tagId",
    r.Trigger: "triggerId",
    r.Variable: "variableId",
    r.Folder: "folderId",
    r.CustomTemplate: "templateId",
    r.Client: "clientId",
    r.Transformation: "transformationId",
    r.Zone: "zoneId",
    r.GtagConfig: "gtagConfigId",
}
DROPPED: dict[type[GtmModel], set[str]] = {
    **{model: BOOKKEEPING | {own} for model, own in OWN_ID.items()},
    r.BuiltInVariable: BOOKKEEPING,
    # features are read-only in the API, and a version's name and description label a publish
    r.Container: {"accountId", "containerId", "tagIds", "path", "fingerprint", "tagManagerUrl", "features"},
    r.ContainerVersion: {
        "accountId",
        "containerId",
        "containerVersionId",
        "path",
        "fingerprint",
        "tagManagerUrl",
        "name",
        "description",
    },
}
ASSERTED_IDS = {(r.Container, "publicId")}

# ContainerVersion field -> (spec key, model, identity key in the spec).
COLLECTIONS: dict[str, tuple[str, type[GtmModel], str]] = {
    "builtInVariable": ("built_in_variables", r.BuiltInVariable, "type"),
    "folder": ("folders", r.Folder, "name"),
    "customTemplate": ("custom_templates", r.CustomTemplate, "name"),
    "variable": ("variables", r.Variable, "name"),
    "trigger": ("triggers", r.Trigger, "name"),
    "tag": ("tags", r.Tag, "name"),
    "client": ("clients", r.Client, "name"),
    "transformation": ("transformations", r.Transformation, "name"),
    "zone": ("zones", r.Zone, "name"),
    "gtagConfig": ("gtag_configs", r.GtagConfig, "label"),
}

TRIGGER_REFS: dict[tuple[type[GtmModel], str], str] = {
    (r.Tag, "firingTriggerId"): "firing_triggers",
    (r.Tag, "blockingTriggerId"): "blocking_triggers",
    (r.Variable, "enablingTriggerId"): "enabling_triggers",
    (r.Variable, "disablingTriggerId"): "disabling_triggers",
    (r.ZoneBoundary, "customEvaluationTriggerId"): "custom_evaluation_triggers",
}
TYPE_REF = {(r.Tag, "type"), (r.Variable, "type"), (r.Client, "type"), (r.Transformation, "type")}
TYPE_REFS = {(r.ZoneTypeRestriction, "whitelistedTypeId")}

# ID-like names that are data, not GTM-assigned identity.
KEPT_IDS = {
    (r.GalleryReference, "galleryTemplateId"),
    (r.GalleryReference, "templateDeveloperId"),
    (r.ZoneChildContainer, "publicId"),
    (r.Trigger, "uniqueTriggerId"),
}

# Code bodies written as sidecar files: (resource, its type) -> (parameter key, extension).
CODE_PARAMETERS: dict[tuple[type[GtmModel], str], tuple[str, str]] = {
    (r.Tag, "html"): ("html", "html"),
    (r.Variable, "jsm"): ("javascript", "js"),
}
TEMPLATE_DATA_EXTENSION = "tpl"
CODE_FIELDS = {(r.CustomTemplate, "templateData")}

# Spec key order per model; a spec-only key such as gtag configs' `label` comes first where listed.
KEY_ORDER: dict[type[GtmModel], list[str]] = {
    r.Container: ["name", "public_id", "notes", "usage_context", "domain_name", "tagging_server_urls"],
    r.ContainerVersion: ["deleted"],
    r.BuiltInVariable: ["type", "name"],
    r.Folder: ["name", "notes"],
    r.CustomTemplate: ["name", "gallery_reference", "template_data"],
    r.Tag: [
        "name",
        "type",
        "folder",
        "firing_triggers",
        "blocking_triggers",
        "setup_tag",
        "teardown_tag",
        "paused",
        "live_only",
        "tag_firing_option",
        "priority",
        "schedule_start_ms",
        "schedule_end_ms",
        "consent_settings",
        "monitoring_metadata",
        "monitoring_metadata_tag_name_key",
        "notes",
        "parameter",
    ],
    r.Trigger: [
        "name",
        "type",
        "folder",
        "custom_event_filter",
        "filter",
        "auto_event_filter",
        "event_name",
        "wait_for_tags",
        "wait_for_tags_timeout",
        "check_validation",
        "unique_trigger_id",
        "interval",
        "interval_seconds",
        "limit",
        "max_timer_length_seconds",
        "selector",
        "visibility_selector",
        "visible_percentage_min",
        "visible_percentage_max",
        "continuous_time_min_milliseconds",
        "total_time_min_milliseconds",
        "vertical_scroll_percentage_list",
        "horizontal_scroll_percentage_list",
        "notes",
        "parameter",
    ],
    r.Variable: [
        "name",
        "type",
        "folder",
        "enabling_triggers",
        "disabling_triggers",
        "schedule_start_ms",
        "schedule_end_ms",
        "format_value",
        "notes",
        "parameter",
    ],
    r.Client: ["name", "type", "folder", "priority", "notes", "parameter"],
    r.Transformation: ["name", "type", "folder", "notes", "parameter"],
    r.Zone: ["name", "boundary", "type_restriction", "child_container", "notes"],
    r.GtagConfig: ["label", "type", "parameter"],
    r.GalleryReference: [
        "host",
        "owner",
        "repository",
        "version",
        "signature",
        "gallery_template_id",
        "template_developer_id",
        "is_modified",
    ],
    r.TagConsentSetting: ["consent_status", "consent_type"],
    r.SetupTag: ["tag_name", "stop_on_setup_failure"],
    r.TeardownTag: ["tag_name", "stop_teardown_on_failure"],
    r.Condition: ["type", "parameter"],
    r.ZoneBoundary: ["condition", "custom_evaluation_triggers"],
    r.ZoneTypeRestriction: ["enable", "whitelisted_type_id"],
    r.ZoneChildContainer: ["public_id", "nickname"],
    r.VariableFormatValue: [
        "case_conversion_type",
        "convert_null_to_value",
        "convert_undefined_to_value",
        "convert_true_to_value",
        "convert_false_to_value",
        "convert_to_boolean",
        "convert_to_number",
    ],
}
SPEC_ONLY_KEYS = {r.GtagConfig: {"label"}}
UNMODELLED = "unmodelled"

# What GTM may fill in for a field a spec leaves out: (model, the object's type or None for any, spec path)
# -> GTM's value. Unverified while the API is read-only to us, so a difference one explains is still drift,
# only labelled as GTM's default.
DEFAULTS: dict[tuple[type[GtmModel], str | None, tuple[str, ...]], Any] = {
    (r.Variable, None, ("format_value",)): {},
    (r.Tag, None, ("monitoring_metadata",)): {"type": "MAP"},
    (r.Tag, None, ("consent_settings",)): {"consentStatus": "NOT_SET"},
    (r.Variable, "v", ("parameter", "setDefaultValue")): {"type": "BOOLEAN", "value": "false"},
}


def snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _bounds(annotation: Any) -> tuple[int | None, int | None]:
    if get_origin(annotation) in (Union, types.UnionType):
        found = [b for a in get_args(annotation) if (b := _bounds(a)) != (None, None)]
        return found[0] if found else (None, None)
    if get_origin(annotation) is not Annotated:
        return (None, None)
    metadata = [m for meta in get_args(annotation)[1:] for m in (meta, *getattr(meta, "metadata", ()))]
    ge = next((m.ge for m in metadata if getattr(m, "ge", None) is not None), None)
    le = next((m.le for m in metadata if getattr(m, "le", None) is not None), None)
    return ge, le


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, GtmModel)


@cache
def fields(model: type[GtmModel]) -> tuple[Field, ...]:
    """Every field of a model with its role, in spec key order (dropped fields last)."""
    placed: list[Field] = []
    for info in model.model_fields.values():
        alias, annotation = info.alias, unwrap(info.annotation)
        inner = unwrap(get_args(annotation)[0]) if get_origin(annotation) is list else None
        if alias in DROPPED.get(model, ()):
            role, key = Role.DROPPED, snake(alias)
        elif (model, alias) in ASSERTED_IDS:
            role, key = Role.ASSERTED, snake(alias)
        elif model is r.ContainerVersion and alias in COLLECTIONS:
            role, key = Role.COLLECTION, COLLECTIONS[alias][0]
        elif model is r.ContainerVersion and alias == "container":
            role, key = Role.CONTAINER, "container"
        elif (model, alias) in TRIGGER_REFS:
            role, key = Role.TRIGGER_REFS, TRIGGER_REFS[model, alias]
        elif alias == "parentFolderId":
            role, key = Role.FOLDER_REF, "folder"
        elif (model, alias) in TYPE_REF:
            role, key = Role.TYPE_REF, "type"
        elif (model, alias) in TYPE_REFS:
            role, key = Role.TYPE_REFS, snake(alias)
        elif (model, alias) in CODE_FIELDS:
            role, key = Role.CODE, snake(alias)
        elif annotation is r.Parameter:
            role, key = Role.PARAMETER, snake(alias)
        elif inner is r.Parameter:
            role, key = Role.PARAMETER_LIST, snake(alias)
        elif _is_model(annotation):
            role, key = Role.NESTED, snake(alias)
        elif _is_model(inner):
            role, key = Role.NESTED_LIST, snake(alias)
        else:
            role, key = Role.COPIED, snake(alias)
        if role is Role.COPIED and re.search(r"Ids?$", alias) and (model, alias) not in KEPT_IDS:
            raise TypeError(
                f"{model.__name__}.{alias} looks like an ID; drop, translate or keep it in overlay.py"
            )
        model_type = (
            annotation
            if role in (Role.NESTED, Role.CONTAINER)
            else inner
            if role is Role.NESTED_LIST
            else None
        )
        placed.append(Field(alias, key, role, annotation, model_type, _bounds(info.annotation)))
    order = KEY_ORDER.get(model)
    if order is None:
        raise TypeError(f"{model.__name__} has no key order in overlay.py")
    spec_keys = {f.key for f in placed if f.role not in (Role.DROPPED, Role.COLLECTION, Role.CONTAINER)}
    expected = set(order) - SPEC_ONLY_KEYS.get(model, set())
    if spec_keys != expected:
        raise TypeError(
            f"{model.__name__} key order is out of date: missing {sorted(spec_keys - expected)}, "
            f"stale {sorted(expected - spec_keys)}"
        )
    position = {key: i for i, key in enumerate(order)}
    return tuple(sorted(placed, key=lambda f: position.get(f.key, len(position))))


def check_all() -> None:
    """Classify every model the spec can reach; raises on the first gap."""
    seen: set[type[GtmModel]] = set()
    todo: list[type[GtmModel]] = [r.ContainerVersion]
    while todo:
        model = todo.pop()
        if model in seen:
            continue
        seen.add(model)
        for f in fields(model):
            if f.role is Role.COLLECTION:
                todo.append(COLLECTIONS[f.alias][1])
            elif f.model is not None:
                todo.append(f.model)


check_all()
