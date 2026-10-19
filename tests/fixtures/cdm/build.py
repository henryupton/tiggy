"""Builds the synthetic container exports in this directory.

    uv run python tests/fixtures/cdm/build.py          # rewrite the JSON exports
    uv run python tests/fixtures/cdm/build.py --check  # exit 1 if the committed files are stale

Modelled on how a digital analytics team runs GTM, with every identifier, host and value made up:
an Analytics web container loaded before consent, a Marketing web container loaded after it, a
server-side container behind the Analytics container's Google tag, and a long-lived web container
nobody has ever cleaned up.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.fixtures.cdm import builders as b  # noqa: E402
from tests.fixtures.cdm.builders import condition, permission, row, text  # noqa: E402

HERE = Path(__file__).resolve().parent
EXPORT_TIME = "2026-10-19 09:30:00"

# Event attributes the site pushes as `event_attributes` with every `track_event`.
PROPERTIES = sorted(
    [
        "build_number",
        "download_format",
        "element_text",
        "environment",
        "item_brand",
        "item_category",
        "item_category2",
        "item_id",
        "item_name",
        "item_variant",
        "license_type",
        "link_url",
        "list_name",
        "method",
        "page_section",
        "page_template",
        "plan_name",
        "platform",
        "search_filters",
        "search_results_count",
        "search_term",
        "site_language",
        "sort_order",
        "user_logged_in",
        "wishlist_id",
    ]
)
# GA4 parameters sent under another name than the event attribute they carry.
GA4_ALIASES = {"item_id": "content_id", "search_term": "search_query"}


def dlv(prop: str) -> str:
    return "{{DLV - event_attributes." + prop + "}}"


def ga4_rows(*props: str) -> list[tuple[str, str]]:
    """eventSettingsTable rows for event attributes, aliased where GA4 knows them by another name."""
    return sorted((GA4_ALIASES.get(p, p), dlv(p)) for p in props)


def payload_js() -> str:
    """The first-party payload: one aligned `key: {{variable}},` line per value, sections marked `// `."""

    def section(title: str, pairs: list[tuple[str, str]]) -> list[str]:
        width = max(len(k) for k, _ in pairs) + 2
        return [f"    // {title}"] + [f"    {(k + ':').ljust(width)}{v}," for k, v in pairs]

    lines = ["function() {", "  var payload = {"]
    lines += section(
        "Envelope",
        [("event_name", "{{DLV - event_name}}"), ("container_version", "{{Container Version}}")],
    )
    lines += section(
        "Page",
        [
            ("page_hostname", "{{Page Hostname}}"),
            ("page_location", "{{Page URL}}"),
            ("page_referrer", "{{Referrer}}"),
            ("utm_campaign", "{{URL - utm_campaign}}"),
        ],
    )
    lines += section("Event", [(p, dlv(p)) for p in PROPERTIES])
    lines += section("Debug", [("debug_mode", "{{Debug Mode}}")])
    lines += ["  };", "  payload.sent_at = new Date().toISOString();", "  return payload;", "}"]
    return "\n".join(lines)


POST_HTML = """<script>
  (function () {
    var body = JSON.stringify({{CJS - first-party payload}});
    var endpoint = 'https://collect.example.com/v1/events';
    if (navigator.sendBeacon && navigator.sendBeacon(endpoint, body)) {
      return;
    }
    fetch(endpoint, {method: 'POST', body: body, keepalive: true, credentials: 'omit'});
  })();
</script>"""

RESET_HTML = """<script>
  window.dataLayer = window.dataLayer || [];
  window.dataLayer.push({event_name: undefined, event_attributes: undefined});
</script>
"""

# The two spaces before the escaped newline are deliberate: pasted tag code often carries them.
UET_BASE_HTML = """<script>
  (function (w, d, src) {
    w.uetq = w.uetq || [];  \n    var s = d.createElement('script');
    s.async = true;
    s.src = src;
    s.onload = function () {
      var queue = new w.UET({ti: '{{C - Microsoft UET tag ID}}', q: w.uetq});
      w.uetq = queue;
      w.uetq.push('pageLoad');
    };
    d.head.appendChild(s);
  })(window, document, 'https://uet.example.net/bat.js');
</script>
"""

UET_PURCHASE_HTML = """<script>
  window.uetq = window.uetq || [];
  window.uetq.push('event', 'purchase', {
    revenue_value: {{DLV - ecommerce.value}},
    currency: '{{DLV - ecommerce.currency}}'
  });
</script>
"""

LINKEDIN_HTML = """<script>
  window._linkedin_partner_id = '{{C - LinkedIn partner ID}}';
  window._linkedin_data_partner_ids = window._linkedin_data_partner_ids || [];
  window._linkedin_data_partner_ids.push(window._linkedin_partner_id);
  (function (d) {
    var s = d.createElement('script');
    s.async = true;
    s.src = 'https://insight.example.net/insight.min.js';
    d.head.appendChild(s);
  })(document);
</script>
"""


def _field(kind: str, name: str, label: str, **extra) -> dict:
    return {"type": kind, "name": name, "displayName": label, **extra}


def _choices(*values: str) -> list[dict]:
    return [{"value": v, "displayValue": v} for v in values]


def _consent_types(*types: str, read: bool, write: bool) -> list[dict]:
    """access_consent's consentTypes: one {consentType, read, write} mapping per type."""
    return [{"consentType": t, "read": read, "write": write} for t in types]


def _globals(*keys: tuple[str, bool, bool, bool]) -> list[dict]:
    """access_globals' keys: (key, read, write, execute) for each window global a template touches."""
    return [{"key": k, "read": r, "write": w, "execute": x} for k, r, w, x in keys]


TABLE_COLUMNS = [
    {"defaultValue": "", "displayName": "Name", "name": "name", "type": "TEXT"},
    {"defaultValue": "", "displayName": "Value", "name": "value", "type": "TEXT"},
]
REQUIRED = [{"type": "NON_EMPTY"}]

