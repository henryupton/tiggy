"""Reading a spec directory: every fragment, strictly, merged into one set of objects."""

import os
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from tiggy.spec.overlay import CODE_PARAMETERS, COLLECTIONS, TEMPLATE_DATA_EXTENSION
from tiggy.spec.problems import Collector, Origin, Problem, at, describe
from tiggy.spec.yamlio import Map, YamlError, load

VERSION = 1
KINDS = {key: identity for key, _, identity in COLLECTIONS.values()}
SINGLETONS = ("container", "version")
TOP_LEVEL = {"tiggy", *SINGLETONS, *KINDS}
EXPECTED = ", ".join(sorted(TOP_LEVEL - {"tiggy"}))
FRAGMENT_SUFFIXES = (".yml", ".yaml")
LOCK = ".tiggy-import.lock"  # held by an import while it writes; left behind if it was killed
CODE_SUFFIXES = {f".{ext}" for _, ext in CODE_PARAMETERS.values()} | {f".{TEMPLATE_DATA_EXTENSION}"}


@dataclass(frozen=True)
class Entry:
    node: Map
    origin: Origin


@dataclass
class LoadedSpec:
    root: Path
    singletons: dict[str, Entry] = field(default_factory=dict)
    collections: dict[str, list[Entry]] = field(default_factory=dict)
    problems: Collector = field(default_factory=Collector)
    referenced: set[Path] = field(default_factory=set)

    def read_file(self, ref: Any, origin: Origin) -> str:
        """The exact text of a sidecar named by a `$file` path relative to the spec root."""
        if not isinstance(ref, str) or not ref:
            self.problems.add(origin, f"$file needs a path relative to the spec root, got {ref!r}")
            return ""
        path = PurePosixPath(ref)
        if path.is_absolute() or ".." in path.parts or any(p.startswith(".") for p in path.parts[:-1]):
            self.problems.add(origin, f"$file {ref!r} must stay inside the spec and out of dot-directories")
            return ""
        if path.suffix.lower() in FRAGMENT_SUFFIXES:
            self.problems.add(origin, f"$file {ref!r} can't be a YAML file; those are fragments")
            return ""
        try:
            if any(_linked(self.root, path.parts[: i + 1]) for i in range(len(path.parts))):
                self.problems.add(origin, f"$file {ref!r} goes through a link, which Tiggy doesn't follow")
                return ""
            target = (self.root / path).resolve(strict=True)
        except FileNotFoundError:
            self.problems.add(origin, f"$file {ref!r} does not exist")
            return ""
        except (OSError, RuntimeError, ValueError) as e:  # NUL bytes, permissions
            self.problems.add(origin, f"$file {ref!r} can't be read: {getattr(e, 'strerror', None) or e}")
            return ""
        if not target.is_file():
            self.problems.add(origin, f"$file {ref!r} isn't a regular file")
            return ""
        on_disk = _on_disk(self.root, path)
        if on_disk != path.as_posix():
            spelled = (
                f"is written in a different Unicode form from the name on disk, {ascii(on_disk)}"
                if unicodedata.normalize("NFC", on_disk) == unicodedata.normalize("NFC", path.as_posix())
                else f"is spelled {on_disk!r} on disk"
            )
            self.problems.add(
                origin, f"$file {ref!r} {spelled}; Linux and other exact-name systems won't find it"
            )
            return ""
        try:
            body = target.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            self.problems.add(origin, f"$file {ref!r} is not UTF-8")
        except OSError as e:
            self.problems.add(origin, f"$file {ref!r} can't be read: {e.strerror or e}")
        else:
            self.referenced.add(target)
            return body
        return ""

    def unreferenced(self) -> list[Problem]:
        """Code files no `$file` names: most likely renamed or left behind, since nothing reads them."""
        return [
            Problem(Origin(_relative(self.root, path)), "no $file refers to this code file")
            for path in _walk(self.root)[0]
            if path.suffix.lower() in CODE_SUFFIXES and _resolved(path) not in self.referenced
        ]


def _singular(key: str) -> str:
    return key[:-1].replace("_", " ")


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _resolved(path: Path) -> Path | None:
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None


def _linked(root: Path, parts: tuple[str, ...]) -> bool:
    """Whether a path under the root is a link; refused, since Windows checkouts make links text files."""
    return root.joinpath(*parts).is_symlink()


def _unusable(path: Path) -> str | None:
    """Why a fragment can't be read as one, or None when it can."""
    try:
        if path.is_symlink():
            return "a link, which Tiggy doesn't follow"
        if not path.is_file():
            return "isn't a regular file"
    except OSError as e:
        return f"can't be read: {e.strerror or e}"
    return None


def _on_disk(root: Path, path: PurePosixPath) -> str:
    """A relative path spelled the way the filesystem stores it, which a typed path may not match.

    macOS and Windows find a file whatever its case or Unicode normalisation; Linux needs it exact.
    """
    current, spelled = root, []
    for part in path.parts:
        try:
            names = os.listdir(current)
        except OSError:
            return path.as_posix()
        match = part if part in names else next((n for n in names if _folded(n) == _folded(part)), part)
        spelled.append(match)
        current = current / match
    return "/".join(spelled)


def _folded(name: str) -> str:
    return unicodedata.normalize("NFC", unicodedata.normalize("NFD", name).casefold())


