"""What differs between a container and a spec: every difference in each object, where the spec spells it.

`same` on σ (canon.semantic) decides whether an object differs; the walk only says where, so it can
spell a difference badly but can't hide one. An object that differs where the walk finds nothing still
gets a Change, carrying no values. Secrets are masked before the verdict and scrubbed from both sides
after it, so no Change holds a secret's value.
"""

import json
import re
from collections import Counter
from collections.abc import Callable, Collection, Iterator
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from enum import StrEnum
from functools import partial
from pathlib import PurePosixPath
from typing import Any

from tiggy.gtm.base import GtmModel
from tiggy.gtm.resources import Container, ContainerVersion, GtagConfig
from tiggy.secrets import PLACEHOLDER, is_placeholder
from tiggy.spec import canon
from tiggy.spec.codec import EXPLICIT, NO_SHORTHAND, scalar
from tiggy.spec.compiler import BY_SPEC_KEY, Compiled
from tiggy.spec.layout import SECTION_ORDER
from tiggy.spec.loader import KINDS, Entry, LoadedSpec
from tiggy.spec.overlay import COLLECTIONS, DEFAULTS, UNMODELLED, Field, Role, fields
from tiggy.spec.problems import Origin, Window, at, at_item, difference, long, same, window
from tiggy.spec.refs import spelled

SIDES = ("the container", "the spec")
NOT_STORED = ", not the stored value"
REFS = (Role.TRIGGER_REFS, Role.FOLDER_REF, Role.TYPE_REF, Role.TYPE_REFS)
NAMING = ("type", "key")  # a parameter's members that say what it is, never what it holds
Path = tuple[str | int, ...]


class _Absent:
    def __repr__(self) -> str:
        return "ABSENT"


ABSENT: Any = _Absent()  # nothing on that side, which None (JSON null) can't stand for


class Op(StrEnum):
    ONLY_LEFT = "only_left"
    ONLY_RIGHT = "only_right"
    CHANGED = "changed"
    MOVED = "moved"  # a list item on both sides at different indexes, which left and right hold


@dataclass(frozen=True)
class Secret:
    """A secret's place in a value: named by the spec, or None when recognised only by its value."""

    name: str | None
    stored: bool = True  # False: the container holds a value other than the store's

    def __str__(self) -> str:
        if self.name is None:
            return "<secret>"
        return f"<secret {self.name}{'' if self.stored else NOT_STORED}>"


@dataclass(frozen=True)
class Change:
    collection: str  # the spec's top-level key: tags, triggers, ..., container, version
    identity: str | None  # name; type for built-ins; label, tagId or type for gtag configs
    op: Op
    path: Path = ()  # spec keys, GTM parameter keys and indexes; () is the whole object
    left: Any = ABSENT  # the container's side, scrubbed
    right: Any = ABSENT
    origin: Origin | None = None
    window: Window | None = None
    members: bool = False  # a trigger set: left and right hold only the members that differ
    default: bool = False  # one side lacks only what GTM may fill in (unverified)
    unexplained: str | None = None  # the whole message, when the walk can't say more; never a value


@dataclass
class Diff:
    changes: list[Change] = field(default_factory=list)  # by object, in spec section order
    unread_secrets: int = 0
    semantic_only: int = 0  # objects equal by σ but not exactly


@dataclass(frozen=True)
class _Subject:
    collection: str
    identity: str | None
    model: type[GtmModel]
    a: Any  # exact canon; ABSENT when only in the spec
    b: Any


