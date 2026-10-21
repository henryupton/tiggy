"""Secrets: a spec names each one as {$secret: name}, and only export and an opted-in check read it.

The server container's Meta Conversions API tags send an access token, held in a constant variable.
That token must never land in the repository, never be printed, and still survive a round trip.
"""

import base64
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import (
    FIXTURES,
    export_secrets,
    fragment_of,
    load_export,
    replace_in_spec,
    run_tiggy,
    secret_store,
    store_for,
    write_export,
)
from tiggy.secrets import AwsSecretsManager, GcpSecretManager, SecretError, SecretRef, secret_bearing
from tiggy.spec import importer

SERVER = FIXTURES / "cdm" / "server.json"
TOKEN = "TEST_TOKEN_NOT_A_SECRET"  # what the fixture's constant holds
NAME = "const-meta-api-token"
REF = f"{{$secret: {NAME}}}"
UNREAD = "note: 1 secret value not compared (--resolve-secrets to compare them)\n"


def _spec_text(spec: Path) -> str:
    return "".join(p.read_bytes().decode("utf-8") for p in sorted(spec.rglob("*")) if p.is_file())


def _variables(spec: Path) -> str:
    return fragment_of(spec, "variables", "const - meta api token").relative_to(spec).as_posix()


def _tag(version: dict[str, Any], name: str) -> dict[str, Any]:
    return next(t for t in version["tag"] if t["name"] == name)


def _param(item: dict[str, Any], key: str) -> dict[str, Any]:
    return next(p for p in item["parameter"] if p["key"] == key)


def _server(tmp_path: Path, edit) -> Path:
    data = load_export(SERVER)
    edit(data["containerVersion"])
    return write_export(data, tmp_path / "server.json")


def _rotated(version: dict[str, Any]) -> None:
    constant = next(v for v in version["variable"] if v["name"] == "const - meta api token")
    _param(constant, "value")["value"] = "ROTATED_IN_THE_UI"


