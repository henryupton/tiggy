"""GTM container exports built in code, in the shape Admin > Export Container writes them.

Everything here is synthetic. Object builders (`ga4_event`, `data_layer`, `custom_event`, ...) return
an object's type and parameters; a `Container` adds names, IDs, folders, firing and the
account/container stamp, and `Container.export()` returns the whole export as GTM lays it out.
IDs and fingerprints come from one counter per container, so a build is deterministic.

Shapes marked UNVERIFIED are informed guesses no real export has confirmed yet: where a published
version's name and description sit, where a tag's priority, notes and paused sit, and how a
condition writes ignore_case. Tiggy compares containers by content, not key order, so a wrong guess
there changes realism, not results.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

# Built-in triggers every web container has, by the IDs GTM reserves for them.
ALL_PAGES = "2147479553"
CONSENT_INITIALIZATION = "2147479572"
INITIALIZATION = "2147479573"

# Display names GTM writes beside each built-in variable type.
BUILT_IN_NAMES = {
    "PAGE_URL": "Page URL",
    "PAGE_HOSTNAME": "Page Hostname",
    "PAGE_PATH": "Page Path",
    "REFERRER": "Referrer",
    "EVENT": "Event",
    "CLICK_ELEMENT": "Click Element",
    "CLICK_URL": "Click URL",
    "CLICK_TEXT": "Click Text",
    "CONTAINER_VERSION": "Container Version",
    "CONTAINER_ID": "Container ID",
    "DEBUG_MODE": "Debug Mode",
    "ENVIRONMENT_NAME": "Environment Name",
    "RANDOM_NUMBER": "Random Number",
    "EVENT_NAME": "Event Name",
    "CLIENT_NAME": "Client Name",
    "REQUEST_PATH": "Request Path",
    "QUERY_STRING": "Query String",
}

FEATURES = {
    "WEB": {"clients": False, "transformations": False},
    "SERVER": {"clients": True, "transformations": True},
}

CONSENT_TYPES = (
    "ad_storage",
    "analytics_storage",
    "ad_user_data",
    "ad_personalization",
    "functionality_storage",
    "personalization_storage",
    "security_storage",
)


# Parameters


def text(key: str | None, value: str | None = None) -> dict[str, Any]:
    """A TEMPLATE parameter; no value writes it the way GTM stores an empty field."""
    p: dict[str, Any] = {"type": "TEMPLATE"}
    if key is not None:
        p["key"] = key
    if value is not None:
        p["value"] = value
    return p


def boolean(key: str, value: bool) -> dict[str, Any]:
    return {"type": "BOOLEAN", "key": key, "value": "true" if value else "false"}


def integer(key: str | None, value: int) -> dict[str, Any]:
    p: dict[str, Any] = {"type": "INTEGER"}
    if key is not None:
        p["key"] = key
    p["value"] = str(value)
    return p


def listing(key: str | None, items: Sequence[dict[str, Any]] | None) -> dict[str, Any]:
    """A LIST parameter; None writes a LIST with no `list` at all, as GTM does for an empty table."""
    p: dict[str, Any] = {"type": "LIST"}
    if key is not None:
        p["key"] = key
    if items is not None:
        p["list"] = list(items)
    return p


def mapping(key: str | None, entries: Sequence[dict[str, Any]]) -> dict[str, Any]:
    p: dict[str, Any] = {"type": "MAP"}
    if key is not None:
        p["key"] = key
    p["map"] = list(entries)
    return p


def row(**columns: str) -> dict[str, Any]:
    """One row of a GTM table: an unkeyed MAP of TEMPLATE cells, in the order given."""
    return mapping(None, [text(k, v) for k, v in columns.items()])


def table(key: str, rows: Iterable[tuple[str, str]], columns: tuple[str, str]) -> dict[str, Any]:
    """A two-column table such as eventSettingsTable (parameter, parameterValue)."""
    name, value = columns
    return listing(key, [row(**{name: n, value: v}) for n, v in rows])


def tag_reference(tag_name: str, weak: bool | None = None) -> dict[str, Any]:
    p: dict[str, Any] = {"type": "TAG_REFERENCE", "key": "tagReference", "value": tag_name}
    if weak is not None:
        p["isWeakReference"] = weak
    return p


def condition(
    kind: str, arg0: str, arg1: str, *, negate: bool = False, ignore_case: bool = False
) -> dict[str, Any]:
    """A trigger condition: `kind` is EQUALS, CONTAINS, MATCH_REGEX and so on."""
    parameters = [text("arg0", arg0), text("arg1", arg1)]
    if ignore_case:  # UNVERIFIED: the key, type and position of ignore_case
        parameters.append(boolean("ignore_case", True))
    if negate:
        parameters.append(boolean("negate", True))
    return {"type": kind, "parameter": parameters}


# Tags: each returns the tag's type and parameters


def google_tag(tag_id: str, config_settings: Iterable[tuple[str, str]] = ()) -> dict[str, Any]:
    parameters = [text("tagId", tag_id)]
    settings = list(config_settings)
    if settings:
        parameters.append(table("configSettingsTable", settings, ("parameter", "parameterValue")))
    return {"type": "googtag", "parameter": parameters}


def ga4_event(
    event_name: str,
    *,
    measurement_id: str,
    settings: Iterable[tuple[str, str]] = (),
    user_properties: Iterable[tuple[str, str]] = (),
    send_ecommerce: bool = False,
) -> dict[str, Any]:
    """A GA4 Event tag (gaawe); `settings` are its eventSettingsTable rows, in order."""
    parameters = [boolean("sendEcommerceData", send_ecommerce), boolean("enhancedUserId", False)]
    rows = list(settings)
    if rows:
        parameters.append(table("eventSettingsTable", rows, ("parameter", "parameterValue")))
    properties = list(user_properties)
    if properties:
        parameters.append(table("userProperties", properties, ("name", "value")))
    parameters += [text("eventName", event_name), text("measurementIdOverride", measurement_id)]
    return {"type": "gaawe", "parameter": parameters}


def custom_html(html: str, *, document_write: bool = False) -> dict[str, Any]:
    return {
        "type": "html",
        "parameter": [text("html", html), boolean("supportDocumentWrite", document_write)],
    }


def template_tag(template_type: str, parameters: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {"type": template_type, "parameter": list(parameters)}


def ads_conversion(
    conversion_id: str,
    conversion_label: str,
    *,
    value: str | None = None,
    currency: str | None = None,
    order_id: str | None = None,
) -> dict[str, Any]:
    """A Google Ads Conversion Tracking tag (awct)."""
    parameters = [
        boolean("enableNewCustomerReporting", False),
        boolean("enableConversionLinker", True),
        boolean("enableProductReporting", False),
    ]
    if value is not None:
        parameters.append(text("conversionValue", value))
    if order_id is not None:
        parameters.append(text("orderId", order_id))
    parameters += [
        boolean("enableEnhancedConversion", False),
        text("conversionCookiePrefix", "_gcl"),
        boolean("enableShippingData", False),
        text("conversionId", conversion_id),
        text("conversionLabel", conversion_label),
    ]
    if currency is not None:
        parameters.append(text("currencyCode", currency))
    parameters.append(boolean("rdp", False))
    return {"type": "awct", "parameter": parameters}


def conversion_linker() -> dict[str, Any]:
    return {
        "type": "gclidw",
        "parameter": [
            boolean("enableCrossDomain", False),
            boolean("enableUrlPassthrough", False),
            boolean("enableCookieOverrides", False),
        ],
    }


def universal_analytics(
    track_type: str, tracking_id: str, *, event: tuple[str, str] | None = None
) -> dict[str, Any]:
    """A Universal Analytics tag (ua), as long-lived containers still hold; `event` is (category, action)."""
    parameters = [
        boolean("enableEcommerce", False),
        boolean("setTrackerName", False),
        boolean("doubleClick", False),
        text("useDebugVersion", "false"),
        text("trackingId", tracking_id),
        text("trackType", track_type),
        boolean("enableLinkId", False),
    ]
    if event is not None:
        parameters += [text("eventCategory", event[0]), text("eventAction", event[1])]
    return {"type": "ua", "parameter": parameters}


def server_ga4() -> dict[str, Any]:
    """sGTM's Google Analytics: GA4 tag, forwarding every event parameter and user property."""
    return {
        "type": "sgtmgaaw",
        "parameter": [
            boolean("redactVisitorIp", False),
            text("epToIncludeDropdown", "all"),
            text("upToIncludeDropdown", "all"),
        ],
    }


