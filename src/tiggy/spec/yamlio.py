"""The only module that touches YAML: a strict loader, a canonical emitter, and a guard joining them.

The loader types only `true`, `false`, `null` and canonical decimal ints. Any other plain scalar that
a YAML 1.1 or 1.2 reader would type (`no`, `0x10`, `1.5`, `2024-10-31`) is an error, so a file means
the same to Tiggy as to every other YAML tool. The emitter writes nothing the loader would misread,
and `render` proves it by reparsing before anything reaches disk.
"""

import bisect
import re
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml import events as ev
from ruamel.yaml.error import MarkedYAMLError, YAMLError
from ruamel.yaml.reader import ReaderError

CANONICAL_INT = re.compile(r"-?(0|[1-9][0-9]*)")
INT64 = range(-(2**63), 2**63)
# Plain scalars that ruamel or PyYAML, under YAML 1.1 or 1.2, read as something other than a string.
YAML_TYPED = re.compile(
    r"""(
      y|Y|yes|Yes|YES|n|N|no|No|NO|True|TRUE|False|FALSE|on|On|ON|off|Off|OFF|~|Null|NULL|<<|=
    | [-+]?0b[01_]+ | [-+]?0o?[0-7_]+ | [-+]?0x[0-9a-fA-F_]+ | [-+][0-9_]+ | [0-9][0-9_]*
    | [-+]?[1-9][0-9_]*(:[0-5]?[0-9])+
    | [-+]?[0-9][0-9_]*\.[0-9_]*([eE][-+]?[0-9]+)? | [-+]?[0-9][0-9_]*[eE][-+]?[0-9]+
    | [-+]?\.[0-9_]+([eE][-+][0-9]+)? | [-+]?[0-9][0-9_]*(:[0-5]?[0-9])+\.[0-9_]*
    | [-+]?\.(inf|Inf|INF) | \.(nan|NaN|NAN)
    | [0-9]{4}-[0-9]{1,2}-[0-9]{1,2}
      (([Tt]|[ \t]+)[0-9]{1,2}:[0-9]{1,2}:[0-9]{1,2}([.,][0-9]*)?([ \t]*(Z|[-+][0-9]{1,2}(:[0-9]{2})?))?)?
    )""",
    re.VERBOSE,
)
# go-yaml (yq) drops every _ from a scalar starting with a sign or digit, then reads it with Go's
# number parsers, which take any radix prefix in either case and a sign after 0b or 0o. Applied to a
# leading . too, which over-approximates go-yaml there.
GO_NUMBER = re.compile(
    r"""(
      [-+]?(0[xX][0-9a-fA-F]+ | 0[bB][01]+ | 0[oO][0-7]+ | 0[0-7]* | [1-9][0-9]*)
    | [-+]?(\.[0-9]+ | [0-9]+(\.[0-9]*)?)([eE][-+]?[0-9]+)?
    | 0b[-+]?[01]+ | -0b[01]+ | 0o[-+]?[0-7]+ | -0o[0-7]+
    )""",
    re.VERBOSE,
)
INDICATORS = set("-?:,[]{}#&*!|>'\"%@`")
UNSAFE_IN_PLAIN = set(",[]{}#\t")
UNSAFE_IN_FLOW = set("?:")
RAW_REFUSED = {"\u2028": "U+2028", "\u2029": "U+2029", "\x85": "U+0085", "\ufeff": "U+FEFF"}
MAX_IMPLICIT_KEY = 1000
MAX_DEPTH = 64  # well inside the nesting every reader (and GTM's JSON) handles
BLOCK_HEADER = re.compile(r"[|>]([1-9])?[-+]?([1-9])?")


class YamlError(Exception):
    def __init__(self, message: str, line: int | None = None):
        super().__init__(message)
        self.line = line


class Map(dict):
    """A loaded mapping that remembers where it and each of its keys appeared (1-based lines)."""

    line: int = 0
    end: int = 0
    key_lines: dict[str, int]


class Seq(list):
    line: int = 0
    end: int = 0
    item_lines: list[int]


class Flow(dict):
    """A mapping the emitter writes on one line."""


# Loading


