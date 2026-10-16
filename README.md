# Tiggy

Declarative Google Tag Manager. Describe a GTM container in YAML, then validate, plan and apply it the way
[Tundra](https://github.com/henryupton/tundra) does for Snowflake permissions.

Early days: `validate` works against a container export; the rest is planned.

```bash
pip install tiggy-cli

tiggy validate container.json   # every reference between tags, triggers and variables resolves
tiggy spec-test spec/           # planned: the spec matches the live container
tiggy run spec/ --dry           # planned: the GTM changes that would make it match
tiggy run spec/                 # planned: make them, in a new workspace
tiggy import <container>        # planned: write a live container out as a spec
```

## validate

```bash
tiggy validate examples/broken-container.json
tiggy validate container.json --format json --strict
```

Takes a container export (Admin > Export Container). Reports references that don't resolve (triggers,
`{{variables}}`, setup and teardown tags, custom templates, folders), built-ins used but not enabled,
duplicate names and IDs, variable and tag-sequencing loops, and unused triggers, variables and templates.

Exits 0 when valid, 1 when not (`--strict` counts warnings), 2 when the input can't be read.

## How it fits together

| Layer          | Where              | Does                                                                         |
| -------------- | ------------------ | ---------------------------------------------------------------------------- |
| Resource model | `tiggy/gtm/`       | One Pydantic model per GTM resource, generated from Google's API schema      |
| Adapters       | `tiggy/io/`        | Read and write a format (a container export today; the API and YAML next)    |
| Graph          | `tiggy/graph.py`   | What refers to what. The only code that knows GTM's reference rules          |
| Checks         | `tiggy/checks.py`  | Rules over the graph, each yielding findings                                 |
| Report         | `tiggy/report.py`  | Findings as text or JSON                                                     |

The models are generated from the Tag Manager API v2 discovery document, vendored in `schema/`. Either
enum spelling parses (the API's `customEvent`, an export's `CUSTOM_EVENT`), and fields GTM sends that the
schema lacks are kept, so a load and dump returns the input unchanged. To pick up API changes:

```bash
uv run python scripts/generate_resources.py --refresh
```
