# Test fixtures

GTM container exports the tests run Tiggy against. Two kinds are committed, and a third can be
pointed at from outside the repository.

## `cdm/`: synthetic containers

Built by `cdm/build.py` from the builders in `cdm/builders.py`, and modelled on how a digital
analytics team runs GTM day to day. Every identifier, host and value is made up: account `1000`,
containers `101` to `104`, public IDs `GTM-TEST001` to `GTM-TEST004`.

| File | Models |
| --- | --- |
| `web_analytics.json` | The Analytics web container, loaded before consent, exported as a published version (87). A Google tag that picks its server container URL from a Page Hostname lookup table; GA4 event tags on a generic `track_event` Custom Event trigger (no event allowlist), on specific events, and on `track_event` narrowed by `event_name`, with eventSettingsTable rows that alias some attributes (`item_id` is sent as `content_id`, `search_term` as `search_query`); one `DLV - event_attributes.<property>` Data Layer variable per event attribute (version 2, no default); the first-party payload Custom JavaScript variable with its alphabetical `// Event` section; the custom HTML tag that POSTs it, with a teardown tag; an Amplitude event tag from a container-authored template; a Cookiebot CMP gallery template on Consent Initialization; folders and notes. |
| `web_marketing.json` | The Marketing web container, loaded after consent, as a workspace export. A Meta Pixel gallery template (with `galleryTemplateId`, so tags use `cvt_<id>`), Microsoft UET and LinkedIn Insight as custom HTML, a Google Ads conversion linker and conversion tags; consent NEEDED with `ad_storage` (and `ad_user_data`); triggers on `cookie_consent_marketing`, `purchase` and `sign_up`; a blocking trigger for internal traffic; one paused tag and one setup tag. |
| `server.json` | A server container. A GA4 client, the GA4 server tag on a Client Name trigger and blocked for `page_view_anon`, Meta Conversions API tags from a gallery template whose `accessToken` reads `{{const - meta api token}}` (a placeholder constant), Event Data variables, and a transformation that keeps the phone number from GA4. |
| `web_legacy.json` | A long-lived web container nobody has cleaned, as published version 412 with no name. Unused Data Layer variables (some version 1); an orphan trigger; paused Universal Analytics tags (type `ua`) and a paused hard-coded gtag.js; a custom HTML tag reading `{{Click Text}}`, which isn't enabled; a tag with no trigger; an unused container-authored variable template. Names that stress slugging: case-only twins (triggers `CE - Sign_up` and `CE - sign_up`, folders `Archive` and `archive`), `/` in folder, trigger and tag names, non-ASCII names (an accented folder and variable, a tag named only in Japanese, which slugs to the `code` fallback), and two 150-character tag names identical for their first 149, so their 80-character slugs collide. One empty folder, and notes on the container, folders, variables, triggers and tags. |

| File | Tags | Triggers | Variables | Folders | Templates | Built-ins | Clients | Transformations |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `web_analytics.json` | 11 | 6 | 30 | 6 | 2 | 7 | 0 | 0 |
| `web_marketing.json` | 9 | 4 | 8 | 5 | 1 | 5 | 0 | 0 |
| `server.json` | 3 | 4 | 9 | 6 | 1 | 3 | 1 | 1 |
| `web_legacy.json` | 12 | 5 | 8 | 5 | 1 | 6 | 0 | 0 |

All four import (exit 0), pass `import --check` (exit 0), and `tiggy export` followed by a re-import
reproduces the spec byte for byte. `tiggy validate` exits 0 with 0 errors and 0 warnings on the first
three. On `web_legacy.json` it exits 1 with 1 error (`built-in-not-enabled`) and 10 warnings (7
`unused-variable`, 1 `unused-trigger`, 1 `unused-template`, 1 `tag-never-fires`); the spec it imports
to reports the same. `tests/test_fixtures.py` holds all of this to account.

### Templates

Templates come in all three forms GTM writes: container-authored (`cvt_<containerId>_<templateId>`, no
gallery reference), gallery with a `galleryTemplateId` (`cvt_<galleryTemplateId>`), and gallery without
one, which tags spell in the container form. Container-authored `templateData` opens with `___INFO___`
and the Template Editor's placeholder ID; gallery `templateData` opens with `___TERMS_OF_SERVICE___`, as
real gallery templates do, though the notice in it is the fixtures' own wording rather than Google's.
Each template's permissions cover every sandboxed API its code requires (`access_consent`,
`write_data_layer`, `access_globals` for `fbq` and `amplitude`, `inject_script`, `logging`, and on the
server `send_http`, `read_event_data`, cookies and container data), and entries someone has changed from
the default carry `clientAnnotations.isEditedByUser`, as real exports do.