def load(text: str) -> Any:
    _check_raw(text)
    events = _events(text)
    start = next((e for e in events if isinstance(e, ev.DocumentStartEvent)), None)
    if sum(isinstance(e, ev.DocumentStartEvent) for e in events) != 1:
        raise YamlError("a fragment holds exactly one YAML document", 1)
    if start.explicit or getattr(start, "version", None) or getattr(start, "tags", None):
        raise YamlError("document markers and % directives aren't allowed", 1)
    stream = iter(e for e in events if not isinstance(e, ev.StreamStartEvent | ev.StreamEndEvent))
    next(stream)
    root = next(stream)
    if isinstance(root, ev.DocumentEndEvent):
        raise YamlError("the fragment is empty", 1)
    return _Loader(text, stream).node(root, flow=False, indent=-1, depth=0)


def _events(text: str) -> list[ev.Event]:
    """Every parse event, stopping as soon as nesting passes MAX_DEPTH (ruamel slows with depth)."""
    events: list[ev.Event] = []
    depth = 0
    try:
        for event in YAML(typ="safe", pure=True).parse(text):
            events.append(event)
            if isinstance(event, ev.CollectionStartEvent):
                depth += 1
                if depth > MAX_DEPTH:
                    raise YamlError(f"nested more than {MAX_DEPTH} levels deep", _line(event))
            elif isinstance(event, ev.CollectionEndEvent):
                depth -= 1
    except MarkedYAMLError as e:
        mark = e.problem_mark or e.context_mark
        raise YamlError(
            _with_hint(f"not valid YAML: {e.problem or e.context}", text, mark), _mark_line(mark)
        ) from e
    except ReaderError as e:
        line = text[: e.position].count("\n") + 1 if isinstance(e.position, int) else None
        raise YamlError(f"not valid YAML: {e.reason} ({e.character!r})", line) from e
    except YAMLError as e:
        raise YamlError(f"not valid YAML: {e}") from e
    except (ValueError, OverflowError) as e:  # ruamel's own, for escapes past U+10FFFF
        line = _bad_escape_line(text)
        raise YamlError("not valid YAML: an escape past U+10FFFF, the last Unicode character", line) from e
    except RecursionError as e:
        raise YamlError("nested too deeply") from e
    return events


def _bad_escape_line(text: str) -> int | None:
    """The first line whose text, read up to there, trips the escape; the scanner reads in order."""
    lines = text.split("\n")
    low, high = 1, len(lines)
    while low < high:
        middle = (low + high) // 2
        if _trips_escape("\n".join(lines[:middle])):
            high = middle
        else:
            low = middle + 1
    return low if _trips_escape("\n".join(lines[:low])) else None


def _trips_escape(text: str) -> bool:
    try:
        for _ in YAML(typ="safe", pure=True).parse(text):
            pass
    except (ValueError, OverflowError):
        return True
    except Exception:
        return False
    return False


def _mark_line(mark: Any) -> int | None:
    return mark.line + 1 if mark else None


def _with_hint(message: str, text: str, mark: Any) -> str:
    """A parse error on a line holding an unquoted {{variable}} is almost always that."""
    line = text.split("\n")[mark.line] if mark and mark.line < text.count("\n") + 1 else ""
    if _unquoted_braces(line):
        return f"{message}. A value holding a {{{{variable}}}} needs quoting: '{{{{name}}}}'"
    return message


def _unquoted_braces(line: str) -> bool:
    """Whether a line has `{{` outside quotes and comments. A quote opens a scalar only at its start."""
    quote, i = None, 0
    while i < len(line):
        c = line[i]
        if quote == "'":
            if c == "'":
                quote, i = (None, i) if line[i + 1 : i + 2] != "'" else (quote, i + 1)
        elif quote == '"':
            if c == "\\":
                i += 1
            elif c == '"':
                quote = None
        elif c in "'\"" and (i == 0 or line[i - 1] in " \t[{,:-?"):
            quote = c
        elif c == "#" and (i == 0 or line[i - 1] in " \t"):
            return False
        elif line.startswith("{{", i):
            return True
        i += 1
    return False


def _check_raw(text: str) -> None:
    """Refuse characters YAML readers disagree about as line breaks or byte-order marks."""
    for number, line in enumerate(text.split("\n"), 1):
        line = line.removesuffix("\r")
        if "\r" in line:
            raise YamlError("a bare CR line ending: some YAML readers don't end a line there. Use LF", number)
        for char, name in RAW_REFUSED.items():
            if char in (line[1:] if number == 1 and char == "\ufeff" else line):
                raise YamlError(
                    f"raw {name} isn't allowed: YAML readers disagree about it. "
                    f'Write it as an escape in a double-quoted string ("\\u{ord(char):04x}")',
                    number,
                )


