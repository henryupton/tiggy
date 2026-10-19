"""The adopting repo's pre-commit hooks and checkouts, run against imported specs.

The analytics team's repo runs pre-commit-hooks v6.0.0's trailing-whitespace, end-of-file-fixer and
check-yaml with no excludes, and some of the team check out on Windows. Hooks run in-process through
their real entry points; checkouts go through a real git with an isolated configuration.
"""

import ast
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from pre_commit_hooks import check_yaml, end_of_file_fixer, trailing_whitespace_fixer

from tests.conftest import (
    ALL_EXPORTS,
    CDM_EXPORTS,
    FIXTURES,
    ROOT,
    THIRD_PARTY_EXPORTS,
    Result,
    export_id,
    load_export,
    run_tiggy,
    store_for,
    write_export,
)

ANALYTICS = FIXTURES / "cdm" / "web_analytics.json"
CODE_SUFFIXES = (".tpl", ".html", ".js")
IDENTITY = {"built_in_variables": "type", "gtag_configs": "label"}

# The Analytics container with notes typed the ways that trip whitespace fixers: trailing spaces and
# tabs, blank lines at the end, CRLF pasted from Windows, leading indentation, and literal blocks that
# end a fragment.
AWKWARD = "awkward-notes"
AWKWARD_NOTES = {
    ("container", None): "Loaded before consent.\nOwned by the analytics team.\n",
    ("tag", "Google tag"): "URL from LT - server container URL.  \nKeep in step with Marketing.",
    ("tag", "GA4 - wishlist"): "A line ending in a tab\t\nand one after it",
    ("tag", "GA4 - sign_up"): "\n\n",
    ("tag", "Cookiebot CMP"): "Consent Initialization only.\n\n\n",
    ("trigger", "CE - track_event"): "No event allowlist.\r\nPasted from a Windows editor.\r\n",
    ("variable", "LT - server container URL"): "Hosts:\n  www.example.com\n  staging.example.com\n",
    ("variable", "URL - utm_campaign"): "\tIndented with a tab\nsecond line\n",
    ("folder", "Data Layer"): "  Cloned from an existing DLV\nversion 2, no default",
    ("folder", "GA4"): "GA4 event tags\nfire on track_event\n",
}

COMMITTED = [*CDM_EXPORTS, *THIRD_PARTY_EXPORTS]  # each has sidecars that end untidily
HOOKED = [*ALL_EXPORTS, AWKWARD]  # for tests about fragments, where the notes matter

PRE_COMMIT_CONFIG = """\
{exclude_line}
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v6.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
"""

DIFFERENCE = re.compile(r"≠ (\w+) ('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"): ")
EXPORT_TIME = re.compile(rb'"exportTime": "[^"]*"')


def _id(param: Path | str) -> str:
    return export_id(param) if isinstance(param, Path) else param


@pytest.fixture
def source(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    """The export under test: a fixture file, or the awkward-notes variant written into tmp_path."""
    if request.param == AWKWARD:
        return _awkward_export(tmp_path / f"{AWKWARD}.json")
    return request.param


def _awkward_export(path: Path) -> Path:
    data = load_export(ANALYTICS)
    version = data["containerVersion"]
    for (kind, name), notes in AWKWARD_NOTES.items():
        if kind == "container":
            version["container"]["notes"] = notes
            continue
        (found,) = [o for o in version[kind] if o["name"] == name]
        found["notes"] = notes
    return write_export(data, path)


@pytest.fixture
def git(tmp_path_factory: pytest.TempPathFactory) -> Callable[..., bytes]:
    """`git(*args, cwd=...)`: git with no system or user configuration, failing the test on error."""
    config = tmp_path_factory.mktemp("git") / "config"
    config.write_bytes(b"")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}  # a hook's GIT_DIR, say
    env.update(
        GIT_CONFIG_GLOBAL=str(config),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="Analytics Team",
        GIT_AUTHOR_EMAIL="analytics@example.com",
        GIT_COMMITTER_NAME="Analytics Team",
        GIT_COMMITTER_EMAIL="analytics@example.com",
    )

    def run(*args: str | os.PathLike, cwd: Path) -> bytes:
        argv = ["git", "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", *map(os.fspath, args)]
        done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, check=False)
        assert done.returncode == 0, f"{' '.join(argv)}\n{done.stderr.decode('utf-8', 'replace')}"
        return done.stdout

    return run