def compare(
    version: dict[str, Any],
    compiled: Compiled,
    spec: LoadedSpec,
    values: dict[str, str] | None = None,
    known: tuple[str, ...] = (),
    exact: bool = False,
) -> Diff:
    """`version` as exported against the spec compiled from `spec`, secrets standing in for themselves.

    `values` are fetched secrets by stand-in, compared as `_masked` says; `known` are values a secret
    holds, never shown. `exact` makes an object equal only by σ a difference too.
    """
    dumped = canon.dump(compiled.version)
    left, right = canon.canon(version), canon.canon(dumped)
    entries, scrub = _entries(spec), _Scrub(known, values)
    found = Diff()
    for s in _subjects(left, right, _labels(compiled, dumped), values):
        entry = entries.get((s.collection, s.identity))
        here = entry.origin if entry else None
        if s.a is ABSENT or s.b is ABSENT:
            only = Op.ONLY_RIGHT if s.a is ABSENT else Op.ONLY_LEFT
            found.changes.append(
                Change(s.collection, s.identity, only, origin=here if s.a is ABSENT else None)
            )
            continue
        a, b = canon.semantic(s.a, s.model), canon.semantic(s.b, s.model)
        a, unread = _masked(a, b, values)
        found.unread_secrets += unread
        if same(a, b):
            if not same(_masked(s.a, s.b, values)[0], s.b):
                found.semantic_only += 1
                if exact:
                    loose = "differs only in order or empty lists"
                    found.changes.append(
                        Change(s.collection, s.identity, Op.CHANGED, origin=here, unexplained=loose)
                    )
            continue
        walk = _Walk(s).object(s.model, scrub(a, *_wild(b)), scrub(b))
        changes = [
            replace(c, origin=locate(entry, c) if entry else None, default=_default(s, c)) for c in walk
        ]
        if not changes:
            path = difference(a, b).split(" (", 1)[0]  # the path alone: what follows holds values
            changes = [
                Change(s.collection, s.identity, Op.CHANGED, origin=here, unexplained=f"differs at {path}")
            ]
        found.changes += changes
    return found


def _subjects(
    left: dict[str, Any], right: dict[str, Any], labels: dict[str, str], values: dict[str, str] | None
) -> Iterator[_Subject]:
    """Each object in either canon, in spec section order; what no collection holds belongs to `version`."""
    collected = {alias for alias in COLLECTIONS if _collected(alias, left, right)}
    for key in ("container", "version", *sorted(BY_SPEC_KEY, key=SECTION_ORDER.index)):  # raises on a gap
        if key == "container":
            yield _Subject(key, None, Container, left.get(key, {}), right.get(key, {}))
        elif key == "version":
            a, b = (
                {k: v for k, v in side.items() if k != "container" and k not in collected}
                for side in (left, right)
            )
            yield _Subject(key, None, ContainerVersion, a, b)
        elif (alias := BY_SPEC_KEY[key][0]) in collected:
            model, a, b = BY_SPEC_KEY[key][1], left.get(alias, {}), right.get(alias, {})
            if model is GtagConfig:
                yield from _gtag_configs(a, b, labels, values)
                continue
            for identity in sorted(set(a) | set(b), key=str):
                yield _Subject(key, identity, model, a.get(identity, ABSENT), b.get(identity, ABSENT))


