"""A stand-in for boto3's Secrets Manager client, so tests can export specs that name secrets offline.

On PYTHONPATH it shadows any real boto3. It serves the JSON object in the file TIGGY_FAKE_SECRETS names:
`name` for the current version, `name@version` for a pinned one. Every request is appended to the file
TIGGY_FAKE_SECRETS_LOG names, when set, so a test can see what was asked for.
"""

import json
import os


class ResourceNotFoundException(Exception):
    pass


class _SecretsManager:
    def __init__(self, region_name):
        self.region_name = region_name

    def get_secret_value(self, SecretId, VersionId=None):  # noqa: N803 (boto3's own argument names)
        log = os.environ.get("TIGGY_FAKE_SECRETS_LOG")
        if log:
            with open(log, "a", encoding="utf-8") as f:
                f.write(json.dumps({"region": self.region_name, "id": SecretId, "version": VersionId}) + "\n")
        with open(os.environ["TIGGY_FAKE_SECRETS"], encoding="utf-8") as f:
            values = json.load(f)
        key = SecretId if VersionId is None else f"{SecretId}@{VersionId}"
        if key not in values:
            raise ResourceNotFoundException(key)
        return {"Name": SecretId, "SecretString": values[key]}


def client(service_name, region_name=None):
    if service_name != "secretsmanager":
        raise ValueError(f"the fake boto3 only fakes secretsmanager, not {service_name}")
    return _SecretsManager(region_name)