def _line(event: ev.Event) -> int:
    return event.start_mark.line + 1


class _Loader:
    def __init__(self, text: str, stream):
        self.text = text
        self.stream = stream
        self.quoted: list[tuple[int, int]] = []  # spans of quoted scalars in the current flow collection

    def node(self, event: ev.Event, flow: bool, indent: int, depth: int) -> Any:
        """`indent` is the column of the innermost block collection, as libyaml tracks it."""
        if isinstance(event, ev.AliasEvent) or getattr(event, "anchor", None):
            raise YamlError("anchors and aliases aren't allowed; each object stands alone", _line(event))
        if getattr(event, "tag", None):
            raise YamlError(f"explicit tags such as {event.tag} aren't allowed", _line(event))
        if isinstance(event, ev.ScalarEvent):
            self.portable(event, indent)
            if flow and event.style in ("'", '"'):
                self.quoted.append((event.start_mark.index, event.end_mark.index))
            try:
                return _scalar(event.value, event.style, flow)
            except ValueError as e:
                raise YamlError(str(e), _line(event)) from e
        if depth >= MAX_DEPTH:
            raise YamlError(f"nested more than {MAX_DEPTH} levels deep", _line(event))
        opens_flow = not flow and bool(getattr(event, "flow_style", False))
        if opens_flow:
            self.quoted = []
        flow = flow or opens_flow
        if not flow:
            indent = event.start_mark.column
        value = self.collection(event, flow, indent, depth + 1)
        if opens_flow:
            self.flow_tabs(event.start_mark.index, value.end)
        return value

    def collection(self, event: ev.Event, flow: bool, indent: int, depth: int) -> Any:
        if isinstance(event, ev.SequenceStartEvent):
            seq = Seq()
            seq.line, seq.item_lines = _line(event), []
            for item in self.stream:
                if isinstance(item, ev.SequenceEndEvent):
                    seq.end = item.end_mark.index
                    return seq
                seq.item_lines.append(_line(item))
                seq.append(self.node(item, flow, indent, depth))
        if isinstance(event, ev.MappingStartEvent):
            mapping = Map()
            mapping.line = _line(event)
            mapping.key_lines = {}
            for key_event in self.stream:
                if isinstance(key_event, ev.MappingEndEvent):
                    mapping.end = key_event.end_mark.index
                    return mapping
                if isinstance(key_event, ev.ScalarEvent) and key_event.value == "<<":
                    raise YamlError(
                        "<< can't be a key: YAML readers take it for a merge key, quoted or not",
                        _line(key_event),
                    )
                if flow and self.explicit_key(key_event.start_mark.index):
                    raise YamlError(
                        "drop the ? before a key inside {}: YAML readers disagree on whether ?a is the key "
                        "a or ?a. Quote a key that starts with ?",
                        _line(key_event),
                    )
                key = self.node(key_event, flow, indent, depth)
                if not isinstance(key, str):
                    raise YamlError(_key_message(key_event), _line(key_event))
                if key in mapping:
                    raise YamlError(f'"{key}" appears twice in one mapping', _line(key_event))
                mapping.key_lines[key] = _line(key_event)
                mapping[key] = self.node(next(self.stream), flow, indent, depth)
        raise YamlError(f"unexpected YAML event {type(event).__name__}", _line(event))

    def explicit_key(self, start: int) -> bool:
        """Whether the key starting at `start` follows a `?` indicator."""
        i = start - 1
        while i >= 0 and self.text[i] in " \t\r\n":
            i -= 1
        return i >= 0 and self.text[i] == "?"

    def flow_tabs(self, start: int, end: int) -> None:
        """PyYAML refuses a tab used as a separator inside [] or {}, so Tiggy does too."""
        for i in (i for i, c in enumerate(self.text[start:end], start) if c == "\t"):
            k = bisect.bisect_right(self.quoted, i, key=lambda span: span[0]) - 1
            if k < 0 or not i < self.quoted[k][1]:
                line = self.text[:i].count("\n") + 1
                raise YamlError("a tab inside [] or {}: some YAML readers refuse it. Use spaces", line)

    def portable(self, event: ev.ScalarEvent, indent: int) -> None:
        """Refuse scalar syntax ruamel reads but libyaml-based readers (go-yaml, yq) refuse."""
        start = event.start_mark.index
        if event.style == '"':
            raw = self.text[start : event.end_mark.index]
            i = 1
            while i < len(raw) - 1:
                if raw[i] == "\\":
                    if raw[i + 1] == "/":
                        line = self.text[: start + i].count("\n") + 1
                        raise YamlError("write / rather than \\/; some YAML readers refuse that escape", line)
                    i += 1
                i += 1
        elif event.style in ("|", ">"):
            line = _block_tab(self.text, start, event.start_mark.line, indent)
            if line is not None:
                raise YamlError(
                    "a tab in a block scalar's indentation, which some YAML readers refuse. Indent "
                    "with spaces, with an indentation indicator (|2) if the text starts with a tab",
                    line,
                )