def _collected(alias: str, left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Whether both sides hold the collection as canon keys it: by name, or gtag configs as a list."""
    shape = list if COLLECTIONS[alias][2] == "label" else dict
    return all(isinstance(side.get(alias, shape()), shape) for side in (left, right))


def _gtag_configs(
    a: list[str], b: list[str], labels: dict[str, str], values: dict[str, str] | None
) -> Iterator[_Subject]:
    """Paired by tagId when every config on both sides has its own; otherwise each pairs with one equal to it
    once its secrets are masked, named by the spec's label where there is one, else by type.

    Unverified: no export to hand has a gtag config.
    """
    left, right = [json.loads(c) for c in a], [json.loads(c) for c in b]
    ids = [_tag_id(c) for c in left], [_tag_id(c) for c in right]
    if all(i for side in ids for i in side) and all(len(set(side)) == len(side) for side in ids):
        by_id = dict(zip(ids[0], left)), dict(zip(ids[1], right))
        for i in sorted(set(by_id[0]) | set(by_id[1])):
            yield _Subject(
                "gtag_configs", labels.get(i, i), GtagConfig, by_id[0].get(i, ABSENT), by_id[1].get(i, ABSENT)
            )
        return
    rest = [(y, canon.semantic(y, GtagConfig)) for y in right]
    for x in left:
        sx = canon.semantic(x, GtagConfig)
        i = next((i for i, (_, sy) in enumerate(rest) if same(_masked(sx, sy, values)[0], sy)), None)
        if i is None:
            yield _Subject("gtag_configs", x.get("type"), GtagConfig, x, ABSENT)
        else:
            y = rest.pop(i)[0]
            yield _Subject("gtag_configs", labels.get(_key(y), y.get("type")), GtagConfig, x, y)
    for y, _ in rest:
        yield _Subject("gtag_configs", labels.get(_key(y), y.get("type")), GtagConfig, ABSENT, y)


def _tag_id(config: dict[str, Any]) -> str | None:
    found = [
        p.get("value")
        for p in config.get("parameter") or []
        if isinstance(p, dict) and p.get("key") == "tagId" and p.get("type") == "TEMPLATE"
    ]
    return found[0] if len(found) == 1 and isinstance(found[0], str) and found[0] else None


def _labels(compiled: Compiled, dumped: dict[str, Any]) -> dict[str, str]:
    """Each gtag config's spec label, by its tagId and by its canon (JSON), which `_key` also gives."""
    found = {}
    for c in dumped.get("gtagConfig") or []:
        label = compiled.labels.get(c.get("gtagConfigId"))
        if label is None:
            continue
        (exact,) = canon.canon({**dumped, "gtagConfig": [c]})["gtagConfig"]
        found[exact] = label
        if tag_id := _tag_id(c):
            found[tag_id] = label
    return found


def _entries(spec: LoadedSpec) -> dict[tuple[str, str | None], Entry]:
    found: dict[tuple[str, str | None], Entry] = {(key, None): e for key, e in spec.singletons.items()}
    for key, entries in spec.collections.items():
        found.update(((key, e.node[KINDS[key]]), e) for e in entries)
    return found


def _masked(left: Any, right: Any, values: dict[str, str] | None) -> tuple[Any, int]:
    """`left` with the value at each of the spec's secrets replaced by the secret's stand-in.

    Without `values`, any non-empty value counts as the secret, unread. With them, a value that isn't
    the secret's becomes a second stand-in, so the difference shows without showing either value.
    """
    if is_placeholder(right) and isinstance(left, str):
        if values is None:
            return (right, 1) if left else (left, 0)
        return (right, 0) if values.get(right) == left else (f"{right}{NOT_STORED}", 0)
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


def _wild(node: Any) -> tuple[dict[Path, str | None], set[str]]:
    """Each parameter where the spec's side holds a secret, by `_item` path, with the secret's name (None
    where names differ); and every other string it holds, which a spec writes out, so no secret."""
    wild: dict[Path, str | None] = {}
    public: set[str] = set()

    def walk(node: Any, path: Path) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, (*path, key))
        elif isinstance(node, list):
            for value in node:
                walk(value, (*path, _item(value)))
        elif is_placeholder(node):
            name, held = node[len(PLACEHOLDER) :], path[:-1]  # a secret is always a parameter's value
            wild[held] = name if wild.get(held, name) == name else None
        elif isinstance(node, str):
            public.add(node)

    walk(node, ())
    return wild, public


def _item(value: Any) -> str:
    """A list item in a secret's path: by its GTM key where it has one, so a list σ couldn't key still lines
    up with one it did; else `*`, so a secret's column covers every row."""
    key = value.get("key") if isinstance(value, dict) else None
    return key if isinstance(key, str) else "*"


def _holder(path: Path, wild: dict[Path, str | None]) -> Path | None:
    """The parameter holding a secret that `path` is within, whatever shape it has taken there."""
    return next((path[:i] for i in range(len(path), 0, -1) if path[:i] in wild), None)


class _Scrub:
    """A value with no secret left in it to show: each stand-in, each known secret value, and each string
    within a parameter where the spec holds a secret (`wild`) that it doesn't write out elsewhere
    (`public`) becomes a Secret; a known value inside a longer string is cut out.
    """

    def __init__(self, known: tuple[str, ...], values: dict[str, str] | None):
        self.values = values
        self.known = sorted({v for v in (*known, *(values or {}).values()) if v}, key=len, reverse=True)

    def __call__(
        self,
        node: Any,
        wild: dict[Path, str | None] | None = None,
        public: Collection[str] = (),
        path: Path = (),
    ) -> Any:
        if isinstance(node, dict):
            return {key: self(value, wild, public, (*path, key)) for key, value in node.items()}
        if isinstance(node, list):
            return [self(value, wild, public, (*path, _item(value))) for value in node]
        if not isinstance(node, str):
            return node
        if is_placeholder(node):
            name = node[len(PLACEHOLDER) :]
            return Secret(name.removesuffix(NOT_STORED), stored=not name.endswith(NOT_STORED))
        held = _holder(path, wild) if wild and node and node not in public else None
        if held is not None and path[-1] not in NAMING:
            name = wild[held]
            stored = self.values is None or name is None or self.values.get(PLACEHOLDER + name) == node
            return Secret(name, stored)
        if node in self.known:
            return Secret(None)
        for value in self.known:
            node = node.replace(value, str(Secret(None)))
        return node


