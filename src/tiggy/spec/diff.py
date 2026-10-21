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
from tiggy.secrets import PLACEHOLDER, SecretRef, is_placeholder
from tiggy.spec import canon
from tiggy.spec.codec import EXPLICIT, NO_SHORTHAND, Codec, Unwritable, scalar
from tiggy.spec.compiler import BY_SPEC_KEY, Compiled
from tiggy.spec.layout import SECTION_ORDER
from tiggy.spec.loader import KINDS, Entry, LoadedSpec
from tiggy.spec.overlay import COLLECTIONS, DEFAULTS, UNMODELLED, Field, Role, fields
from tiggy.spec.problems import Origin, Window, at, at_item, difference, long, same, window
from tiggy.spec.refs import spelled
from tiggy.spec.yamlio import Map

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


class Form(StrEnum):
    """What a Change's σ values are, so `written` can spell them as a spec does."""

    PARAMETER = "parameter"  # one parameter, written on its own or as a list's item
    KEYED = "keyed"  # one parameter, written under its GTM key: the path's last segment
    PARAMETERS = "parameters"  # parameters σ keyed, or a list of them


@dataclass(frozen=True)
class Secret:
    """A secret's place in a value: named by the spec, or None when recognised only by its value."""

    name: str | None  # as it stands in: `name@version` where the spec pins a version
    stored: bool = True  # False: the container holds a value other than the store's
    ref: SecretRef | None = field(default=None, compare=False, repr=False)  # as the spec names it

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
    unexplained: str | None = None  # what the walk says in place of values, after any path; never a value
    form: Form | type[GtmModel] | None = None  # a model: an item `_spec_keyed`; None: as a spec writes it


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
    refs: Collection[SecretRef] = (),
) -> Diff:
    """`version` as exported against the spec compiled from `spec`, secrets standing in for themselves.

    `values` are fetched secrets by stand-in, compared as `_masked` says; `known` are values a secret
    holds, never shown; `refs` are the spec's secrets, so its side names each as it does. `exact` makes
    an object equal only by σ a difference too.
    """
    dumped = canon.dump(compiled.version)
    left, right = canon.canon(version), canon.canon(dumped)
    entries, scrub = _entries(spec), _Scrub(known, values, refs)
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


def _unkeyed(path: Path, wild: dict[Path, str | None]) -> bool:
    """Whether a list of parameters at `path`, which σ couldn't key, is where the spec holds a secret: it may
    show whole, so any of its parameters may hold the secret under another key."""
    return any(held[:-1] == path for held in wild)