# Container-authored: the Template Editor's own INFO, no gallery reference, no terms section.
AMPLITUDE_TPL = b.template_file(
    b.template_info(
        "TAG",
        "Amplitude Event",
        "Sends one event, with its properties, through the Amplitude SDK the page has loaded.",
        "WEB",
    ),
    [
        _field("TEXT", "eventType", "Event type", simpleValueType=True, valueValidators=REQUIRED),
        _field("SIMPLE_TABLE", "eventProperties", "Event properties", simpleTableColumns=TABLE_COLUMNS),
    ],
    """const callInWindow = require('callInWindow');
const copyFromWindow = require('copyFromWindow');
const logToConsole = require('logToConsole');
const makeTableMap = require('makeTableMap');

// The page loads and initialises the SDK; this tag only sends events through it.
if (!copyFromWindow('amplitude')) {
  logToConsole('Amplitude Event: no SDK on the page, dropped', data.eventType);
  return data.gtmOnFailure();
}
const properties = makeTableMap(data.eventProperties || [], 'name', 'value') || {};
callInWindow('amplitude.track', data.eventType, properties);
data.gtmOnSuccess();
""",
    permissions=[
        permission(
            "access_globals",
            keys=_globals(("amplitude", True, False, False), ("amplitude.track", False, False, True)),
        ),
        permission("logging", edited=False, environments="debug"),
    ],
    created="14/03/2026, 10:12:41",
)

COOKIEBOT_TPL = b.template_file(
    b.template_info(
        "TAG",
        "Cookiebot CMP",
        "Loads the consent banner and sets Google Consent Mode defaults before any other tag fires.",
        "WEB",
        brand="example-cmp",
        categories=["UTILITY"],
    ),
    [
        _field("TEXT", "serial", "Domain group ID", simpleValueType=True, valueValidators=REQUIRED),
        _field("CHECKBOX", "enableConsentMode", "Enable Google Consent Mode", simpleValueType=True),
        _field("TEXT", "waitForUpdate", "Wait for update (ms)", simpleValueType=True, defaultValue="500"),
        _field(
            "PARAM_TABLE",
            "defaultConsent",
            "Default consent state by region",
            paramTableColumns=[
                {"param": _field("TEXT", key, key, simpleValueType=True), "isUnique": key == "region"}
                for key in ("region", "ad_storage", "analytics_storage", "ad_user_data", "ad_personalization")
            ],
        ),
        _field("CHECKBOX", "adsDataRedaction", "Redact ads data", simpleValueType=True),
        _field("CHECKBOX", "urlPassthrough", "Pass through URL parameters", simpleValueType=True),
    ],
    """const gtagSet = require('gtagSet');
const injectScript = require('injectScript');
const logToConsole = require('logToConsole');
const makeNumber = require('makeNumber');
const setDefaultConsentState = require('setDefaultConsentState');

if (data.enableConsentMode) {
  (data.defaultConsent || []).forEach((row) => {
    const state = {
      ad_storage: row.ad_storage,
      analytics_storage: row.analytics_storage,
      ad_user_data: row.ad_user_data,
      ad_personalization: row.ad_personalization,
      wait_for_update: makeNumber(data.waitForUpdate)
    };
    if (row.region) {
      state.region = row.region.split(',');
    }
    setDefaultConsentState(state);
  });
  gtagSet('ads_data_redaction', data.adsDataRedaction);
  gtagSet('url_passthrough', data.urlPassthrough);
}

const src = 'https://consent.example.net/uc.js?cbid=' + data.serial;
logToConsole('Cookiebot CMP: loading', src);
injectScript(src, data.gtmOnSuccess, data.gtmOnFailure, 'cmp');
""",
    permissions=[
        permission(
            "access_consent",
            consentTypes=_consent_types(
                "ad_storage",
                "analytics_storage",
                "ad_user_data",
                "ad_personalization",
                read=False,
                write=True,
            ),
        ),
        permission("write_data_layer", keyPatterns=["ads_data_redaction", "url_passthrough"]),
        permission("inject_script", urls=["https://consent.example.net/*"]),
        permission("logging", edited=False, environments="debug"),
    ],
    created="02/02/2026, 16:40:05",
    gallery=True,
)

META_PIXEL_TPL = b.template_file(
    b.template_info(
        "TAG",
        "Meta Pixel",
        "Loads the Meta Pixel and sends a standard or custom event.",
        "WEB",
        gallery_id="T3STP",
        brand="example-pixels",
        categories=["ADVERTISING", "CONVERSIONS"],
    ),
    [
        _field("TEXT", "pixelId", "Pixel ID", simpleValueType=True, valueValidators=REQUIRED),
        _field(
            "RADIO",
            "eventName",
            "Event",
            radioItems=[
                {"value": "standard", "displayValue": "Standard"},
                {"value": "custom", "displayValue": "Custom"},
            ],
            simpleValueType=True,
        ),
        _field(
            "SELECT",
            "standardEventName",
            "Standard event",
            selectItems=_choices("PageView", "Purchase", "CompleteRegistration", "Lead"),
            simpleValueType=True,
        ),
        _field("TEXT", "customEventName", "Custom event", simpleValueType=True),
        _field("SIMPLE_TABLE", "objectPropertyList", "Object properties", simpleTableColumns=TABLE_COLUMNS),
        _field("CHECKBOX", "consent", "Honour ad_storage consent", simpleValueType=True),
        _field("CHECKBOX", "advancedMatching", "Advanced matching", simpleValueType=True),
    ],
    """const callInWindow = require('callInWindow');
const injectScript = require('injectScript');
const isConsentGranted = require('isConsentGranted');
const logToConsole = require('logToConsole');
const makeTableMap = require('makeTableMap');

const custom = data.eventName === 'custom';
const name = custom ? data.customEventName : data.standardEventName;
const properties = makeTableMap(data.objectPropertyList || [], 'name', 'value') || {};

const send = () => {
  if (data.consent) {
    callInWindow('fbq', 'consent', isConsentGranted('ad_storage') ? 'grant' : 'revoke');
  }
  callInWindow('fbq', 'init', data.pixelId);
  callInWindow('fbq', custom ? 'trackCustom' : 'track', name, properties);
  logToConsole('Meta Pixel', name);
  data.gtmOnSuccess();
};

injectScript('https://pixel.example.net/events.js', send, data.gtmOnFailure, 'meta-pixel');
""",
    permissions=[
        permission("access_globals", keys=_globals(("fbq", True, True, True), ("_fbq", True, True, False))),
        permission("access_consent", consentTypes=_consent_types("ad_storage", read=True, write=False)),
        permission("inject_script", urls=["https://pixel.example.net/events.js"]),
        permission("logging", edited=False, environments="debug"),
    ],
    created="21/11/2025, 08:03:19",
    gallery=True,
)