def _default(subject: _Subject, change: Change) -> bool:
    if change.op not in (Op.ONLY_LEFT, Op.ONLY_RIGHT):
        return False
    side, value = (subject.a, change.left) if change.op is Op.ONLY_LEFT else (subject.b, change.right)
    kind = side.get("type") if isinstance(side.get("type"), str) else None
    keys = [(subject.model, None, change.path), (subject.model, kind, change.path)]
    return any(key in DEFAULTS and same(DEFAULTS[key], value) for key in keys)


class _Walk:
    """The differences between one object's two sides, σ and scrubbed, at the paths a spec spells."""

    def __init__(self, subject: _Subject):
        self.subject = subject

    def change(self, op: Op, path: Path, left: Any = ABSENT, right: Any = ABSENT, **more: Any) -> Change:
        return Change(self.subject.collection, self.subject.identity, op, path, left, right, **more)

    def object(self, model: type[GtmModel], a: Any, b: Any, path: Path = ()) -> Iterator[Change]:
        if not (isinstance(a, dict) and isinstance(b, dict)):
            yield from self.value(a, b, path)
            return
        modelled = fields(model)
        for f in modelled:
            yield from self.field(f, a.get(f.alias, ABSENT), b.get(f.alias, ABSENT), (*path, f.key))
        known = {f.alias for f in modelled}
        for key in _keys(b, a):
            if key not in known:
                yield from self.value(a.get(key, ABSENT), b.get(key, ABSENT), (*path, UNMODELLED, key))

    def field(self, f: Field, x: Any, y: Any, path: Path) -> Iterator[Change]:
        if _same(x, y):
            return
        if f.role is Role.TRIGGER_REFS and isinstance(x, list) and isinstance(y, list):
            gone, new = _spelled(_minus(x, y), f.role), _spelled(_minus(y, x), f.role)
            yield self.change(Op.CHANGED, path, gone, new, members=True)
        elif f.role in REFS:
            form = _spelled(x, f.role) == _spelled(y, f.role)  # a template's form is all that differs
            x, y = _spelled(x, f.role, form), _spelled(y, f.role, form)
            yield self.change(_op(x, y), path, x, y)
        elif x is ABSENT or y is ABSENT:
            yield self.change(_op(x, y), path, x, y)
        elif f.role is Role.PARAMETER:
            yield from self.parameter(x, y, path)
        elif f.role is Role.PARAMETER_LIST:
            yield from self.parameters(x, y, path)
        elif f.role is Role.NESTED:
            yield from self.object(f.model, x, y, path)
        elif f.role is Role.NESTED_LIST and isinstance(x, list) and isinstance(y, list):
            nested = partial(self.object, f.model)
            yield from self.sequence(x, y, path, nested, partial(_spec_keyed, f.model))
        else:
            yield from self.value(x, y, path)

    def parameters(self, x: Any, y: Any, path: Path) -> Iterator[Change]:
        """A parameter list or map: by GTM key where σ keyed it, else as a sequence."""
        if isinstance(x, dict) and isinstance(y, dict):
            for key in _keys(y, x):
                yield from self.parameter(x.get(key, ABSENT), y.get(key, ABSENT), (*path, key))
        elif isinstance(x, list) and isinstance(y, list):
            yield from self.sequence(x, y, path, self.parameter)
        else:
            yield from self.value(x, y, path)

    def parameter(self, x: Any, y: Any, path: Path) -> Iterator[Change]:
        """One difference where both read as different shorthand scalars; otherwise one per member."""
        if _same(x, y):
            return
        sx, sy = _scalar(x), _scalar(y)
        if sx is not NO_SHORTHAND and sy is not NO_SHORTHAND and not _same(sx, sy):
            yield from self.value(sx, sy, path)
            return
        if not (isinstance(x, dict) and isinstance(y, dict)):
            yield self.change(_op(x, y), path, x, y)
            return
        for member in _keys(y, x):
            p, q = x.get(member, ABSENT), y.get(member, ABSENT)
            if member in ("list", "map") and x.get("type") == y.get("type"):
                p, q = _emptied(p, q), _emptied(q, p)
            at = (*path, EXPLICIT[member]) if member in EXPLICIT else (*path, "$unmodelled", member)
            if _same(p, q):
                continue
            if p is ABSENT or q is ABSENT:
                yield self.change(_op(p, q), at, p, q)
            elif member == "value" and "TRIGGER_REFERENCE" in (x.get("type"), y.get("type")):
                yield self.change(Op.CHANGED, path, *(spelled(v, Role.TRIGGER_REFS) for v in (p, q)))
            elif member == "value":  # at the parameter itself, as a side reads in shorthand where it can
                yield from self.value(p if sx is NO_SHORTHAND else sx, q if sy is NO_SHORTHAND else sy, path)
            elif member == "list" and isinstance(p, list) and isinstance(q, list):
                yield from self.sequence(p, q, path, self.parameter)
            elif member == "map":
                yield from self.parameters(p, q, path)
            else:
                yield from self.value(p, q, at)

    def sequence(
        self,
        a: list[Any],
        b: list[Any],
        path: Path,
        item: Callable[[Any, Any, Path], Iterator[Change]],
        whole: Callable[[Any], Any] = lambda v: v,
    ) -> Iterator[Change]:
        """Items matched in order; one gone in one place and new in another is moved, not two changes.

        `whole` spells an item only one side has, which a change shows whole.
        """
        ka, kb = [_key(v) for v in a], [_key(v) for v in b]
        gone, new = [], []
        for tag, i1, i2, j1, j2 in SequenceMatcher(None, ka, kb, autojunk=False).get_opcodes():
            if tag == "replace" and i2 - i1 == j2 - j1:
                for i, j in zip(range(i1, i2), range(j1, j2)):
                    yield from item(a[i], b[j], (*path, j))
            elif tag != "equal":
                gone += range(i1, i2)
                new += range(j1, j2)
        for i in gone:
            j = next((j for j in new if kb[j] == ka[i]), None)
            if j is None:
                yield self.change(Op.ONLY_LEFT, (*path, i), whole(a[i]))
            else:
                new.remove(j)
                yield self.change(Op.MOVED, (*path, j), i, j)
        for j in new:
            yield self.change(Op.ONLY_RIGHT, (*path, j), right=whole(b[j]))

    def value(self, x: Any, y: Any, path: Path) -> Iterator[Change]:
        if _same(x, y):
            return
        if isinstance(x, dict) and isinstance(y, dict):
            for key in _keys(y, x):
                yield from self.value(x.get(key, ABSENT), y.get(key, ABSENT), (*path, key))
        elif isinstance(x, str) and isinstance(y, str) and long(x, y):
            yield self.change(Op.CHANGED, path, x, y, window=window(x, y))
        else:
            yield self.change(_op(x, y), path, x, y)