class _Scrub:
    """A value with no secret left in it to show: each stand-in, each known secret value, and each string
    within a parameter where the spec holds a secret (`wild`), or a list of parameters `_unkeyed`, that it
    doesn't write out elsewhere (`public`) becomes a Secret; a known value inside a longer string is cut out.
    """

    def __init__(self, known: tuple[str, ...], values: dict[str, str] | None, refs: Collection[SecretRef]):
        self.values = values
        self.known = sorted({v for v in (*known, *(values or {}).values()) if v}, key=len, reverse=True)
        self.refs = {ref.placeholder: ref for ref in refs}

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
            if wild and _unkeyed(path, wild):
                wild = {path: None, **wild}
            return [self(value, wild, public, (*path, _item(value))) for value in node]
        if not isinstance(node, str):
            return node
        if is_placeholder(node):
            name = node.removesuffix(NOT_STORED)[len(PLACEHOLDER) :]
            return Secret(name, stored=not node.endswith(NOT_STORED), ref=self.refs.get(PLACEHOLDER + name))
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
            yield from self.sequence(x, y, path, nested, partial(_spec_keyed, f.model), f.model)
        else:
            yield from self.value(x, y, path)

    def parameters(self, x: Any, y: Any, path: Path) -> Iterator[Change]:
        """A parameter list or map: by GTM key where σ keyed it or could but for a repeated key, else as a
        sequence."""
        if isinstance(x, list) and isinstance(y, list):
            yield from self.sequence(x, y, path, self.parameter, form=Form.PARAMETER)
        elif (gx := _by_key(x)) is None or (gy := _by_key(y)) is None:
            yield from self.value(x, y, path, Form.PARAMETERS)
        else:
            for key in _keys(gy, gx):
                xs, ys = gx.get(key, []), gy.get(key, [])
                if xs and ys and len(xs) + len(ys) > 2:
                    side = SIDES[0] if len(xs) > 1 else SIDES[1]
                    yield self.change(Op.CHANGED, (*path, key), unexplained=f"repeated in {side}")
                else:
                    yield from self.parameter(_one(xs), _one(ys), (*path, key), keyed=True)

    def parameter(self, x: Any, y: Any, path: Path, keyed: bool = False) -> Iterator[Change]:
        """One difference where both read as different shorthand scalars; otherwise one per member."""
        if _same(x, y):
            return
        sx, sy = _scalar(x), _scalar(y)
        if sx is not NO_SHORTHAND and sy is not NO_SHORTHAND and not _same(sx, sy):
            yield from self.value(sx, sy, path)
            return
        if not (isinstance(x, dict) and isinstance(y, dict)):
            yield self.change(_op(x, y), path, x, y, form=Form.KEYED if keyed else Form.PARAMETER)
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
                yield from self.sequence(p, q, path, self.parameter, form=Form.PARAMETER)
            elif member == "map":
                yield from self.parameters(p, q, path)
            else:
                yield from self.value(p, q, at, Form.PARAMETERS if member == "list" else None)

    def sequence(
        self,
        a: list[Any],
        b: list[Any],
        path: Path,
        item: Callable[[Any, Any, Path], Iterator[Change]],
        whole: Callable[[Any], Any] = lambda v: v,
        form: Form | type[GtmModel] | None = None,
    ) -> Iterator[Change]:
        """Items matched in order; one gone in one place and new in another is moved, not two changes, and
        within a run that changed, one in place of a like one is walked as that one changed. In index order,
        a run's items gone before those new.

        `whole` spells an item only one side has, which a change shows whole, as `form` says.
        """
        ka, kb = [_key(v) for v in a], [_key(v) for v in b]
        runs = [
            (i1, i2, j1, j2, tag == "replace" and i2 - i1 == j2 - j1)  # aligned: paired by position
            for tag, i1, i2, j1, j2 in SequenceMatcher(None, ka, kb, autojunk=False).get_opcodes()
            if tag != "equal"
        ]
        moved: dict[int, int] = {}  # where the spec has an item: where the container has it
        new = [j for _, _, j1, j2, aligned in runs if not aligned for j in range(j1, j2)]
        for i in (i for i1, i2, _, _, aligned in runs if not aligned for i in range(i1, i2)):
            j = next((j for j in new if kb[j] == ka[i] and j not in moved), None)
            if j is not None:
                moved[j] = i
        for i1, i2, j1, j2, aligned in runs:
            gone = [i for i in range(i1, i2) if i not in moved.values()]
            came = [j for j in range(j1, j2) if j not in moved]
            pairs = dict(zip(gone, came)) if aligned else _paired(gone, came, a, b)
            gone = [i for i in gone if i not in pairs]
            came = [j for j in range(j1, j2) if j not in pairs.values()]
            for i, j in [*pairs.items(), (i2, j2)]:
                while gone and gone[0] < i:
                    n = gone.pop(0)
                    yield self.change(Op.ONLY_LEFT, (*path, n), whole(a[n]), form=form)
                while came and came[0] < j:
                    n = came.pop(0)
                    if n in moved:
                        yield self.change(Op.MOVED, (*path, n), moved[n], n)
                    else:
                        yield self.change(Op.ONLY_RIGHT, (*path, n), right=whole(b[n]), form=form)
                if i < i2:
                    yield from item(a[i], b[j], (*path, j))

    def value(self, x: Any, y: Any, path: Path, form: Form | None = None) -> Iterator[Change]:
        """`form` says what the values are when they aren't both mappings, which are walked key by key."""
        if _same(x, y):
            return
        if isinstance(x, dict) and isinstance(y, dict):
            for key in _keys(y, x):
                yield from self.value(x.get(key, ABSENT), y.get(key, ABSENT), (*path, key))
        elif isinstance(x, str) and isinstance(y, str) and long(x, y):
            yield self.change(Op.CHANGED, path, x, y, window=window(x, y))
        else:
            yield self.change(_op(x, y), path, x, y, form=form)


def _same(x: Any, y: Any) -> bool:
    return x is y if ABSENT in (x, y) else same(x, y)


def _paired(gone: list[int], came: list[int], a: list[Any], b: list[Any]) -> dict[int, int]:
    """Items gone from a run paired with those new in it where at least half of what they hold is equal,
    the likest and nearest first, so a renamed cell reads as itself rather than as one row gone and another
    new. Never out of order, which would hide a move."""
    theirs = [_leaves(shown(b[j])) for j in came]
    scored = []
    for x, i in enumerate(gone):
        mine = _leaves(shown(a[i]))
        for y, j in enumerate(came):
            if (score := _likeness(mine, theirs[y])) >= 0.5:
                scored.append((-score, abs(x - y), i, j))
    paired: dict[int, int] = {}
    for _, _, i, j in sorted(scored):
        crossed = any((i < k) != (j < v) for k, v in paired.items())
        if i not in paired and j not in paired.values() and not crossed:
            paired[i] = j
    return dict(sorted(paired.items()))


def _likeness(x: dict[Path, str], y: dict[Path, str]) -> float:
    """The share of leaves (scalars and lists, by path into mappings) equal on both sides."""
    union = x.keys() | y.keys()
    equal = sum(k in x and k in y and x[k] == y[k] for k in union)
    return equal / len(union) if union else 0.0


