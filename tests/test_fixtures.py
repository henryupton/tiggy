"""The fixtures themselves: current, well-formed, unmodified where borrowed, and free of real identifiers."""

import hashlib
import json
import os
import re
import shutil
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from tests.conftest import (
    ALL_EXPORTS,
    CDM_EXPORTS,
    FIXTURES,
    ROOT,
    TESTS,
    THIRD_PARTY_EXPORTS,
    export_id,
    load_export,
    run_tiggy,
)
from tests.fixtures.cdm import build

COMMITTED = CDM_EXPORTS + THIRD_PARTY_EXPORTS

# Git blob SHAs of the upstream files (see third_party/NOTICE.md); equal means byte for byte unmodified.
UPSTREAM_BLOBS = {
    "bounteous_youtube_tracking.json": "b8a06a1690708221ccd4c294cb3b1553f8b596d5",
    "stape_magento_meta_web.json": "16d431f38b16f07ecbd49ec0d0adf22766486221",
    "stape_shopify_meta_ga4_ads_server.json": "27cc238bea3a68e65a9887f4dd31e1371f175c53",
    "stape_woocommerce_meta_web.json": "76524e89395dc37ab3761e4b43d658686659414f",
}

# `tiggy validate` on each synthetic export: (exit code, errors, warnings), as fixtures/README.md says.
VALIDATE = {
    "server.json": (0, 0, 0),
    "web_analytics.json": (0, 0, 0),
    "web_legacy.json": (1, 1, 10),
    "web_marketing.json": (0, 0, 0),
}

# The permission each sandboxed API needs, for the ones the synthetic templates call.
API_PERMISSIONS = {
    "callInWindow": "access_globals",
    "copyFromWindow": "access_globals",
    "getAllEventData": "read_event_data",
    "getContainerVersion": "read_container_data",
    "getCookieValues": "get_cookies",
    "gtagSet": "write_data_layer",
    "injectScript": "inject_script",
    "isConsentGranted": "access_consent",
    "logToConsole": "logging",
    "sendHttpRequest": "send_http",
    "setCookie": "set_cookies",
    "setDefaultConsentState": "access_consent",
}
NEEDS_NO_PERMISSION = {"JSON", "Math", "generateRandom", "getTimestampMillis", "makeNumber", "makeTableMap"}


def test_cdm_exports_are_what_build_writes():
    built = build.build_all()
    assert sorted(built) == sorted(p.name for p in CDM_EXPORTS), "run tests/fixtures/cdm/build.py"
    for name, body in built.items():
        committed = (FIXTURES / "cdm" / name).read_bytes().decode("utf-8")
        assert committed == body, f"{name} is stale: run uv run python tests/fixtures/cdm/build.py"


def test_cdm_build_is_deterministic():
    assert build.build_all() == build.build_all()


@pytest.mark.parametrize("export", COMMITTED, ids=export_id)
def test_fixture_is_a_container_export(export: Path):
    data = load_export(export)
    assert isinstance(data.get("containerVersion"), dict)
    assert isinstance(data["containerVersion"].get("container"), dict)
    assert data.get("exportFormatVersion") is not None


@pytest.mark.parametrize("export", THIRD_PARTY_EXPORTS, ids=export_id)
def test_third_party_fixture_is_unmodified(export: Path):
    body = export.read_bytes()
    blob = hashlib.sha1(b"blob %d\0" % len(body) + body).hexdigest()
    assert blob == UPSTREAM_BLOBS[export.name], f"{export.name} no longer matches its upstream file"
    notice = (FIXTURES / "third_party" / "NOTICE.md").read_text(encoding="utf-8")
    assert f"`{export.name}`" in notice
    licences = re.findall(r"`(licenses/[^`]+)`", notice)
    assert licences and all((FIXTURES / "third_party" / p).is_file() for p in licences)


@pytest.mark.parametrize("export", CDM_EXPORTS, ids=export_id)
def test_cdm_secrets_are_placeholders(export: Path):
    """A constant that holds a token or key says so in its value, so nothing real can pass as one."""
    version = load_export(export)["containerVersion"]
    for variable in version.get("variable", []):
        if variable["type"] != "c" or not re.search(r"token|secret|api key", variable["name"], re.IGNORECASE):
            continue
        (value,) = [p["value"] for p in variable["parameter"] if p["key"] == "value"]
        assert value.endswith("_NOT_A_SECRET"), f"{variable['name']} = {value!r}"