The server Meta Conversions API template's parameter names, and the order its tags store them in,
follow the Apache-2.0 [stape-io/facebook-tag](https://github.com/stape-io/facebook-tag) template as
committed in `third_party/stape_shopify_meta_ga4_ads_server.json` (see `third_party/NOTICE.md`). Only
the names are borrowed; the labels and code are written for these fixtures.

### Shapes not yet checked against a real export

These are informed guesses, marked `UNVERIFIED` in `cdm/builders.py`. Tiggy compares containers by
content rather than key order, so a wrong guess makes a fixture less realistic without changing what
the tests prove. Replace the guess and drop the marker once a real export shows the shape.

- Where a published version's `name` and `description` sit in `containerVersion`.
- Where a tag's `priority`, `notes` and `paused` sit among its keys.
- How a trigger condition writes `ignore_case` (key, type and position).

### Sidecars and regenerating

Sidecar bodies deliberately differ in how they end, as real ones do: some have no final newline, the
`.tpl` files end in blank lines, one custom HTML tag has trailing spaces, and others are already clean.
That is what pre-commit's whitespace fixers trip over.

To change a fixture, edit `cdm/build.py` (or `cdm/builders.py`) and regenerate:

```bash
uv run python tests/fixtures/cdm/build.py          # rewrite the JSON
uv run python tests/fixtures/cdm/build.py --check  # exit 1 when the committed files are stale
```

`tests/test_fixtures.py` fails when the committed JSON differs from what `build.py` writes. The JSON is
written as GTM writes an export (four-space indent, no final newline), and the root `.gitattributes`
marks `tests/fixtures/**` and `tests/goldens/**` as `-text` so git never converts their line endings.

## `third_party/`: public exports

Four public container exports, byte for byte as published, under their own licences. Sources, commits
and licences are in `third_party/NOTICE.md`, the licence texts in `third_party/licenses/`, and
`tests/test_fixtures.py` checks each file still matches its upstream git blob. Don't edit them: a test
that needs a variation writes a modified copy to its own `tmp_path`.

They import and round-trip cleanly. `tiggy validate` exits 1 on `bounteous_youtube_tracking.json`, whose
upstream placeholder `{{YOUR_GA_TRACKING_ID}}` is 1 error, and 0 on the stape containers, with unused
triggers and variables as warnings (1 in each web container, 6 in the server one).

## The hygiene rule

This repository is public, so no file under `tests/` may hold a real container's identifiers: no real
container public IDs, account IDs, measurement IDs, tokens or API keys, internal hostnames, API Gateway
URLs or ticket keys, and nothing copied from a private container or repository.
`tests/test_fixtures.py` enforces it on every text file under `tests/` (fixtures, test sources, goldens
and this README), except `third_party/`, which holds real public exports under their own licences.

The rules say what an identifier may look like, so the test never has to name what it protects:

- GTM public IDs look like `GTM-TEST001` (three digits), GA4 measurement IDs like `G-TEST000001`,
  Google Ads IDs like `AW-000000001` and Universal Analytics IDs like `UA-000000-1`.
- Every hostname, whether in a URL, protocol-relative (`//host/...`) or bare (a lookup-table key), is
  under `example.com`, `example.net` or `example.org`, or one of five public hosts real exports can't
  avoid: `tagmanager.google.com` (every export's `tagManagerUrl`), `github.com` (every gallery
  template's `galleryReference`), `developers.google.com` (the terms notice atop gallery templates),
  `googletagmanager.com` (where gtm.js and gtag.js load from) and `google-analytics.com` (analytics.js
  and GA collection). Bare hostnames are recognised by a list of common TLDs, so an internal host on an
  unusual TLD can slip past; the deny-list below covers that.
- Nothing is shaped like a ticket key (capitals, a hyphen, digits) other than the IDs above and
  `UTF-8`, and nothing names an API Gateway URL.
- Secrets in constants announce themselves, such as `TEST_TOKEN_NOT_A_SECRET`.

For the specifics a shape can't catch (a company or product name, a real account ID), point
`TIGGY_HYGIENE_DENYLIST` at a file **outside** the repository holding one forbidden string per line:

```bash
TIGGY_HYGIENE_DENYLIST=~/private/tiggy-denylist.txt uv run pytest tests/test_fixtures.py
```

Every non-empty line, trimmed, is matched case-insensitively against the same files. A hit reports the
file, the line and the deny-list's line number, never the entry itself, so a CI log can't publish the
list. A deny-list inside the repository is refused. Tests plant values in a temporary copy of `tests/` to
prove each rule, and the deny-list, catches what it should.

## Running Tiggy from tests

`run_tiggy` in `tests/conftest.py` runs this checkout's own console script, `.venv/bin/tiggy` (or
`.venv\Scripts\tiggy.exe`), and checks once that it imports this checkout's `src/tiggy`; there is no
fallback to `PATH`, so run `uv sync` first. Each run starts from a pinned environment (`PATH`, `HOME`,
`SYSTEMROOT` and the temp directory variables, plus `LC_ALL=C.UTF-8`, `PYTHONUTF8=1`, `COLUMNS=200`,
`NO_COLOR=1`), with a test's own `env` on top. `scrub(text, *paths)` swaps absolute paths for `<path1>`,
`<path2>`, ... so CLI output can be compared with a golden.

## `TIGGY_EXTRA_EXPORTS`: your own containers, kept out of the repo

Real containers are the best test of Tiggy and can't be committed. Export them from the GTM UI (Admin >
Export Container, or Versions > Export for a published version) into a directory **outside** this
repository and point the tests at it:

```bash
TIGGY_EXTRA_EXPORTS=~/gtm-exports uv run pytest
```

Every `*.json` there joins `ALL_EXPORTS` in `tests/conftest.py`, with test IDs `extra/<file stem>`, so
the round-trip tests run against them too. pytest refuses to start when the directory is inside the
repository, missing, or holds no `*.json`, and stops after collection when no selected test runs against
an `extra/` export, so a run can't pass without reading them. Test output can quote container contents,
so treat a run against real exports like the exports themselves.
