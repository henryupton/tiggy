"""A spec reads the same in every YAML tool the adopting repo runs.

The analytics team's pre-commit `check-yaml` loads every fragment with ruamel (YAML 1.2), their scripts
use PyYAML (YAML 1.1, pure Python or libyaml), and engineers poke at fragments with yq (go-yaml). A
fragment Tiggy writes, or accepts, has to mean the same to all of them: otherwise a script reads `no`
as False while Tiggy compiles the string "no" into the container.
"""

import functools
import itertools
import json
import operator
import re
import shutil
import subprocess
import warnings
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest
import yaml
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from pre_commit_hooks import check_yaml
from ruamel.yaml import YAML

from tests.conftest import ALL_EXPORTS, CDM_EXPORTS, base_env, export_id
from tiggy.spec import yamlio
from tiggy.spec.codec import TYPES, Codec
from tiggy.spec.problems import Origin
from tiggy.spec.yamlio import Flow, YamlError

# Readers


def _ruamel_yaml_1_1() -> Callable[[str], Any]:
    reader = YAML(typ="safe", pure=True)
    reader.version = (1, 1)

    def load(text: str) -> Any:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # ruamel warns about 1.1 floats with no dot, then reads them
            return reader.load(text)

    return load


READERS: dict[str, Callable[[str], Any]] = {
    "PyYAML safe_load": yaml.safe_load,
    "ruamel as check-yaml loads it (YAML 1.2)": check_yaml.yaml.load,
    "ruamel under YAML 1.1": _ruamel_yaml_1_1(),
}
if yaml.__with_libyaml__:
    READERS["PyYAML CSafeLoader (libyaml)"] = functools.partial(yaml.load, Loader=yaml.CSafeLoader)


def _find_yq() -> str | None:
    """mikefarah's yq, the one with `-o=json`; the jq wrapper also called yq takes other flags."""
    found = shutil.which("yq")
    if found is None:
        return None
    version = subprocess.run([found, "--version"], capture_output=True, text=True, check=False)
    return found if "mikefarah" in version.stdout else None


YQ = _find_yq()
needs_yq = pytest.mark.skipif(YQ is None, reason="yq (github.com/mikefarah/yq) is not on PATH")


def _plain(value: Any) -> Any:
    """Tiggy's Map, Seq and Flow as the dicts and lists every other reader returns."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _short(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 70 else f"{text[:55]}... ({len(text)} chars)"


def _difference(a: Any, b: Any, path: str = "(root)", ordered: bool = True) -> str | None:
    """Where two loaded trees first differ: exact types (True is not 1), and key order when `ordered`."""
    if type(a) is not type(b):
        return f"{path}: {_short(a)} ({type(a).__name__}) vs {_short(b)} ({type(b).__name__})"
    if isinstance(a, dict):
        if (list(a) if ordered else sorted(a)) != (list(b) if ordered else sorted(b)):
            return f"{path}: keys {_short(list(a))} vs {_short(list(b))}"
        for key in a:
            found = _difference(a[key], b[key], f"{path}.{key!r}", ordered)
            if found:
                return found
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: {len(a)} items vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            found = _difference(x, y, f"{path}[{i}]", ordered)
            if found:
                return found
        return None
    return None if a == b else f"{path}: {_short(a)} vs {_short(b)}"


def _disagreement(read: Callable[[str], Any], text: str, expected: Any) -> str | None:
    """How a reader's view of `text` differs from `expected`, which is what Tiggy reads."""
    try:
        got = read(text)
    except Exception as e:  # any reader's refusal is a disagreement, whatever it raises
        return f"refuses it: {type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"
    found = _difference(expected, _plain(got))
    return f"reads it differently at {found}" if found else None


def _disagreements(text: str, expected: Any) -> list[str]:
    return [
        f"{name} {found}" for name, read in READERS.items() if (found := _disagreement(read, text, expected))
    ]


def _yq_read(paths: list[Path], cwd: Path) -> list[tuple[bool, Any]]:
    """What yq reads from each file, as (True, value), or (False, its error) for a file it refuses.

    yq stops at the first file it can't read, having printed one JSON line per file before it, so
    each refusal costs one more run starting after it.
    """
    out: list[tuple[bool, Any]] = []
    start = 0
    while start < len(paths):
        chunk = [p.relative_to(cwd).as_posix() for p in paths[start : start + 800]]
        run = subprocess.run(
            [YQ, "-o=json", "-I=0", ".", *chunk], capture_output=True, cwd=cwd, env=base_env(), check=False
        )
        # Not splitlines(): that also splits at U+2028 and U+0085, which a JSON string may hold raw.
        lines = [line for line in run.stdout.decode("utf-8").split("\n") if line]
        out.extend((True, json.loads(line)) for line in lines)
        if run.returncode == 0:
            assert len(lines) == len(chunk), f"yq printed {len(lines)} documents for {len(chunk)} files"
            start += len(chunk)
        else:
            assert len(lines) < len(chunk), run.stderr.decode("utf-8", "replace")
            out.append((False, run.stderr.decode("utf-8", "replace").strip()))
            start += len(lines) + 1
    return out