def _same(x: Any, y: Any) -> bool:
    return x is y if ABSENT in (x, y) else same(x, y)


def _op(x: Any, y: Any) -> Op:
    return Op.ONLY_RIGHT if x is ABSENT else Op.ONLY_LEFT if y is ABSENT else Op.CHANGED


def _keys(first: dict[str, Any], then: dict[str, Any]) -> list[str]:
    """Keys in `first`'s order (the spec's), then those only in `then`."""
    return [*first, *(key for key in then if key not in first)]


def _key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=repr)


def _minus(a: list[Any], b: list[Any]) -> list[Any]:
    """`a` without one of each item `b` has: a multiset difference, type-exact."""
    rest = Counter(map(_key, b))
    out = []
    for value in a:
        if rest[_key(value)]:
            rest[_key(value)] -= 1
        else:
            out.append(value)
    return out


def _spelled(value: Any, role: Role, form: bool = False) -> Any:
    if isinstance(value, list) and role in (Role.TRIGGER_REFS, Role.TYPE_REFS):
        return [spelled(v, role, form) for v in value]
    return spelled(value, role, form)


def _scalar(p: Any) -> Any:
    """The scalar a parameter is written as in shorthand, or NO_SHORTHAND."""
    return scalar(p) if isinstance(p, dict) and set(p) <= {"type", "value"} else NO_SHORTHAND