def test_cdm_fixtures_round_trip_as_json():
    for export in CDM_EXPORTS:
        text = export.read_bytes().decode("utf-8")
        assert json.dumps(json.loads(text), indent=4, ensure_ascii=False) == text


@pytest.mark.parametrize("export", CDM_EXPORTS, ids=export_id)
def test_cdm_templates_are_shaped_like_real_ones(export: Path):
    """Gallery templates open with the terms section and container-authored ones with INFO, and every
    sandboxed API a template's code requires has its permission."""
    for template in load_export(export)["containerVersion"].get("customTemplate", []):
        name, data = template["name"], template["templateData"]
        first = "TERMS_OF_SERVICE" if "galleryReference" in template else "INFO"
        assert data.startswith(f"___{first}___\n\n"), f"{name} doesn't open with {first}"
        sections = dict(re.findall(r"___([A-Z_]+)___\n\n(.*?)\n\n\n(?=___|\Z)", data, re.DOTALL))
        (code,) = [body for title, body in sections.items() if title.startswith("SANDBOXED_JS")]
        permissions = [body for title, body in sections.items() if title.endswith("_PERMISSIONS")]
        granted = {p["instance"]["key"]["publicId"] for body in permissions for p in json.loads(body)}
        needed = set()
        for api in re.findall(r"require\('([^']+)'\)", code):
            assert api in API_PERMISSIONS or api in NEEDS_NO_PERMISSION, f"{name}: what does {api} need?"
            needed |= {API_PERMISSIONS[api]} if api in API_PERMISSIONS else set()
        assert needed <= granted, f"{name}: no permission for {sorted(needed - granted)}"


@pytest.mark.parametrize("export", ALL_EXPORTS, ids=export_id)
def test_export_imports_checks_and_round_trips(export: Path, tmp_path: Path):
    """import writes a spec, --check finds it equivalent, and export then import reproduces it exactly."""
    spec, again, compiled = tmp_path / "spec", tmp_path / "again", tmp_path / "compiled.json"
    for args in (
        ("import", export, spec),
        ("import", export, spec, "--check"),
        ("export", spec, "-o", compiled),
        ("import", compiled, again),
    ):
        result = run_tiggy(*args)
        assert result.code == 0, f"tiggy {' '.join(map(str, args))}\n{result}"
    assert _files(again) == _files(spec)


@pytest.mark.parametrize("export", CDM_EXPORTS, ids=export_id)
def test_cdm_validate_findings_are_as_documented(export: Path):
    result = run_tiggy("validate", export, "--format", "json")
    summary = json.loads(result.out)["summary"]
    assert (result.code, summary["errors"], summary["warnings"]) == VALIDATE[export.name], str(result)


def _files(directory: Path) -> dict[str, bytes]:
    return {p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob("*") if p.is_file()}


# Hygiene. This repository is public, so the rules say what identifiers may look like rather than
# listing real ones; anything specific lives in a private deny-list outside the repository.

ID_RULES = {  # (any such ID, the synthetic form it has to take)
    "GTM container ID": (re.compile(r"\bGTM-[0-9A-Z]+\b"), re.compile(r"GTM-(?:TEST)\d{3}")),
    "GA4 measurement ID": (re.compile(r"\bG-[0-9A-Z]+\b"), re.compile(r"G-(?:TEST)\d+")),
    "Google Ads ID": (re.compile(r"\bAW-[0-9]+\b"), re.compile(r"AW-0+\d?")),
    "Universal Analytics ID": (re.compile(r"\bUA-[0-9]+-[0-9]+\b"), re.compile(r"UA-0+-\d+")),
}
TICKET_KEY = re.compile(r"\b(?!(?:GTM|AW|UA)-)[A-Z][0-9A-Z]+-[0-9]+\b")
NOT_TICKET_KEYS = {"UTF-8"}
API_GATEWAY = re.compile(r"execute[-]api", re.IGNORECASE)