def _yq_disagreements(docs: Iterable[tuple[str, str, Any]], directory: Path) -> list[str]:
    """Each (label, text, expected) that yq reads differently from Tiggy, or refuses."""
    docs = list(docs)
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, (_, text, _) in enumerate(docs):
        path = directory / f"{i:05}.yml"
        path.write_bytes(text.encode("utf-8"))
        paths.append(path)
    problems = []
    for (label, text, expected), (ok, got) in zip(docs, _yq_read(paths, directory), strict=True):
        if not ok:
            problems.append(f"{label}: yq refuses {text!r}: {got}")
        elif found := _difference(expected, got):
            problems.append(f"{label}: yq reads {text!r} differently at {found}")
    return problems


def _report(problems: list[str], limit: int = 25) -> str:
    shown = "\n".join(problems[:limit])
    return f"{len(problems)} disagreements:\n{shown}" + ("\n..." if len(problems) > limit else "")


# 1. What `tiggy import` writes, for every fixture


def _fragments(spec: Path) -> list[Path]:
    return sorted(p for pattern in ("*.yml", "*.yaml") for p in spec.rglob(pattern))


@pytest.mark.parametrize("export", ALL_EXPORTS, ids=export_id)
def test_check_yaml_and_scripts_read_an_adopted_container_as_tiggy_does(export: Path, imported):
    """check-yaml (ruamel 1.2), a PyYAML script and a YAML 1.1 reader load every fragment to the same data."""
    spec = imported(export)
    fragments = _fragments(spec)
    assert spec / "container.yml" in fragments
    problems = []
    for path in fragments:
        text = path.read_bytes().decode("utf-8")
        expected = _plain(yamlio.load(text))
        assert expected["tiggy"] == 1 and type(expected["tiggy"]) is int, path
        problems += [f"{path.relative_to(spec)}: {p}" for p in _disagreements(text, expected)]
    assert not problems, _report(problems)


@needs_yq
@pytest.mark.parametrize("export", ALL_EXPORTS, ids=export_id)
def test_yq_reads_an_adopted_container_as_tiggy_does(export: Path, imported, tmp_path: Path):
    spec = imported(export)
    fragments = _fragments(spec)
    expected = [_plain(yamlio.load(p.read_bytes().decode("utf-8"))) for p in fragments]
    results = _yq_read(fragments, spec)
    problems = []
    for path, want, (ok, got) in zip(fragments, expected, results, strict=True):
        name = path.relative_to(spec).as_posix()
        if not ok:
            problems.append(f"{name}: yq refuses it: {got}")
        elif found := _difference(want, got):
            problems.append(f"{name}: yq reads it differently at {found}")
    assert not problems, _report(problems)


def _typed_unquoted(value: str) -> bool:
    """Whether some Python reader takes `value`, written unquoted, for something other than that string."""
    for read in READERS.values():
        try:
            if read(f"k: {value}\n") != {"k": value}:
                return True
        except Exception:
            return True
    return False


def test_the_analytics_teams_containers_hold_strings_only_quoting_keeps_strings(imported):
    """The fixture tests aren't vacuous: the specs hold strings a reader would type if left bare."""
    found: dict[str, str] = {}
    for export in CDM_EXPORTS:
        spec = imported(export, export.stem)
        for path in _fragments(spec):
            for value in _scalars(_plain(yamlio.load(path.read_bytes().decode("utf-8")))):
                if isinstance(value, str) and _typed_unquoted(value):
                    found.setdefault(value, f"{export.name}: {path.relative_to(spec).as_posix()}")
    assert {"", "true", "false", "500", "000000001", "63072000"} <= set(found), found