# Parameter names follow stape-io/facebook-tag (Apache-2.0), as tests/fixtures/third_party's
# stape_shopify_meta_ga4_ads_server.json holds it; the labels and code are the fixtures' own.
META_CAPI_TPL = b.template_file(
    b.template_info(
        "TAG",
        "Meta Conversions API",
        "Sends server events to the Meta Conversions API.",
        "SERVER",
        gallery_id="T3STC",
        brand="example-pixels",
        categories=["ADVERTISING", "CONVERSIONS"],
    ),
    [
        _field(
            "SELECT",
            "inheritEventName",
            "Event name",
            selectItems=[
                {"value": "inherit", "displayValue": "Inherit from client"},
                {"value": "override", "displayValue": "Override"},
            ],
            simpleValueType=True,
            subParams=[
                _field(
                    "RADIO",
                    "eventName",
                    "Event type",
                    radioItems=[
                        {
                            "value": "standard",
                            "displayValue": "Standard",
                            "subParams": [
                                _field(
                                    "SELECT",
                                    "eventNameStandard",
                                    "",
                                    selectItems=_choices(
                                        "PageView", "Purchase", "CompleteRegistration", "Lead", "ViewContent"
                                    ),
                                    simpleValueType=True,
                                )
                            ],
                        },
                        {
                            "value": "custom",
                            "displayValue": "Custom",
                            "subParams": [_field("TEXT", "eventNameCustom", "", simpleValueType=True)],
                        },
                    ],
                    simpleValueType=True,
                    enablingConditions=[
                        {"paramName": "inheritEventName", "paramValue": "override", "type": "EQUALS"}
                    ],
                )
            ],
        ),
        _field(
            "SELECT",
            "actionSource",
            "Action source",
            selectItems=_choices("website", "app", "email", "other"),
            simpleValueType=True,
            defaultValue="website",
        ),
        _field("TEXT", "accessToken", "API access token", simpleValueType=True, valueValidators=REQUIRED),
        _field("TEXT", "pixelId", "Pixel ID", simpleValueType=True, valueValidators=REQUIRED),
        _field(
            "CHECKBOX",
            "enableMultipixelSetup",
            "",
            checkboxText="Send to more than one pixel",
            simpleValueType=True,
        ),
        _field("TEXT", "testId", "Test event code", simpleValueType=True),
        _field("TEXT", "itemIdKey", "Item ID key", simpleValueType=True, canBeEmptyString=True),
        _field(
            "CHECKBOX",
            "generateFbp",
            "",
            checkboxText="Set _fbp when it is missing",
            simpleValueType=True,
            defaultValue=True,
        ),
        _field("CHECKBOX", "useHttpOnlyCookie", "", checkboxText="HttpOnly cookies", simpleValueType=True),
        _field(
            "CHECKBOX",
            "enableEventEnhancement",
            "",
            checkboxText="Fill user data from the event",
            simpleValueType=True,
            defaultValue=True,
        ),
        _field(
            "CHECKBOX",
            "useOptimisticScenario",
            "",
            checkboxText="Succeed without waiting for Meta",
            simpleValueType=True,
        ),
        *(
            _field(
                "GROUP",
                f"{table}Group",
                label,
                groupStyle="ZIPPY_CLOSED",
                subParams=[_field("SIMPLE_TABLE", table, "", simpleTableColumns=TABLE_COLUMNS)],
            )
            for table, label in (
                ("serverEventDataList", "Server event data"),
                ("userDataList", "User data"),
                ("customDataList", "Custom data"),
            )
        ),
        _field(
            "GROUP",
            "consentSettingsGroup",
            "Consent",
            groupStyle="ZIPPY_CLOSED",
            subParams=[
                _field(
                    "RADIO",
                    "adStorageConsent",
                    "",
                    radioItems=_choices("optional", "required"),
                    simpleValueType=True,
                    defaultValue="optional",
                )
            ],
        ),
        _field(
            "GROUP",
            "logsGroup",
            "Logging",
            groupStyle="ZIPPY_CLOSED",
            subParams=[
                _field(
                    "RADIO",
                    "logType",
                    "",
                    radioItems=_choices("no", "debug", "always"),
                    simpleValueType=True,
                    defaultValue="debug",
                )
            ],
        ),
    ],
    """const JSON = require('JSON');
const Math = require('Math');
const generateRandom = require('generateRandom');
const getAllEventData = require('getAllEventData');
const getContainerVersion = require('getContainerVersion');
const getCookieValues = require('getCookieValues');
const getTimestampMillis = require('getTimestampMillis');
const logToConsole = require('logToConsole');
const makeTableMap = require('makeTableMap');
const sendHttpRequest = require('sendHttpRequest');
const setCookie = require('setCookie');

const event = getAllEventData();
const consent = event.consent_state || {};
if (data.adStorageConsent === 'required' && consent.ad_storage === false) {
  return data.gtmOnSuccess();
}

const now = getTimestampMillis();
let fbp = getCookieValues('_fbp')[0];
if (!fbp && data.generateFbp) {
  fbp = 'fb.1.' + now + '.' + generateRandom(1000000000, 2147483647);
  setCookie('_fbp', fbp, {domain: 'auto', path: '/', secure: true, httpOnly: !!data.useHttpOnlyCookie});
}

const user = makeTableMap(data.userDataList || [], 'name', 'value') || {};
user.fbp = fbp;
user.fbc = getCookieValues('_fbc')[0];
if (data.enableEventEnhancement) {
  user.client_ip_address = user.client_ip_address || event.ip_override;
  user.client_user_agent = user.client_user_agent || event.user_agent;
}

const serverEvent = makeTableMap(data.serverEventDataList || [], 'name', 'value') || {};
const standard = data.eventName === 'standard';
serverEvent.event_name = data.inheritEventName === 'inherit'
  ? event.event_name
  : (standard ? data.eventNameStandard : data.eventNameCustom);
serverEvent.event_time = Math.round(now / 1000);
serverEvent.action_source = data.actionSource;
serverEvent.user_data = user;
serverEvent.custom_data = makeTableMap(data.customDataList || [], 'name', 'value') || {};

const body = {data: [serverEvent]};
if (data.testId) {
  body.test_event_code = data.testId;
}

const log = data.logType === 'always' || (data.logType === 'debug' && getContainerVersion().debugMode);
const url = 'https://graph.example.net/v1/' + data.pixelId + '/events?access_token=' + data.accessToken;
const options = {headers: {'content-type': 'application/json'}, method: 'POST'};
if (data.useOptimisticScenario) {
  data.gtmOnSuccess();
}
sendHttpRequest(url, options, JSON.stringify(body)).then((result) => {
  if (log) {
    logToConsole('Meta Conversions API', serverEvent.event_name, result.statusCode);
  }
  if (data.useOptimisticScenario) {
    return;
  }
  if (result.statusCode >= 200 && result.statusCode < 300) {
    data.gtmOnSuccess();
  } else {
    data.gtmOnFailure();
  }
});
""",
    permissions=[
        permission("read_event_data", eventDataAccess="any"),
        permission("get_cookies", cookieAccess="specific", cookieNames=["_fbp", "_fbc"]),
        permission(
            "set_cookies",
            allowedCookies=[{"name": "_fbp", "domain": "*", "path": "*", "secure": "any", "session": "any"}],
        ),
        permission("send_http", allowedUrls="specific", urls=["https://graph.example.net/"]),
        permission("logging", environments="all"),
        permission("read_container_data", edited=False),
    ],
    created="09/09/2026, 13:27:52",
    gallery=True,
)