SYNTHETIC_DOMAINS = ("example.com", "example.net", "example.org")
VENDOR_HOSTS = {  # public hosts real exports name, and why a fixture may too
    "tagmanager.google.com": "every export's tagManagerUrl",
    "github.com": "every gallery template's galleryReference",
    "developers.google.com": "the terms notice atop gallery templates",
    "googletagmanager.com": "where gtm.js and gtag.js load from",
    "google-analytics.com": "where analytics.js and GA collection live",
}
NOT_HOSTS = {"tiggy.io"}  # Tiggy's own module, which a test may import
# A bare hostname is a dotted name ending in one of these. Code's dotted names (os.path,
# data.gtmOnSuccess) end in none of them; TLDs that double as file extensions (py, md, sh) are left out.
TLDS = "com|net|org|io|co|ai|app|dev|cloud|biz|us|uk|au|nz|eu|de|ca|xyz|internal|corp|lan|intranet"
BARE_HOST = re.compile(
    rf"(?<![\w.-])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:{TLDS})(?![\w-])", re.IGNORECASE
)
SCHEME_HOST = re.compile(r"\b[a-z][a-z0-9+.-]*://([^/\s'\"`?#<>()\[\]{}\\,;|]+)", re.IGNORECASE)
PROTOCOL_RELATIVE_HOST = re.compile(r"(?<![\w:/])//((?:[a-z0-9-]+\.)+[a-z0-9-]+)", re.IGNORECASE)
JSON_ESCAPE = re.compile(r"\\[nrt\"]")  # code inside JSON strings: `\nfoo` must not hide `foo`
SKIPPED_DIRS = {"__pycache__", ".pytest_cache", ".hypothesis", ".ruff_cache"}


def read_denylist(raw: str | None) -> list[str]:
    """Forbidden substrings from TIGGY_HYGIENE_DENYLIST: one per non-empty line of a file outside the repo."""
    if not raw:
        return []
    path = Path(raw).expanduser().resolve()
    if path == ROOT or ROOT in path.parents:
        raise ValueError("TIGGY_HYGIENE_DENYLIST names a file inside the repository; keep it outside")
    if not path.is_file():
        raise ValueError(f"TIGGY_HYGIENE_DENYLIST={raw} is not a file")
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def hygiene_violations(root: Path, denylist: Sequence[str] = ()) -> list[str]:
    """Where the text files under `root`, a tests/ tree, break the hygiene rule. Deny-list hits name
    the entry's line number, never its text, so a CI log can't publish the list."""
    found = []
    denied = [entry.casefold() for entry in denylist]
    for path in _scanned(root):
        where = path.relative_to(root).as_posix()
        data = path.read_bytes()
        if b"\0" in data:
            continue  # binary, such as .DS_Store
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            found.append(f"{where}: isn't UTF-8, so it can't be checked")
            continue
        for number, line in enumerate(text.splitlines(), 1):
            found += [f"{where}:{number}: {problem}" for problem in _problems(JSON_ESCAPE.sub(" ", line))]
            folded = line.casefold()
            found += [f"{where}:{number}: deny-list line {n}" for n, d in enumerate(denied, 1) if d in folded]
    return found


def _scanned(root: Path) -> list[Path]:
    """Every file under a tests/ tree but third-party exports (real, and licensed as they are) and caches."""
    third_party = root / "fixtures" / "third_party"
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and not SKIPPED_DIRS & set(p.relative_to(root).parts) and third_party not in p.parents
    )


def _problems(line: str) -> Iterator[str]:
    for what, (shape, synthetic) in ID_RULES.items():
        for match in shape.finditer(line):
            if not synthetic.fullmatch(match.group()):
                yield f"{what} {match.group()!r} isn't synthetic"
    for match in TICKET_KEY.finditer(line):
        if match.group() not in NOT_TICKET_KEYS:
            yield f"{match.group()!r} is shaped like a ticket key"
    if API_GATEWAY.search(line):
        yield "an API Gateway URL"
    for host in sorted(set(_hosts(line))):
        if not _allowed(host):
            yield f"host {host!r} is outside the example domains and the vendor allowlist"


def _hosts(line: str) -> Iterator[str]:
    for match in SCHEME_HOST.finditer(line):
        host = match.group(1).rsplit("@", 1)[-1].split(":", 1)[0]
        if re.fullmatch(r"[a-z0-9.-]+", host, re.IGNORECASE):  # not a {{variable}} or other template
            yield host.lower().rstrip(".")
    for pattern in (PROTOCOL_RELATIVE_HOST, BARE_HOST):
        for match in pattern.finditer(line):
            host = match.group(match.lastindex or 0).lower().rstrip(".")
            if host not in NOT_HOSTS:
                yield host


def _allowed(host: str) -> bool:
    return any(host == d or host.endswith(f".{d}") for d in (*SYNTHETIC_DOMAINS, *VENDOR_HOSTS))


def test_tests_hold_only_synthetic_identifiers():
    try:
        denylist = read_denylist(os.environ.get("TIGGY_HYGIENE_DENYLIST"))
    except ValueError as e:
        pytest.fail(str(e), pytrace=False)
    found = hygiene_violations(TESTS, denylist)
    assert not found, "\n".join(found)