def _leaves(value: Any, path: Path = ()) -> dict[Path, str]:
    if isinstance(value, dict) and value:
        return {k: v for key, item in value.items() for k, v in _leaves(item, (*path, key)).items()}
    return {path: _key(value)}


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


def _by_key(params: Any) -> dict[str, list[Any]] | None:
    """Parameters σ keyed, or listed for a repeated key, as each key's; None where one has no key."""
    if isinstance(params, dict):
        return {key: [p] for key, p in params.items()}
    if not isinstance(params, list) or not all(
        isinstance(p, dict) and isinstance(p.get("key"), str) for p in params
    ):
        return None
    found: dict[str, list[Any]] = {}
    for p in params:
        found.setdefault(p["key"], []).append({k: v for k, v in p.items() if k != "key"})
    return found


def _one(items: list[Any]) -> Any:
    return ABSENT if not items else items[0] if len(items) == 1 else items


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


def written(value: Any, form: Form | type[GtmModel] | None, secret: Callable[[Secret], str]) -> Any:
    """A Change's side as a spec writes it, through the codec, each Secret the string `secret` gives it; σ's
    spelling for any parameter the codec can't write."""
    return _Spelling().side(_secrets(value, secret), form)


def _secrets(value: Any, secret: Callable[[Secret], str]) -> Any:
    if isinstance(value, Secret):
        return secret(value)
    if isinstance(value, dict):
        return type(value)((key, _secrets(v, secret)) for key, v in value.items())
    if isinstance(value, list):
        return [_secrets(v, secret) for v in value]
    return value


class _Spelling:
    """σ back to GTM's shape for the codec, a trigger reference standing in as a token it can round-trip."""

    def __init__(self):
        self.refs: dict[str, Any] = {}
        self.tokens: dict[str, str] = {}
        self.codec = Codec(
            trigger_ref=self.refs.__getitem__, trigger_id=lambda ref, _: self.tokens[_key(ref)]
        )

    def side(self, value: Any, form: Form | type[GtmModel] | None) -> Any:
        if form is Form.PARAMETER:
            return self.parameter(value)
        if form is Form.KEYED:
            return self.parameter(value, keyed=True)
        if form is Form.PARAMETERS:
            return self.parameters(value)
        if isinstance(form, type):
            return self.nested(form, value)
        return value

    def parameter(self, p: Any, keyed: bool = False) -> Any:
        if not isinstance(p, dict):
            return p
        try:  # a keyed parameter never writes its key, so any will do
            return self.codec.encode(self.gtm(p, "" if keyed else None), keyed)
        except (Unwritable, ValueError, KeyError):
            return shown(p)

    def parameters(self, value: Any) -> Any:
        if isinstance(value, dict):
            items = [self.gtm(p, key) for key, p in value.items()]
        elif isinstance(value, list) and all(
            isinstance(p, dict) and isinstance(p.get("key"), str) for p in value
        ):
            items = [self.gtm(p) for p in value]
        elif isinstance(value, list):
            return [self.parameter(p) for p in value]
        else:
            return value
        try:
            return self.codec.encode_list(items)
        except (Unwritable, ValueError, KeyError):
            return shown(value)

    def nested(self, model: type[GtmModel], value: Any) -> Any:
        """An item `_spec_keyed`: spec keys already, but σ parameters."""
        if not isinstance(value, dict):
            return value
        by_key = {f.key: f for f in fields(model)}
        out = Map()
        for key, v in value.items():
            f = by_key.get(key)
            if f is None or v is None:
                out[key] = v
            elif f.role is Role.PARAMETER:
                out[key] = self.parameter(v)
            elif f.role is Role.PARAMETER_LIST:
                out[key] = self.parameters(v)
            elif f.role is Role.NESTED:
                out[key] = self.nested(f.model, v)
            elif f.role is Role.NESTED_LIST and isinstance(v, list):
                out[key] = [self.nested(f.model, item) for item in v]
            else:
                out[key] = v
        return out

    def gtm(self, p: Any, key: str | None = None) -> Any:
        """A σ parameter as GTM holds it, under `key` where σ keyed it by one, its empty list or map back."""
        if not isinstance(p, dict):
            return p
        out = {} if key is None else {"key": key}
        out.update(p)
        if out.get("type") == "TRIGGER_REFERENCE" and isinstance(out.get("value"), list):
            ref = spelled(out["value"], Role.TRIGGER_REFS)
            out["value"] = self.tokens.setdefault(_key(ref), f"ref{len(self.tokens)}")
            self.refs[out["value"]] = ref
        if isinstance(p.get("map"), dict):
            out["map"] = [self.gtm(v, k) for k, v in p["map"].items()]
        elif isinstance(p.get("map"), list):
            out["map"] = [self.gtm(v) for v in p["map"]]
        if isinstance(p.get("list"), list):
            out["list"] = [self.gtm(v) for v in p["list"]]
        for member, kind in (("list", "LIST"), ("map", "MAP")):
            if out.get("type") == kind and member not in out and "value" not in out:
                out[member] = []
        return out


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