COOKIE_READER_TPL = b.template_file(
    b.template_info("MACRO", "Cookie Reader (legacy)", "", "WEB"),
    [_field("TEXT", "cookieName", "Cookie name", simpleValueType=True, valueValidators=REQUIRED)],
    """const getCookieValues = require('getCookieValues');

return getCookieValues(data.cookieName)[0];
""",
    permissions=[permission("get_cookies", cookieAccess="any")],
    created="07/06/2018, 15:02:11",
)


def web_analytics() -> dict:
    """The Analytics web container, loaded before consent: GA4, Amplitude and the first-party payload."""
    c = b.Container(
        "Example - Analytics",
        public_id="GTM-TEST001",
        container_id="101",
        notes="Loaded before consent. Analytics only; ad pixels live in Example - Marketing.",
    )
    c.built_ins(
        "PAGE_URL", "PAGE_HOSTNAME", "PAGE_PATH", "REFERRER", "EVENT", "CONTAINER_VERSION", "DEBUG_MODE"
    )
    amplitude_folder = c.folder("Amplitude")
    consent_folder = c.folder("Consent")
    data_layer_folder = c.folder(
        "Data Layer", notes="One variable per event attribute: version 2, no default."
    )
    first_party = c.folder("First-party")
    ga4_folder = c.folder("GA4")
    triggers = c.folder("Triggers")

    cookiebot = c.template(
        "Cookiebot CMP",
        COOKIEBOT_TPL,
        gallery_reference=b.gallery(
            "example-cmp",
            "cookiebot-cmp-template",
            "5f1e2d3c4b5a69788796a5b4c3d2e1f0a9b8c7d6",
            "0f9e8d7c6b5a49382716f5e4d3c2b1a00f1e2d3c4b5a69788796a5b4c3d2e1f0",
        ),
    )
    amplitude = c.template("Amplitude Event", AMPLITUDE_TPL)

    c.variable("DLV - event_name", b.data_layer("event_name"), folder=data_layer_folder)
    formats = {
        "search_term": {"caseConversionType": "LOWERCASE"},
        "page_template": {"convertUndefinedToValue": text(None, "(not set)")},
    }
    for prop in PROPERTIES:
        c.variable(
            f"DLV - event_attributes.{prop}",
            b.data_layer(f"event_attributes.{prop}"),
            folder=data_layer_folder,
            format_value=formats.get(prop),
        )
    c.variable("C - GA4 measurement ID", b.constant("G-TEST000001"), folder=ga4_folder)
    c.variable(
        "CJS - first-party payload",
        b.custom_js(payload_js()),
        folder=first_party,
        notes="Keys under // Event stay in alphabetical order, one per event attribute.",
    )
    c.variable(
        "LT - server container URL",
        b.lookup_table(
            "{{Page Hostname}}",
            [
                ("www.example.com", "https://sgtm.example.com"),
                ("app.example.com", "https://sgtm.example.com"),
                ("staging.example.org", "https://sgtm.staging.example.org"),
            ],
            default="https://sgtm.example.com",
        ),
    )
    c.variable("URL - utm_campaign", b.url("QUERY", query_key="utm_campaign"))

    track_event = c.trigger(
        "CE - track_event",
        b.custom_event("track_event"),
        folder=triggers,
        notes="Every dictionary event arrives as track_event; no event allowlist.",
    )
    search = c.trigger("CE - view_search_results", b.custom_event("view_search_results"), folder=triggers)
    sign_up = c.trigger("CE - sign_up", b.custom_event("sign_up"), folder=triggers)
    downloaded = c.trigger(
        "CE - track_event - file_download",
        b.custom_event("track_event", filters=[condition("EQUALS", "{{DLV - event_name}}", "file_download")]),
        folder=triggers,
    )
    wishlist = c.trigger(
        "CE - track_event - wishlist",
        b.custom_event(
            "track_event",
            filters=[condition("MATCH_REGEX", "{{DLV - event_name}}", "^(add_to|remove_from)_wishlist$")],
        ),
        folder=triggers,
    )
    production = c.trigger(
        "PV - production hosts",
        b.page_view(
            [
                condition("MATCH_REGEX", "{{Page Hostname}}", r"^(www|app)\.example\.com$", ignore_case=True),
                condition("STARTS_WITH", "{{Page Path}}", "/admin", negate=True),
            ]
        ),
    )

    measurement_id = "{{C - GA4 measurement ID}}"
    c.tag(
        "Google tag",
        b.google_tag(
            measurement_id,
            [("send_page_view", "false"), ("server_container_url", "{{LT - server container URL}}")],
        ),
        firing=[b.INITIALIZATION],
        folder=ga4_folder,
        priority=10,
    )
    c.tag(
        "GA4 - page_view",
        b.ga4_event(
            "page_view",
            measurement_id=measurement_id,
            settings=[("page_location", "{{Page URL}}"), ("page_referrer", "{{Referrer}}")],
        ),
        firing=[production],
        folder=ga4_folder,
    )
    c.tag(
        "GA4 - track_event",
        b.ga4_event(
            "{{DLV - event_name}}",
            measurement_id=measurement_id,
            settings=ga4_rows(
                "build_number",
                "environment",
                "item_brand",
                "item_category",
                "item_category2",
                "item_id",
                "item_name",
                "item_variant",
                "link_url",
                "list_name",
                "page_section",
                "page_template",
                "platform",
                "site_language",
            ),
            user_properties=[("user_logged_in", dlv("user_logged_in"))],
        ),
        firing=[track_event],
        folder=ga4_folder,
    )
    c.tag(
        "GA4 - view_search_results",
        b.ga4_event(
            "view_search_results",
            measurement_id=measurement_id,
            settings=ga4_rows("search_filters", "search_results_count", "search_term", "sort_order"),
        ),
        firing=[search],
        folder=ga4_folder,
    )
    c.tag(
        "GA4 - sign_up",
        b.ga4_event("sign_up", measurement_id=measurement_id, settings=ga4_rows("method", "plan_name")),
        firing=[sign_up],
        folder=ga4_folder,
    )
    c.tag(
        "GA4 - file_download",
        b.ga4_event(
            "file_download",
            measurement_id=measurement_id,
            settings=ga4_rows("download_format", "item_id", "item_variant", "license_type"),
        ),
        firing=[downloaded],
        folder=ga4_folder,
    )
    c.tag(
        "GA4 - wishlist",
        b.ga4_event(
            "{{DLV - event_name}}",
            measurement_id=measurement_id,
            settings=ga4_rows("item_id", "item_variant", "wishlist_id"),
        ),
        firing=[wishlist],
        folder=ga4_folder,
    )
    c.tag(
        "Amplitude - track_event",
        b.template_tag(
            amplitude,
            [
                text("eventType", "{{DLV - event_name}}"),
                b.table("eventProperties", [(p, dlv(p)) for p in PROPERTIES], ("name", "value")),
            ],
        ),
        firing=[track_event],
        folder=amplitude_folder,
    )
    c.tag(
        "HTML - first-party POST",
        b.custom_html(POST_HTML),
        firing=[track_event],
        teardown=("HTML - reset event_attributes", False),
        folder=first_party,
        notes="Posts CJS - first-party payload to the collector on every track_event.",
    )
    c.tag(
        "HTML - reset event_attributes",
        b.custom_html(RESET_HTML),
        folder=first_party,
        firing_option="UNLIMITED",
    )
    c.tag(
        "Cookiebot CMP",
        b.template_tag(
            cookiebot,
            [
                text("serial", "00000000-0000-4000-8000-000000000000"),
                b.boolean("enableConsentMode", True),
                text("waitForUpdate", "500"),
                b.listing(
                    "defaultConsent",
                    [
                        b.mapping(
                            None,
                            [
                                text("region", "EU,EEA,GB"),
                                text("ad_storage", "denied"),
                                text("analytics_storage", "denied"),
                                text("ad_user_data", "denied"),
                                text("ad_personalization", "denied"),
                            ],
                        ),
                        b.mapping(
                            None,
                            [
                                text("region", ""),
                                text("ad_storage", "denied"),
                                text("analytics_storage", "granted"),
                                text("ad_user_data", "denied"),
                                text("ad_personalization", "denied"),
                            ],
                        ),
                    ],
                ),
                b.boolean("adsDataRedaction", True),
                b.boolean("urlPassthrough", False),
            ],
        ),
        firing=[b.CONSENT_INITIALIZATION],
        folder=consent_folder,
        consent="NOT_NEEDED",
    )
    return c.export(
        EXPORT_TIME,
        version_id="87",
        version_name="Add wishlist_id to GA4 and Amplitude",
        description="wishlist_id joins the payload, GA4 - wishlist and Amplitude - track_event.",
    )