def _emptied(p: Any, other: Any) -> Any:
    """ABSENT as the empty list or map σ dropped, shaped like `other`, so the rows or keys differing show."""
    return type(other)() if p is ABSENT and isinstance(other, list | dict) else p


def _spec_keyed(model: type[GtmModel], value: Any) -> Any:
    """A nested object's σ under the spec's keys and in its order, as an item only one side has shows."""
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for f in fields(model):
        if f.alias not in value:
            continue
        v = value[f.alias]
        if f.role is Role.NESTED:
            v = _spec_keyed(f.model, v)
        elif f.role is Role.NESTED_LIST and isinstance(v, list):
            v = [_spec_keyed(f.model, item) for item in v]
        elif f.role in REFS:
            v = _spelled(v, f.role)
        out[f.key] = v
    known = {f.alias for f in fields(model)}
    if extras := {k: v for k, v in value.items() if k not in known}:
        out[UNMODELLED] = extras
    return out


def where(path: Path) -> str:
    """A path as the spec spells it: `parameter.eventSettingsTable[2].parameterValue`, `['a key']`."""
    out = ""
    for segment in path:
        if isinstance(segment, int):
            out += f"[{segment}]"
        elif re.fullmatch(r"\$?[\w-]+", segment):
            out += f".{segment}" if out else segment
        else:
            out += f"[{segment!r}]"
    return out


def shown(value: Any) -> Any:
    """A value as far as σ can say how a spec writes it: parameters in shorthand, secrets kept as Secret."""
    if isinstance(value, dict):
        if (found := _scalar(value)) is not NO_SHORTHAND:
            return shown(found)
        if value.get("type") == "MAP" and set(value) == {"type", "map"} and isinstance(value["map"], dict):
            return {key: shown(v) for key, v in value["map"].items()}
        if value.get("type") == "LIST" and set(value) == {"type", "list"} and isinstance(value["list"], list):
            return [shown(v) for v in value["list"]]
        return {key: shown(v) for key, v in value.items()}
    if isinstance(value, list):
        return [shown(v) for v in value]
    return value


def locate(entry: Entry, change: Change) -> Origin:
    """The line in the spec a Change is about: as deep as its path goes there, or a sidecar's line."""
    path = change.path[:-1] if change.op is Op.ONLY_LEFT else change.path  # the spec has nothing at the end
    node, origin = entry.node, entry.origin
    for segment in path:
        step = _child(node, segment, origin)
        if step is None:
            break
        node, origin = step
    if change.members and isinstance(node, list):
        first = next((i for i, item in enumerate(node) if item in change.right), None)
        origin = origin if first is None else at_item(origin, node, first)
    if isinstance(node, dict) and isinstance(node.get("$file"), str):
        texts = isinstance(change.left, str) and isinstance(change.right, str)
        found = change.window or (window(change.left, change.right) if texts else None)
        return Origin(PurePosixPath(node["$file"]).as_posix(), found.line if found else None)
    return origin


def _child(node: Any, segment: str | int, origin: Origin) -> tuple[Any, Origin] | None:
    """One step into a spec node along a diff path, and where it lands; None where the spec stops."""
    if isinstance(node, dict) and "$type" in node:  # an explicit parameter keeps its items in $list or $map
        member = "$list" if isinstance(segment, int) else None if segment.startswith("$") else "$map"
        if member and isinstance(node.get(member), list):
            node, origin = node[member], at(origin, node, member)
    if isinstance(segment, int):
        if isinstance(node, list) and segment < len(node):
            return node[segment], at_item(origin, node, segment)
    elif isinstance(node, dict) and segment in node:
        return node[segment], at(origin, node, segment)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            if isinstance(item, dict) and item.get("$key") == segment:
                return item, at_item(origin, node, i)
    return None
