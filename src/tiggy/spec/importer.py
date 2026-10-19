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
from tiggy.secrets import Fetching, Offline, SecretRef, distinctive, secret_bearing
from tiggy.spec import canon, diff, layout
from tiggy.spec.codec import Codec, dotted
from tiggy.spec.compiler import compile_spec
from tiggy.spec.convert import to_spec
from tiggy.spec.diff import Diff
from tiggy.spec.layout import SpecObject
from tiggy.spec.loader import KINDS, LOCK, load_spec
from tiggy.spec.overlay import CODE_PARAMETERS, COLLECTIONS, TEMPLATE_DATA_EXTENSION
from tiggy.spec.problems import Problem, SpecError
from tiggy.spec.refs import ImportRefs
from tiggy.spec.yamlio import Map, YamlError, render, unrepresentable

GITATTRIBUTES = "*.tpl -text\n*.html -text\n*.js -text\n"
EDITORCONFIG = "[*.{tpl,html,js}]\ntrim_trailing_whitespace = false\ninsert_final_newline = false\n"
REFERENCE = re.compile(r"\{\{([^{}]+)\}\}")
AROUND_REFERENCES = re.compile(r"(?i)\s*(?:bearer|basic)?[\W_]*")  # what a credential holds besides them
TABLES = {"smm", "remm"}  # lookup and regex tables
NAMING = {"name", "key", "header", "headername"}  # map entries naming the value beside them


class ImportError_(Exception):
    """Import can't proceed; nothing has been written."""


@dataclass
class ImportResult:
    counts: Counter = field(default_factory=Counter)
    fragments: list[str] = field(default_factory=list)
    sidecars: list[str] = field(default_factory=list)
    secrets: dict[str, str] = field(default_factory=dict)  # the name written for each, -> where it was
    shared: dict[str, str] = field(default_factory=dict)  # a secret named for its place -> the one it matched


@dataclass
class Secrets:
    """The secrets in an export: each name's value and origin, and the parameters to write as references."""

    values: dict[str, str] = field(default_factory=dict)
    origins: dict[str, str] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)  # value -> the name its credential holds it as
    held: dict[int, str] = field(default_factory=dict)  # id() of each parameter to write as a reference
    places: dict[int, str] = field(default_factory=dict)  # id() of each of those -> where it is
    shared: dict[str, str] = field(default_factory=dict)  # a name given a place -> the secret it matched
    unwritable: list[str] = field(default_factory=list)  # secrets no reference could stand in for
    written: set[int] = field(default_factory=set)  # held parameters the codec has written

    def name_for(self, p: dict[str, Any]) -> str | None:
        """The secret a parameter is written as, noted as written: the codec writes nothing else for it."""
        name = self.held.get(id(p))
        if name:
            self.written.add(id(p))
        return name

    def resolve(self, ref: SecretRef, origin: Any) -> str:
        return self.values.get(ref.name, ref.placeholder)

    def masked(self, text: str) -> str:
        """`text` with each distinctive secret shown as `<secret name>`, for an object named with one."""
        for value, name in sorted(self.names.items(), key=lambda item: -len(item[0])):  # longest first
            if distinctive(value):
                text = text.replace(value, f"<secret {name}>")
        return text


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
    if secrets.unwritable:
        raise ImportError_(
            "can't import this container without writing a secret: "
            f"{secrets.masked('; '.join(secrets.unwritable))}, and only a whole parameter value can be "
            "{$secret: name}"
        )
    planned, result = _plan(source, secrets)
    unwritten = [
        f"{name} is in {secrets.places[i]}" for i, name in secrets.held.items() if i not in secrets.written
    ]
    if unwritten:
        raise ImportError_(
            "can't import this container without writing a secret: "
            f"{secrets.masked('; '.join(unwritten))}, which Tiggy copies as it is, not as {{$secret: name}}"
        )
    files = {}
    for path, tree in planned.fragments.items():
        try:
            files[path] = render(tree).encode("utf-8")
        except YamlError as e:
            raise ImportError_(f"can't write {path}: {e}") from e
    files.update({path: body.encode("utf-8") for path, body in planned.sidecars.items()})
    for path, data in files.items():  # whatever else went wrong, no file goes out holding a secret
        for name, value in secrets.values.items():
            if distinctive(value) and value.encode("utf-8") in data:
                raise ImportError_(f"can't import this container: {secrets.masked(path)} would hold {name}")
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
    result.shared = dict(sorted(secrets.shared.items()))
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
    left, right = (_hidden(v, secrets.names) for v in (source, canon.dump(compiled.version)))
    difference = canon.compare(left, right, ("the export", "the written spec"))
    if difference is None and _public_id(compiled.version) != source["container"].get("publicId"):
        difference = "container.publicId"
    if difference is not None:
        raise ImportError_(f"the written spec doesn't reproduce the container, at {_shown(difference)}")
    if compiled.warnings:
        raise ImportError_(f"the written spec has stray files: {compiled.warnings[0]}")