def web_marketing() -> dict:
    """The Marketing web container, loaded after consent: ad pixels and conversions."""
    c = b.Container(
        "Example - Marketing",
        public_id="GTM-TEST002",
        container_id="102",
        notes="Loaded after consent (cookie_consent_marketing).",
    )
    c.built_ins("PAGE_URL", "PAGE_HOSTNAME", "PAGE_PATH", "REFERRER", "EVENT")
    exclusions = c.folder("Exclusions")
    google_ads = c.folder("Google Ads")
    linkedin = c.folder("LinkedIn")
    meta = c.folder("Meta")
    microsoft = c.folder("Microsoft Advertising")

    pixel = c.template(
        "Meta Pixel",
        META_PIXEL_TPL,
        gallery_reference=b.gallery(
            "example-pixels",
            "meta-pixel-template",
            "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
            "1d2c3b4a59687766554433221100ffeeddccbbaa99887766554433221100ffee",
            template_id="T3STP",
        ),
    )

    c.variable("C - Meta pixel ID", b.constant("000000000000001"), folder=meta)
    c.variable("C - Google Ads conversion ID", b.constant("000000001"), folder=google_ads)
    c.variable("C - Microsoft UET tag ID", b.constant("000000001"), folder=microsoft)
    c.variable("C - LinkedIn partner ID", b.constant("0000001"), folder=linkedin)
    c.variable("Cookie - internal_user", b.cookie("internal_user"), folder=exclusions)
    c.variable("DLV - ecommerce.currency", b.data_layer("ecommerce.currency"))
    c.variable("DLV - ecommerce.transaction_id", b.data_layer("ecommerce.transaction_id"))
    c.variable("DLV - ecommerce.value", b.data_layer("ecommerce.value"))

    accepted = c.trigger("CE - cookie_consent_marketing", b.custom_event("cookie_consent_marketing"))
    purchase = c.trigger("CE - purchase", b.custom_event("purchase"))
    sign_up = c.trigger("CE - sign_up", b.custom_event("sign_up"))
    internal = c.trigger(
        "Block - internal traffic",
        b.custom_event(".*", regex=True, filters=[condition("EQUALS", "{{Cookie - internal_user}}", "true")]),
        folder=exclusions,
        notes="Blocks every ad tag for staff browsers, which carry the internal_user cookie.",
    )

    pixel_id = text("pixelId", "{{C - Meta pixel ID}}")
    c.tag(
        "Meta - Pixel - Base",
        b.template_tag(
            pixel,
            [
                pixel_id,
                text("eventName", "standard"),
                text("standardEventName", "PageView"),
                b.boolean("consent", True),
                b.boolean("advancedMatching", False),
            ],
        ),
        firing=[accepted],
        blocking=[internal],
        folder=meta,
        firing_option="ONCE_PER_LOAD",
        consent=["ad_storage"],
    )
    c.tag(
        "Meta - Pixel - Purchase",
        b.template_tag(
            pixel,
            [
                pixel_id,
                text("eventName", "standard"),
                text("standardEventName", "Purchase"),
                b.table(
                    "objectPropertyList",
                    [("value", "{{DLV - ecommerce.value}}"), ("currency", "{{DLV - ecommerce.currency}}")],
                    ("name", "value"),
                ),
                b.boolean("consent", True),
                b.boolean("advancedMatching", False),
            ],
        ),
        firing=[purchase],
        blocking=[internal],
        setup=("Meta - Pixel - Base", True),
        folder=meta,
        consent=["ad_storage"],
    )
    c.tag(
        "Meta - Pixel - CompleteRegistration",
        b.template_tag(
            pixel,
            [
                pixel_id,
                text("eventName", "standard"),
                text("standardEventName", "CompleteRegistration"),
                b.boolean("consent", True),
                b.boolean("advancedMatching", False),
            ],
        ),
        firing=[sign_up],
        blocking=[internal],
        folder=meta,
        consent=["ad_storage"],
    )
    c.tag(
        "Microsoft Ads - UET base",
        b.custom_html(UET_BASE_HTML),
        firing=[accepted],
        blocking=[internal],
        folder=microsoft,
        consent=["ad_storage"],
    )
    c.tag(
        "Microsoft Ads - UET purchase",
        b.custom_html(UET_PURCHASE_HTML),
        firing=[purchase],
        blocking=[internal],
        folder=microsoft,
        consent=["ad_storage"],
    )
    c.tag(
        "LinkedIn - Insight",
        b.custom_html(LINKEDIN_HTML),
        firing=[accepted],
        blocking=[internal],
        folder=linkedin,
        paused=True,
        consent=["ad_storage"],
        notes="Paused until the partner ID is confirmed.",
    )
    c.tag("Google Ads - Conversion Linker", b.conversion_linker(), firing=[b.ALL_PAGES], folder=google_ads)
    conversion_id = "{{C - Google Ads conversion ID}}"
    c.tag(
        "Google Ads - purchase",
        b.ads_conversion(
            conversion_id,
            "TestLabelPurchase01",
            value="{{DLV - ecommerce.value}}",
            currency="{{DLV - ecommerce.currency}}",
            order_id="{{DLV - ecommerce.transaction_id}}",
        ),
        firing=[purchase],
        blocking=[internal],
        folder=google_ads,
        consent=["ad_storage", "ad_user_data"],
    )
    c.tag(
        "Google Ads - sign_up",
        b.ads_conversion(conversion_id, "TestLabelSignUp01"),
        firing=[sign_up],
        blocking=[internal],
        folder=google_ads,
        consent=["ad_storage", "ad_user_data"],
    )
    return c.export(EXPORT_TIME)