def _walk(root: Path) -> tuple[list[Path], list[Problem]]:
    """Every file under the root outside dot-directories, and what stopped Tiggy seeing the rest."""
    files: list[Path] = []
    problems: list[Problem] = []

    def unlistable(e: OSError) -> None:
        where = _relative(root, Path(e.filename)) if e.filename else str(root)
        problems.append(Problem(Origin(where), f"can't be listed: {e.strerror or e}"))

    for directory, subdirectories, names in os.walk(root, onerror=unlistable):
        here = Path(directory)
        kept = []
        for name in sorted(subdirectories):
            if name.startswith("."):
                continue
            where = Origin(_relative(root, here / name))
            try:
                linked = (here / name).is_symlink()
            except OSError as e:
                problems.append(Problem(where, f"can't be read: {e.strerror or e}"))
                continue
            if linked:
                problems.append(Problem(where, "a linked directory, which Tiggy doesn't follow"))
                continue
            kept.append(name)
        subdirectories[:] = kept
        files.extend(here / name for name in names if not name.startswith("."))
    return sorted(files, key=lambda p: _relative(root, p)), problems


def fragments(root: Path) -> list[Path]:
    return [p for p in _walk(root)[0] if p.suffix in FRAGMENT_SUFFIXES]


def looks_like_spec(root: Path) -> bool:
    """Whether a directory has anything a spec could be made of, so the loader's own reasons apply."""
    files, problems = _walk(root)
    return bool(problems) or any(p.suffix.lower() in FRAGMENT_SUFFIXES for p in files)


def load_spec(root: Path) -> LoadedSpec:
    spec = LoadedSpec(root)
    problems = spec.problems
    seen: dict[tuple[str, str], Origin] = {}
    files, walk_problems = _walk(root)
    problems.problems.extend(walk_problems)
    paths = []
    rejected = False
    for path in files:
        name = _relative(root, path)
        if path.suffix.lower() not in FRAGMENT_SUFFIXES:
            continue
        if path.suffix not in FRAGMENT_SUFFIXES:
            problems.add(Origin(name), "fragments end in lower-case .yml or .yaml")
        elif reason := _unusable(path):
            problems.add(Origin(name), reason)
        else:
            paths.append(path)
            continue
        rejected = True
    if not paths and not rejected:
        problems.add(Origin(str(root)), "no .yml fragments found; is this a spec directory?")
    # Once a fragment is missing or unread, "no fragment defines `container`" may only be that.
    loaded = not rejected
    if os.path.lexists(root / LOCK):
        problems.add(
            Origin(LOCK), "an import into this directory was interrupted, so the spec may be incomplete"
        )
        loaded = False
    for path in paths:
        name = _relative(root, path)
        try:
            tree = load(path.read_bytes().decode("utf-8"))
        except UnicodeDecodeError as e:
            problems.add(Origin(name), f"not UTF-8 (byte {e.start})")
            loaded = False
            continue
        except OSError as e:
            problems.add(Origin(name), f"can't be read: {e.strerror or e}")
            loaded = False
            continue
        except YamlError as e:
            problems.add(Origin(name, e.line), str(e))
            loaded = False
            continue
        origin = Origin(name, 1)
        if not isinstance(tree, dict) or next(iter(tree), None) != "tiggy":
            problems.add(origin, "every fragment starts with `tiggy: 1`")
            loaded = False
            continue
        if type(tree["tiggy"]) is not int or tree["tiggy"] != VERSION:
            problems.add(
                at(origin, tree, "tiggy"),
                f"tiggy: {describe(tree['tiggy'])} is not a spec version this Tiggy reads ({VERSION})",
            )
            loaded = False
            continue
        for key, value in tree.items():
            here = at(origin, tree, key)
            if key == "tiggy" or key.startswith("x-"):
                continue
            if key not in TOP_LEVEL:
                problems.add(
                    here,
                    f"unknown top-level key {key!r}; expected one of {EXPECTED}",
                )
            elif key in SINGLETONS:
                if key in spec.singletons:
                    problems.add(here, f"{key} is already defined at {spec.singletons[key].origin}")
                elif not isinstance(value, dict):
                    problems.add(here, f"{key} must be a mapping")
                    loaded = False
                else:
                    spec.singletons[key] = Entry(value, at(origin, value))
            elif not isinstance(value, list):
                problems.add(here, f"{key} must be a list")
            else:
                entries = spec.collections.setdefault(key, [])
                for i, item in enumerate(value):
                    item_origin = at(Origin(name, value.item_lines[i]), item)
                    if not isinstance(item, dict):
                        problems.add(
                            item_origin, f"each entry under {key} is a mapping, got {describe(item)}"
                        )
                        continue
                    identity = item.get(KINDS[key])
                    if not isinstance(identity, str) or not identity:
                        problems.add(item_origin, f"each entry under {key} needs a non-empty {KINDS[key]}")
                        continue
                    if (key, identity) in seen:
                        problems.add(
                            item_origin,
                            f'{_singular(key)} "{identity}" is already defined at {seen[key, identity]}',
                        )
                        continue
                    seen[key, identity] = item_origin
                    entries.append(Entry(item, item_origin))
    if paths and loaded and "container" not in spec.singletons:
        problems.add(Origin(str(root)), "no fragment defines `container`")
    return spec
