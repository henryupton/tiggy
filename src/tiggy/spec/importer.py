"""`tiggy import`: a container into a spec directory, proven equivalent before anything lands.

The spec is staged beside the target, reloaded from disk, compiled and compared with the source by
canon. Only an exact match is moved into place; anything else leaves the target untouched.
"""

import json
import os
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tiggy.gtm.resources import BuiltInVariable, Container, ContainerVersion, CustomTemplate
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
    planned, result = _plan(source)
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
            _commit_into(target, files, source)
        else:
            _commit_new(target, files, source)
    except OSError as e:
        raise ImportError_(f"can't write {e.filename or spec_dir}: {e.strerror or e}") from e
    result.fragments = sorted(planned.fragments)
    result.sidecars = sorted(planned.sidecars)
    return result


def _commit_new(target: Path, files: dict[str, bytes], source: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".tiggy-import-", dir=target.parent))
    try:
        _stage(staging, files, source)
        os.chmod(staging, 0o777 & ~umask())
        os.rename(staging, target)  # refuses a directory another import has filled meanwhile
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _commit_into(target: Path, files: dict[str, bytes], source: dict[str, Any]) -> None:
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
        _stage(staging, files, source)
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


def _stage(staging: Path, files: dict[str, bytes], source: dict[str, Any]) -> None:
    """Write the spec and prove it reproduces the source before it goes anywhere."""
    for path, data in files.items():
        _write(staging / path, data)
    try:
        compiled = compile_spec(load_spec(staging))
    except SpecError as e:
        raise ImportError_(f"the spec Tiggy wrote doesn't load back, which is a bug in Tiggy:\n{e}") from e
    difference = canon.compare(source, canon.dump(compiled.version), ("the export", "the written spec"))
    if difference is not None:
        raise ImportError_(f"the written spec doesn't reproduce the container, at {difference}")
    if compiled.warnings:
        raise ImportError_(f"the written spec has stray files: {compiled.warnings[0]}")


@dataclass
class Comparison:
    differences: list[str]  # empty when the container and the spec are equivalent
    warnings: list[Problem]  # about the spec's files, which don't affect equivalence


def check(source: dict[str, Any], spec_dir: Path) -> Comparison:
    """What differs between a ContainerVersion as exported and a spec."""
    _preconditions(source)
    left = canon.canon(source)
    compiled = compile_spec(load_spec(spec_dir))
    try:
        right = canon.canon(canon.dump(compiled.version))
    except canon.CanonError as e:
        raise ImportError_(f"can't compare with {spec_dir}: {e}") from e
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
    return Comparison(differences, compiled.warnings)


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


def _plan(source: dict[str, Any]) -> tuple[layout.Plan, ImportResult]:
    refs = ImportRefs(source)
    codec = Codec(trigger_ref=refs.trigger_ref, trigger_id=refs.trigger_id)
    result = ImportResult()
    try:
        container = to_spec(Container, source["container"], refs, codec)
        version = to_spec(ContainerVersion, source, refs, codec)
        built_ins = None
        if source.get("builtInVariable") is not None:
            built_ins = [to_spec(BuiltInVariable, b, refs, codec) for b in source["builtInVariable"]]
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