def server() -> dict:
    """The server container: a GA4 client feeding GA4 and the Meta Conversions API."""
    c = b.Container("Example - Server", public_id="GTM-TEST003", container_id="103", usage_context="SERVER")
    c.built_ins("EVENT_NAME", "CLIENT_NAME", "REQUEST_PATH")
    clients_folder = c.folder("Clients")
    event_data_folder = c.folder("Event Data")
    ga4_folder = c.folder("GA4")
    meta = c.folder("Meta")
    settings = c.folder(
        "Settings", notes="Constants the tags read. Placeholders only; live values are set in GTM."
    )
    triggers = c.folder("Triggers")

    capi = c.template(
        "Meta Conversions API",
        META_CAPI_TPL,
        gallery_reference=b.gallery(
            "example-pixels",
            "meta-capi-template",
            "0a1b2c3d4e5f60718293a4b5c6d7e8f901234567",
            "ffeeddccbbaa99887766554433221100ffeeddccbbaa99887766554433221100",
            template_id="T3STC",
        ),
    )

    c.variable("const - meta api token", b.constant("TEST_TOKEN_NOT_A_SECRET"), folder=settings)
    c.variable("const - meta pixel id", b.constant("000000000000001"), folder=settings)
    for key in ("currency", "event_id", "transaction_id", "user_id", "value"):
        c.variable(f"ed - {key}", b.event_data(key), folder=event_data_folder)
    c.variable(
        "ed - user_data.email_address", b.event_data("user_data.email_address"), folder=event_data_folder
    )
    c.variable(
        "ed - user_data.phone_number", b.event_data("user_data.phone_number"), folder=event_data_folder
    )

    ga4_client = condition("EQUALS", "{{Client Name}}", "GA4")
    from_ga4 = c.trigger(
        "Client - GA4",
        b.always([ga4_client, condition("STARTS_WITH", "{{Request Path}}", "/g/collect")]),
        folder=triggers,
    )
    anonymous = c.trigger(
        "Event - page_view_anon",
        b.always([condition("EQUALS", "{{Event Name}}", "page_view_anon")]),
        folder=triggers,
        notes="Consentless page views: counted by the first-party collector, never sent to GA4.",
    )
    purchase = c.trigger("GA4 - purchase", b.custom_event("purchase", filters=[ga4_client]), folder=triggers)
    sign_up = c.trigger("GA4 - sign_up", b.custom_event("sign_up", filters=[ga4_client]), folder=triggers)

    c.client(
        "GA4",
        "gaaw_client",
        [
            text("cookieDomain", "auto"),
            text("cookieMaxAgeInSec", "63072000"),
            b.boolean("activateDefaultPaths", True),
            text("cookiePath", "/"),
            b.boolean("migrateFromJsClientId", False),
            text("cookieManagement", "server"),
            text("cookieName", "FPID"),
        ],
        folder=clients_folder,
    )

    c.tag("GA4", b.server_ga4(), firing=[from_ga4], blocking=[anonymous], folder=ga4_folder)
    user_data = [
        row(name="em", value="{{ed - user_data.email_address}}"),
        row(name="ph", value="{{ed - user_data.phone_number}}"),
        row(name="external_id", value="{{ed - user_id}}"),
    ]

    def capi_tag(event_name: str, custom_data: list | None) -> dict:
        """A Meta Conversions API tag, its parameters in the order the Stape export stores them."""
        return b.template_tag(
            capi,
            [
                text("logType", "debug"),
                text("adStorageConsent", "required"),
                text("eventNameStandard", event_name),
                b.boolean("generateFbp", True),
                b.listing("userDataList", user_data),
                text("accessToken", "{{const - meta api token}}"),
                text("inheritEventName", "override"),
                text("actionSource", "website"),
                text("pixelId", "{{const - meta pixel id}}"),
                b.boolean("useHttpOnlyCookie", False),
                text("itemIdKey"),
                b.listing("customDataList", custom_data),
                b.boolean("useOptimisticScenario", False),
                text("eventName", "standard"),
                b.listing("serverEventDataList", [row(name="event_id", value="{{ed - event_id}}")]),
                b.boolean("enableEventEnhancement", True),
                b.boolean("enableMultipixelSetup", False),
            ],
        )

    c.tag(
        "Meta CAPI - Purchase",
        capi_tag(
            "Purchase",
            [
                row(name="value", value="{{ed - value}}"),
                row(name="currency", value="{{ed - currency}}"),
                row(name="order_id", value="{{ed - transaction_id}}"),
            ],
        ),
        firing=[purchase],
        folder=meta,
    )
    c.tag(
        "Meta CAPI - CompleteRegistration",
        capi_tag("CompleteRegistration", None),
        firing=[sign_up],
        folder=meta,
    )

    c.transformation(
        "Exclude phone number from GA4",
        "tf_exclude_params",
        [
            b.listing("excludedParamsTable", [row(excludedParams="user_data.phone_number")]),
            b.boolean("matchingConditionsEnabled", False),
            b.boolean("allTagsExcept", False),
            text("booleanExpressionString"),
            b.listing("affectedTags", [b.mapping(None, [b.tag_reference("GA4", weak=False)])]),
            b.listing("affectedTagTypes", None),
        ],
        folder=ga4_folder,
    )
    return c.export(EXPORT_TIME)


