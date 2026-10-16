"""What GTM provides without a resource to back it: built-in variable names and trigger IDs."""

from tiggy.gtm.resources import BuiltInVariableType as B

# Display names as they appear in exports. Partial by design: only pairs seen in real exports,
# since the API publishes the types but not the names.
VARIABLE_NAMES: dict[B, str] = {
    B.CLICK_CLASSES: "Click Classes",
    B.CLICK_ELEMENT: "Click Element",
    B.CLICK_ID: "Click ID",
    B.CLICK_TARGET: "Click Target",
    B.CLICK_TEXT: "Click Text",
    B.CLICK_URL: "Click URL",
    B.CLIENT_NAME: "Client Name",
    B.CONTAINER_ID: "Container ID",
    B.CONTAINER_VERSION: "Container Version",
    B.DEBUG_MODE: "Debug Mode",
    B.ELEMENT_VISIBILITY_RATIO: "Percent Visible",
    B.ELEMENT_VISIBILITY_TIME: "On-Screen Duration",
    B.ENVIRONMENT_NAME: "Environment Name",
    B.ERROR_LINE: "Error Line",
    B.ERROR_MESSAGE: "Error Message",
    B.ERROR_URL: "Error URL",
    B.EVENT: "Event",
    B.EVENT_NAME: "Event Name",
    B.FORM_CLASSES: "Form Classes",
    B.FORM_ELEMENT: "Form Element",
    B.FORM_ID: "Form ID",
    B.FORM_TARGET: "Form Target",
    B.FORM_TEXT: "Form Text",
    B.FORM_URL: "Form URL",
    B.HISTORY_SOURCE: "History Source",
    B.HTML_ID: "HTML ID",
    B.NEW_HISTORY_FRAGMENT: "New History Fragment",
    B.NEW_HISTORY_STATE: "New History State",
    B.OLD_HISTORY_FRAGMENT: "Old History Fragment",
    B.OLD_HISTORY_STATE: "Old History State",
    B.PAGE_HOSTNAME: "Page Hostname",
    B.PAGE_PATH: "Page Path",
    B.PAGE_URL: "Page URL",
    B.RANDOM_NUMBER: "Random Number",
    B.REFERRER: "Referrer",
    B.SCROLL_DEPTH_DIRECTION: "Scroll Direction",
    B.SCROLL_DEPTH_THRESHOLD: "Scroll Depth Threshold",
    B.SCROLL_DEPTH_UNITS: "Scroll Depth Units",
    B.VIDEO_CURRENT_TIME: "Video Current Time",
    B.VIDEO_DURATION: "Video Duration",
    B.VIDEO_PERCENT: "Video Percent",
    B.VIDEO_PROVIDER: "Video Provider",
    B.VIDEO_STATUS: "Video Status",
    B.VIDEO_TITLE: "Video Title",
    B.VIDEO_URL: "Video URL",
    B.VIDEO_VISIBLE: "Video Visible",
}

# Triggers every web container has. GTM reserves IDs from FIRST_TRIGGER_ID up for them.
FIRST_TRIGGER_ID = 2147479553
TRIGGER_NAMES: dict[str, str] = {
    "2147479553": "All Pages",
    "2147479572": "Consent Initialization - All Pages",
    "2147479573": "Initialization - All Pages",
}


def is_trigger_id(trigger_id: str) -> bool:
    return trigger_id.isdigit() and int(trigger_id) >= FIRST_TRIGGER_ID


def trigger_name(trigger_id: str) -> str:
    return TRIGGER_NAMES.get(trigger_id, f"built-in trigger {trigger_id}")
