"""Errors found in a spec, collected rather than raised one at a time."""

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Origin:
    file: str
    line: int | None = None

    def __str__(self) -> str:
        return f"{self.file}:{self.line}" if self.line else self.file


@dataclass(frozen=True)
class Problem:
    origin: Origin | None
    message: str

    def __str__(self) -> str:
        return f"{self.origin}: {self.message}" if self.origin else self.message


class SpecError(Exception):
    def __init__(self, problems: list[Problem]):
        super().__init__("\n".join(str(p) for p in problems))
        self.problems = problems


@dataclass
class Collector:
    """Accumulates problems so one run reports all of them, the way Tundra does."""

    problems: list[Problem] = field(default_factory=list)

    def add(self, origin: Origin | None, message: str) -> None:
        self.problems.append(Problem(origin, message))


def at(origin: Origin, node: Any, key: str | None = None) -> Origin:
    """The origin of a loaded node, or of one of its keys, within a file."""
    lines = getattr(node, "key_lines", None)
    if key is not None and lines and key in lines:
        return Origin(origin.file, lines[key])
    return Origin(origin.file, getattr(node, "line", None) or origin.line)


def at_item(origin: Origin, seq: Any, index: int) -> Origin:
    """The origin of one item in a loaded sequence."""
    lines = getattr(seq, "item_lines", None)
    if lines and index < len(lines):
        return Origin(origin.file, lines[index])
    return at(origin, seq)


def same(a: Any, b: Any) -> bool:
    return difference(a, b) is None


def difference(
    a: Any, b: Any, path: str = "", sides: tuple[str, str] = ("the left", "the right")
) -> str | None:
    """Where two JSON-like values first differ. Dict order is ignored; types are exact (True is not 1)."""
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b), key=str):
            here = f"{path}.{key}" if path else str(key)
            if key not in a or key not in b:
                return f"{here} (missing from {sides[0] if key not in a else sides[1]})"
            found = difference(a[key], b[key], here, sides)
            if found:
                return found
        return None
    if isinstance(a, list | tuple) and isinstance(b, list | tuple):
        if len(a) != len(b):
            return f"{path or '(root)'} ({len(a)} items in {sides[0]}, {len(b)} in {sides[1]})"
        for i, (x, y) in enumerate(zip(a, b)):
            found = difference(x, y, f"{path}[{i}]", sides)
            if found:
                return found
        return None
    if isinstance(a, str) and isinstance(b, str) and a != b and max(len(repr(a)), len(repr(b))) > 80:
        i = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
        line, column = a.count("\n", 0, i) + 1, i - (a.rfind("\n", 0, i) + 1) + 1
        window = slice(max(0, i - 20), i + 40)
        return (
            f"{path or '(root)'} (from line {line}, character {column}: "
            f"{a[window]!r} in {sides[0]}, {b[window]!r} in {sides[1]})"
        )
    if type(a) is not type(b) or a != b:
        return f"{path or '(root)'} ({_short(a)} in {sides[0]}, {_short(b)} in {sides[1]})"
    return None


def describe(value: Any, limit: int = 80) -> str:
    """A value as the spec spells it (true, null, {a: 1}), cut short so a long one can't flood a message."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, dict | list):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = repr(value)
    return text if len(text) <= limit else f"{text[: limit - 15]}... ({len(text)} chars)"


def _short(value: Any, limit: int = 80) -> str:
    text = repr(value)
    return text if len(text) <= limit else f"{text[: limit - 15]}... ({len(text)} chars)"
