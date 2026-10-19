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
under its own key.

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
it a drift gate for CI.

Exits 0 when written or equivalent, 1 when `--check` finds differences, 2 when it can't run.

Code files (`.html`, `.js`, `.tpl`) must stay byte-exact, so keep whitespace fixers away from them; import writes a
`.gitattributes` and `.editorconfig` that do this for git and editors.

## export

```bash
tiggy export spec/ -o container.json
```

Compiles a spec to a container export with fresh IDs, on stdout unless `-o` names a file. Problems go to stderr,
so a broken spec never writes half an export. It reimports to the same spec byte for byte; whether GTM's own
Import Container accepts it hasn't been tried yet.

## validate

```bash
tiggy validate spec/
tiggy validate examples/broken-container.json --format json --strict
```

Takes a spec directory or a container export. Reports references that don't resolve (triggers,
`{{variables}}`, setup and teardown tags, custom templates, folders), built-ins used but not enabled,
duplicate names and IDs, variable and tag-sequencing loops, and unused triggers, variables and templates. For a
spec, every finding points at its file and line, and a code file no `$file` names is a warning.

Exits 0 when valid, 1 when not (`--strict` counts warnings; a spec file it can't read is a problem too), 2 when
the input isn't a spec or an export, or is an export Tiggy would misread. `import --check` exits 2 on a spec that
doesn't load, since 1 there means the two differ.

Every command exits 130 when interrupted and 141 when whatever reads its output stops early (`| head`).

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