# Variables


def data_layer(key: str, *, version: int = 2, default: str | None = None) -> dict[str, Any]:
    parameters = [integer("dataLayerVersion", version), boolean("setDefaultValue", default is not None)]
    if default is not None:
        parameters.append(text("defaultValue", default))
    parameters.append(text("name", key))
    return {"type": "v", "parameter": parameters}


def custom_js(javascript: str) -> dict[str, Any]:
    return {"type": "jsm", "parameter": [text("javascript", javascript)]}


def constant(value: str) -> dict[str, Any]:
    return {"type": "c", "parameter": [text("value", value)]}


def lookup_table(
    input_: str, rows: Iterable[tuple[str, str]], *, default: str | None = None
) -> dict[str, Any]:
    """A Lookup Table (smm) from `input_` through `rows` of (key, value)."""
    parameters = [boolean("setDefaultValue", default is not None), text("input", input_)]
    if default is not None:
        parameters.append(text("defaultValue", default))
    parameters.append(table("map", rows, ("key", "value")))
    return {"type": "smm", "parameter": parameters}


def url(component: str, *, query_key: str | None = None) -> dict[str, Any]:
    """A URL variable (u); `component` is URL, PROTOCOL, HOST, PATH, QUERY or FRAGMENT."""
    parameters = [text("component", component)]
    if query_key is not None:
        parameters.append(text("queryKey", query_key))
    return {"type": "u", "parameter": parameters}