@dataclass
class Comparison:
    diff: Diff  # no changes when the container and the spec are equivalent
    warnings: list[Problem]  # about the spec's files, which don't affect equivalence


def check(
    source: dict[str, Any], spec_dir: Path, secrets: Fetching | None = None, exact: bool = False
) -> Comparison:
    """What differs between a ContainerVersion as exported and a spec, secrets fetched by `secrets`.

    Without `secrets`, nothing is fetched: a secret only has to have some value in the export. With it,
    each is compared with what it fetches, and either way no secret's value is ever shown.
    """
    _preconditions(source)
    offline = Offline()
    spec = load_spec(spec_dir)
    compiled = compile_spec(spec, offline)
    values = None
    if secrets is not None:
        values = {ref.placeholder: secrets(ref, origin) for ref, origin in offline.refs.items()}
    expected, found = _public_id(compiled.version), source["container"].get("publicId")
    if expected is not None and found != expected:
        raise ImportError_(
            f"{spec_dir} is the spec for container {expected}, but the export is of "
            f"{found or 'a container with no public ID'}"
        )
    known = tuple(find_secrets(source).values.values())
    try:
        return Comparison(diff.compare(source, compiled, spec, values, known, exact), compiled.warnings)
    except canon.CanonError as e:
        raise ImportError_(f"can't compare with {spec_dir}: {e}") from e


def _shown(difference: str) -> str:
    return re.sub(r"'\\x00secret:([^']*)'", r"<secret \1>", difference)


def _hidden(node: Any, names: dict[str, str]) -> Any:
    """`node` with each string that is a secret's value swapped for that secret's stand-in."""
    if isinstance(node, dict):
        return {key: _hidden(value, names) for key, value in node.items()}
    if isinstance(node, list):
        return [_hidden(value, names) for value in node]
    return SecretRef(names[node]).placeholder if isinstance(node, str) and node in names else node


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
    """Each value a credential holds, under it or through the variables it uses, and where else it is."""
    found = Secrets()
    variables = {v.get("name"): v for v in source.get("variable") or [] if isinstance(v, dict)}

    def record(param: dict[str, Any], label: str, where: str, matched: str | None = None) -> None:
        value = param["value"]
        if id(param) in found.held:
            return
        name = found.names.get(value) if matched is None else None  # one value in several credentials
        if name is None:
            name, n = layout.slug(label, "secret"), 2
            while name in found.values:
                name, n = f"{layout.slug(label, 'secret')}-{n}", n + 1
            found.values[name], found.origins[name] = value, where
            found.names.setdefault(value, name)
            if matched:
                found.shared[name] = matched
        found.held[id(param)], found.places[id(param)] = name, where

    def take(param: dict[str, Any], label: str, where: str, seen: frozenset[str]) -> None:
        value = param.get("value")
        if not isinstance(value, str) or not value:
            return
        if "{{" not in value:
            record(param, label, where)
            return
        for ref in REFERENCE.findall(value):
            for output, name, path in _outputs(variables.get(ref)) if ref not in seen else ():
                take(output, name, f"variable {ref!r}" + (f", {dotted(path)}" if path else ""), seen | {ref})
        if not AROUND_REFERENCES.fullmatch(REFERENCE.sub("", value)):
            found.unwritable.append(
                f"{where} mixes a reference with other text, so which part is secret isn't clear"
            )

    for owner, label, node in _owners(source):
        for path, credential, param in _credentials(node, ()):
            for here, held in _templates(param, path):
                take(held, f"{label} {credential}", f"{owner}, {dotted(here)}", frozenset())
    recognisable = {value: name for value, name in found.names.items() if distinctive(value)}
    for owner, label, node in _owners(source):
        for path, text, holder in _walk(node, ()):
            if holder is not None and id(holder) in found.held:
                continue
            where = f"{owner}, {dotted(path)}"
            if holder is not None and text in recognisable:  # its own name, so a placeholder can't leak one
                key = next((s for s in reversed(path) if isinstance(s, str)), "secret")
                record(
                    holder,
                    str(label) if path == ("parameter", "value") else f"{label} {key}",
                    where,
                    recognisable[text],
                )
            else:
                found.unwritable += [
                    f"{name} is part of {where}" for value, name in recognisable.items() if value in text
                ]
    return found


