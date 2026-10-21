"""Secrets a spec names rather than holds: `{$secret: name}`, read from GCP or AWS when one is needed.

A spec says only which secret; where secrets live (the provider, and its project or region) is in a
targets file, so specs move between stores unchanged. Nothing offline (validate, `import --check` by
default) fetches a secret: a placeholder stands in for each value, and never reaches an export.
"""

import base64
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from tiggy.spec.problems import Origin
from tiggy.spec.yamlio import YamlError, load

PLACEHOLDER = "\x00secret:"  # then the name; no spec or export can spell it, so it can't pass for a value
NAME = re.compile(r"[A-Za-z0-9/_+=.@-]+")  # what GCP and AWS allow between them; each checks its own
SECRET_WORDS = {"token", "secret", "password", "passwd", "authorization"}
SECRET_PAIRS = {
    ("api", "key"),
    ("private", "key"),
    ("secret", "key"),
    ("signing", "key"),
    ("access", "token"),
    ("auth", "token"),
    ("api", "secret"),
    ("client", "secret"),
}
DISTINCTIVE = 12  # characters; a shorter secret could be an everyday value, so only its credential holds it


class SecretError(Exception):
    """A secret couldn't be read; the message names it and its store, never a value."""


@dataclass(frozen=True)
class SecretRef:
    name: str
    version: str | None = None  # None is the latest

    def __str__(self) -> str:
        return self.name if self.version is None else f"{self.name} (version {self.version})"

    @property
    def placeholder(self) -> str:
        return PLACEHOLDER + (self.name if self.version is None else f"{self.name}@{self.version}")


Resolve = Callable[[SecretRef, Origin], str]


def is_placeholder(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(PLACEHOLDER)


def secret_bearing(key: Any) -> bool:
    """Whether a GTM key or header name names a credential: accessToken, apiKeys, X-Api-Key and the like."""
    if not isinstance(key, str):
        return False
    words = [w.lower() for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", key)]
    # Plurals only in pairs: a key ending in tokens alone (maxTokens) is more often a count.
    return bool(words) and (
        words[-1] in SECRET_WORDS or (*words[-2:-1], words[-1].removesuffix("s")) in SECRET_PAIRS
    )


def distinctive(value: str) -> bool:
    """Whether a secret is long enough that finding its value somewhere else means finding the secret."""
    return len(value) >= DISTINCTIVE


@dataclass
class Offline:
    """Stands in for every secret, for checks that compare everything else."""

    refs: dict[SecretRef, Origin] = field(default_factory=dict)  # each, and where it's first named

    def __call__(self, ref: SecretRef, origin: Origin) -> str:
        self.refs.setdefault(ref, origin)
        return ref.placeholder


class Store(Protocol):
    def read(self, ref: SecretRef) -> str: ...


@dataclass
class Fetching:
    """Reads each secret from a store once, collecting failures rather than stopping at the first."""

    store: Store
    values: dict[SecretRef, str] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    def __call__(self, ref: SecretRef, origin: Origin) -> str:
        if ref not in self.values:
            try:
                self.values[ref] = self.store.read(ref)
            except SecretError as e:
                self.failures.append(f"{origin}: {e}")
                self.values[ref] = ref.placeholder
        return self.values[ref]


@dataclass(frozen=True)
class GcpSecretManager:
    project: str
    prefix: str = ""

    def read(self, ref: SecretRef) -> str:
        name = self.prefix + ref.name
        where = f"projects/{self.project}/secrets/{name}/versions/{ref.version or 'latest'}"
        try:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession
        except ImportError as e:
            raise SecretError("reading GCP Secret Manager needs `pip install 'tiggy-cli[gcp]'`") from e
        try:
            credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            response = AuthorizedSession(credentials).get(
                f"https://secretmanager.googleapis.com/v1/{where}:access", timeout=30
            )
        except Exception as e:  # no credentials, no network: the library's own types vary
            raise SecretError(f"can't read {where}: {type(e).__name__}") from e
        if response.status_code != 200:
            raise SecretError(f"can't read {where}: HTTP {response.status_code}")
        try:
            return base64.b64decode(response.json()["payload"]["data"]).decode("utf-8")
        except (KeyError, ValueError) as e:
            raise SecretError(f"{where} isn't UTF-8 text") from e


@dataclass(frozen=True)
class AwsSecretsManager:
    region: str
    prefix: str = ""

    def read(self, ref: SecretRef) -> str:
        name = self.prefix + ref.name
        where = f"{name} in {self.region}" + (f" (version {ref.version})" if ref.version else "")
        try:
            import boto3
        except ImportError as e:
            raise SecretError("reading AWS Secrets Manager needs `pip install 'tiggy-cli[aws]'`") from e
        request: dict[str, str] = {"SecretId": name}
        if ref.version:
            request["VersionId"] = ref.version
        try:
            found = boto3.client("secretsmanager", region_name=self.region).get_secret_value(**request)
        except Exception as e:  # botocore's error types are generated at runtime
            raise SecretError(f"can't read {where}: {type(e).__name__}") from e
        if not isinstance(found.get("SecretString"), str):
            raise SecretError(f"{where} is binary, not text")
        return found["SecretString"]


PROVIDERS: dict[str, tuple[type, str]] = {
    "gcp": (GcpSecretManager, "project"),
    "aws": (AwsSecretsManager, "region"),
}


def load_targets(path: Path) -> Store:
    """The secret store a targets file names: `secrets: {gcp: {project: ...}}` or `{aws: {region: ...}}`."""
    try:
        data = load(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        raise SecretError(f"can't read {path}: {getattr(e, 'strerror', None) or e}") from e
    except YamlError as e:
        raise SecretError(f"{path}:{e.line}: {e}") from e
    if not isinstance(data, dict) or data.get("tiggy") != 1:
        raise SecretError(f"{path} isn't a targets file: it starts `tiggy: 1`")
    unknown = sorted(set(data) - {"tiggy", "secrets"})
    if unknown:
        raise SecretError(f"{path}: unknown keys {unknown}")
    secrets = data.get("secrets")
    if not isinstance(secrets, dict) or len(secrets) != 1 or next(iter(secrets)) not in PROVIDERS:
        raise SecretError(
            f"{path}: secrets names one store, gcp or aws, such as `secrets: {{gcp: {{project: p}}}}`"
        )
    ((provider, settings),) = secrets.items()
    store, required = PROVIDERS[provider]
    if not isinstance(settings, dict) or set(settings) - {required, "prefix"} or required not in settings:
        raise SecretError(f"{path}: secrets.{provider} takes {required} and, optionally, prefix")
    if not all(isinstance(v, str) and v for v in settings.values()):
        raise SecretError(f"{path}: secrets.{provider} values are non-empty strings")
    return store(settings[required], settings.get("prefix", ""))