def _copy_of_tests(tmp_path: Path) -> Path:
    copy = tmp_path / "tests"
    shutil.copytree(TESTS, copy, ignore=shutil.ignore_patterns(*SKIPPED_DIRS))
    return copy


def _append(root: Path, relpath: str, line: str) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"\n{line}\n")


def test_hygiene_rules_catch_what_is_planted_in_every_kind_of_file(tmp_path: Path):
    """Each value is assembled at run time, so this file doesn't trip its own scan."""
    copy = _copy_of_tests(tmp_path)
    assert hygiene_violations(copy) == [], "the copy should start clean, third-party exports included"
    host = ".".join
    planted = {
        # (file, line): what the report must say about it
        ("fixtures/cdm/web_marketing.json", "GTM-" + "K7Q2XPL"): "GTM container ID",
        ("fixtures/README.md", "measurement ID: " + "G-" + "Q7XK2PL9ZZ"): "GA4 measurement ID",
        ("conftest.py", "# conversion " + "AW-" + "381920457"): "Google Ads ID",
        ("fixtures/cdm/build.py", "# property " + "UA-" + "3819204-7"): "Universal Analytics ID",
        ("goldens/import/out.txt", "see " + "WEB" + "-1234"): "shaped like a ticket key",
        (
            "goldens/spec/tags.yml",
            "url: https://" + host(["a1b2c3", "execute" + "-api", "aws"]),
        ): "API Gateway",
        ("fixtures/cdm/builders.py", "# " + host(["sgtm", "acme-corp", "io"])): "host",
        ("test_fixtures.py", "# https://" + "jenkins:8080/job"): "host 'jenkins'",
        ("goldens/spec/custom.html", "<script src='//" + host(["cdn", "acme", "ninja"]) + "/a.js'>"): "host",
        ("goldens/spec/lookup.yml", "- key: " + host(["WWW", "Acme-Corp", "COM", "AU"])): "host",
    }
    for relpath, line in planted:
        _append(copy, relpath, line)
    allowed = [
        "GTM-TEST009 G-TEST000009 AW-000000009 UA-000000-9 UTF-8",
        "https://www.googletagmanager.com/gtm.js //cdn.example.org/a.js https://tagmanager.google.com/#/x",
        "- key: " + host(["shop", "example", "net"]) + " " + host(["tiggy", "io"]),
    ]
    for line in allowed:
        _append(copy, "goldens/allowed.txt", line)
    _append(copy, "fixtures/third_party/NOTICE.md", "GTM-" + "K7Q2XPL")

    found = hygiene_violations(copy)
    for (relpath, line), expected in planted.items():
        hits = [f for f in found if f.startswith(f"{relpath}:") and expected in f]
        assert hits, f"{expected} in {relpath} went unreported: {line!r}\n" + "\n".join(found)
    assert not [f for f in found if f.startswith(("goldens/allowed.txt", "fixtures/third_party/"))], found


def test_denylist_entries_are_caught_in_any_case_without_being_echoed(tmp_path: Path):
    """The entries are assembled at run time, so this file doesn't hold them itself."""
    copy = _copy_of_tests(tmp_path)
    marker, unused = " ".join(["Zq", "Private", "Marker"]), " ".join(["never", "planted"])
    denylist = tmp_path / "denylist.txt"
    denylist.write_text(f"\n  {marker}  \n\n{unused}\n", encoding="utf-8")
    entries = read_denylist(str(denylist))
    assert entries == [marker, unused]
    _append(copy, "fixtures/cdm/web_legacy.json", marker.upper())
    _append(copy, "goldens/import/out.txt", f"a {marker.lower()}, lower-cased")

    found = hygiene_violations(copy, entries)
    assert sorted(f.split(":")[0] for f in found) == [
        "fixtures/cdm/web_legacy.json",
        "goldens/import/out.txt",
    ]
    assert all(f.endswith("deny-list line 1") for f in found), found
    assert marker.casefold() not in "\n".join(found).casefold()


def test_denylist_has_to_live_outside_the_repository(tmp_path: Path):
    with pytest.raises(ValueError, match="inside the repository"):
        read_denylist(str(ROOT / "README.md"))
    with pytest.raises(ValueError, match="is not a file"):
        read_denylist(str(tmp_path / "missing.txt"))
    assert read_denylist(None) == read_denylist("") == []
