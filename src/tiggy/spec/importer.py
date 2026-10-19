"""`tiggy import`: a container into a spec directory, proven equivalent before anything lands.

The spec is staged beside the target, reloaded from disk, compiled and compared with the source by
canon. Only an exact match is moved into place; anything else leaves the target untouched.
"""

import json
import os
import re
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tiggy.gtm.resources import BuiltInVariable, Container, ContainerVersion, CustomTemplate
from tiggy.secrets import Fetching, Offline, SecretRef, is_placeholder, secret_bearing
from tiggy.spec import canon, layout
from tiggy.spec.codec import Codec
from tiggy.spec.compiler import compile_spec
from tiggy.spec.convert import to_spec
from tiggy.spec.layout import SpecObject
from tiggy.spec.loader import KINDS, LOCK, load_spec
from tiggy.spec.overlay import CODE_PARAMETERS, COLLECTIONS, TEMPLATE_DATA_EXTENSION, fields
from tiggy.spec.problems import Problem, SpecError
from tiggy.spec.refs import ImportRefs
from tiggy.spec.yamlio import Map, YamlError, render, unrepresentable

GITATTRIBUTES = "*.tpl -text\n*.html -text\n*.js -text\n"
EDITORCONFIG = "[*.{tpl,html,js}]\ntrim_trailing_whitespace = false\ninsert_final_newline = false\n"
VERSION_FIELDS = {f.alias: f.key for f in fields(ContainerVersion)}
SIDES = ("the container", "the spec")


class ImportError_(Exception):
    """Import can't proceed; nothing has been written."""


@dataclass
class ImportResult:
    counts: Counter = field(default_factory=Counter)
    fragments: list[str] = field(default_factory=list)
    sidecars: list[str] = field(default_factory=list)
    secrets: dict[str, str] = field(default_factory=dict)  # the name written for each, -> where it was


@dataclass
class Secrets:
    """The secrets in an export: the parameters holding one, and the value and origin of each name."""

    names: dict[int, str] = field(default_factory=dict)  # id() of a parameter -> its secret's name
    values: dict[str, str] = field(default_factory=dict)
    origins: dict[str, str] = field(default_factory=dict)

    def resolve(self, ref: SecretRef, origin: Any) -> str:
        return self.values.get(ref.name, ref.placeholder)


def import_version(source: dict[str, Any], spec_dir: Path) -> ImportResult:
    """Write `source`, a ContainerVersion exactly as the export holds it, out as a spec in `spec_dir`."""
    try:
        target = spec_dir.resolve()  # `.`, the cwd and links all mean the directory itself
        existing = sorted(c.name for c in target.iterdir()) if target.is_dir() else None
        if existing is None and target.exists():
            raise ImportError_(f"{spec_dir} already exists and isn't a directory")
    except (OSError, RuntimeError) as e:
        raise ImportError_(f"can't use {spec_dir}: {getattr(e, 'strerror', None) or e}") from e
    if existing and LOCK in existing:
        raise ImportError_(
            f"an import into {spec_dir} was interrupted, or is running now, and may have left part of a "
            "spec there. If it isn't running, empty the directory and import again"
        )
    if existing:
        raise ImportError_(
            f"{spec_dir} already exists and isn't empty. Importing into an existing spec "
            "isn't supported yet; use --check to compare instead"
        )
    _preconditions(source)
    secrets = find_secrets(source)
    planned, result = _plan(source, secrets)
    files = {}
    for path, tree in planned.fragments.items():
        try:
            files[path] = render(tree).encode("utf-8")
        except YamlError as e:
            raise ImportError_(f"can't write {path}: {e}") from e
    files.update({path: body.encode("utf-8") for path, body in planned.sidecars.items()})
    files.update({".gitattributes": GITATTRIBUTES.encode(), ".editorconfig": EDITORCONFIG.encode()})
    try:
        if existing is not None:
            _commit_into(target, files, source, secrets)
        else:
            _commit_new(target, files, source, secrets)
    except OSError as e:
        raise ImportError_(f"can't write {e.filename or spec_dir}: {e.strerror or e}") from e
    result.fragments = sorted(planned.fragments)
    result.sidecars = sorted(planned.sidecars)
    result.secrets = dict(sorted(secrets.origins.items()))
    return result