# Two names longer than a slug keeps (80 characters), identical up to their last character.
LONG_NAME = (
    "HTML - Session recording snippet pasted from the vendor's onboarding email; ask the web team "
    "before editing, and keep it for the yearly audit - copy "
)
LONG_A, LONG_B = LONG_NAME + "A", LONG_NAME + "B"
assert len(LONG_A) == len(LONG_B) == 150

GTAG_HTML = """<!-- Hard-coded before the Google tag existed; paused, not removed. -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-TEST000004"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', 'G-TEST000004');
</script>
"""

ANALYTICS_JS_HTML = """<script>
(function(i,s,o,g,r,a,m){i['GoogleAnalyticsObject']=r;i[r]=i[r]||function(){
(i[r].q=i[r].q||[]).push(arguments)},i[r].l=1*new Date();a=s.createElement(o),
m=s.getElementsByTagName(o)[0];a.async=1;a.src=g;m.parentNode.insertBefore(a,m)
})(window,document,'script','//www.google-analytics.com/analytics.js','ga');
ga('create', '{{C - UA property ID}}', 'auto');
ga('send', 'pageview');
</script>"""

CLICK_HTML = """<script>
  window.dataLayer.push({event: 'old_click', click_text: '{{Click Text}}', page_path: '{{Page Path}}'});
</script>
"""

SESSION_RECORDING_HTML = """<script>
  (function (d) {
    var s = d.createElement('script');
    s.async = true;
    s.src = '//static.example.org/recorder/v1/recorder.js';
    d.head.appendChild(s);
  })(document);
</script>
"""