def _scalars(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        yield from value.keys()
        for v in value.values():
            yield from _scalars(v)
    elif isinstance(value, list):
        for v in value:
            yield from _scalars(v)
    else:
        yield value


# 2. Properties: render and the parameter codec over generated trees

TRICKY = (
    "yes", "no", "on", "off", "y", "n", "Yes", "NO", "On", "OFF", "True", "FALSE", "true", "false",
    "~", "null", "Null", "NULL", "", "0x10", "0X10", "012", "0o12", "0b101", "1_000", "1e3", "1E3",
    "1.5", "-1.5", ".5", "+1", "-0", "00", "0.", ".inf", "-.inf", ".NaN", "2024-10-31",
    "2001-12-14t21:59:43.10-05:00", "2001-12-14 21:59:43.10 -5", "12:30", "1:30:00", "190:20:30.15",
    "<<", "=", "-", "?", ":", "|", ">", "#", "&a", "*a", "!a", "!!str", "%a", "@a", "`a", "'", '"',
    "- a", "? a", ": a", "a: b", "a:b", "a #b", "a#b", "{{Page URL}}", "{a}", "[a]", "a,b", "a, b",
    " lead", "trail ", "  ", "\t", "a\tb", "\n", "a\nb", "a\n", "a\n\n", "\n\na", " a\nb", "\ta\n",
    "a \nb", "\r\n", "a\rb", "\u2028", "\u2029", "\x85", "\ufeff", "\ufeffa", "\x00", "\x1b", "\x7f",
    "\u00a0", "\u200b", "\ue000", "\U0010fffd", "é", "日本語", "🙂", "---", "...", "--- a", "... a",
    "9223372036854775807", "9223372036854775808", "-9223372036854775808", "-9223372036854775809",
    "{{DLV - event_attributes.page_section}}", "<event_name>", "\\", "\\n", "\\u0041",
)  # fmt: skip
TEXT = st.text(st.characters(exclude_categories=("Cs",)), max_size=40)
# Long enough to pass YAML's 1024-character implicit-key limit once quoted.
LONG = st.builds(
    operator.mul,
    st.sampled_from(("x", "é", "yes ", "a: b", "日本", "'", '"', " ", "\n", "{{x}}")),
    st.integers(990, 1100),
)
# Multi-line text, which render writes as a literal block where every reader agrees on it.
LINES = st.lists(
    st.one_of(
        st.sampled_from(("", "a", " a", "\ta", "a ", "# a", "- a", "a: b", "---", "...", "{{x}}", "'", "é")),
        st.text(st.characters(exclude_categories=("Cs",), exclude_characters="\n"), max_size=12),
    ),
    min_size=2,
    max_size=5,
).map("\n".join)
STRINGS = st.one_of(st.sampled_from(TRICKY), TEXT, LINES, st.sampled_from(TRICKY), TEXT, LINES, LONG)
KEYS = STRINGS.filter(lambda key: key != "<<")  # a merge key to every reader, quoted or not
SCALARS = st.one_of(STRINGS, st.integers(-(2**63), 2**63 - 1), st.booleans(), st.none())
VALUES = st.recursive(
    SCALARS,
    lambda inner: st.one_of(
        st.lists(inner, max_size=4),
        st.dictionaries(KEYS, inner, max_size=4),
        st.dictionaries(KEYS, inner, max_size=3).map(Flow),
    ),
    max_leaves=24,
)
TREES = st.dictionaries(KEYS, VALUES, min_size=1, max_size=5)

FAST = {"max_examples": 150, "derandomize": True}
THOROUGH = {"max_examples": 2500}
COMMON = {"deadline": None, "suppress_health_check": [HealthCheck.too_slow, HealthCheck.data_too_large]}


def _long_key_inside_a_flow_list(value: Any, flow: bool = False, listed: bool = False) -> bool:
    """A dict reached through a list inside a Flow, with a long key: the known gap at the end of this file."""
    if isinstance(value, dict):
        if listed and any(len(k) > 100 for k in value):
            return True
        flow = flow or isinstance(value, Flow)
        return any(_long_key_inside_a_flow_list(v, flow, listed) for v in value.values())
    if isinstance(value, list):
        return any(_long_key_inside_a_flow_list(v, flow, listed or flow) for v in value)
    return False


def _rendered(tree: dict[str, Any]) -> tuple[str, str, Any]:
    """`tree` rendered, checked against Tiggy and every reader; returned for yq to check in one run."""
    text = yamlio.render(tree)
    expected = _plain(tree)
    assert _difference(expected, _plain(yamlio.load(text))) is None, text
    problems = _disagreements(text, expected)
    assert not problems, f"{_report(problems)}\n--- rendered\n{text}"
    return ("a rendered tree", text, expected)


# What render can write, so a run that never reached one of them fails rather than passing quietly.
EMITTER_FORMS = {
    "a literal block": re.compile(r"[:-] \|[-+0-9]*$", re.MULTILINE),
    "an explicit ? key": re.compile(r"^ *\? ", re.MULTILINE),
    "an inline flow mapping": re.compile(r"[:-] \{"),
    "a double-quoted escape": re.compile(r'"[^"\n]*\\'),
    "a single-quoted string": re.compile(r"(^|[:-] )'", re.MULTILINE),
}


def _render_round_trips(options: dict[str, Any], directory: Path) -> None:
    seen: list[tuple[str, str, Any]] = []

    @settings(**options, **COMMON)
    @given(TREES)
    def check(tree: dict[str, Any]) -> None:
        assume(not _long_key_inside_a_flow_list(tree))
        seen.append(_rendered(tree))

    check()
    missing = [name for name, form in EMITTER_FORMS.items() if not any(form.search(t) for _, t, _ in seen)]
    assert not missing, f"no generated tree was written with {missing}"
    if YQ:
        problems = _yq_disagreements(seen, directory)
        assert not problems, _report(problems)


def test_whatever_tiggy_writes_every_yaml_tool_reads_back_exactly(tmp_path: Path):
    """render accepts every representable tree; Tiggy, PyYAML, ruamel 1.1 and 1.2 and yq read it back."""
    _render_round_trips(FAST, tmp_path)


@pytest.mark.slow
def test_whatever_tiggy_writes_every_yaml_tool_reads_back_exactly_thoroughly(tmp_path: Path):
    _render_round_trips(THOROUGH, tmp_path)


# GTM parameters: mostly the keys and values a container holds, sometimes ones that force the explicit form.
REAL_KEYS = st.sampled_from(
    ("eventName", "eventSettingsTable", "parameter", "parameterValue", "name", "value", "html",
     "measurementIdOverride", "sendEcommerceData", "tagId")
)  # fmt: skip
ODD_KEYS = st.sampled_from(
    ("", "$type", "$file", "$key", "$unmodelled", "<<", "yes", "no", "1", "007", "null", "true", "a: b",
     "{{Page URL}}", "-", "~", "key with spaces", "é", "x" * 1100)
)  # fmt: skip
GTM_KEYS = st.one_of(REAL_KEYS, REAL_KEYS, REAL_KEYS, ODD_KEYS, TEXT)
PARAMETER_VALUES = st.one_of(
    st.sampled_from(
        ("{{Click Text}}", "{{DLV - event_attributes.page_section}}", "G-TEST000001", "AW-000000001",
         "page_view", "track_event", "https://www.example.com/collect")
    ),
    st.sampled_from(TRICKY),
    TEXT,
    LINES,
)  # fmt: skip
BOOLEAN_VALUES = st.one_of(
    st.sampled_from(("true", "false")), st.sampled_from(("True", "FALSE", "1", "yes", "")), PARAMETER_VALUES
)
INTEGER_VALUES = st.one_of(
    st.sampled_from(("0", "1", "-1", "42", "9223372036854775807")),
    st.sampled_from(("01", "-0", "+1", "1_000", "9223372036854775808", "1.5")),
    PARAMETER_VALUES,
)
COMMON_TYPES = st.sampled_from(("TEMPLATE", "BOOLEAN", "INTEGER", "LIST", "MAP"))
TRIGGER_IDS = st.sampled_from(("12", "2147479553", "2147479572", "0", "trigger-id"))


@st.composite
def parameters(draw: st.DrawFn, keyed: bool, depth: int = 0) -> dict[str, Any]:
    """A GTM Parameter as an export holds it, with fields in any order."""
    kind = draw(st.one_of(COMMON_TYPES, COMMON_TYPES, st.sampled_from(sorted(TYPES))))
    fields: dict[str, Any] = {}
    if draw(st.integers(0, 23)):  # rarely, no type at all
        fields["type"] = kind
    if keyed or draw(st.integers(0, 4)) == 0:  # list items rarely have keys
        fields["key"] = draw(GTM_KEYS)
    values = {"BOOLEAN": BOOLEAN_VALUES, "INTEGER": INTEGER_VALUES, "TRIGGER_REFERENCE": TRIGGER_IDS}
    if kind not in ("LIST", "MAP") or draw(st.integers(0, 5)) == 0:
        if draw(st.integers(0, 7)):  # rarely absent, and rarely null
            fields["value"] = draw(values.get(kind, PARAMETER_VALUES)) if draw(st.integers(0, 9)) else None
    if depth < 3 and (kind == "LIST" or draw(st.integers(0, 9)) == 0):
        fields["list"] = draw(st.lists(parameters(keyed=False, depth=depth + 1), max_size=3) | st.none())
    if depth < 3 and (kind == "MAP" or draw(st.integers(0, 9)) == 0):
        fields["map"] = draw(st.lists(parameters(keyed=True, depth=depth + 1), max_size=3) | st.none())
    if draw(st.integers(0, 11)) == 0:
        fields["isWeakReference"] = draw(st.booleans())
    if draw(st.integers(0, 14)) == 0:
        fields["unmodelledField"] = draw(st.one_of(TEXT, st.booleans(), st.integers(0, 9)))
    return dict(draw(st.permutations(list(fields.items()))))


def _codec() -> Codec:
    return Codec(
        trigger_ref=lambda trigger_id: Flow(trigger_id=trigger_id),
        trigger_id=lambda ref, origin: ref["trigger_id"],
    )


def _parameters_round_trip(params: list[dict[str, Any]], single: dict[str, Any], keyed: bool) -> tuple:
    """Checks a parameter alone and a tag's parameter list; returns the list's YAML and the single's form."""
    node = _codec().encode(single, keyed)
    decoded = _codec().decode(node, single["key"] if keyed else None)
    assert _difference(single, decoded, ordered=False) is None, f"encoded as {node!r}"
    # As a tag's `parameter:` block: encoded, rendered, read by every tool, decoded.
    tree = {"parameter": _codec().encode_list(params)}
    text, expected = _rendered(tree)[1:]
    decoded_list = _codec().decode_list(yamlio.load(text)["parameter"], Origin("tags.yml"))
    assert _difference(params, decoded_list, ordered=False) is None, text
    explicit = isinstance(node, dict) and "$type" in node
    return ("a tag's parameters", text, expected), "explicit" if explicit else "readable"


def _codec_round_trips(options: dict[str, Any], directory: Path) -> None:
    seen: list[tuple[str, str, Any]] = []

    @settings(**options, **COMMON)
    @given(
        st.lists(parameters(keyed=True), min_size=1, max_size=4),
        st.booleans().flatmap(lambda keyed: st.tuples(parameters(keyed=keyed), st.just(keyed))),
    )
    def check(params: list[dict[str, Any]], single: tuple[dict[str, Any], bool]) -> None:
        doc, form = _parameters_round_trip(params, *single)
        seen.append(doc)
        forms.add(form)

    forms: set[str] = set()
    check()
    assert forms == {"explicit", "readable"}, f"only the {forms} form was generated"
    if YQ:
        problems = _yq_disagreements(seen, directory)
        assert not problems, _report(problems)


def test_every_gtm_parameter_survives_the_spec_exactly(tmp_path: Path):
    """decode(encode(p)) == p, and a tag's parameters survive render, every reader and decode_list."""
    _codec_round_trips(FAST, tmp_path)


@pytest.mark.slow
def test_every_gtm_parameter_survives_the_spec_exactly_thoroughly(tmp_path: Path):
    _codec_round_trips(THOROUGH, tmp_path)


# 3. A deterministic sweep of plain scalars that some YAML reader types


def _cases(word: str) -> list[str]:
    """Every upper- and lower-case spelling of `word`."""
    return sorted({"".join(c) for c in itertools.product(*((ch.lower(), ch.upper()) for ch in word))})


SIGNS = ("", "+", "-")
INT_PREFIXES = ("", "0", "00", "0x", "0X", "0o", "0O", "0b", "0B", "0d", "0_", "_")
INT_DIGITS = (
    "0", "1", "7", "8", "9", "10", "12", "17", "19", "101", "777", "ff", "FF", "1f", "e1", "1_0", "1__0",
    "_1", "1_", "-1", "+1", "9223372036854775807", "9223372036854775808", "18446744073709551616",
)  # fmt: skip
MANTISSAS = (
    "1", "1.", "1.0", "1.5", ".5", "0.5", "00.5", "1_0.5", "1.5_0", "1_000", "685.230_15", ".", "._5",
    "1._5",
)  # fmt: skip
EXPONENTS = ("", "e3", "E3", "e+3", "e-3", "E+03", "e", "e+", "e_3", "e3_0", "e3.5")
SPECIAL_FLOATS = (".inf", ".Inf", ".INF", ".iNf", ".nan", ".NaN", ".NAN", ".nAn", "NaN", ".infinity", "inf.")
SEXAGESIMALS = (
    "1:30", "0:30", "01:30", "1:3", "1:60", "1:59", "60:59", "190:20:30", "190:20:30.15", "1:30.5", "12:30",
    "12:30:00", "1_0:30", "1:30:", ":30", "1::30", "1:30.", "1:5:5:5", "1:30e3", "1:-30",
)  # fmt: skip
DATES = (
    "2001-12-14", "2001-1-1", "2001-12-1", "01-12-14", "20011-12-14", "2001-13-14", "2001-02-30",
    "0000-00-00", "2001-12",
)  # fmt: skip
TIMES = (
    "", "t21:59:43", "T21:59:43", " 21:59:43", "\t21:59:43", "  21:59:43", "T21:59", "T2:59:43",
    "T21:59:43.10", "T21:59:43,10", "T21:59:43.", "T21:59:43.1234567890", "T25:61:61",
)  # fmt: skip
ZONES = ("", "Z", "z", " Z", "-05:00", "+05:30", "-5", " -5", "+0530", "+05:3")
NEAR_NUMBERS = (
    "1.2.3", "1.0.0-beta", "v1", "1x", "x1", "1 2", "1,000", "1.000,5", "0.0.0.0", "127.0.0.1", "::1",
    "1e", "e1", "0e0", "0x", "0b", "0o", "+", "-", "-0", "+0", "-0.0", "0.", "1:", ":1", "0x1g", "0b2",
    "0o8", "08", "0_8", "1 #2", "1#2",
)  # fmt: skip
NODE_SYNTAX = (
    "---", "...", "--- a", "... a", "---a", "...a", "a #b", "a#b", "!!str a", "!!int 1", "!!bool yes",
    "!x a", "&x a", "*x", "%YAML 1.2", "? a", ": a", "- a", "a: b", "a:b", "a :b", "a\tb", "a \tb",
)  # fmt: skip
GTM_VALUES = (
    "{{Page URL}}", "{{DLV - event_attributes.page_section}}", "a {{b}}", "https://www.example.com/a?b=c#d",
    "GTM-TEST001", "G-TEST000001", "AW-000000001", "UA-000000-1", "track_event", "page_view", "en-AU",
    "Ok", "O", "NA", "No.", "no!", "yes, please", "Yes please",
)  # fmt: skip


@functools.cache
def sweep_candidates() -> tuple[str, ...]:
    found: dict[str, None] = {}

    def add(*items: str) -> None:
        found.update(dict.fromkeys(items))

    # Booleans and nulls of YAML 1.1 and 1.2, and the float words, in every case
    for word in ("y", "n", "yes", "no", "on", "off", "true", "false", "null", "none", "nil", "nan", "inf"):
        add(*_cases(word))
    add("", "~", "<<", "=", "<<<", "==", "<< ", "Infinity", "-Infinity")
    # Integers in every base, sign and separator, including past 64 bits
    for parts in itertools.product(SIGNS, INT_PREFIXES, INT_DIGITS):
        add("".join(parts))
    # Floats, and sexagesimal (base 60) numbers, which only YAML 1.1 has
    for parts in itertools.product(SIGNS, MANTISSAS, EXPONENTS):
        add("".join(parts))
    for parts in itertools.product(SIGNS, SPECIAL_FLOATS):
        add("".join(parts))
    for parts in itertools.product(SIGNS, SEXAGESIMALS):
        add("".join(parts))
    # Timestamps, well and badly formed
    for date, time, zone in itertools.product(DATES, TIMES, ZONES):
        if time or not zone:
            add(date + time + zone)
    add(*NEAR_NUMBERS, *NODE_SYNTAX, *GTM_VALUES)
    # Indicators: alone, doubled, leading, trailing, inside, and with spaces
    for c in "-?:,[]{}#&*!|>'\"%@`":
        add(c, c + c, c + "a", c + " a", "a" + c, "a" + c + "b", "a " + c, "a " + c + " b", c + "1", "1" + c)
    return tuple(found)


CONTEXTS: dict[str, Callable[[str], str]] = {
    "a block value": lambda s: f"k: {s}\n",
    "a block key": lambda s: f"{s}: v\n",
    "a block sequence item": lambda s: f"k:\n- {s}\n",
    "a flow sequence item": lambda s: f"k: [{s}]\n",
    "a flow mapping value": lambda s: f"k: {{a: {s}}}\n",
    "a flow mapping key": lambda s: f"k: {{{s}: v}}\n",
}


@functools.cache
def loader_sweep() -> tuple[tuple[tuple[str, str, Any], ...], tuple[str, ...], dict[str, tuple[int, int]]]:
    """Each candidate unquoted in each context: what Tiggy accepted, every disagreement, and counts.

    Returns (accepted as (label, text, value), problems, {context: (accepted, refused)}).
    """
    accepted, problems = [], []
    counts = {}
    for context, wrap in CONTEXTS.items():
        took = refused = 0
        for candidate in sweep_candidates():
            text = wrap(candidate)
            try:
                expected = _plain(yamlio.load(text))
            except YamlError:
                refused += 1
                continue
            took += 1
            label = f"{candidate!r} as {context}"
            accepted.append((label, text, expected))
            problems += [
                f"{label}: Tiggy reads {_short(expected)}, but {p}" for p in _disagreements(text, expected)
            ]
        counts[context] = (took, refused)
    return tuple(accepted), tuple(problems), counts


def test_tiggy_refuses_or_agrees_on_every_plain_scalar():
    """Unquoted, a candidate is either refused or read exactly as PyYAML and ruamel 1.1 and 1.2 read it."""
    accepted, problems, counts = loader_sweep()
    assert len(sweep_candidates()) >= 2500
    assert not problems, _report(list(problems))
    for context, (took, refused) in counts.items():
        assert took >= 100 and refused >= 1000, f"{context}: {took} accepted, {refused} refused"


def test_the_sweep_holds_the_typed_spellings_tiggy_has_to_refuse():
    """The sweep isn't vacuous: it covers what some reader types, and Tiggy reads only its four forms."""
    candidates = set(sweep_candidates())
    must_refuse = {"yes", "No", "OFF", "y", "~", "Null", "0x10", "012", "0o12", "0b101", "1_000", "1e3",
                   "1.5", ".inf", ".NaN", "1:30", "190:20:30.15", "2001-12-14", "2001-12-14t21:59:43",
                   "+1", "-0", "9223372036854775808", "<<", "="}  # fmt: skip
    assert must_refuse <= candidates
    for candidate in sorted(must_refuse):
        with pytest.raises(YamlError):
            yamlio.load(f"k: {candidate}\n")
    accepted = {label: value for label, _, value in loader_sweep()[0]}
    assert accepted["'true' as a block value"] == {"k": True}
    assert accepted["'null' as a block value"] == {"k": None}
    assert accepted["'-9223372036854775808' as a flow sequence item"] == {"k": [-(2**63)]}
    assert accepted["'a {{b}}' as a block value"] == {"k": "a {{b}}"}


@needs_yq
def test_yq_agrees_on_every_plain_scalar_tiggy_accepts(tmp_path: Path):
    accepted = loader_sweep()[0]
    assert len(accepted) >= 600
    problems = _yq_disagreements(accepted, tmp_path)
    assert not problems, _report(problems)


@functools.cache
def emitter_sweep() -> tuple[tuple[tuple[str, str, Any], ...], tuple[str, ...]]:
    """Each candidate written by render as a value, a key, and inside a Flow, then read by every reader."""
    written, problems = [], []
    for candidate in sweep_candidates():
        trees = {
            "a value": {"k": candidate},
            "a list item": {"k": [candidate]},
            "a Flow value": {"k": Flow(a=candidate)},
        }
        if candidate != "<<":
            trees["a key"] = {candidate: "v"}
            trees["a Flow key"] = {"k": Flow({candidate: "v"})}
        for where, tree in trees.items():
            label = f"{candidate!r} written as {where}"
            try:
                text = yamlio.render(tree)
            except YamlError as e:
                problems.append(f"{label}: render refuses it: {e}")
                continue
            written.append((label, text, _plain(tree)))
            problems += [f"{label}: {text!r}: {p}" for p in _disagreements(text, _plain(tree))]
    return tuple(written), tuple(problems)


def test_every_string_tiggy_writes_reads_back_as_that_string():
    """However a string is spelled, render quotes it so every reader gets the string back."""
    written, problems = emitter_sweep()
    assert not problems, _report(list(problems))
    assert len(written) == 5 * len(sweep_candidates()) - 2  # << is never a key
    # Quoting is only for strings that need it: what GTM holds day to day stays readable.
    plain = {text for _, text, _ in written}
    for value in ("track_event", "GTM-TEST001", "G-TEST000001", "en-AU", "1.2.3", "Ok", "page_view"):
        assert f"k: {value}\n" in plain, value


@needs_yq
def test_yq_reads_every_string_tiggy_writes_as_that_string(tmp_path: Path):
    problems = _yq_disagreements(emitter_sweep()[0], tmp_path)
    assert not problems, _report(problems)


# Structures an engineer might type into a fragment by hand: comments, layouts, block scalars,
# escapes, node properties and documents. As with scalars, Tiggy refuses each or agrees.
HAND_WRITTEN = {
    "a trailing comment": "k: v # note\n",
    "a comment line between keys": "a: 1\n# note\nb: 2\n",
    "indentation by four": "k:\n    a: 1\n    b:\n        - x\n",
    "a compact nested sequence": "k:\n- - a\n  - b\n- c\n",
    "a sequence of mappings": "k:\n- a: 1\n  b: 2\n- a: 3\n",
    "a multi-line plain scalar": "k: a\n  b\n\n  c\n",
    "a multi-line plain scalar in flow": "k: [a\n  b, c]\n",
    "a multi-line single-quoted scalar": "k: 'a\n  b\n\n  c'\n",
    "a multi-line double-quoted scalar": 'k: "a\n  b \\\n  c"\n',
    "a flow mapping over lines": "k: {a: 1,\n  b: 2}\n",
    "a flow mapping with a trailing comma": "k: {a: 1, }\n",
    "a single-pair flow sequence": "k: [a: b]\n",
    "an explicit key in a flow sequence": "k: [? a : b]\n",
    "an explicit key": "? a\n: b\n",
    "an explicit key with no value": "? a\n",
    "an empty value": "k:\n",
    "an empty value in flow": "k: {a: }\n",
    "an adjacent value after a quoted key in flow": 'k: {"a":b}\n',
    "spaces before the colon": "k   : v\n",
    "a tab after the colon": "k:\tv\n",
    "a tab after a dash": "k:\n-\ta\n",
    "a tab indenting a mapping": "k:\n\ta: 1\n",
    "a tab inside a flow sequence": "k: [a,\tb]\n",
    "a tab at the end of a plain scalar": "k: a\t\n",
    "no final newline": "k: v",
    "CRLF line endings": "a: 1\r\nb: |\r\n  x\r\n  y\r\n",
    "a bare CR": "a: 1\rb: 2\n",
    "a byte-order mark": "\ufeffk: v\n",
    "a byte-order mark mid-file": "a: 1\n\ufeffb: 2\n",
    "a raw NEL": "k: a\x85b\n",
    "a raw line separator": "k: a\u2028b\n",
    "a raw control character": "k: a\x07b\n",
    "a literal block": "k: |\n  a\n   b\n\n  c\n",
    "a literal block, keep, at the end of the file": "k: |+\n  a\n\n\n",
    "a literal block with an indentation indicator": "k: |2\n   a\n  b\n",
    "a literal block with chomping then indicator": "k: |-2\n   a\n",
    "a literal block with a more-indented blank line": "k: |\n  a\n    \n  b\n",
    "a literal block opening with a more-indented blank line": "k: |\n    \n  a\n",
    "a literal block holding tabs": "k: |\n  a\tb\n  \tc\n",
    "a literal block indented by a tab": "k: |\n\ta\n",
    "a literal block with a comment on its header": "k: | # note\n  a\n",
    "an empty literal block": "k: |\nj: 1\n",
    "a folded block with more-indented lines": "k: >\n  a\n    b\n  c\n\n  d\n",
    "a folded block with tab-led lines": "k: >\n  a\n  \tb\n  c\n",
    "a folded block, keep": "k: >+\n  a\n\n",
    "every YAML 1.2 escape": r'k: "\0\a\b\t\n\v\f\r\e\ \"\\\N\_\L\P\x41\u00e9\U0001F642"' "\n",
    "an escaped slash": 'k: "a\\/b"\n',
    "an escaped tab character": 'k: "a\\\tb"\n',
    "a surrogate pair escape": 'k: "\\ud83d\\ude42"\n',
    "an escape past U+10FFFF": 'k: "\\U00110000"\n',
    "an anchor and alias": "a: &x 1\nb: *x\n",
    "a merge key": "a: &x {b: 1}\nc:\n  <<: *x\n",
    "a quoted merge key": "'<<': 1\n",
    "a local tag": "k: !x v\n",
    "a core tag": "k: !!str 1\n",
    "a YAML 1.1 directive": "%YAML 1.1\n---\nk: yes\n",
    "a document start marker": "---\nk: v\n",
    "a document end marker": "k: v\n...\n",
    "two documents": "a: 1\n---\nb: 2\n",
    "a duplicate key": "k: 1\nk: 2\n",
    "a duplicate key, quoted differently": "k: 1\n'k': 2\n",
    "a null key": "~: a\n",
    "nesting past 64 levels": "k: " + "[" * 70 + "]" * 70 + "\n",
    "nesting 60 levels": "k: " + "[" * 60 + "]" * 60 + "\n",
    "an implicit key past 1024 characters": "x" * 1100 + ": v\n",
    "an implicit key of 1024 characters": "x" * 1024 + ": v\n",
    "an explicit key past 1024 characters": "? " + "x" * 1100 + "\n: v\n",
    "an unquoted {{variable}}": "k: {{Page URL}}\n",
    "an unquoted {{variable}} inside text": "k: a {{Page URL}}\n",
    "a colon inside a flow scalar": "k: {a: b:c}\n",
    "a key ending in a colon inside flow": "k: {a:}\n",
    "a tab leading a double-quoted continuation": 'k: "a\n\tb"\n',
    "a tab leading a plain continuation": "k: a\n \tb\n",
    "a literal block of blank lines, keep": "k: |+\n\n\n",
    "a plain scalar ending in a no-break space": "k: a\u00a0\n",
    "a raw noncharacter": "k: \ufffe\n",
    "keys outside ASCII": "é: ü\n🙂: v\n",
}


@functools.cache
def hand_written_sweep() -> tuple[tuple[tuple[str, str, Any], ...], tuple[str, ...], tuple[str, ...]]:
    """(accepted as (label, text, value), refused labels, disagreements)."""
    accepted, refused, problems = [], [], []
    for label, text in HAND_WRITTEN.items():
        try:
            expected = _plain(yamlio.load(text))
        except YamlError:
            refused.append(label)
            continue
        accepted.append((label, text, expected))
        problems += [
            f"{label}: Tiggy reads {_short(expected)}, but {p}" for p in _disagreements(text, expected)
        ]
    return tuple(accepted), tuple(refused), tuple(problems)


def test_tiggy_refuses_or_agrees_on_every_hand_edit():
    accepted, refused, problems = hand_written_sweep()
    assert not problems, _report(list(problems))
    # What some reader reads differently, refuses, or reads as something else: Tiggy refuses all of it.
    assert {
        "a tab inside a flow sequence", "a bare CR", "a byte-order mark mid-file", "a raw NEL",
        "a raw line separator", "an escaped slash", "a surrogate pair escape", "an anchor and alias",
        "a merge key", "a quoted merge key", "a local tag", "a core tag", "a YAML 1.1 directive",
        "two documents", "a duplicate key", "a duplicate key, quoted differently", "a null key",
        "an empty value", "an unquoted {{variable}}", "nesting past 64 levels",
    } <= set(refused)  # fmt: skip
    assert len(accepted) >= 30, [label for label, _, _ in accepted]


def test_a_duplicate_key_is_refused_at_its_second_line():
    """PyYAML keeps the last of two equal keys and ruamel refuses them, so Tiggy refuses, naming the line."""
    text = "tiggy: 1\ntags:\n- name: GA4 - page_view\n  type: gaawe\n  type: html\n"
    assert yaml.safe_load(text)["tags"][0]["type"] == "html"
    with pytest.raises(YamlError, match='"type" appears twice in one mapping') as refused:
        yamlio.load(text)
    assert refused.value.line == 5


@needs_yq
def test_yq_agrees_on_every_hand_edit_tiggy_accepts(tmp_path: Path):
    problems = _yq_disagreements(hand_written_sweep()[0], tmp_path)
    assert not problems, _report(problems)


@pytest.mark.parametrize(
    "make",
    [lambda v: [v], lambda v: {"a": v}, lambda v: Flow(a=v)],
    ids=["sequences", "mappings", "flow mappings"],
)
def test_a_tree_at_the_nesting_limit_writes_and_reads_everywhere(make: Callable[[Any], Any], tmp_path: Path):
    """render writes 64 levels, which every tool reads back; a 65th is refused before anything is written."""

    def nested(levels: int) -> dict[str, Any]:
        value: Any = "a\nb\n"
        for _ in range(levels - 1):
            value = make(value)
        return {"k": value}

    text = yamlio.render(nested(64))
    assert not _disagreements(text, _plain(nested(64)))
    if YQ:
        assert not _yq_disagreements([("64 levels", text, _plain(nested(64)))], tmp_path)
    with pytest.raises(YamlError, match="nests more than 64 levels deep"):
        yamlio.render(nested(65))


# 4. Known gaps


@pytest.mark.xfail(
    strict=True,
    raises=YamlError,
    reason="known gap: _one_line_mapping doesn't recurse into dicts inside lists, so a Flow holding a list "
    "of Flow mappings whose key is past YAML's 1024-character implicit-key limit is written inline and "
    "render raises rather than writing it in block form",
)
def test_a_flow_list_of_mappings_with_a_very_long_key_still_writes():
    tree = {"": Flow({"": [Flow({"x" * 1100: None})]})}
    text = yamlio.render(tree)
    assert _difference(_plain(tree), _plain(yamlio.load(text))) is None
    assert not _disagreements(text, _plain(tree))