def _commit_new(target: Path, files: dict[str, bytes], source: dict[str, Any], secrets: Secrets) -> None:
    created = _make_parents(target.parent)
    try:
        staging = Path(tempfile.mkdtemp(prefix=".tiggy-import-", dir=target.parent))
        try:
            _stage(staging, files, source, secrets)
            os.chmod(staging, 0o777 & ~umask())
            os.rename(staging, target)  # refuses a directory another import has filled meanwhile
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    except BaseException:
        for directory in created:
            try:
                directory.rmdir()
            except OSError:  # something else has written there meanwhile
                break
        raise


def _make_parents(directory: Path) -> list[Path]:
    """Create `directory` and its missing parents; those this created, deepest first."""
    missing = []
    while not directory.exists():
        missing.append(directory)
        directory = directory.parent
    created = []
    for path in reversed(missing):
        try:
            path.mkdir()
        except FileExistsError:
            continue
        created.append(path)
    return created[::-1]


def _commit_into(target: Path, files: dict[str, bytes], source: dict[str, Any], secrets: Secrets) -> None:
    """Fill an existing empty directory in place, so its inode, mode and anyone's cwd in it survive."""
    lock = target / LOCK
    try:
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError as e:
        raise ImportError_(f"another import is writing into {target}") from e
    staging = None
    try:
        if any(child.name != LOCK for child in target.iterdir()):
            raise ImportError_(f"something else wrote into {target} while importing")
        staging = Path(tempfile.mkdtemp(prefix=".tiggy-import-", dir=target))
        _stage(staging, files, source, secrets)
        moved = []
        try:
            for child in sorted(staging.iterdir()):
                child.rename(target / child.name)
                moved.append(child.name)
        except BaseException:
            for name in moved:  # all or nothing, Ctrl-C included
                (target / name).rename(staging / name)
            raise
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
        lock.unlink(missing_ok=True)


def _stage(staging: Path, files: dict[str, bytes], source: dict[str, Any], secrets: Secrets) -> None:
    """Write the spec and prove it reproduces the source (secrets included) before it goes anywhere."""
    for path, data in files.items():
        _write(staging / path, data)
    try:
        compiled = compile_spec(load_spec(staging), secrets.resolve)
    except SpecError as e:
        raise ImportError_(f"the spec Tiggy wrote doesn't load back, which is a bug in Tiggy:\n{e}") from e
    names = {value: name for name, value in secrets.values.items()}
    left, right = (_hidden(v, names) for v in (source, canon.dump(compiled.version)))
    difference = canon.compare(left, right, ("the export", "the written spec"))
    if difference is None and _public_id(compiled.version) != source["container"].get("publicId"):
        difference = "container.publicId"
    if difference is not None:
        raise ImportError_(f"the written spec doesn't reproduce the container, at {_shown(difference)}")
    if compiled.warnings:
        raise ImportError_(f"the written spec has stray files: {compiled.warnings[0]}")


@dataclass
class Comparison:
    differences: list[str]  # empty when the container and the spec are equivalent
    warnings: list[Problem]  # about the spec's files, which don't affect equivalence
    unread_secrets: int = 0  # secret values not compared, since none were fetched


def check(source: dict[str, Any], spec_dir: Path, secrets: Fetching | None = None) -> Comparison:
    """What differs between a ContainerVersion as exported and a spec, secrets fetched by `secrets`.

    Without `secrets`, nothing is fetched: a secret only has to have some value in the export. With it,
    each is compared with what it fetches, and either way no secret's value is ever shown.
    """
    _preconditions(source)
    left = canon.canon(source)
    offline = Offline()
    compiled = compile_spec(load_spec(spec_dir), offline)
    values = None
    if secrets is not None:
        values = {ref.placeholder: secrets(ref, origin) for ref, origin in offline.refs.items()}
    expected, found = _public_id(compiled.version), source["container"].get("publicId")
    if expected is not None and found != expected:
        raise ImportError_(
            f"{spec_dir} is the spec for container {expected}, but the export is of "
            f"{found or 'a container with no public ID'}"
        )
    try:
        right = canon.canon(canon.dump(compiled.version))
    except canon.CanonError as e:
        raise ImportError_(f"can't compare with {spec_dir}: {e}") from e
    left, unread = _masked(left, right, values)
    differences = []
    for key in sorted(set(left) | set(right)):
        name = COLLECTIONS[key][0] if key in COLLECTIONS else VERSION_FIELDS.get(key, key)
        if key not in right:
            differences.append(f"{name}: only in the container")
        elif key not in left:
            differences.append(f"{name}: only in the spec")
        elif key in COLLECTIONS and isinstance(left[key], dict) and isinstance(right[key], dict):
            a, b = left[key], right[key]
            for identity in sorted(set(a) | set(b), key=str):
                if identity not in b:
                    differences.append(f"{name} {identity!r}: only in the container")
                elif identity not in a:
                    differences.append(f"{name} {identity!r}: only in the spec")
                elif found := canon.difference(a[identity], b[identity], sides=SIDES):
                    differences.append(f"{name} {identity!r}: {found}")
        elif found := canon.difference(left[key], right[key], sides=SIDES):
            differences.append(f"{name}: {found}")
    return Comparison([_shown(d) for d in differences], compiled.warnings, unread)