def _key_message(event: ev.Event) -> str:
    if isinstance(event, ev.ScalarEvent):
        return f"mapping keys are strings; quote this one ('{event.value}')"
    return "mapping keys are strings, not lists or mappings. A {{variable}} needs quoting: '{{name}}'"


def _block_tab(text: str, start: int, header_line: int, parent: int) -> int | None:
    """The line of a tab libyaml would take for indentation in the block scalar at `start`."""
    header = BLOCK_HEADER.match(text, start)
    increment = int((header.group(1) or header.group(2) or "0") if header else "0")
    indent = (parent + increment if parent >= 0 else increment) if increment else 0
    max_indent = 0
    body = text.find("\n", start)
    if body < 0:
        return None
    for offset, line in enumerate(text[body + 1 :].split("\n")):
        line = line.removesuffix("\r")
        column = 0
        while (indent == 0 or column < indent) and column < len(line) and line[column] == " ":
            column += 1
        max_indent = max(max_indent, column)
        if (indent == 0 or column < indent) and column < len(line) and line[column] == "\t":
            return header_line + 2 + offset
        if column < len(line):
            if indent == 0:
                indent = max(max_indent, parent + 1, 1)
            elif column < indent:
                return None
    return None


def _scalar(text: str, style: str | None, flow: bool) -> Any:
    """A scalar's value, or ValueError when YAML readers would disagree about it."""
    if any("\ud800" <= c <= "\udfff" for c in text):
        raise ValueError(
            "surrogate escapes such as \\ud83d aren't characters; "
            "write the code point as one escape (\\U0001F600)"
        )
    if style is not None:
        return text
    if text in ("true", "false"):
        return text == "true"
    if text == "null":
        return None
    if is_canonical_int(text):
        return int(text)
    if text == "" and flow:
        raise ValueError("an empty value inside {} or []. A {{variable}} needs quoting: '{{name}}'")
    if CANONICAL_INT.fullmatch(text) and text != "-0":
        raise ValueError(
            f"{text} is too big an integer for some YAML readers, which read past 64 bits as a float. "
            f"Quote it ('{text}') for a string"
        )
    if ambiguous(text):
        raise ValueError(
            f"{text or '(empty)'} isn't a string to every YAML reader. Quote it ('{text}') for a string; "
            "unquoted, Tiggy reads only true, false, null and 64-bit decimal integers"
        )
    if flow and UNSAFE_IN_FLOW & set(text):
        raise ValueError(f"quote '{text}': some YAML readers can't read ? or : unquoted inside {{}} or []")
    return text


def is_canonical_int(text: str) -> bool:
    """Written as an int, read as the same int by every reader: no sign but -, no leading zeros, 64-bit."""
    return (
        len(text) <= 20 and CANONICAL_INT.fullmatch(text) is not None and text != "-0" and int(text) in INT64
    )


def ambiguous(text: str) -> bool:
    """Whether some YAML reader takes this plain scalar for something other than the string."""
    if text == "" or YAML_TYPED.fullmatch(text):
        return True
    return text[0] in "-+.0123456789" and GO_NUMBER.fullmatch(text.replace("_", "")) is not None


# Emitting