CHAT_HTML = """<script src="https://chat.example.net/widget.js" data-team="support" async></script>
"""

SURVEY_HTML = """<div id="survey"></div>
<script src="https://survey.example.net/embed.js" async></script>"""

BLOG_HTML = """<script>
  window.dataLayer.push({event: 'blog_view', blog_language: 'ja'});
</script>
"""


def web_legacy() -> dict:
    """A web container nobody has cleaned since it was set up: dead variables, paused UA, odd names."""
    c = b.Container(
        "Example - Legacy",
        public_id="GTM-TEST004",
        container_id="104",
        notes="Inherited from the previous agency. Nobody is sure what half of it does.",
        first_fingerprint=1_496_000_000_000,
    )
    c.built_ins("PAGE_URL", "PAGE_HOSTNAME", "PAGE_PATH", "REFERRER", "EVENT", "DEBUG_MODE")
    archive = c.folder("Archive", notes="Old tags kept in case someone asks. Nobody has.")
    archive_twin = c.folder("archive")
    universal = c.folder("UA / GA3 (deprecated)")
    personal = c.folder("Données personnelles")
    c.folder("Q3 cleanup", notes="Move things here before deleting them.")

    c.template("Cookie Reader (legacy)", COOKIE_READER_TPL)

    c.variable("C - UA property ID", b.constant("UA-000000-1"), folder=universal)
    c.variable(
        "DLV - pageCategory",
        b.data_layer("pageCategory", version=1),
        notes="Pushed by the old CMS templates; the current site never sets it.",
    )
    c.variable("DLV - userType", b.data_layer("userType", version=1))
    c.variable("DLV - ecommerce.items", b.data_layer("ecommerce.items"))
    c.variable("DLV - event_attributes.legacy_id", b.data_layer("event_attributes.legacy_id"))
    c.variable("Catégorie de page", b.data_layer("page.category"), folder=personal)
    c.variable("URL - gclid", b.url("QUERY", query_key="gclid"), folder=archive)
    c.variable("DLV - promoCode (old)", b.data_layer("promoCode", default="none"), folder=archive_twin)

    sign_up_old = c.trigger(
        "CE - Sign_up", b.custom_event("Sign_up"), notes="The old site pushed Sign_up, capitalised."
    )
    sign_up = c.trigger("CE - sign_up", b.custom_event("sign_up"))
    c.trigger(
        "CE - checkout_step (old)",
        b.custom_event("checkout_step"),
        folder=archive,
        notes="Fired the old checkout funnel tags, all deleted since.",
    )
    blog = c.trigger("PV - /blog/ pages", b.page_view([condition("STARTS_WITH", "{{Page Path}}", "/blog/")]))
    clicks = c.trigger("Click - All Elements", {"type": "CLICK"})

    c.tag(
        "UA - Pageview",
        b.universal_analytics("TRACK_PAGEVIEW", "{{C - UA property ID}}"),
        firing=[b.ALL_PAGES],
        folder=universal,
        paused=True,
        notes="Paused when Universal Analytics stopped processing; kept for reference.",
    )
    c.tag(
        "UA - Event - Sign_up",
        b.universal_analytics("TRACK_EVENT", "{{C - UA property ID}}", event=("Account", "Sign up")),
        firing=[sign_up_old],
        folder=universal,
        paused=True,
    )
    c.tag(
        "HTML - analytics.js (UA, old)",
        b.custom_html(ANALYTICS_JS_HTML),
        firing=[b.ALL_PAGES],
        folder=universal,
        paused=True,
    )
    c.tag(
        "Google tag - G-TEST000004",
        b.google_tag("G-TEST000004"),
        firing=[b.ALL_PAGES],
    )
    c.tag(
        "HTML - gtag.js (before the Google tag)",
        b.custom_html(GTAG_HTML),
        firing=[b.ALL_PAGES],
        folder=archive,
        paused=True,
    )
    c.tag(
        "GA4 - sign_up",
        b.ga4_event("sign_up", measurement_id="G-TEST000004"),
        firing=[sign_up],
    )
    c.tag("HTML - Click tracking (old)", b.custom_html(CLICK_HTML), firing=[clicks], folder=archive)
    c.tag(LONG_A, b.custom_html(SESSION_RECORDING_HTML), firing=[b.ALL_PAGES], paused=True)
    c.tag(LONG_B, b.custom_html(SESSION_RECORDING_HTML), firing=[b.ALL_PAGES])
    c.tag(
        "HTML - Chat widget / support bubble",
        b.custom_html(CHAT_HTML),
        firing=[b.ALL_PAGES],
        notes="The chat contract ended; the widget still loads.",
    )
    c.tag("HTML - Survey (no trigger)", b.custom_html(SURVEY_HTML), folder=archive_twin)
    c.tag("タグ - 日本語ブログ", b.custom_html(BLOG_HTML), firing=[blog], folder=personal)
    return c.export(EXPORT_TIME, version_id="412")


CONTAINERS = {
    "web_analytics.json": web_analytics,
    "web_marketing.json": web_marketing,
    "server.json": server,
    "web_legacy.json": web_legacy,
}


def build_all() -> dict[str, str]:
    """File name to export text, exactly as committed."""
    return {name: b.dumps(build()) for name, build in CONTAINERS.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if a committed export is stale")
    args = parser.parse_args()
    built = build_all()
    stale = []
    for name, body in built.items():
        path = HERE / name
        current = path.read_bytes().decode("utf-8") if path.exists() else None
        if current == body:
            continue
        stale.append(name)
        if not args.check:
            path.write_bytes(body.encode("utf-8"))
    orphans = sorted(p.name for p in HERE.glob("*.json") if p.name not in built)
    for name in stale:
        print(f"{'stale' if args.check else 'wrote'} {name}")
    for name in orphans:
        print(f"not built by build.py: {name}")
    return 1 if args.check and (stale or orphans) else 0


if __name__ == "__main__":
    raise SystemExit(main())