def _shown(difference: str) -> str:
    return re.sub(r"'\\x00secret:([^']*)'", r"<secret \1>", difference)


def _hidden(node: Any, names: dict[str, str]) -> Any:
    """`node` with each string that is a secret's value swapped for that secret's stand-in."""
    if isinstance(node, dict):
        return {key: _hidden(value, names) for key, value in node.items()}
    if isinstance(node, list):
        return [_hidden(value, names) for value in node]
    return SecretRef(names[node]).placeholder if isinstance(node, str) and node in names else node


def _masked(left: Any, right: Any, values: dict[str, str] | None) -> tuple[Any, int]:
    """`left` with the value at each of the spec's secrets replaced by the secret's stand-in.

    Without `values`, any non-empty value counts as the secret, unread. With them, a value that isn't
    the secret's becomes a second stand-in, so the difference shows without showing either value.
    """
    if is_placeholder(right) and isinstance(left, str):
        if values is None:
            return (right, 1) if left else (left, 0)
        return (right, 0) if values.get(right) == left else (f"{right}, not the stored value", 0)
    if isinstance(left, dict) and isinstance(right, dict):
        out, count = {}, 0
        for key, value in left.items():
            out[key], n = _masked(value, right[key], values) if key in right else (value, 0)
            count += n
        return out, count
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        pairs = [_masked(a, b, values) for a, b in zip(left, right, strict=True)]
        return [value for value, _ in pairs], sum(n for _, n in pairs)
    return left, 0


def _public_id(version: ContainerVersion) -> str | None:
    return version.container.public_id if version.container else None


def _preconditions(source: dict[str, Any]) -> None:
    problems = []
    try:
        version = ContainerVersion.model_validate_json(json.dumps(source), strict=True)
    except (ValidationError, TypeError, ValueError) as e:
        raise ImportError_(f"can't import this container: it isn't a valid ContainerVersion ({e})") from e
    changed = canon.difference(source, canon.dump(version), sides=("the export", "Tiggy's reading"))
    if changed:
        # Specs spell values the way exports do; anything else would be rewritten, not reproduced.
        problems.append(f"Tiggy reads a value differently from how the export writes it, at {changed}")
    if not isinstance(source.get("container"), dict):
        problems.append("the export has no container")
    for alias, (key, _, identity) in COLLECTIONS.items():
        if alias in source and not isinstance(source[alias], list):
            problems.append(f"{alias} is {source[alias]!r}, and a spec can only hold a list of {key}")
            continue
        if identity == "label":
            continue
        field_ = "type" if identity == "type" else "name"
        for item in source.get(alias) or []:
            if not isinstance(item.get(field_), str) or not item.get(field_):
                problems.append(f"a {alias} has no {field_}")
            elif alias == "builtInVariable" and not (isinstance(item.get("name"), str) and item["name"]):
                problems.append(f"built-in variable {item['type']} has no name, which a spec needs")
    problems += canon.duplicates(source)
    if problems:
        raise ImportError_("can't import this container: " + "; ".join(problems))


def _label(type_: Any, taken: set[str]) -> str:
    """A gtag config's spec label: its type, numbered when the type repeats."""
    base = type_ if isinstance(type_, str) and type_.strip() else "gtag config"
    label, n = base, 2
    while label in taken:
        label, n = f"{base} {n}", n + 1
    taken.add(label)
    return label


def umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


