"""The generated GTM resource models: regenerating reproduces the committed file, and every enum value
whose export spelling can't be derived (one with a digit) is spelled out."""

import copy
import importlib.util
import json
import re
import subprocess
import sys
from types import ModuleType

import pytest
from pydantic import TypeAdapter

from tests.conftest import ROOT
from tiggy.gtm import resources
from tiggy.gtm.base import DIGIT_SPELLINGS, GtmEnum


def generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "generate_resources", ROOT / "scripts" / "generate_resources.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def discovery(module: ModuleType) -> dict:
    return json.loads(module.DISCOVERY.read_text(encoding="utf-8"))


def generated_enums() -> list[type[GtmEnum]]:
    return [
        value
        for value in vars(resources).values()
        if isinstance(value, type) and issubclass(value, GtmEnum) and value.__module__ == resources.__name__
    ]


def test_regenerating_resources_reproduces_the_committed_file():
    """The generator's output, formatted as its main() formats it, without touching the committed file."""
    module = generator()
    rendered = module.Generator(discovery(module)).render()
    formatted = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--stdin-filename", str(module.OUTPUT), "-"],
        input=rendered.encode("utf-8"),
        capture_output=True,
        cwd=ROOT,
        check=True,
    ).stdout.decode("utf-8")

    # A Windows checkout may hold the file with CRLF endings; git stores it with LF either way.
    committed = module.OUTPUT.read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert formatted.replace("\r\n", "\n") == committed, (
        "src/tiggy/gtm/resources.py is stale: run uv run python scripts/generate_resources.py"
    )


def test_every_generated_enum_value_with_a_digit_has_an_export_spelling():
    with_digits = [
        (enum, member) for enum in generated_enums() for member in enum if re.search(r"[0-9]", member.value)
    ]

    assert with_digits, "the discovery document has no enum value with a digit, so this test proves nothing"
    for enum, member in with_digits:
        assert member.value in DIGIT_SPELLINGS, (
            f"{enum.__name__}.{member.name} ({member.value}) has no export spelling"
        )
        spelled = DIGIT_SPELLINGS[member.value]
        assert enum(spelled) is member, f"an export's {spelled} doesn't read as {enum.__name__}.{member.name}"
        written = TypeAdapter(enum).dump_python(member, mode="json", context={"casing": "export"})
        assert written == spelled, f"{enum.__name__}.{member.name} is written {written!r} in an export"


def test_digit_spellings_name_only_values_the_api_has():
    values = {member.value for enum in generated_enums() for member in enum}

    assert sorted(set(DIGIT_SPELLINGS) - values) == []


def test_the_generator_refuses_a_digit_value_it_cant_spell():
    module = generator()
    document = copy.deepcopy(discovery(module))
    usage = document["schemas"]["Container"]["properties"]["usageContext"]["items"]
    usage["enum"].append("iosSdk6")
    usage["enumDescriptions"].append("")

    with pytest.raises(SystemExit, match=r"add the export spelling of \['iosSdk6'\] to DIGIT_SPELLINGS"):
        module.Generator(document).render()