def _requests(log: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


# Import


def test_importing_the_server_container_writes_the_meta_token_as_a_reference(tmp_path: Path):
    spec = tmp_path / "spec"

    result = run_tiggy("import", SERVER, spec)

    assert result.code == 0, result
    assert TOKEN not in _spec_text(spec)
    assert f"value: {REF}\n" in (spec / _variables(spec)).read_text(encoding="utf-8")
    assert "1 secret written as {$secret: name}" in result.out, result
    assert f"  {NAME}  (from variable 'const - meta api token')\n" in result.out, result
    assert TOKEN not in result.out + result.err


def test_a_token_pasted_straight_into_tags_is_one_secret_however_many_tags_hold_it(tmp_path: Path):
    def paste(version):
        for name in ("Meta CAPI - Purchase", "Meta CAPI - CompleteRegistration"):
            _param(_tag(version, name), "accessToken")["value"] = "PASTED_TOKEN"

    spec = tmp_path / "spec"
    result = run_tiggy("import", _server(tmp_path, paste), spec)

    assert result.code == 0, result
    assert "PASTED_TOKEN" not in _spec_text(spec) + result.out
    assert "1 secret written" in result.out, result
    assert "  meta-capi-purchase-accesstoken  (from tag 'Meta CAPI - Purchase', parameter.accessToken)\n" in (
        result.out
    )
    tags = (spec / fragment_of(spec, "tags", "Meta CAPI - Purchase")).read_text(encoding="utf-8")
    assert tags.count("{$secret: meta-capi-purchase-accesstoken}") == 2, tags


def test_a_token_read_from_the_request_is_not_a_secret_to_store(tmp_path: Path):
    def from_request(version):
        for name in ("Meta CAPI - Purchase", "Meta CAPI - CompleteRegistration"):
            _param(_tag(version, name), "accessToken")["value"] = "{{Event Data - client_id}}"

    spec = tmp_path / "spec"
    result = run_tiggy("import", _server(tmp_path, from_request), spec)

    assert result.code == 0, result
    assert "secret" not in result.out, result
    assert TOKEN in (spec / _variables(spec)).read_text(encoding="utf-8")


def _debug_header(version: dict[str, Any]) -> None:
    _tag(version, "Meta CAPI - Purchase")["parameter"].append(
        {"type": "TEMPLATE", "key": "debugHeader", "value": TOKEN}
    )


def test_the_meta_token_reused_as_a_debug_header_is_a_reference_of_its_own(tmp_path: Path):
    spec, live, output = tmp_path / "spec", _server(tmp_path, _debug_header), tmp_path / "container.json"

    result = run_tiggy("import", live, spec)

    assert result.code == 0, result
    assert TOKEN not in _spec_text(spec) + result.out
    tags = (spec / fragment_of(spec, "tags", "Meta CAPI - Purchase")).read_text(encoding="utf-8")
    assert "debugHeader: {$secret: meta-capi-purchase-debugheader}\n" in tags, tags
    assert "2 secrets written" in result.out, result
    assert "  meta-capi-purchase-debugheader  (from tag 'Meta CAPI - Purchase', parameter.debugHeader)\n" in (
        result.out
    )
    assert f"  meta-capi-purchase-debugheader  (the value of {NAME})\n" in result.out, result
    checked = run_tiggy("import", live, spec, "--check", env={"TIGGY_TARGETS": None})
    assert (checked.code, checked.err) == (0, UNREAD.replace("1 secret value", "2 secret values")), checked
    store = secret_store(tmp_path / "store", {NAME: "ROTATED", "meta-capi-purchase-debugheader": "ROTATED"})
    exported = run_tiggy("export", spec, "-o", output, env=store)
    assert exported.code == 0, exported
    purchase = _tag(load_export(output)["containerVersion"], "Meta CAPI - Purchase")
    assert _param(purchase, "debugHeader")["value"] == "ROTATED"


def test_a_placeholder_the_token_shares_never_sends_the_token_out_as_another_value(tmp_path: Path):
    stape = FIXTURES / "third_party" / "stape_shopify_meta_ga4_ads_server.json"
    spec, output, placeholder = tmp_path / "spec", tmp_path / "container.json", "PUT_YOUR_VALUE_HERE"

    result = run_tiggy("import", stape, spec)

    assert result.code == 0, result
    assert "  const-meta-pixel-id  (the value of const-meta-api-token)\n" in result.out, result
    assert "  exception-no-meta-pixel-arg1  (the value of const-meta-api-token)\n" in result.out, result
    token_only = secret_store(tmp_path / "token-only", {NAME: "STORE_TOKEN_NOT_A_SECRET"})
    refused = run_tiggy("export", spec, "-o", output, env=token_only)
    assert (refused.code, output.exists()) == (2, False), refused
    assert "can't read const-meta-pixel-id in test-region" in refused.err, refused
    values = {**export_secrets(stape), NAME: "STORE_TOKEN_NOT_A_SECRET"}
    exported = run_tiggy("export", spec, "-o", output, env=secret_store(tmp_path / "store", values))
    assert exported.code == 0, exported
    version = load_export(output)["containerVersion"]
    constants = {v["name"]: _param(v, "value")["value"] for v in version["variable"] if v["type"] == "c"}
    assert constants.pop("const - meta api token") == "STORE_TOKEN_NOT_A_SECRET"
    assert set(constants.values()) == {placeholder}, constants
    assert "STORE_TOKEN_NOT_A_SECRET" not in json.dumps(version["trigger"])


@pytest.mark.parametrize(
    ("held", "written", "where"),
    [
        (
            {
                "type": "LIST",
                "key": "accessToken",
                "list": [
                    {"type": "TEMPLATE", "value": "LISTED_ACCESS_TOKEN"},
                    {"type": "TEMPLATE", "value": "BACKUP_ACCESS_TOKEN"},
                ],
            },
            ["- {$secret: meta-capi-purchase-accesstoken}", "- {$secret: meta-capi-purchase-accesstoken-2}"],
            ["accessToken[0]", "accessToken[1]"],
        ),
        (
            {
                "type": "MAP",
                "key": "accessToken",
                "map": [
                    {"type": "TEMPLATE", "key": "primary", "value": "LISTED_ACCESS_TOKEN"},
                    {"type": "TEMPLATE", "key": "backup", "value": "BACKUP_ACCESS_TOKEN"},
                ],
            },
            [
                "primary: {$secret: meta-capi-purchase-accesstoken}",
                "backup: {$secret: meta-capi-purchase-accesstoken-2}",
            ],
            ["accessToken.primary", "accessToken.backup"],
        ),
    ],
    ids=["a-list", "a-map"],
)
def test_tokens_a_template_holds_as_a_list_or_map_are_each_a_reference(tmp_path: Path, held, written, where):
    def holding(version):
        parameters = _tag(version, "Meta CAPI - Purchase")["parameter"]
        parameters[next(i for i, p in enumerate(parameters) if p["key"] == "accessToken")] = held

    spec, output = tmp_path / "spec", tmp_path / "container.json"
    result = run_tiggy("import", _server(tmp_path, holding), spec)

    assert result.code == 0, result
    assert "LISTED_ACCESS_TOKEN" not in _spec_text(spec) + result.out
    assert "BACKUP_ACCESS_TOKEN" not in _spec_text(spec) + result.out
    tags = (spec / fragment_of(spec, "tags", "Meta CAPI - Purchase")).read_text(encoding="utf-8")
    assert all(f" {line}\n" in tags for line in written), tags
    assert "3 secrets written" in result.out, result
    for name, path in zip(("meta-capi-purchase-accesstoken", "meta-capi-purchase-accesstoken-2"), where):
        assert f"  {name}  (from tag 'Meta CAPI - Purchase', parameter.{path})\n" in result.out, result
    values = {
        NAME: TOKEN,
        "meta-capi-purchase-accesstoken": "LISTED_ACCESS_TOKEN",
        "meta-capi-purchase-accesstoken-2": "BACKUP_ACCESS_TOKEN",
    }
    exported = run_tiggy("export", spec, "-o", output, env=secret_store(tmp_path / "store", values))
    assert exported.code == 0, exported
    purchase = _tag(load_export(output)["containerVersion"], "Meta CAPI - Purchase")
    assert _param(purchase, "accessToken") == held


def _variable(version: dict[str, Any], name: str, type_: str, parameters: list[dict[str, Any]]) -> None:
    constant = next(v for v in version["variable"] if v["name"] == "const - meta api token")
    variable = json.loads(json.dumps(constant))
    variable.update(
        variableId=str(90 + len(version["variable"])), name=name, type=type_, parameter=parameters
    )
    version["variable"].append(variable)


def _using(access_token: str, *variables: tuple[str, str, list[dict[str, Any]]]):
    def edit(version):
        for variable in variables:
            _variable(version, *variable)
        for name in ("Meta CAPI - Purchase", "Meta CAPI - CompleteRegistration"):
            _param(_tag(version, name), "accessToken")["value"] = access_token

    return edit


def _constant(value: str) -> list[dict[str, Any]]:
    return [{"type": "TEMPLATE", "key": "value", "value": value}]


LOOKUP = [
    {"type": "BOOLEAN", "key": "setDefaultValue", "value": "false"},
    {"type": "TEMPLATE", "key": "input", "value": "{{Event Name}}"},
    {
        "type": "LIST",
        "key": "map",
        "list": [
            {
                "type": "MAP",
                "map": [
                    {"type": "TEMPLATE", "key": "key", "value": "www.example.com"},
                    {"type": "TEMPLATE", "key": "value", "value": "LOOKUP_TOKEN_NOT_A_SECRET"},
                ],
            }
        ],
    },
]


@pytest.mark.parametrize(
    ("edit", "names", "values"),
    [
        (_using("Bearer {{const - meta api token}}"), [NAME], [TOKEN]),
        (
            _using(
                "{{const - meta api token}}{{const - token tail}}",
                ("const - token tail", "c", _constant("TOKEN_TAIL_NOT_A_SECRET")),
            ),
            [NAME, "const-token-tail"],
            [TOKEN, "TOKEN_TAIL_NOT_A_SECRET"],
        ),
        (
            _using(
                "{{const - token alias}}",
                ("const - token alias", "c", _constant("{{const - meta api token}}")),
            ),
            [NAME],
            [TOKEN],
        ),
        (
            _using("{{lookup - meta token}}", ("lookup - meta token", "smm", LOOKUP)),
            ["lookup-meta-token-www-example-com"],
            ["LOOKUP_TOKEN_NOT_A_SECRET"],
        ),
    ],
    ids=["after-an-auth-scheme", "two-joined", "through-another-constant", "in-a-lookup-table"],
)
def test_a_token_a_credential_reaches_through_its_references_is_the_secret(
    tmp_path: Path, edit, names, values
):
    spec, live, output = tmp_path / "spec", _server(tmp_path, edit), tmp_path / "container.json"

    result = run_tiggy("import", live, spec)

    assert result.code == 0, result
    assert not any(value in _spec_text(spec) + result.out for value in values), result
    assert [line.split()[0] for line in result.out.splitlines() if "  (from variable " in line] == names
    exported = run_tiggy("export", spec, "-o", output, env=store_for(live, tmp_path / "store"))
    assert exported.code == 0, exported
    versions = [load_export(e)["containerVersion"] for e in (output, live)]
    variables = [{v["name"]: v["parameter"] for v in version["variable"]} for version in versions]
    assert variables[0] == variables[1]
    tokens = [_param(_tag(version, "Meta CAPI - Purchase"), "accessToken") for version in versions]
    assert tokens[0] == tokens[1]


def _endpoint(version: dict[str, Any]) -> None:
    url = f"https://graph.example.com/v18.0/000000000000001/events?access_token={TOKEN}"
    _variable(version, "const - meta capi endpoint", "c", _constant(url))


def _template_code(version: dict[str, Any]) -> None:
    version["customTemplate"][0]["templateData"] += f"\nconst fallbackToken = '{TOKEN}';\n"


@pytest.mark.parametrize(
    ("edit", "place"),
    [
        (_endpoint, "variable 'const - meta capi endpoint', parameter.value"),
        (_template_code, "custom template 'Meta Conversions API', templateData"),
    ],
    ids=["in-an-endpoint-url", "in-template-code"],
)
def test_a_token_inside_a_longer_value_stops_the_import_and_says_where(tmp_path: Path, edit, place):
    spec = tmp_path / "spec"

    result = run_tiggy("import", _server(tmp_path, edit), spec)

    assert (result.code, result.out) == (2, ""), result
    assert result.err == (
        f"Error: can't import this container without writing a secret: {NAME} is part of {place}, "
        "and only a whole parameter value can be {$secret: name}\n"
    )
    assert not spec.exists()


def _unmodelled(where: str):
    def edit(version):
        owner = {"tag": _tag(version, "Meta CAPI - Purchase"), "version": version}.get(where)
        owner = version["container"] if owner is None else owner
        owner["futureSettings"] = {
            "type": "MAP",
            "map": [{"type": "TEMPLATE", "key": "apiKey", "value": "shortkey"}],
        }

    return edit


@pytest.mark.parametrize(
    ("edit", "problem"),
    [
        (
            lambda version: _param(_tag(version, "Meta CAPI - Purchase"), "accessToken").update(
                value="sk_{{const - meta api token}}"
            ),
            " without writing a secret: tag 'Meta CAPI - Purchase', parameter.accessToken mixes a reference "
            "with other text, so which part is secret isn't clear, and only a whole parameter value can be "
            "{$secret: name}",
        ),
        (
            _unmodelled("tag"),
            " without writing a secret: meta-capi-purchase-apikey is in tag 'Meta CAPI - Purchase', "
            "futureSettings.apiKey, which Tiggy copies as it is, not as {$secret: name}",
        ),
        (
            _unmodelled("version"),
            " without writing a secret: version-apikey is in the version, futureSettings.apiKey, which Tiggy "
            "copies as it is, not as {$secret: name}",
        ),
        (
            _unmodelled("container"),
            " without writing a secret: container-apikey is in the container, futureSettings.apiKey, which "
            "Tiggy copies as it is, not as {$secret: name}",
        ),
        (
            lambda version: _tag(version, "Meta CAPI - Purchase")["parameter"].append(
                {"type": "INTEGER", "key": "apiKey", "value": "123456789012345"}
            ),
            ": tag 'Meta CAPI - Purchase': parameter.apiKey can't be written exactly yet: it holds a secret, "
            "and only a plain text value can be {$secret: name}",
        ),
    ],
    ids=[
        "text-around-a-reference",
        "an-unmodelled-field",
        "on-the-version",
        "on-the-container",
        "an-integer",
    ],
)
def test_a_credential_no_reference_can_stand_in_for_stops_the_import(tmp_path: Path, edit, problem):
    spec = tmp_path / "spec"

    result = run_tiggy("import", _server(tmp_path, edit), spec)

    assert (result.code, result.out, result.err) == (2, "", f"Error: can't import this container{problem}\n")
    assert not spec.exists()


def test_a_short_token_is_a_reference_only_where_its_credential_holds_it(tmp_path: Path):
    def short(version):  # 'debug' is also the Purchase tag's logType, and in the template's code
        for name in ("Meta CAPI - Purchase", "Meta CAPI - CompleteRegistration"):
            _param(_tag(version, name), "accessToken")["value"] = "debug"

    spec = tmp_path / "spec"
    result = run_tiggy("import", _server(tmp_path, short), spec)

    assert result.code == 0, result
    tags = (spec / fragment_of(spec, "tags", "Meta CAPI - Purchase")).read_text(encoding="utf-8")
    assert "accessToken: {$secret: meta-capi-purchase-accesstoken}\n" in tags, tags
    assert "logType: debug\n" in tags, tags
    assert "the value of" not in result.out, result


def test_a_secret_that_begins_another_is_not_part_of_it(tmp_path: Path):
    def extended(version):
        _tag(version, "Meta CAPI - Purchase")["parameter"].append(
            {"type": "TEMPLATE", "key": "apiSecret", "value": f"{TOKEN}_EXTENDED"}
        )

    spec = tmp_path / "spec"
    result = run_tiggy("import", _server(tmp_path, extended), spec)

    assert result.code == 0, result
    assert "2 secrets written" in result.out and TOKEN not in _spec_text(spec), result


def test_the_value_beside_a_header_naming_a_credential_is_a_secret(tmp_path: Path):
    def header(version):
        entries = [
            {"type": "TEMPLATE", "key": "name", "value": "Authorization"},
            {"type": "TEMPLATE", "key": "value", "value": "Bearer HEADER_TOKEN_NOT_A_SECRET"},
        ]
        _tag(version, "Meta CAPI - Purchase")["parameter"].append(
            {"type": "LIST", "key": "requestHeaders", "list": [{"type": "MAP", "map": entries}]}
        )

    spec = tmp_path / "spec"
    result = run_tiggy("import", _server(tmp_path, header), spec)

    assert result.code == 0, result
    assert "HEADER_TOKEN_NOT_A_SECRET" not in _spec_text(spec) + result.out
    where = "tag 'Meta CAPI - Purchase', parameter.requestHeaders[0].value"
    assert f"  meta-capi-purchase-authorization  (from {where})\n" in result.out, result


@pytest.mark.parametrize(
    ("key", "credential"),
    [
        ("accessToken", True),
        ("apiSecret", True),
        ("secretKey", True),
        ("signingKey", True),
        ("apiKeys", True),
        ("accessTokens", True),
        ("Authorization", True),
        ("X-Api-Key", True),
        ("maxTokens", False),
        ("itemIdKey", False),
        ("keyPath", False),
    ],
)
def test_which_keys_name_a_credential(key: str, credential: bool):
    assert secret_bearing(key) is credential


def test_a_secret_the_writer_misses_still_never_reaches_a_file(tmp_path: Path, monkeypatch):
    name_for = importer.Secrets.name_for

    def lossy(self, p):  # drops a reference yet reports it written
        self.written.add(id(p))
        return None if self.held.get(id(p)) in self.shared else name_for(self, p)

    monkeypatch.setattr(importer.Secrets, "name_for", lossy)
    version = load_export(_server(tmp_path, _debug_header))["containerVersion"]

    with pytest.raises(importer.ImportError_) as raised:
        importer.import_version(version, tmp_path / "spec")

    assert str(raised.value) == f"can't import this container: folders/meta/tags.yml would hold {NAME}"
    assert not (tmp_path / "spec").exists()


# Validate


def _findings(spec: Path, *args: str) -> tuple[int, list[dict[str, Any]]]:
    result = run_tiggy("validate", spec, "--format", "json", *args)
    assert result.code in (0, 1), result
    return result.code, json.loads(result.out)["findings"]


def test_a_spec_naming_secrets_validates_without_a_secret_store(imported):
    spec = imported(SERVER)

    code, findings = _findings(spec)

    assert code == 0
    assert [f for f in findings if f["rule"] in ("literal-secret", "spec-problem")] == []


def test_a_token_pasted_into_the_spec_is_flagged_and_fails_strict(imported):
    spec = imported(SERVER)
    replace_in_spec(spec, _variables(spec), REF, "PASTED_TOKEN")

    _, findings = _findings(spec)
    strict, _ = _findings(spec, "--strict")

    flagged = [f for f in findings if f["rule"] == "literal-secret"]
    assert [(f["kind"], f["name"], f["severity"]) for f in flagged] == [
        ("variable", "const - meta api token", "warning")
    ]
    assert "Meta CAPI" in flagged[0]["message"] and "{$secret: name}" in flagged[0]["message"]
    assert "PASTED_TOKEN" not in json.dumps(findings)
    assert strict == 1


def test_an_export_is_not_held_to_how_a_spec_is_written():
    result = run_tiggy("validate", SERVER, "--format", "json")

    assert "literal-secret" not in result.out, result


@pytest.mark.parametrize(
    ("written", "message"),
    [
        ("{$secret: meta api token}", "$secret names a secret, got 'meta api token'"),
        (f"{{$secret: {NAME}, version: 3}}", "version is a string such as '3', got 3"),
        (f"{{$secret: {NAME}, versoin: '3'}}", "a $secret takes version and nothing else, not ['versoin']"),
    ],
    ids=["a-name-with-spaces", "an-unquoted-version", "a-misspelt-key"],
)
def test_a_malformed_secret_reference_is_a_spec_problem(imported, written: str, message: str):
    spec = imported(SERVER)
    replace_in_spec(spec, _variables(spec), REF, written)

    code, findings = _findings(spec)

    assert code == 1
    assert [f["message"] for f in findings if f["rule"] == "spec-problem"] == [message]


# Export


def test_export_without_a_secret_store_refuses_and_writes_nothing(imported, tmp_path: Path):
    spec, output = imported(SERVER), tmp_path / "container.json"

    result = run_tiggy("export", spec, "-o", output, env={"TIGGY_TARGETS": None})

    assert (result.code, result.out) == (2, ""), result
    assert result.err == f"Error: the spec names secrets ({NAME}); --targets says where to read them from\n"
    assert not output.exists()


def test_export_reads_the_current_token_from_the_store(imported, tmp_path: Path):
    spec, output, log = imported(SERVER), tmp_path / "container.json", tmp_path / "requests.jsonl"
    env = secret_store(tmp_path / "store", {NAME: "ROTATED_IN_THE_STORE"})

    result = run_tiggy("export", spec, "-o", output, env={**env, "TIGGY_FAKE_SECRETS_LOG": str(log)})

    assert result.code == 0, result
    constant = next(
        v
        for v in load_export(output)["containerVersion"]["variable"]
        if v["type"] == "c" and v["name"] == "const - meta api token"
    )
    assert _param(constant, "value")["value"] == "ROTATED_IN_THE_STORE"
    assert _requests(log) == [{"region": "test-region", "id": NAME, "version": None}]
    assert "ROTATED_IN_THE_STORE" not in result.out + result.err


def test_a_pinned_version_reads_that_version(imported, tmp_path: Path):
    spec, output, log = imported(SERVER), tmp_path / "container.json", tmp_path / "requests.jsonl"
    replace_in_spec(spec, _variables(spec), REF, f"{{$secret: {NAME}, version: '3'}}")
    env = secret_store(tmp_path / "store", {NAME: "LATEST", f"{NAME}@3": "VERSION_THREE"})

    result = run_tiggy("export", spec, "-o", output, env={**env, "TIGGY_FAKE_SECRETS_LOG": str(log)})

    assert result.code == 0, result
    assert '"value": "VERSION_THREE"' in output.read_text(encoding="utf-8")
    assert _requests(log) == [{"region": "test-region", "id": NAME, "version": "3"}]


def test_a_prefix_in_the_targets_file_scopes_every_name(imported, tmp_path: Path):
    spec, output, log = imported(SERVER), tmp_path / "container.json", tmp_path / "requests.jsonl"
    env = secret_store(tmp_path / "store", {f"gtm/server/{NAME}": TOKEN})
    Path(env["TIGGY_TARGETS"]).write_text(
        "tiggy: 1\nsecrets:\n  aws:\n    region: test-region\n    prefix: gtm/server/\n", encoding="utf-8"
    )

    result = run_tiggy("export", spec, "-o", output, env={**env, "TIGGY_FAKE_SECRETS_LOG": str(log)})

    assert result.code == 0, result
    assert [r["id"] for r in _requests(log)] == [f"gtm/server/{NAME}"]


def test_a_secret_missing_from_the_store_names_where_the_spec_asks_for_it(imported, tmp_path: Path):
    spec, output = imported(SERVER), tmp_path / "container.json"

    result = run_tiggy("export", spec, "-o", output, env=secret_store(tmp_path / "store", {}))

    assert (result.code, result.out) == (2, ""), result
    relpath = _variables(spec)
    assert result.err.startswith("Error: can't read the spec's secrets:\n"), result
    assert (
        f"{relpath}:" in result.err
        and f"can't read {NAME} in test-region: ResourceNotFoundException" in result.err
    )
    assert not output.exists()


@pytest.mark.parametrize(
    ("targets", "message"),
    [
        ("secrets:\n  aws: {region: r}\n", "isn't a targets file: it starts `tiggy: 1`"),
        ("tiggy: 1\nsecrets:\n  vault: {address: a}\n", "secrets names one store, gcp or aws"),
        (
            "tiggy: 1\nsecrets:\n  aws: {region: r}\n  gcp: {project: p}\n",
            "secrets names one store, gcp or aws",
        ),
        ("tiggy: 1\nsecrets:\n  gcp: {region: r}\n", "secrets.gcp takes project and, optionally, prefix"),
        ("tiggy: 1\nsecrets:\n  aws: {region: ''}\n", "secrets.aws values are non-empty strings"),
        ("tiggy: 1\nsecret:\n  aws: {region: r}\n", "unknown keys ['secret']"),
    ],
    ids=["no-header", "an-unknown-store", "two-stores", "the-wrong-setting", "an-empty-region", "a-typo"],
)
def test_a_bad_targets_file_is_refused_before_anything_is_read(imported, tmp_path: Path, targets, message):
    spec, file = imported(SERVER), tmp_path / "targets.yml"
    file.write_text(targets, encoding="utf-8")

    result = run_tiggy("export", spec, "-o", tmp_path / "container.json", "--targets", file)

    assert result.code == 2, result
    assert result.err.startswith(f"Error: {file}") and message in result.err, result


# import --check


def test_a_token_rotated_in_the_ui_is_not_drift_offline_and_says_so(imported, tmp_path: Path):
    spec, live = imported(SERVER), _server(tmp_path, _rotated)

    result = run_tiggy("import", live, spec, "--check", env={"TIGGY_TARGETS": None})

    assert (result.code, result.out) == (0, f"{spec} is equivalent to {live}\n"), result
    assert result.err == UNREAD


def test_resolving_secrets_finds_the_rotated_token_without_printing_either_value(imported, tmp_path: Path):
    spec, live = imported(SERVER), _server(tmp_path, _rotated)
    env = secret_store(tmp_path / "store", {NAME: TOKEN})

    result = run_tiggy("import", live, spec, "--check", "--resolve-secrets", env=env)

    assert result.code == 1, result
    assert result.out.splitlines()[0] == (
        f"≠ variables 'const - meta api token': parameter[0].value"
        f" (<secret {NAME}, not the stored value> in the container, <secret {NAME}> in the spec)"
    ), result
    assert TOKEN not in result.out + result.err and "ROTATED_IN_THE_UI" not in result.out + result.err


def test_resolving_secrets_against_a_matching_store_is_equivalent_with_no_note(imported, tmp_path: Path):
    spec = imported(SERVER)
    env = secret_store(tmp_path / "store", {NAME: TOKEN})

    result = run_tiggy("import", SERVER, spec, "--check", "--resolve-secrets", env=env)

    assert (result.code, result.out, result.err) == (0, f"{spec} is equivalent to {SERVER}\n", ""), result


def test_a_token_pasted_into_a_tag_in_the_ui_is_drift_shown_without_its_value(imported, tmp_path: Path):
    def pasted(version):
        _param(_tag(version, "Meta CAPI - Purchase"), "accessToken")["value"] = TOKEN

    spec, live = imported(SERVER), _server(tmp_path, pasted)

    result = run_tiggy("import", live, spec, "--check", env={"TIGGY_TARGETS": None})

    assert result.code == 1, result
    assert (
        "parameter[5].value (<secret meta-capi-purchase-accesstoken> in the container, "
        "'{{const - meta api token}}' in the spec)" in result.out
    ), result
    assert TOKEN not in result.out + result.err


def test_a_warning_about_an_object_named_with_the_token_does_not_show_it(tmp_path: Path):
    def named(version):
        tag = _tag(version, "Meta CAPI - Purchase")
        tag.update(name=f"Meta {TOKEN}", tagFiringOption="SOME_NEW_OPTION")

    result = run_tiggy("import", _server(tmp_path, named), tmp_path / "spec")

    assert result.code == 2, result
    assert result.err.startswith(f'! tag "Meta <secret {NAME}>": tagFiringOption SOME_NEW_OPTION'), result
    assert TOKEN not in result.err


def test_a_token_cleared_in_the_ui_is_drift_even_offline(imported, tmp_path: Path):
    def cleared(version):
        constant = next(v for v in version["variable"] if v["name"] == "const - meta api token")
        _param(constant, "value")["value"] = ""

    spec, live = imported(SERVER), _server(tmp_path, cleared)

    result = run_tiggy("import", live, spec, "--check", env={"TIGGY_TARGETS": None})

    assert result.code == 1, result
    assert f"('' in the container, <secret {NAME}> in the spec)" in result.out, result


@pytest.mark.parametrize(
    "args",
    [("--check", "--resolve-secrets"), ("--resolve-secrets",)],
    ids=["without-targets", "without-check"],
)
def test_resolve_secrets_needs_check_and_a_store(imported, tmp_path: Path, args):
    spec = imported(SERVER)
    env = {"TIGGY_TARGETS": None} if "--check" in args else secret_store(tmp_path / "store", {NAME: TOKEN})

    result = run_tiggy("import", SERVER, spec if "--check" in args else tmp_path / "new", *args, env=env)

    assert result.code == 2, result
    assert result.err == "Error: --resolve-secrets is for --check, with --targets naming the secret store\n"


def test_an_import_that_loses_a_secret_says_where_without_printing_it(tmp_path: Path, monkeypatch):
    compile_ = importer.compile_spec

    def lossy(spec, secrets=None):
        compiled = compile_(spec, secrets)
        constant = next(v for v in compiled.version.variable if v.name == "const - meta api token")
        constant.parameter[0].value = "LOST_ON_THE_WAY_BACK"
        return compiled

    monkeypatch.setattr(importer, "compile_spec", lossy)

    with pytest.raises(importer.ImportError_) as raised:
        importer.import_version(load_export(SERVER)["containerVersion"], tmp_path / "spec")

    assert f"(<secret {NAME}> in the export, 'LOST_ON_THE_WAY_BACK' in the written spec)" in str(raised.value)
    assert TOKEN not in str(raised.value)
    assert not (tmp_path / "spec").exists()


# The stores themselves, in process: the CLI tests above use a stand-in boto3


class _Response:
    def __init__(self, status: int, body: dict[str, Any]):
        self.status_code, self._body = status, body

    def json(self) -> dict[str, Any]:
        return self._body


def _fake_google(monkeypatch, status: int, body: dict[str, Any]) -> list[str]:
    urls: list[str] = []

    class AuthorizedSession:
        def __init__(self, credentials):
            assert credentials == "credentials"

        def get(self, url, timeout):
            urls.append(url)
            return _Response(status, body)

    google = types.ModuleType("google")
    auth = types.ModuleType("google.auth")
    auth.default = lambda scopes: ("credentials", "a-project")
    transport = types.ModuleType("google.auth.transport")
    requests = types.ModuleType("google.auth.transport.requests")
    requests.AuthorizedSession = AuthorizedSession
    google.auth, auth.transport, transport.requests = auth, transport, requests
    for name, module in [
        ("google", google),
        ("google.auth", auth),
        ("google.auth.transport", transport),
        ("google.auth.transport.requests", requests),
    ]:
        monkeypatch.setitem(sys.modules, name, module)
    return urls


def test_gcp_secret_manager_reads_a_pinned_version_under_the_prefix(monkeypatch):
    payload = {"payload": {"data": base64.b64encode(b"a-token").decode()}}
    urls = _fake_google(monkeypatch, 200, payload)

    value = GcpSecretManager("tracking", "gtm-").read(SecretRef("meta-api-token", "3"))

    assert value == "a-token"
    assert urls == [
        "https://secretmanager.googleapis.com/v1/projects/tracking/secrets/gtm-meta-api-token/versions/3:access"
    ]


def test_gcp_secret_manager_names_the_secret_it_could_not_read(monkeypatch):
    _fake_google(monkeypatch, 404, {})

    with pytest.raises(
        SecretError, match=r"^can't read projects/tracking/secrets/x/versions/latest: HTTP 404$"
    ):
        GcpSecretManager("tracking").read(SecretRef("x"))


@pytest.mark.parametrize(
    ("store", "module", "extra"),
    [(GcpSecretManager("p"), "google.auth", "gcp"), (AwsSecretsManager("r"), "boto3", "aws")],
    ids=["gcp", "aws"],
)
def test_a_store_without_its_library_says_which_extra_to_install(monkeypatch, store, module, extra):
    monkeypatch.setitem(sys.modules, module, None)

    with pytest.raises(SecretError, match=rf"pip install 'tiggy-cli\[{extra}\]'"):
        store.read(SecretRef("x"))
