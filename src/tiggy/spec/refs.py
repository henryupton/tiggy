"""IDs to names on import, names back to IDs on compile.

A spec never holds a GTM-assigned ID except where a reference dangles: `{trigger_id: '9'}` and
`{folder_id: '77'}` keep a broken reference visible instead of losing it.
"""

from dataclasses import dataclass
from typing import Any

from tiggy.gtm import builtins
from tiggy.spec.problems import Collector, Origin, describe
from tiggy.spec.yamlio import Flow

BUILT_IN_TRIGGER_IDS = {name: trigger_id for trigger_id, name in builtins.TRIGGER_NAMES.items()}


@dataclass(frozen=True)
class Template:
    name: str
    container_id: str | None
    template_id: str | None
    gallery_id: str | None

    @property
    def gallery_type(self) -> str | None:
        return f"cvt_{self.gallery_id}" if self.gallery_id else None

    @property
    def container_type(self) -> str | None:
        if self.container_id and self.template_id:
            return f"cvt_{self.container_id}_{self.template_id}"
        return None

    @property
    def expected_form(self) -> str:
        return "gallery" if self.gallery_id else "container"


def templates_of(version: dict[str, Any]) -> list[Template]:
    return [
        Template(
            t.get("name", ""),
            t.get("containerId"),
            t.get("templateId"),
            (t.get("galleryReference") or {}).get("galleryTemplateId"),
        )
        for t in version.get("customTemplate") or []
    ]


def resolve_type(type_: str, templates: list[Template]) -> tuple[Template, str] | None:
    """The custom template a `cvt_` type names, and which of GTM's two spellings it used.

    None when no template matches, or when more than one does, since then the type can't say which.
    """
    found = spellings(type_, templates)
    return found[0] if len(found) == 1 else None


def spellings(type_: str, templates: list[Template]) -> list[tuple[Template, str]]:
    """Every custom template that spells `type_`, with the form it used (its usual one if both match)."""
    found = []
    for template in templates:
        forms = [
            form
            for form, spelled in (("gallery", template.gallery_type), ("container", template.container_type))
            if type_ == spelled
        ]
        if forms:
            found.append((template, template.expected_form if template.expected_form in forms else forms[0]))
    return found


class ImportRefs:
    """Names for the IDs in an export-shaped ContainerVersion, and back again."""

    def __init__(self, version: dict[str, Any]):
        self.triggers = {t["triggerId"]: t["name"] for t in version.get("trigger") or [] if "triggerId" in t}
        self.trigger_ids = {name: trigger_id for trigger_id, name in self.triggers.items()}
        self.folders = {f["folderId"]: f["name"] for f in version.get("folder") or [] if "folderId" in f}
        self.templates = templates_of(version)

    def trigger_ref(self, trigger_id: str) -> Any:
        if trigger_id in self.triggers:
            return self.triggers[trigger_id]
        if builtins.is_trigger_id(trigger_id):
            if trigger_id in builtins.TRIGGER_NAMES:
                return Flow(built_in=builtins.TRIGGER_NAMES[trigger_id])
            return Flow(built_in_id=trigger_id)
        return Flow(trigger_id=trigger_id)

    def trigger_id(self, ref: Any, origin: Origin) -> str:
        """The source ID a trigger_ref came from, so an import can check its own encoding."""
        if isinstance(ref, str) and ref in self.trigger_ids:
            return self.trigger_ids[ref]
        if isinstance(ref, dict) and len(ref) == 1:
            ((form, value),) = ref.items()
            if form == "built_in" and isinstance(value, str) and value in BUILT_IN_TRIGGER_IDS:
                return BUILT_IN_TRIGGER_IDS[value]
            if form in ("built_in_id", "trigger_id") and isinstance(value, str):
                return value
        raise ValueError(f"{origin}: {ref!r} is not a trigger reference this import wrote")

    def folder_ref(self, folder_id: str) -> Any:
        return self.folders[folder_id] if folder_id in self.folders else Flow(folder_id=folder_id)

    def type_ref(self, type_: Any) -> Any:
        if not isinstance(type_, str) or not type_.startswith("cvt_"):
            return type_
        found = resolve_type(type_, self.templates)
        if found is None:
            return type_
        template, form = found
        return (
            Flow(template=template.name)
            if form == template.expected_form
            else Flow(template=template.name, form=form)
        )


class CompileRefs:
    """IDs for the names in a spec, once every object has been given one."""

    def __init__(self, problems: Collector, container_id: str):
        self.problems = problems
        self.container_id = container_id
        self.triggers: dict[str, str] = {}
        self.folders: dict[str, str] = {}
        self.templates: dict[str, Template] = {}

    def trigger_id(self, ref: Any, origin: Origin) -> str:
        if isinstance(ref, str):
            if ref in self.triggers:
                return self.triggers[ref]
            return self._fail(origin, f'trigger "{ref}" is not defined in this spec')
        if isinstance(ref, dict) and len(ref) == 1:
            ((form, value),) = ref.items()
            if form == "built_in" and isinstance(value, str) and value in BUILT_IN_TRIGGER_IDS:
                return BUILT_IN_TRIGGER_IDS[value]
            if form == "built_in":
                return self._fail(
                    origin,
                    f"{describe(value)} is not a known built-in trigger ({', '.join(BUILT_IN_TRIGGER_IDS)})",
                )
            if form == "built_in_id" and isinstance(value, str) and builtins.is_trigger_id(value):
                return value
            if form == "trigger_id" and isinstance(value, str):
                return value
        return self._fail(
            origin,
            f"{describe(ref)} is not a trigger reference (a name, {{built_in: ...}}, or {{trigger_id: ...}})",
        )

    def folder_id(self, ref: Any, origin: Origin) -> str:
        if isinstance(ref, str):
            if ref in self.folders:
                return self.folders[ref]
            return self._fail(origin, f'folder "{ref}" is not declared under folders')
        if isinstance(ref, dict) and set(ref) == {"folder_id"} and isinstance(ref["folder_id"], str):
            return ref["folder_id"]
        return self._fail(origin, f"{describe(ref)} is not a folder reference (a name or {{folder_id: ...}})")

    def type_string(self, ref: Any, origin: Origin) -> str:
        if isinstance(ref, str):
            return ref
        if (
            isinstance(ref, dict)
            and isinstance(ref.get("template"), str)
            and set(ref) <= {"template", "form"}
        ):
            template = self.templates.get(ref["template"])
            if template is None:
                return self._fail(origin, f'custom template "{ref["template"]}" is not defined in this spec')
            form = ref.get("form", template.expected_form)
            if form == "gallery" and template.gallery_type:
                twins = sorted(
                    t.name
                    for t in self.templates.values()
                    if t.gallery_id == template.gallery_id and t != template
                )
                if twins:
                    return self._fail(
                        origin,
                        f'custom template "{template.name}" shares its gallery template with "{twins[0]}", '
                        "so GTM can't tell which this means; use form: container",
                    )
                return template.gallery_type
            if form == "container" and template.container_type:
                return template.container_type
            return self._fail(origin, f'form {form!r} isn\'t available for template "{template.name}"')
        return self._fail(origin, f"{describe(ref)} is not a type (a GTM type string or {{template: name}})")

    def _fail(self, origin: Origin, message: str) -> str:
        self.problems.add(origin, message)
        return "0"