def render(tree: dict[str, Any]) -> str:
    """The canonical text for a fragment, proven to load back to exactly `tree`.

    Multiline strings are literal blocks where possible; if the reparse disagrees, the fragment is
    re-rendered with every multiline string double-quoted, which always round-trips.
    """
    problem = unrepresentable(tree)
    if problem:
        raise YamlError(problem)
    if _depth(tree) > MAX_DEPTH:
        raise YamlError(f"it nests more than {MAX_DEPTH} levels deep")
    for literals in (True, False):
        text = "\n".join(_Emitter(literals).mapping(tree, 0)) + "\n"
        try:
            if first_difference(tree, load(text)) is None:
                return text
        except YamlError:
            pass
    raise YamlError("the YAML written doesn't load back to the same value; this is a bug in Tiggy")


def lines(value: Any) -> list[str]:
    """A value on its own as block YAML, the lines `render` writes for it under a key: a mapping's entries,
    a sequence's items, or a scalar (a literal block's body keeping its indent)."""
    head, *body = render({"v": value}).splitlines()
    if head == "v:":
        return [line[2:] for line in body]
    return [head.removeprefix("v: "), *body]


def one_line(value: Any) -> str:
    """A value as the emitter writes it on one line: a scalar, or a flow mapping or sequence of them."""
    return _inline(value)


def _depth(value: Any) -> int:
    if isinstance(value, dict):
        return 1 + max(map(_depth, value.values()), default=0)
    if isinstance(value, list):
        return 1 + max(map(_depth, value), default=0)
    return 0


def unrepresentable(value: Any, path: str = "") -> str | None:
    """Why a value can't be written exactly as YAML, or None when it can."""
    if isinstance(value, dict):
        for key, item in value.items():
            label = key if isinstance(key, str) and re.fullmatch(r"[\w$ -]{1,60}", key) else repr(key)[:60]
            here = f"{path}.{label}" if path else label
            found = (
                (f"{here}: keys must be strings" if not isinstance(key, str) else None)
                or (
                    f"{here}: << can't be a key; YAML readers take it for a merge key"
                    if key == "<<"
                    else None
                )
                or unrepresentable(key, here)
                or unrepresentable(item, here)
            )
            if found:
                return found
        return None
    if isinstance(value, list):
        for i, item in enumerate(value):
            found = unrepresentable(item, f"{path}[{i}]")
            if found:
                return found
        return None
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return (
            None
            if value in INT64
            else f"{path or '(root)'}: {value} is outside the 64-bit range YAML readers share"
        )
    if isinstance(value, str):
        if any("\ud800" <= c <= "\udfff" for c in value):
            return f"{path or '(root)'}: {value!r} holds a lone surrogate, which isn't a character"
        return None
    return f"{path or '(root)'}: {type(value).__name__} values such as {value!r} can't be written exactly yet"


class _Emitter:
    def __init__(self, literals: bool):
        self.literals = literals

    def mapping(self, mapping: dict[str, Any], indent: int) -> list[str]:
        lines = []
        pad = " " * indent
        for key, value in mapping.items():
            key_text = _one_line(key, flow=False)
            if len(key_text) <= MAX_IMPLICIT_KEY:
                lines.extend(self.entry(f"{pad}{key_text}:", value, indent))
            else:
                # YAML caps an implicit key at 1024 characters; past that, `? key` / `: value`.
                lines.append(f"{pad}? {key_text}")
                lines.extend(self.entry(f"{pad}:", value, indent))
        return lines

    def entry(self, head: str, value: Any, indent: int) -> list[str]:
        if self.is_literal(value):
            return [f"{head} {_literal_header(value)}", *_literal_body(value, indent + 2)]
        if isinstance(value, dict) and value and not _one_line_mapping(value):
            return [head, *self.mapping(value, indent + 2)]
        if isinstance(value, list) and value:
            return [head, *self.sequence(value, indent + 2)]
        return [f"{head} {_inline(value)}"]

    def sequence(self, items: list[Any], indent: int) -> list[str]:
        lines = []
        dash = f"{' ' * indent}- "
        for item in items:
            if self.is_literal(item):
                lines.append(dash + _literal_header(item))
                lines.extend(_literal_body(item, indent + 2))
            elif isinstance(item, dict) and item and not _one_line_mapping(item):
                body = self.mapping(item, indent + 2)
                lines.append(dash + body[0].lstrip(" "))
                lines.extend(body[1:])
            elif isinstance(item, list) and item:
                lines.append(dash.rstrip())
                lines.extend(self.sequence(item, indent + 2))
            else:
                lines.append(dash + _inline(item))
        return lines

    def is_literal(self, value: Any) -> bool:
        return (
            self.literals
            and isinstance(value, str)
            and "\n" in value
            and value.strip("\n") != ""
            and "\r" not in value
            and all(c in "\n\t" or _printable(c) for c in value)
            # Trailing whitespace is content here, and whitespace fixers would strip it.
            and not any(line.endswith((" ", "\t")) for line in value.split("\n"))
            and not value.endswith("\n\n")  # end-of-file fixers collapse a final |+
        )