# Reading a spec


def _fragments(spec: Path) -> list[Path]:
    return [
        p
        for p in sorted(spec.rglob("*.yml"))
        if not any(part.startswith(".") for part in p.relative_to(spec).parts)
    ]


def _snapshot(spec: Path) -> dict[str, bytes]:
    """Every file in the spec, dotfiles included, by spec-relative path."""
    return {p.relative_to(spec).as_posix(): p.read_bytes() for p in sorted(spec.rglob("*")) if p.is_file()}


def _file_refs(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        if set(node) == {"$file"}:
            yield node["$file"]
            return
        for value in node.values():
            yield from _file_refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _file_refs(value)


def _sidecar_owners(spec: Path) -> dict[str, tuple[str, str]]:
    """Each sidecar a `$file` names, and the (kind, name) of the object it belongs to."""
    owners: dict[str, tuple[str, str]] = {}
    for fragment in _fragments(spec):
        tree = yaml.safe_load(fragment.read_bytes().decode("utf-8"))
        for kind, items in tree.items():
            if not isinstance(items, list):
                continue
            for item in items:
                for ref in _file_refs(item):
                    assert ref not in owners, f"{ref} is named twice"
                    owners[ref] = (kind, item[IDENTITY.get(kind, "name")])
    on_disk = {rel for rel in _snapshot(spec) if rel.endswith(CODE_SUFFIXES)}
    assert on_disk == set(owners), "a code file no $file names, or a $file with no code file"
    return owners


def _untidy(body: bytes) -> set[str]:
    """Why trailing-whitespace or end-of-file-fixer would rewrite a file holding `body`."""
    reasons = set()
    lines = body.split(b"\n")
    for line in [*(line.removesuffix(b"\r") for line in lines[:-1]), lines[-1]]:
        if line != line.rstrip():
            reasons.add("whitespace at the end of a line")
    ending = body[len(body.rstrip(b"\r\n")) :]
    if body and not body.strip(b"\r\n"):
        reasons.add("nothing but line breaks")
    elif body and not ending:
        reasons.add("no final newline")
    elif ending not in (b"", b"\n", b"\r\n", b"\r"):
        reasons.add("blank lines at the end")
    return reasons


# Running the adopting repo's hooks and Tiggy


def _pre_commit(files: list[str]) -> dict[str, int]:
    """The three hooks in config order, each over the files its `types` select (text, text, yaml)."""
    yaml_files = [f for f in files if f.endswith((".yml", ".yaml"))]
    return {
        "trailing-whitespace": trailing_whitespace_fixer.main(files),
        "end-of-file-fixer": end_of_file_fixer.main(files),
        "check-yaml": check_yaml.main(yaml_files),
    }


def _every_file(spec: Path) -> list[str]:
    return [str(p) for p in sorted(spec.rglob("*")) if p.is_file()]


def _named(result: Result) -> set[tuple[str, str]]:
    """The (kind, name) of every object `import --check` reports as differing."""
    lines = [line for line in result.out.splitlines() if line.startswith("≠ ")]
    named = set()
    for line in lines:
        match = DIFFERENCE.match(line)
        assert match, f"unexpected difference line: {line}"
        named.add((match[1], ast.literal_eval(match[2])))
    assert len(named) == len(lines), result.out
    return named


def _assert_check_names(result: Result, damaged: set[tuple[str, str]]) -> None:
    """`import --check` exits 1 naming exactly the damaged objects, or 0 when nothing was damaged."""
    if not damaged:
        assert result.code == 0, str(result)
        return
    assert result.code == 1, str(result)
    assert _named(result) == damaged, str(result)
    assert f"{len(damaged)} difference" in result.out.splitlines()[-1], str(result)


def _validate_and_export(spec: Path, output: Path, store: dict[str, str]) -> tuple[int, str, int, str, bytes]:
    validated = run_tiggy("validate", spec)
    exported = run_tiggy("export", spec, "-o", output, env=store)
    assert exported.code == 0, str(exported)
    data = EXPORT_TIME.sub(b'"exportTime": ""', output.read_bytes())
    return validated.code, validated.out, exported.code, exported.err, data


def _printed_exclude_line(result: Result) -> str:
    lines = [line.strip() for line in result.out.splitlines() if line.strip().startswith("exclude:")]
    assert len(lines) == 1, str(result)
    return lines[0]


def _exclude(result: Result) -> re.Pattern[str]:
    """The printed exclude, read as pre-commit reads its config (YAML) and compiles it (Python re)."""
    return re.compile(yaml.safe_load(_printed_exclude_line(result))["exclude"])


def _tracked(git: Callable[..., bytes], repo: Path) -> list[str]:
    """Paths as pre-commit gets them: `git ls-files -z`, relative to the repository root."""
    git("add", "-A", cwd=repo)
    return [p for p in git("ls-files", "-z", cwd=repo).decode("utf-8").split("\0") if p]


def _commit_and_clone_on_windows(git: Callable[..., bytes], repo: Path, clone: Path) -> None:
    """Commit everything, then clone the way Git for Windows' default (core.autocrlf=true) checks out."""
    (repo / ".gitattributes").write_bytes(b"* text=auto\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "Adopt the container as it is", cwd=repo)
    git("clone", "-q", "-c", "core.autocrlf=true", repo, clone, cwd=repo.parent)


# Pre-commit with no excludes


@pytest.mark.parametrize("source", HOOKED, ids=_id, indirect=True)
def test_check_yaml_passes_and_the_fixers_leave_every_fragment_byte_identical(
    source: Path, imported: Callable[..., Path]
):
    spec = imported(source)
    before = _snapshot(spec)
    codes = _pre_commit(_every_file(spec))
    after = _snapshot(spec)
    assert codes["check-yaml"] == 0
    assert _fragments(spec), "the spec has no fragments to check"
    untouched = [rel for rel in before if not rel.endswith(CODE_SUFFIXES)]
    assert {".gitattributes", ".editorconfig"} <= set(untouched)
    assert [rel for rel in untouched if after[rel] != before[rel]] == []


@pytest.mark.parametrize("source", ALL_EXPORTS, ids=_id, indirect=True)
def test_the_fixers_rewrite_exactly_the_untidy_sidecars_and_check_names_their_owners(
    request: pytest.FixtureRequest, source: Path, imported: Callable[..., Path]
):
    spec = imported(source)
    owners = _sidecar_owners(spec)
    before = {rel: (spec / rel).read_bytes() for rel in owners}
    reasons = {rel: sorted(_untidy(body)) for rel, body in before.items() if _untidy(body)}
    if request.node.callspec.params["source"] in COMMITTED:
        assert reasons, "the fixtures' sidecars are meant to end untidily, as real ones do"

    _pre_commit(_every_file(spec))
    rewritten = {rel for rel, body in before.items() if (spec / rel).read_bytes() != body}
    assert rewritten == set(reasons), reasons

    result = run_tiggy("import", source, spec, "--check")
    _assert_check_names(result, {owners[rel] for rel in rewritten})


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="known gap: validate reads each sidecar as whatever is on disk, so once the spec is the source "
    "of truth there is no export left to show the fixers rewrote one",
)
@pytest.mark.parametrize("source", ALL_EXPORTS, ids=_id, indirect=True)
def test_validate_alone_notices_the_fixers_rewrote_a_sidecar(source: Path, imported: Callable[..., Path]):
    spec = imported(source)
    before = {rel: (spec / rel).read_bytes() for rel in _sidecar_owners(spec)}
    untidy = sorted(rel for rel, body in before.items() if _untidy(body))
    if not untidy:
        pytest.skip("no sidecar ends untidily, so the fixers change nothing")
    _pre_commit(_every_file(spec))
    if any((spec / rel).read_bytes() == before[rel] for rel in untidy):
        pytest.fail("the fixers left an untidy sidecar alone, so this test proves nothing")

    result = run_tiggy("validate", spec, "--strict")
    assert result.code == 1, str(result)
    assert [rel for rel in untidy if rel not in result.out] == [], str(result)


# Windows checkouts


@pytest.mark.parametrize("source", HOOKED, ids=_id, indirect=True)
def test_a_windows_checkout_with_crlf_fragments_validates_and_exports_identically(
    source: Path, imported: Callable[..., Path], tmp_path: Path
):
    spec, store = imported(source), store_for(source, tmp_path / "store")
    as_imported = _validate_and_export(spec, tmp_path / "lf.json", store)
    for rel, body in _snapshot(spec).items():
        if not rel.endswith(CODE_SUFFIXES):  # what core.autocrlf converts, given import's .gitattributes
            assert b"\r" not in body, f"{rel} already holds a CR"
            (spec / rel).write_bytes(body.replace(b"\n", b"\r\n"))

    assert _validate_and_export(spec, tmp_path / "crlf.json", store) == as_imported
    assert check_yaml.main([str(f) for f in _fragments(spec)]) == 0


@pytest.mark.parametrize("source", ALL_EXPORTS, ids=_id, indirect=True)
def test_import_keeps_git_and_editors_off_the_code_files(
    source: Path, imported: Callable[..., Path], tmp_path: Path, git: Callable[..., bytes]
):
    """Even under a repo-wide `* text=auto eol=crlf` and an .editorconfig that tidies everything."""
    repo = tmp_path / "repo"
    spec = imported(source, "repo/gtm/container")
    git("init", "-q", cwd=repo)
    (repo / ".gitattributes").write_bytes(b"* text=auto eol=crlf\n")
    (repo / ".editorconfig").write_bytes(
        b"root = true\n\n[*]\ninsert_final_newline = true\ntrim_trailing_whitespace = true\n"
    )
    sidecars = sorted(_sidecar_owners(spec))
    fragments = [f.relative_to(spec).as_posix() for f in _fragments(spec)]

    paths = [f"gtm/container/{rel}" for rel in sidecars + fragments]
    output = git("check-attr", "-z", "text", "--", *paths, cwd=repo).decode("utf-8").split("\0")
    text = {path: value for path, _, value in zip(output[::3], output[1::3], output[2::3])}
    assert {rel: text[f"gtm/container/{rel}"] for rel in sidecars} == dict.fromkeys(sidecars, "unset")
    assert {rel: text[f"gtm/container/{rel}"] for rel in fragments} == dict.fromkeys(fragments, "auto")

    off = {"insert_final_newline": "false", "trim_trailing_whitespace": "false"}
    on = {"insert_final_newline": "true", "trim_trailing_whitespace": "true"}
    assert {rel: _editorconfig(spec / rel, repo) for rel in sidecars} == dict.fromkeys(sidecars, off)
    assert {rel: _editorconfig(spec / rel, repo) for rel in fragments} == dict.fromkeys(fragments, on)


@pytest.mark.parametrize("source", ALL_EXPORTS, ids=_id, indirect=True)
def test_a_windows_clone_keeps_every_sidecar_byte_exact_so_check_stays_clean(
    source: Path, imported: Callable[..., Path], tmp_path: Path, git: Callable[..., bytes]
):
    repo, clone = tmp_path / "repo", tmp_path / "windows"
    spec = imported(source, "repo/gtm/container")
    git("init", "-q", cwd=repo)
    _commit_and_clone_on_windows(git, repo, clone)
    cloned = clone / "gtm" / "container"

    original = _snapshot(spec)
    assert sorted(_snapshot(cloned)) == sorted(original)
    for rel, body in original.items():
        want = body if rel.endswith(CODE_SUFFIXES) else body.replace(b"\n", b"\r\n")
        assert (cloned / rel).read_bytes() == want, rel

    result = run_tiggy("import", source, cloned, "--check")
    assert result.code == 0, str(result)


@pytest.mark.parametrize("source", ALL_EXPORTS, ids=_id, indirect=True)
def test_a_windows_clone_without_the_spec_gitattributes_is_caught_by_check(
    request: pytest.FixtureRequest,
    source: Path,
    imported: Callable[..., Path],
    tmp_path: Path,
    git: Callable[..., bytes],
):
    repo, clone = tmp_path / "repo", tmp_path / "windows"
    spec = imported(source, "repo/gtm/container")
    (spec / ".gitattributes").unlink()
    git("init", "-q", cwd=repo)
    _commit_and_clone_on_windows(git, repo, clone)
    cloned = clone / "gtm" / "container"

    owners = _sidecar_owners(spec)
    before = {rel: (spec / rel).read_bytes() for rel in owners}
    # text=auto converts LF-only text; anything already holding a CR is left as it is.
    convertible = {rel for rel, body in before.items() if b"\n" in body and b"\r" not in body}
    if request.node.callspec.params["source"] in COMMITTED:
        assert convertible
    converted = {rel for rel, body in before.items() if (cloned / rel).read_bytes() != body}
    assert converted == convertible
    for rel in converted:
        assert (cloned / rel).read_bytes() == before[rel].replace(b"\n", b"\r\n"), rel

    result = run_tiggy("import", source, cloned, "--check")
    _assert_check_names(result, {owners[rel] for rel in converted})


# The pre-commit exclude import prints

LOCATIONS = [
    "gtm/web-analytics",
    "containers/web.analytics (prod)",
    "containers/the analytics team's web",
    "containers/données",
]


@pytest.mark.parametrize("location", LOCATIONS)
def test_the_printed_exclude_matches_exactly_the_sidecars(
    location: str, tmp_path: Path, git: Callable[..., bytes]
):
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-q", cwd=repo)
    result = run_tiggy("import", ANALYTICS, repo / location)
    assert result.code == 0, str(result)
    exclude = _exclude(result)

    decoys = [
        "README.md",
        "site/app.js",
        "site/index.html",
        "site/emails/receipt.tpl",
        f"{location}-old/tags.html",  # a sibling sharing the spec's name as a prefix
    ]
    if "." in location:
        decoys.append(f"{location.replace('.', 'X')}/legacy.js")  # what an unescaped `.` would match
    for decoy in decoys:
        (repo / decoy).parent.mkdir(parents=True, exist_ok=True)
        (repo / decoy).write_bytes(b"<script></script>\n")
    tracked = _tracked(git, repo)
    sidecars = {f"{location}/{rel}" for rel in _sidecar_owners(repo / location)}
    assert sidecars <= set(tracked), "git lists the sidecars under other names"
    assert {path for path in tracked if exclude.search(path)} == sidecars


@pytest.mark.parametrize("source", HOOKED, ids=_id, indirect=True)
def test_with_the_printed_exclude_pre_commit_leaves_the_spec_as_imported(
    source: Path, tmp_path: Path, git: Callable[..., bytes], monkeypatch: pytest.MonkeyPatch
):
    """Adopt, then freeze: with the exclude pasted in, the hooks pass and the next --check is a no-op."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-q", cwd=repo)
    spec = repo / "gtm" / "container"
    result = run_tiggy("import", source, spec)
    assert result.code == 0, str(result)
    if not _sidecar_owners(spec):
        assert "exclude:" not in result.out
        return
    config = PRE_COMMIT_CONFIG.format(exclude_line=_printed_exclude_line(result))
    (repo / ".pre-commit-config.yaml").write_bytes(config.encode("utf-8"))
    exclude = re.compile(yaml.safe_load(config)["exclude"])

    before = _snapshot(spec)
    monkeypatch.chdir(repo)  # pre-commit runs hooks from the root, on root-relative paths
    selected = [path for path in _tracked(git, repo) if not exclude.search(path)]
    assert _pre_commit(selected) == {"trailing-whitespace": 0, "end-of-file-fixer": 0, "check-yaml": 0}
    assert _snapshot(spec) == before

    result = run_tiggy("import", source, spec, "--check")
    assert result.code == 0, str(result)


def test_outside_a_git_repo_the_hint_names_every_code_extension(tmp_path: Path):
    spec = tmp_path / "spec"
    result = run_tiggy("import", ANALYTICS, spec, env={"GIT_CEILING_DIRECTORIES": str(tmp_path.resolve())})
    assert result.code == 0, str(result)
    (hint,) = [
        line for line in result.out.splitlines() if line.startswith("Code files must stay byte-exact.")
    ]
    suffixes = {Path(rel).suffix for rel in _sidecar_owners(spec)}
    assert suffixes == set(CODE_SUFFIXES)
    assert [suffix for suffix in sorted(suffixes) if suffix not in hint] == []
    assert "exclude:" not in result.out


@pytest.mark.skipif(
    sys.platform not in ("darwin", "win32"),
    reason="elsewhere a non-UTF-8 locale changes how the spec path itself is read",
)
def test_the_hint_survives_a_non_utf8_locale(tmp_path: Path, git: Callable[..., bytes]):
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-q", cwd=repo)
    location = "containers/Ísland"  # Í is C3 8D in UTF-8; cp1252 has no 8D, ASCII neither byte
    env = {"PYTHONUTF8": "0", "PYTHONIOENCODING": "utf-8"}
    if sys.platform == "darwin":
        env["LC_ALL"] = "en_US.US-ASCII"
    result = run_tiggy("import", ANALYTICS, repo / location, env=env)
    assert (repo / location / "container.yml").is_file(), str(result)
    assert result.code == 0, str(result)
    exclude = _exclude(result)
    sidecars = {f"{location}/{rel}" for rel in _sidecar_owners(repo / location)}
    assert {path for path in _tracked(git, repo) if exclude.search(path)} == sidecars


# EditorConfig, as far as these tests need it


def _editorconfig(path: Path, top: Path) -> dict[str, str]:
    """`path`'s EditorConfig properties from the .editorconfig files between it and `top`, nearest last."""
    found, directory = [], path.parent
    while True:
        config = directory / ".editorconfig"
        if config.is_file():
            found.append(config)
            if _sections(config)[0].get("root") == "true":
                break
        if directory == top:
            break
        directory = directory.parent
    properties: dict[str, str] = {}
    for config in reversed(found):
        relative = path.relative_to(config.parent).as_posix()
        for glob, section in _sections(config)[1:]:
            if _glob(glob).fullmatch(relative):
                properties.update(section)
    return properties


def _sections(config: Path) -> list[Any]:
    """[preamble, (glob, properties), ...] from an .editorconfig."""
    sections: list[Any] = [{}]
    current = sections[0]
    for raw in config.read_bytes().decode("utf-8").splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            current = {}
            sections.append((line[1:-1], current))
        else:
            key, _, value = line.partition("=")
            current[key.strip().lower()] = value.strip().lower()
    return sections


def _glob(glob: str) -> re.Pattern[str]:
    """An EditorConfig glob (`*`, `**`, `?`, `{a,b}`) as a regex over paths relative to its file."""
    assert "[" not in glob and ".." not in glob, f"this test can't read the glob {glob!r}"
    anywhere = "" if "/" in glob else "(?:.*/)?"  # no slash: the name matches at any depth
    glob = glob.removeprefix("/")
    out, depth, i = [], 0, 0
    while i < len(glob):
        if glob.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        c = glob[i]
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "{":
            out.append("(?:")
            depth += 1
        elif c == "}" and depth:
            out.append(")")
            depth -= 1
        elif c == "," and depth:
            out.append("|")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile(anywhere + "".join(out))


def test_the_shipped_pre_commit_hook_validates_the_spec_it_is_given(imported: Callable[..., Path]):
    """An adopting repo adds the hook with its spec directory in `args`; pre-commit appends no file names."""
    (hook,) = yaml.safe_load((ROOT / ".pre-commit-hooks.yaml").read_text(encoding="utf-8"))
    assert (hook["id"], hook["language"]) == ("tiggy-validate", "python")
    assert hook["pass_filenames"] is False, "validate takes one spec directory, not the changed files"
    program, *command = shlex.split(hook["entry"])
    assert program == "tiggy"
    spec = imported(ANALYTICS)
    assert run_tiggy(*command, spec).code == 0
    tags = next(spec.glob("folders/*/tags.yml"))
    tags.write_bytes(tags.read_bytes().replace(b"    type:", b"    typ:", 1))
    assert run_tiggy(*command, spec).code == 1
