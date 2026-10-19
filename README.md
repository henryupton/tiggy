# Tiggy

Declarative Google Tag Manager. Describe a GTM container in YAML, then validate, plan and apply it the way
[Tundra](https://github.com/henryupton/tundra) does for Snowflake permissions.

Early days: import, validate and export work; planning and applying against a live container come next.

```bash
pip install tiggy-cli

tiggy import container.json spec/    # write a container export out as a YAML spec
tiggy validate spec/                 # every reference between tags, triggers and variables resolves
tiggy export spec/ -o container.json # the spec as a container export again
tiggy spec-test spec/                # planned: the spec matches the live container
tiggy run spec/ --dry                # planned: the GTM changes that would make it match
```

## The spec

A spec is a directory of YAML fragments. Every `.yml` file under it is read and merged, so files and folders
are for people to organise; the result doesn't depend on them. An object defined twice is an error naming
both places. `tiggy import` lays a container out like this:

```
spec/
├── container.yml                   # container settings and built-in variables
├── custom_templates/
│   ├── facebook-pixel.yml
│   └── facebook-pixel.tpl          # GTM's own template file, byte for byte
├── folders/
│   └── stape-meta/
│       ├── folder.yml
│       ├── tags.yml
│       └── variables.yml
└── unfiled/
    ├── tags.yml
    └── plausible-analytics.html    # custom HTML, byte for byte
```

A tag reads like GTM's own model, with names where GTM has IDs:

```yaml
tiggy: 1
tags:
  - name: GA4 - view_search_results
    type: gaawe
    folder: GA4
    firing_triggers:
      - CE - view_search_results
    tag_firing_option: ONCE_PER_EVENT
    consent_settings:
      consent_status: NOT_SET
    monitoring_metadata: {$type: MAP}
    parameter:
      sendEcommerceData: false
      eventSettingsTable:
        - parameter: search_term
          parameterValue: '{{DLV - search_term}}'
      eventName: view_search_results
```

A parameter's YAML type is its GTM type: a string is a `TEMPLATE`, `true` a `BOOLEAN`, `2` an `INTEGER`, a list a
`LIST` and a mapping a `MAP`, so `'2'` and `2` differ. Anything those can't say exactly is spelled out with
`$type`, such as `{$type: MAP}` for a map GTM stores without entries.

A spec means the same in every YAML tool. Unquoted, only `true`, `false`, `null` and plain integers are anything
but strings; whatever some reader would type differently (`no`, `~`, `0x10`, `1.5`, `12:30`, unquoted dates) has
to be quoted, and anchors, tags, merge keys and multiple documents are refused. Everything Tiggy writes reads the
same in ruamel.yaml, PyYAML and yq, and it reads hand-written YAML only where they agree.

Nothing is defaulted or inferred: a field in the YAML is a field in GTM, and an absent one is absent. A field
Tiggy doesn't model yet goes under `unmodelled:` and passes through untouched; one it does model has to be written
under its own key. An enum value newer than Tiggy's copy of the API schema (`CONSENT_UPDATE` for a trigger type,
say) passes through too, with an `unmodelled-enum` warning that `--strict` fails on.

What GTM sets is left out: IDs, fingerprints, the container's feature flags, and a version's name and description,
which label a publish rather than configure the container. The one exception is `container.public_id`, which isn't
compared as content but asserts which container the spec belongs to.

Code lives in sidecar files named by `{$file: path}`, relative to the spec root and spelled exactly as on disk
(case and Unicode form included, since Linux checkouts compare names exactly). Links, to files or directories,
are refused: a Windows checkout turns them into text files.

## import

```bash
tiggy import container.json spec/
tiggy import container.json spec/ --check
```

Writes the spec into a new or empty directory. Before anything lands, the written spec is reloaded, compiled and
compared with the export file as written, so only GTM-assigned IDs and bookkeeping may differ. An export Tiggy
can't reproduce exactly (a value it would read differently, two objects with one name) is refused, not
approximated. `--check` writes nothing and lists what differs between the export and an existing spec, which makes
it a drift gate for CI. It refuses an export of another container than the spec's `public_id`, so a staging copy
can't pass for production. Exports from the Tag Manager API, which spells enums `customEvent` rather than
`CUSTOM_EVENT`, read the same as the UI's.

Exits 0 when written or equivalent, 1 when `--check` finds differences, 2 when it can't run.

Code files (`.html`, `.js`, `.tpl`) must stay byte-exact, so keep whitespace fixers away from them; import writes a
`.gitattributes` and `.editorconfig` that do this for git and editors.