def _owners(source: dict[str, Any]):
    """Each part of an export: how to name it (`tag 'name'`), a label for its secrets, and itself."""
    version = {k: v for k, v in source.items() if k != "container" and k not in COLLECTIONS}
    yield "the container", "container", source.get("container")
    yield "the version", "version", version
    for alias, (key, _, _) in COLLECTIONS.items():
        for item in source.get(alias) or []:
            label = item.get("name") or item.get("type")
            yield f"{key[:-1].replace('_', ' ')} {label!r}", label, item


def _credentials(node: Any, path: tuple[str | int, ...], header: str | None = None):
    """Each parameter holding a credential, its path and what it's called, other than one inside another.

    A parameter is one by its key, or as the value in a map naming one, as `{name: Authorization, value:
    ...}` does a request header.
    """
    if isinstance(node, list):
        for i, item in enumerate(node):
            key = item.get("key") if isinstance(item, dict) else None
            yield from _credentials(item, (*path, key if isinstance(key, str) else i), header)
    elif isinstance(node, dict):
        parameter, key = "type" in node, node.get("key")
        if parameter and secret_bearing(key):
            yield path, key, node
            return
        if parameter and header and isinstance(key, str) and key.lower().endswith("value"):
            yield path, header, node
            return
        named = _header(node.get("map")) if parameter else None
        for field_, value in node.items():
            here = path if parameter and field_ in ("list", "map") else (*path, field_)
            yield from _credentials(value, here, named if field_ == "map" else None)


def _header(entries: Any) -> str | None:
    """The credential a parameter's map entries name, if they name one."""
    for entry in entries if isinstance(entries, list) else []:
        if (
            isinstance(entry, dict)
            and str(entry.get("key")).lower() in NAMING
            and secret_bearing(entry.get("value"))
        ):
            return entry["value"]
    return None


def _templates(param: dict[str, Any], path: tuple[str | int, ...]):
    """Each value at or under `param`, with its path: list items by index, map entries by key."""
    if param.get("type") in ("TEMPLATE", "INTEGER"):
        yield path, param
    for member in ("list", "map"):
        children = param.get(member)
        for i, child in enumerate(children if isinstance(children, list) else []):
            if isinstance(child, dict):
                key = child.get("key") if member == "map" else None
                yield from _templates(child, (*path, key if isinstance(key, str) else i))


def _outputs(variable: Any):
    """What a constant or lookup table can return: each parameter holding it, a label, and its path."""
    if not isinstance(variable, dict):
        return
    name, kind = variable.get("name"), variable.get("type")
    for p in variable.get("parameter") or []:
        key = p.get("key")
        if kind == "c" and key == "value":
            yield p, name, ()  # a constant is named for itself
        elif kind in TABLES and key == "defaultValue":
            yield p, f"{name} default", ("parameter", key)
        elif kind in TABLES and key == "map":
            for i, row in enumerate(p.get("list") or []):
                entries = {e.get("key"): e for e in row.get("map") or []} if isinstance(row, dict) else {}
                match = entries.get("key", {}).get("value")
                if "value" in entries:
                    label = f"{name} {match if isinstance(match, str) else i}"
                    yield entries["value"], label, ("parameter", "map", i, "value")


def _walk(node: Any, path: tuple[str | int, ...]):
    """Each string in `node`, its path (parameters by key) and the parameter it is the value of, if any."""
    if isinstance(node, str):
        yield path, node, None
    elif isinstance(node, list):
        for i, item in enumerate(node):
            key = item.get("key") if isinstance(item, dict) else None
            yield from _walk(item, (*path, key if isinstance(key, str) else i))
    elif isinstance(node, dict):
        parameter = "type" in node
        for field_, value in node.items():
            if parameter and field_ == "value" and isinstance(value, str):
                yield path, value, node
            else:
                yield from _walk(value, path if parameter and field_ in ("list", "map") else (*path, field_))


def _plan(source: dict[str, Any], secrets: Secrets) -> tuple[layout.Plan, ImportResult]:
    refs = ImportRefs(source)
    codec = Codec(
        trigger_ref=refs.trigger_ref,
        trigger_id=refs.trigger_id,
        secret=secrets.resolve,
        secret_for=secrets.name_for,
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
        if isinstance(parameters.get(key), str):  # not a {$secret: name}, whose value no file may hold
            return [(key, ext, exact[0]["value"])]
    return []


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