def cookie(name: str, *, decode: bool = False) -> dict[str, Any]:
    return {"type": "k", "parameter": [boolean("decodeCookie", decode), text("name", name)]}


def event_data(key_path: str) -> dict[str, Any]:
    """An sGTM Event Data variable (ed)."""
    return {"type": "ed", "parameter": [boolean("setDefaultValue", False), text("keyPath", key_path)]}


# Triggers


def custom_event(
    event: str, *, regex: bool = False, filters: Sequence[dict[str, Any]] = ()
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": "CUSTOM_EVENT",
        "customEventFilter": [condition("MATCH_REGEX" if regex else "EQUALS", "{{_event}}", event)],
    }
    if filters:
        body["filter"] = list(filters)
    return body


def page_view(filters: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    body: dict[str, Any] = {"type": "PAGEVIEW"}
    if filters:
        body["filter"] = list(filters)
    return body


def consent_init(filters: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    body: dict[str, Any] = {"type": "CONSENT_INIT"}
    if filters:
        body["filter"] = list(filters)
    return body


def always(filters: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    """sGTM's Custom trigger: every event a client claims, narrowed by `filters`."""
    body: dict[str, Any] = {"type": "ALWAYS"}
    if filters:
        body["filter"] = list(filters)
    return body


# Custom templates

# The Template Editor opens every gallery template with a TERMS_OF_SERVICE section. This stand-in keeps
# the section, its place and its rough size; the wording is the fixtures' own, not Google's.
TERMS_OF_SERVICE = """Stand-in for the notice the Template Editor writes at the top of every gallery
template, pointing at Google Tag Manager's Community Template Gallery Developer
Terms of Service (https://developers.google.com/tag-manager/gallery-tos). Real
templates carry Google's own wording here."""

# The smallest valid PNG header, standing in for a brand's base64 thumbnail.
THUMBNAIL = "data:image/png;base64,iVBORw0KGgo="


def template_info(
    kind: str,
    name: str,
    description: str,
    context: str,
    *,
    gallery_id: str | None = None,
    brand: str | None = None,
    categories: Sequence[str] = (),
) -> dict[str, Any]:
    """A template's INFO section. Without `brand` it is container-authored, as the Template Editor
    writes one (placeholder ID, empty securityGroups, dummy brand); with it, a gallery template."""
    if brand is None:
        return {
            "type": kind,
            "id": "cvt_temp_public_id",
            "version": 1,
            "securityGroups": [],
            "displayName": name,
            "brand": {"id": "brand_dummy", "displayName": ""},
            "description": description,
            "containerContexts": [context],
        }
    info: dict[str, Any] = {
        "type": kind,
        "id": f"cvt_{gallery_id}" if gallery_id else "cvt_temp_public_id",
        "version": 1,
        "displayName": name,
    }
    if categories:
        info["categories"] = list(categories)
    info["brand"] = {"id": f"github.com_{brand}", "displayName": brand, "thumbnail": THUMBNAIL}
    info["description"] = description
    info["containerContexts"] = [context]
    return info


def template_file(
    info: dict[str, Any],
    parameters: list[dict[str, Any]],
    code: str,
    *,
    permissions: list[dict[str, Any]] | None = None,
    created: str = "01/10/2026, 09:00:00",
    gallery: bool = False,
) -> str:
    """A .tpl body as the Template Editor writes it: sections, JSON at two spaces, three blank lines.

    Gallery templates open with TERMS_OF_SERVICE; container-authored ones open with INFO.
    """
    server = "SERVER" in info.get("containerContexts", [])
    sections = [("TERMS_OF_SERVICE", TERMS_OF_SERVICE)] if gallery else []
    sections += [
        ("INFO", json.dumps(info, indent=2)),
        ("TEMPLATE_PARAMETERS", json.dumps(parameters, indent=2)),
        ("SANDBOXED_JS_FOR_SERVER" if server else "SANDBOXED_JS_FOR_WEB_TEMPLATE", code.rstrip("\n")),
    ]
    if permissions is not None:
        sections.append(
            ("SERVER_PERMISSIONS" if server else "WEB_PERMISSIONS", json.dumps(permissions, indent=2))
        )
    sections += [("TESTS", "scenarios: []"), ("NOTES", f"Created on {created}")]
    return "".join(f"___{title}___\n\n{body}\n\n\n" for title, body in sections)


PermissionValue = str | bool | Sequence["PermissionValue"] | Mapping[str, "PermissionValue"]


def _permission_value(value: PermissionValue) -> dict[str, Any]:
    """A permission parameter value in the Template Editor's encoding: 1 string, 2 list, 3 map, 8 boolean."""
    if isinstance(value, bool):
        return {"type": 8, "boolean": value}
    if isinstance(value, str):
        return {"type": 1, "string": value}
    if isinstance(value, Mapping):
        return {
            "type": 3,
            "mapKey": [_permission_value(k) for k in value],
            "mapValue": [_permission_value(v) for v in value.values()],
        }
    return {"type": 2, "listItem": [_permission_value(v) for v in value]}


def permission(public_id: str, *, edited: bool = True, **params: PermissionValue) -> dict[str, Any]:
    """One permission entry. Params are strings, booleans, lists (inject_script's urls) or mappings
    (access_globals' keys). `edited` writes the clientAnnotations GTM adds once someone has changed an
    entry from its default; untouched ones such as logging go without."""
    entry: dict[str, Any] = {
        "instance": {
            "key": {"publicId": public_id, "versionId": "1"},
            "param": [{"key": k, "value": _permission_value(v)} for k, v in params.items()],
        }
    }
    if edited:
        entry["clientAnnotations"] = {"isEditedByUser": True}
    entry["isRequired"] = True
    return entry


def gallery(
    owner: str, repository: str, version: str, signature: str, template_id: str | None = None
) -> dict[str, str]:
    """A galleryReference; with `template_id`, tags name the template by it (`cvt_<id>`)."""
    reference = {
        "host": "github.com",
        "owner": owner,
        "repository": repository,
        "version": version,
        "signature": signature,
    }
    if template_id is not None:
        reference["galleryTemplateId"] = template_id
    return reference


# Containers


@dataclass
class Container:
    """A container being built. Each add returns what refers to it: a GTM ID, or a template's type."""

    name: str
    public_id: str
    container_id: str
    account_id: str = "1000"
    usage_context: str = "WEB"
    notes: str | None = None
    first_fingerprint: int = 1_759_300_000_000
    _next_id: int = 1
    _objects: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _built_ins: list[str] = field(default_factory=list)

    def _id(self) -> tuple[str, str]:
        """The next GTM ID and a fingerprint (GTM's last-edit time in ms) to go with it."""
        n = self._next_id
        self._next_id += 1
        return str(n), str(self.first_fingerprint + n * 86_413_307)

    def _stamp(self) -> dict[str, str]:
        return {"accountId": self.account_id, "containerId": self.container_id}

    def _add(self, collection: str, obj: dict[str, Any]) -> None:
        self._objects.setdefault(collection, []).append(obj)

    def built_ins(self, *types: str) -> None:
        self._built_ins += types

    def folder(self, name: str, *, notes: str | None = None) -> str:
        folder_id, fingerprint = self._id()
        obj = {**self._stamp(), "folderId": folder_id, "name": name}
        if notes is not None:
            obj["notes"] = notes
        obj["fingerprint"] = fingerprint
        self._add("folder", obj)
        return folder_id

    def template(
        self, name: str, template_data: str, *, gallery_reference: dict[str, str] | None = None
    ) -> str:
        """Adds a custom template; returns the type its tags and variables use."""
        template_id, fingerprint = self._id()
        obj: dict[str, Any] = {
            **self._stamp(),
            "templateId": template_id,
            "name": name,
            "fingerprint": fingerprint,
            "templateData": template_data,
        }
        if gallery_reference is not None:
            obj["galleryReference"] = gallery_reference
        self._add("customTemplate", obj)
        gallery_id = (gallery_reference or {}).get("galleryTemplateId")
        return f"cvt_{gallery_id}" if gallery_id else f"cvt_{self.container_id}_{template_id}"

    def variable(
        self,
        name: str,
        body: dict[str, Any],
        *,
        folder: str | None = None,
        notes: str | None = None,
        format_value: dict[str, Any] | None = None,
    ) -> str:
        variable_id, fingerprint = self._id()
        obj: dict[str, Any] = {**self._stamp(), "variableId": variable_id, "name": name, "type": body["type"]}
        if body.get("parameter"):
            obj["parameter"] = body["parameter"]
        if notes is not None:
            obj["notes"] = notes
        obj["fingerprint"] = fingerprint
        if folder is not None:
            obj["parentFolderId"] = folder
        obj["formatValue"] = {} if format_value is None else format_value
        self._add("variable", obj)
        return variable_id

    def trigger(
        self, name: str, body: dict[str, Any], *, folder: str | None = None, notes: str | None = None
    ) -> str:
        trigger_id, fingerprint = self._id()
        obj: dict[str, Any] = {**self._stamp(), "triggerId": trigger_id, "name": name, **body}
        if notes is not None:
            obj["notes"] = notes
        obj["fingerprint"] = fingerprint
        if folder is not None:
            obj["parentFolderId"] = folder
        self._add("trigger", obj)
        return trigger_id

    def tag(
        self,
        name: str,
        body: dict[str, Any],
        *,
        firing: Sequence[str] = (),
        blocking: Sequence[str] = (),
        folder: str | None = None,
        setup: tuple[str, bool] | None = None,
        teardown: tuple[str, bool] | None = None,
        paused: bool = False,
        firing_option: str = "ONCE_PER_EVENT",
        consent: Sequence[str] | str | None = None,
        priority: int | None = None,
        notes: str | None = None,
    ) -> str:
        """Adds a tag. `setup`/`teardown` are (tag name, stop on failure); `consent` is a list of
        consent types (NEEDED), "NOT_NEEDED", or None for NOT_SET."""
        tag_id, fingerprint = self._id()
        obj: dict[str, Any] = {**self._stamp(), "tagId": tag_id, "name": name, "type": body["type"]}
        if body.get("parameter"):
            obj["parameter"] = body["parameter"]
        if priority is not None:  # UNVERIFIED: where priority, notes and paused sit in a tag
            obj["priority"] = integer(None, priority)
        if notes is not None:
            obj["notes"] = notes
        obj["fingerprint"] = fingerprint
        if firing:
            obj["firingTriggerId"] = list(firing)
        if blocking:
            obj["blockingTriggerId"] = list(blocking)
        if setup is not None:
            obj["setupTag"] = [{"tagName": setup[0], "stopOnSetupFailure": setup[1]}]
        if teardown is not None:
            obj["teardownTag"] = [{"tagName": teardown[0], "stopTeardownOnFailure": teardown[1]}]
        if folder is not None:
            obj["parentFolderId"] = folder
        if paused:
            obj["paused"] = True
        obj["tagFiringOption"] = firing_option
        obj["monitoringMetadata"] = {"type": "MAP"}
        obj["consentSettings"] = _consent(consent)
        self._add("tag", obj)
        return tag_id

    def client(
        self, name: str, type_: str, parameters: Sequence[dict[str, Any]], *, folder: str | None = None
    ) -> str:
        client_id, fingerprint = self._id()
        obj: dict[str, Any] = {
            **self._stamp(),
            "clientId": client_id,
            "name": name,
            "type": type_,
            "parameter": list(parameters),
            "fingerprint": fingerprint,
        }
        if folder is not None:
            obj["parentFolderId"] = folder
        self._add("client", obj)
        return client_id

    def transformation(
        self, name: str, type_: str, parameters: Sequence[dict[str, Any]], *, folder: str | None = None
    ) -> str:
        transformation_id, fingerprint = self._id()
        obj: dict[str, Any] = {
            **self._stamp(),
            "transformationId": transformation_id,
            "name": name,
            "type": type_,
            "parameter": list(parameters),
            "fingerprint": fingerprint,
        }
        if folder is not None:
            obj["parentFolderId"] = folder
        self._add("transformation", obj)
        return transformation_id

    def export(
        self,
        export_time: str,
        *,
        version_id: str = "0",
        version_name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """The export: a workspace export by default, a published version when given its ID and name."""
        account, container = self.account_id, self.container_id
        version: dict[str, Any] = {
            "path": f"accounts/{account}/containers/{container}/versions/{version_id}",
            **self._stamp(),
            "containerVersionId": version_id,
        }
        if version_name is not None:  # UNVERIFIED: where a published version's name and description sit
            version["name"] = version_name
        if description is not None:
            version["description"] = description
        version["container"] = self._container()
        for collection in ("tag", "trigger", "variable", "folder"):
            if collection in self._objects:
                version[collection] = _by_id(collection, self._objects[collection])
        if self._built_ins:
            version["builtInVariable"] = [
                {**self._stamp(), "type": t, "name": BUILT_IN_NAMES[t]} for t in self._built_ins
            ]
        version["fingerprint"] = str(self.first_fingerprint + self._next_id * 86_413_307)
        version["tagManagerUrl"] = (
            f"https://tagmanager.google.com/#/versions/accounts/{account}/containers/{container}"
            f"/versions/{version_id}?apiLink=version"
        )
        for collection in ("customTemplate", "client", "transformation"):
            if collection in self._objects:
                version[collection] = _by_id(collection, self._objects[collection])
        # Through JSON, so no two objects share a dict (deepcopy would keep them shared).
        export = {"exportFormatVersion": 2, "exportTime": export_time, "containerVersion": version}
        return json.loads(json.dumps(export))

    def _container(self) -> dict[str, Any]:
        account, container = self.account_id, self.container_id
        obj: dict[str, Any] = {
            "path": f"accounts/{account}/containers/{container}",
            **self._stamp(),
            "name": self.name,
            "publicId": self.public_id,
            "usageContext": [self.usage_context],
        }
        if self.notes is not None:
            obj["notes"] = self.notes
        extra = FEATURES[self.usage_context]
        obj["fingerprint"] = str(self.first_fingerprint)
        obj["tagManagerUrl"] = (
            f"https://tagmanager.google.com/#/container/accounts/{account}/containers/{container}"
            "/workspaces?apiLink=container"
        )
        obj["features"] = {
            "supportUserPermissions": True,
            "supportEnvironments": True,
            "supportWorkspaces": True,
            "supportGtagConfigs": False,
            "supportBuiltInVariables": True,
            "supportClients": extra["clients"],
            "supportFolders": True,
            "supportTags": True,
            "supportTemplates": True,
            "supportTriggers": True,
            "supportVariables": True,
            "supportVersions": True,
            "supportZones": True,
            "supportTransformations": extra["transformations"],
        }
        obj["tagIds"] = [self.public_id]
        return obj


def _consent(consent: Sequence[str] | str | None) -> dict[str, Any]:
    if consent is None:
        return {"consentStatus": "NOT_SET"}
    if isinstance(consent, str):
        return {"consentStatus": consent}
    unknown = set(consent) - set(CONSENT_TYPES)
    if unknown:
        raise ValueError(f"not Google consent types: {sorted(unknown)}")
    return {"consentStatus": "NEEDED", "consentType": listing(None, [text(None, c) for c in consent])}


ID_KEYS = {
    "tag": "tagId",
    "trigger": "triggerId",
    "variable": "variableId",
    "folder": "folderId",
    "customTemplate": "templateId",
    "client": "clientId",
    "transformation": "transformationId",
}


def _by_id(collection: str, objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """GTM lists each collection in ID order."""
    return sorted(objects, key=lambda o: int(o[ID_KEYS[collection]]))


def dumps(export: dict[str, Any]) -> str:
    """JSON as GTM's export writes it: four-space indent, UTF-8 as is, no final newline."""
    return json.dumps(export, indent=4, ensure_ascii=False)