Credentials are written as references, never values; see [Secrets](#secrets).

## export

```bash
tiggy export spec/ -o container.json
```

Compiles a spec to a container export with fresh IDs, on stdout unless `-o` names a file. Problems go to stderr,
so a broken spec never writes half an export. It reimports to the same spec byte for byte; whether GTM's own
Import Container accepts it hasn't been tried yet. A spec that names secrets needs `--targets` to export.

## Secrets

An access token or API secret in a spec would live in every clone and fork of the repository, so import writes
each one as a reference and lists what to put in your secret store:

```yaml
- name: const - meta api token
  type: c
  parameter:
    value: {$secret: const-meta-api-token}              # the latest version
    # value: {$secret: const-meta-api-token, version: '3'}  # or a pinned one
```

Import treats a value as a secret when its parameter key names one (`accessToken`, `apiSecret`, `apiKey`,
`password` and the like), either directly or through a constant variable the parameter uses whole. The same
value in several places is one secret. `validate` warns about a credential written in plain text in a spec.

Where secrets live is not the spec's business: a targets file names the store, so the same spec reads from
staging and production stores unchanged.

```yaml
tiggy: 1
secrets:
  gcp:                            # GCP Secret Manager, or aws: {region: us-east-1} for AWS Secrets Manager
    project: my-tracking-project
    prefix: gtm/server/           # optional, before every name
```

```bash
pip install 'tiggy-cli[gcp]'  # or tiggy-cli[aws]
tiggy export spec/ -o container.json --targets targets.yml  # or set TIGGY_TARGETS
tiggy import live.json spec/ --check --resolve-secrets --targets targets.yml
```

Each store uses its own default credentials (`gcloud auth application-default login`, an AWS profile). Only
`export` and `--check --resolve-secrets` read secrets. Plain `--check` and `validate` never do: `--check` notes
how many secret values it didn't compare, and still reports one emptied or removed in the container. No command
prints a secret's value; a token rotated in the UI shows as `<secret name, not the stored value>`.

## validate

```bash
tiggy validate spec/
tiggy validate examples/broken-container.json --format json --strict
tiggy validate gtm/web-analytics --format github
```

Takes a spec directory or a container export. Reports references that don't resolve (triggers,
`{{variables}}`, setup and teardown tags, custom templates, folders), built-ins used but not enabled,
duplicate names and IDs, variable and tag-sequencing loops, and unused triggers, variables and templates. A
mistyped name gets a did-you-mean.

For a spec, every finding points at its file and line, relative to the working directory when the spec is inside
it, so a report from the repository root names paths an editor opens. A spec's problems (a name that doesn't
resolve, a key Tiggy doesn't know) come in the same run as the errors in the rest of it; warnings follow once it
has no problems, since an unused trigger may only be the other end of a misspelt one. A code file no `$file`
names is a warning.

`--format json` is one schema whether or not the spec compiles: a problem is a finding with rule `spec-problem`.
`--format github` writes workflow commands, so in GitHub Actions each finding shows on the pull request's diff.

Exits 0 when valid, 1 when not (`--strict` counts warnings), 2 when the input isn't a spec or an export, or is an
export Tiggy would misread. `import --check` exits 2 on a spec that doesn't load, since 1 there means the two
differ.

As a [pre-commit](https://pre-commit.com) hook, with the spec directory as its argument:

```yaml
- repo: https://github.com/henryupton/tiggy
  rev: v0.3.0
  hooks:
    - id: tiggy-validate
      args: [gtm/web-analytics, --strict]
```

Every command exits 130 when interrupted and 141 when whatever reads its output stops early (`| head`). Output is
coloured only on a terminal, and never when `NO_COLOR` is set.

## How it fits together

| Layer          | Where               | Does                                                                           |
| -------------- | ------------------- | ------------------------------------------------------------------------------ |
| Resource model | `tiggy/gtm/`        | One Pydantic model per GTM resource, generated from Google's API schema        |
| Spec           | `tiggy/spec/`       | The YAML format: strict loading, the parameter codec, import, compile, canon   |
| Adapters       | `tiggy/io/`         | Container export JSON                                                          |
| Graph          | `tiggy/graph.py`    | What refers to what. The only code that knows GTM's reference rules            |
| Checks         | `tiggy/checks.py`   | Rules over the graph, each yielding findings                                   |
| Report         | `tiggy/report.py`   | Findings as text or JSON                                                       |

How each GTM field appears in YAML is decided in one place, `tiggy/spec/overlay.py`, and checked against the
generated models when Tiggy starts, so a field Google adds can't be silently dropped.

The models are generated from the Tag Manager API v2 discovery document, vendored in `schema/`. To pick up API
changes:

```bash
uv run python scripts/generate_resources.py --refresh
```

## Tests

```bash
uv sync
uv run pytest                  # everything, about three minutes
uv run pytest -m "not slow"    # the quick loop
```

The scenarios follow how a team adopts Tiggy: codify a live container as-is, change it through pull requests, catch
mistakes in review, notice hotfixes made in the GTM UI, and survive the adopting repo's pre-commit hooks. They run
against synthetic containers in `tests/fixtures/` (see its README) and four redistributable public exports. Known gaps
are written as the behaviour they should have and marked `xfail(strict=True)`, so fixing one turns its test red until
the marker comes off.

To run the same round trips against real containers without committing them, point `TIGGY_EXTRA_EXPORTS` at a
directory of exports kept outside the repo:

```bash
TIGGY_EXTRA_EXPORTS=~/gtm-exports uv run pytest
```

Spec trees and reports are compared with goldens in `tests/goldens/`. After an intended change to layout or output,
regenerate them with `uv run pytest --update-goldens` and review the diff.