def find_secrets(source: dict[str, Any]) -> Secrets:
    """Each value a secret-bearing parameter holds, or names whole as a constant variable (`{{name}}`)."""
    found = Secrets()
    constants = {
        v.get("name"): p
        for v in source.get("variable") or []
        if v.get("type") == "c"
        for p in v.get("parameter") or []
        if p.get("key") == "value" and p.get("type") == "TEMPLATE" and isinstance(p.get("value"), str)
    }
    by_value: dict[str, str] = {}

    def record(param: dict[str, Any], label: str, where: str) -> None:
        value = param["value"]
        if value not in by_value:
            name, n = layout.slug(label, "secret"), 2
            while name in found.values:
                name, n = f"{layout.slug(label, 'secret')}-{n}", n + 1
            by_value[value] = name
            found.values[name], found.origins[name] = value, where
        found.names[id(param)] = by_value[value]

    for alias, (key, _, _) in COLLECTIONS.items():
        for item in source.get(alias) or []:
            for param in _parameters(item):
                value = param.get("value")
                if not secret_bearing(param.get("key")) or param.get("type") != "TEMPLATE":
                    continue
                if not isinstance(value, str) or not value:
                    continue
                whole = re.fullmatch(r"\{\{([^{}]+)\}\}", value)
                if whole and whole[1] in constants:
                    record(constants[whole[1]], whole[1], f"variable {whole[1]!r}")
                elif "{{" not in value:
                    owner = item.get("name") or item.get("type")
                    record(
                        param,
                        f"{owner} {param['key']}",
                        f"{key[:-1].replace('_', ' ')} {owner!r}, {param['key']}",
                    )
    return found


def _parameters(node: Any):
    """Every parameter in an object, nested ones in lists and maps included."""
    if isinstance(node, dict):
        if isinstance(node.get("key"), str) and "type" in node:
            yield node
        for value in node.values():
            yield from _parameters(value)
    elif isinstance(node, list):
        for value in node:
            yield from _parameters(value)


def _plan(source: dict[str, Any], secrets: Secrets) -> tuple[layout.Plan, ImportResult]:
    refs = ImportRefs(source)
    codec = Codec(
        trigger_ref=refs.trigger_ref,
        trigger_id=refs.trigger_id,
        secret=secrets.resolve,
        secret_for=lambda p: secrets.names.get(id(p)),
    )
    result = ImportResult()
    try:
        container = to_spec(Container, source["container"], refs, codec)
        version = to_spec(ContainerVersion, source, refs, codec)
        built_ins = None
        if source.get("builtInVariable") is not None:
            # Sorted, as layout sorts every collection, so how a spec splits them never shows on re-import.
            built_ins = sorted(
                (to_spec(BuiltInVariable, b, refs, codec) for b in source["builtInVariable"]),
                key=lambda node: str(node.get("type")),
            )
        objects: list[SpecObject] = []
        empty = []
        for alias, (key, model, _) in COLLECTIONS.items():
            items = source.get(alias)
            if alias == "builtInVariable" or items is None:
                continue
            if not items:
                empty.append(key)
            labels: set[str] = set()
            for item in items:
                name = item.get(KINDS[key]) or item.get("type")
                try:
                    node = to_spec(model, item, refs, codec)
                    problem = unrepresentable(node)
                    if problem:
                        raise ValueError(problem)
                except ValueError as e:
                    raise ValueError(f"{key[:-1].replace('_', ' ')} {name!r}: {e}") from e
                if KINDS[key] == "label":
                    node = Map({"label": _label(item.get("type"), labels), **node})
                objects.append(SpecObject(key, node, _code(model, item, node)))
                result.counts[key] += 1
    except (ValueError, UnicodeError) as e:
        raise ImportError_(f"can't import this container: {e}") from e
    return layout.plan(container, version or None, built_ins, objects, empty), result


def _code(model, item: dict[str, Any], node: Map) -> list[tuple[str, str, str]]:
    """Code bodies to write as sidecars: GTM's own template files, custom HTML and custom JavaScript."""
    if model is CustomTemplate:
        data = item.get("templateData")
        return [("template_data", TEMPLATE_DATA_EXTENSION, data)] if isinstance(data, str) else []
    type_ = item.get("type")
    code = CODE_PARAMETERS.get((model, type_)) if isinstance(type_, str) else None
    parameters = node.get("parameter")
    if code is None or not isinstance(parameters, dict):
        return []
    key, ext = code
    exact = [p for p in item.get("parameter") or [] if p.get("key") == key]
    if len(exact) == 1 and set(exact[0]) == {"type", "key", "value"} and exact[0]["type"] == "TEMPLATE":
        if isinstance(exact[0]["value"], str):
            return [(key, ext, exact[0]["value"])]
    return []


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