def _one_line_mapping(value: dict[str, Any]) -> bool:
    """A Flow mapping stays on one line unless a key at any depth in it is past YAML's implicit-key limit."""
    return isinstance(value, Flow) and all(
        len(_one_line(k, flow=True)) <= MAX_IMPLICIT_KEY and _fits_one_line(v) for k, v in value.items()
    )


def _fits_one_line(value: Any) -> bool:
    if isinstance(value, dict):
        return _one_line_mapping(value)
    if isinstance(value, list):
        return all(_fits_one_line(v) for v in value)
    return True


def _inline(value: Any) -> str:
    """A value on one line: a scalar, or a flow mapping or sequence of them."""
    if isinstance(value, dict):
        return (
            "{" + ", ".join(f"{_one_line(k, flow=True)}: {_inline_flow(v)}" for k, v in value.items()) + "}"
        )
    if isinstance(value, list):
        return "[" + ", ".join(_inline_flow(v) for v in value) + "]"
    return _one_line(value, flow=False)


def _inline_flow(value: Any) -> str:
    return _inline(value) if isinstance(value, dict | list) else _one_line(value, flow=True)


def _one_line(value: Any, flow: bool) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if _plain_safe(value, flow):
        return value
    if "\n" not in value and _printable(value):
        return "'" + value.replace("'", "''") + "'"
    return _double_quoted(value)


def _plain_safe(text: str, flow: bool) -> bool:
    if not text or text != text.strip() or text[0] in INDICATORS or text.startswith(("---", "...")):
        return False
    if any(c in UNSAFE_IN_PLAIN for c in text) or ": " in text or text.endswith(":"):
        return False
    if flow and UNSAFE_IN_FLOW & set(text):
        return False
    if not _printable(text):
        return False
    return text not in ("true", "false", "null") and not is_canonical_int(text) and not ambiguous(text)


def _printable(text: str) -> bool:
    return all(c.isprintable() and c not in "\u2028\u2029\u0085\ufeff" for c in text)


def _literal_header(text: str) -> str:
    """`|` with an indentation indicator when the first line starts with whitespace, then chomping."""
    first = next((line for line in text.split("\n") if line), "")
    indicator = "2" if first[:1] in (" ", "\t") else ""
    trailing = len(text) - len(text.rstrip("\n"))
    return "|" + indicator + ("-" if trailing == 0 else "" if trailing == 1 else "+")


def _literal_body(text: str, indent: int) -> list[str]:
    body = text if not text.endswith("\n") else text[:-1]
    return [(" " * indent + line) if line else "" for line in body.split("\n")]


def _double_quoted(text: str) -> str:
    escapes = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t", "\0": "\\0"}
    out = []
    for c in text:
        if c in escapes:
            out.append(escapes[c])
        elif not _printable(c):
            code = ord(c)
            out.append(
                f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}" if code < 0x10000 else f"\\U{code:08x}"
            )
        else:
            out.append(c)
    return '"' + "".join(out) + '"'


# Comparison


def first_difference(a: Any, b: Any, path: str = "") -> str | None:
    """The first path at which two trees differ, comparing types exactly (True is not 1)."""
    if type(a) is not type(b) and not (isinstance(a, dict) and isinstance(b, dict)):
        if not (isinstance(a, list) and isinstance(b, list)):
            return path or "(root)"
    if isinstance(a, dict):
        if list(a) != list(b):
            return f"{path} (keys {list(a)} vs {list(b)})"
        for key in a:
            found = first_difference(a[key], b[key], f"{path}.{key}" if path else key)
            if found:
                return found
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path} (length {len(a)} vs {len(b)})"
        for i, (x, y) in enumerate(zip(a, b)):
            found = first_difference(x, y, f"{path}[{i}]")
            if found:
                return found
        return None
    return None if a == b else path or "(root)"
