"""GTM resource models, generated from the Tag Manager API v2 discovery document (revision 20261014).

Do not edit: run scripts/generate_resources.py.
"""

from __future__ import annotations

from pydantic import Field

from tiggy.gtm.base import GtmEnum, GtmModel, Int32


class BuiltInVariableType(GtmEnum):
    BUILT_IN_VARIABLE_TYPE_UNSPECIFIED = "builtInVariableTypeUnspecified"
    PAGE_URL = "pageUrl"
    PAGE_HOSTNAME = "pageHostname"
    PAGE_PATH = "pagePath"
    REFERRER = "referrer"
    EVENT = "event"
    "For web or mobile."
    CLICK_ELEMENT = "clickElement"
    CLICK_CLASSES = "clickClasses"
    CLICK_ID = "clickId"
    CLICK_TARGET = "clickTarget"
    CLICK_URL = "clickUrl"
    CLICK_TEXT = "clickText"
    FIRST_PARTY_SERVING_URL = "firstPartyServingUrl"
    FORM_ELEMENT = "formElement"
    FORM_CLASSES = "formClasses"
    FORM_ID = "formId"
    FORM_TARGET = "formTarget"
    FORM_URL = "formUrl"
    FORM_TEXT = "formText"
    ERROR_MESSAGE = "errorMessage"
    ERROR_URL = "errorUrl"
    ERROR_LINE = "errorLine"
    NEW_HISTORY_URL = "newHistoryUrl"
    OLD_HISTORY_URL = "oldHistoryUrl"
    NEW_HISTORY_FRAGMENT = "newHistoryFragment"
    OLD_HISTORY_FRAGMENT = "oldHistoryFragment"
    NEW_HISTORY_STATE = "newHistoryState"
    OLD_HISTORY_STATE = "oldHistoryState"
    HISTORY_SOURCE = "historySource"
    CONTAINER_VERSION = "containerVersion"
    "For web or mobile."
    DEBUG_MODE = "debugMode"
    RANDOM_NUMBER = "randomNumber"
    "For web or mobile."
    CONTAINER_ID = "containerId"
    "For web or mobile."
    APP_ID = "appId"
    APP_NAME = "appName"
    APP_VERSION_CODE = "appVersionCode"
    APP_VERSION_NAME = "appVersionName"
    LANGUAGE = "language"
    OS_VERSION = "osVersion"
    PLATFORM = "platform"
    SDK_VERSION = "sdkVersion"
    DEVICE_NAME = "deviceName"
    RESOLUTION = "resolution"
    ADVERTISER_ID = "advertiserId"
    ADVERTISING_TRACKING_ENABLED = "advertisingTrackingEnabled"
    HTML_ID = "htmlId"
    ENVIRONMENT_NAME = "environmentName"
    AMP_BROWSER_LANGUAGE = "ampBrowserLanguage"
    AMP_CANONICAL_PATH = "ampCanonicalPath"
    AMP_CANONICAL_URL = "ampCanonicalUrl"
    AMP_CANONICAL_HOST = "ampCanonicalHost"
    AMP_REFERRER = "ampReferrer"
    AMP_TITLE = "ampTitle"
    AMP_CLIENT_ID = "ampClientId"
    AMP_CLIENT_TIMEZONE = "ampClientTimezone"
    AMP_CLIENT_TIMESTAMP = "ampClientTimestamp"
    AMP_CLIENT_SCREEN_WIDTH = "ampClientScreenWidth"
    AMP_CLIENT_SCREEN_HEIGHT = "ampClientScreenHeight"
    AMP_CLIENT_SCROLL_X = "ampClientScrollX"
    AMP_CLIENT_SCROLL_Y = "ampClientScrollY"
    AMP_CLIENT_MAX_SCROLL_X = "ampClientMaxScrollX"
    AMP_CLIENT_MAX_SCROLL_Y = "ampClientMaxScrollY"
    AMP_TOTAL_ENGAGED_TIME = "ampTotalEngagedTime"
    AMP_PAGE_VIEW_ID = "ampPageViewId"
    AMP_PAGE_LOAD_TIME = "ampPageLoadTime"
    AMP_PAGE_DOWNLOAD_TIME = "ampPageDownloadTime"
    AMP_GTM_EVENT = "ampGtmEvent"
    EVENT_NAME = "eventName"
    FIREBASE_EVENT_PARAMETER_CAMPAIGN = "firebaseEventParameterCampaign"
    FIREBASE_EVENT_PARAMETER_CAMPAIGN_ACLID = "firebaseEventParameterCampaignAclid"
    FIREBASE_EVENT_PARAMETER_CAMPAIGN_ANID = "firebaseEventParameterCampaignAnid"
    FIREBASE_EVENT_PARAMETER_CAMPAIGN_CLICK_TIMESTAMP = "firebaseEventParameterCampaignClickTimestamp"
    FIREBASE_EVENT_PARAMETER_CAMPAIGN_CONTENT = "firebaseEventParameterCampaignContent"
    FIREBASE_EVENT_PARAMETER_CAMPAIGN_CP1 = "firebaseEventParameterCampaignCp1"
    FIREBASE_EVENT_PARAMETER_CAMPAIGN_GCLID = "firebaseEventParameterCampaignGclid"
    FIREBASE_EVENT_PARAMETER_CAMPAIGN_SOURCE = "firebaseEventParameterCampaignSource"
    FIREBASE_EVENT_PARAMETER_CAMPAIGN_TERM = "firebaseEventParameterCampaignTerm"
    FIREBASE_EVENT_PARAMETER_CURRENCY = "firebaseEventParameterCurrency"
    FIREBASE_EVENT_PARAMETER_DYNAMIC_LINK_ACCEPT_TIME = "firebaseEventParameterDynamicLinkAcceptTime"
    FIREBASE_EVENT_PARAMETER_DYNAMIC_LINK_LINKID = "firebaseEventParameterDynamicLinkLinkid"
    FIREBASE_EVENT_PARAMETER_NOTIFICATION_MESSAGE_DEVICE_TIME = (
        "firebaseEventParameterNotificationMessageDeviceTime"
    )
    FIREBASE_EVENT_PARAMETER_NOTIFICATION_MESSAGE_ID = "firebaseEventParameterNotificationMessageId"
    FIREBASE_EVENT_PARAMETER_NOTIFICATION_MESSAGE_NAME = "firebaseEventParameterNotificationMessageName"
    FIREBASE_EVENT_PARAMETER_NOTIFICATION_MESSAGE_TIME = "firebaseEventParameterNotificationMessageTime"
    FIREBASE_EVENT_PARAMETER_NOTIFICATION_TOPIC = "firebaseEventParameterNotificationTopic"
    FIREBASE_EVENT_PARAMETER_PREVIOUS_APP_VERSION = "firebaseEventParameterPreviousAppVersion"
    FIREBASE_EVENT_PARAMETER_PREVIOUS_OS_VERSION = "firebaseEventParameterPreviousOsVersion"
    FIREBASE_EVENT_PARAMETER_PRICE = "firebaseEventParameterPrice"
    FIREBASE_EVENT_PARAMETER_PRODUCT_ID = "firebaseEventParameterProductId"
    FIREBASE_EVENT_PARAMETER_QUANTITY = "firebaseEventParameterQuantity"
    FIREBASE_EVENT_PARAMETER_VALUE = "firebaseEventParameterValue"
    VIDEO_PROVIDER = "videoProvider"
    VIDEO_URL = "videoUrl"
    VIDEO_TITLE = "videoTitle"
    VIDEO_DURATION = "videoDuration"
    VIDEO_PERCENT = "videoPercent"
    VIDEO_VISIBLE = "videoVisible"
    VIDEO_STATUS = "videoStatus"
    VIDEO_CURRENT_TIME = "videoCurrentTime"
    SCROLL_DEPTH_THRESHOLD = "scrollDepthThreshold"
    SCROLL_DEPTH_UNITS = "scrollDepthUnits"
    SCROLL_DEPTH_DIRECTION = "scrollDepthDirection"
    ELEMENT_VISIBILITY_RATIO = "elementVisibilityRatio"
    ELEMENT_VISIBILITY_TIME = "elementVisibilityTime"
    ELEMENT_VISIBILITY_FIRST_TIME = "elementVisibilityFirstTime"
    ELEMENT_VISIBILITY_RECENT_TIME = "elementVisibilityRecentTime"
    REQUEST_PATH = "requestPath"
    REQUEST_METHOD = "requestMethod"
    CLIENT_NAME = "clientName"
    QUERY_STRING = "queryString"
    SERVER_PAGE_LOCATION_URL = "serverPageLocationUrl"
    SERVER_PAGE_LOCATION_PATH = "serverPageLocationPath"
    SERVER_PAGE_LOCATION_HOSTNAME = "serverPageLocationHostname"
    VISITOR_REGION = "visitorRegion"
    ANALYTICS_CLIENT_ID = "analyticsClientId"
    ANALYTICS_SESSION_ID = "analyticsSessionId"
    ANALYTICS_SESSION_NUMBER = "analyticsSessionNumber"


class ConditionType(GtmEnum):
    CONDITION_TYPE_UNSPECIFIED = "conditionTypeUnspecified"
    EQUALS = "equals"
    CONTAINS = "contains"
    STARTS_WITH = "startsWith"
    ENDS_WITH = "endsWith"
    MATCH_REGEX = "matchRegex"
    GREATER = "greater"
    GREATER_OR_EQUALS = "greaterOrEquals"
    LESS = "less"
    LESS_OR_EQUALS = "lessOrEquals"
    CSS_SELECTOR = "cssSelector"
    URL_MATCHES = "urlMatches"


class ContainerUsageContext(GtmEnum):
    USAGE_CONTEXT_UNSPECIFIED = "usageContextUnspecified"
    WEB = "web"
    ANDROID = "android"
    IOS = "ios"
    ANDROID_SDK5 = "androidSdk5"
    IOS_SDK5 = "iosSdk5"
    AMP = "amp"
    SERVER = "server"


class ParameterType(GtmEnum):
    TYPE_UNSPECIFIED = "typeUnspecified"
    TEMPLATE = "template"
    "May include variable references."
    INTEGER = "integer"
    BOOLEAN = "boolean"
    LIST = "list"
    MAP = "map"
    TRIGGER_REFERENCE = "triggerReference"
    TAG_REFERENCE = "tagReference"


class TagConsentSettingConsentStatus(GtmEnum):
    NOT_SET = "notSet"
    "Default value where user has not specified any setting on it."
    NOT_NEEDED = "notNeeded"
    "Tag doesn't require any additional consent settings."
    NEEDED = "needed"
    "Tag requires additional consent settings."


class TagFiringOption(GtmEnum):
    TAG_FIRING_OPTION_UNSPECIFIED = "tagFiringOptionUnspecified"
    UNLIMITED = "unlimited"
    "Tag can be fired multiple times per event."
    ONCE_PER_EVENT = "oncePerEvent"
    "Tag can only be fired per event but can be fired multiple times per load (e.g., app load or page load)."
    ONCE_PER_LOAD = "oncePerLoad"
    "Tag can only be fired per load (e.g., app load or page load)."


class TriggerType(GtmEnum):
    EVENT_TYPE_UNSPECIFIED = "eventTypeUnspecified"
    PAGEVIEW = "pageview"
    DOM_READY = "domReady"
    WINDOW_LOADED = "windowLoaded"
    CUSTOM_EVENT = "customEvent"
    TRIGGER_GROUP = "triggerGroup"
    INIT = "init"
    CONSENT_INIT = "consentInit"
    SERVER_PAGEVIEW = "serverPageview"
    ALWAYS = "always"
    FIREBASE_APP_EXCEPTION = "firebaseAppException"
    FIREBASE_APP_UPDATE = "firebaseAppUpdate"
    FIREBASE_CAMPAIGN = "firebaseCampaign"
    FIREBASE_FIRST_OPEN = "firebaseFirstOpen"
    FIREBASE_IN_APP_PURCHASE = "firebaseInAppPurchase"
    FIREBASE_NOTIFICATION_DISMISS = "firebaseNotificationDismiss"
    FIREBASE_NOTIFICATION_FOREGROUND = "firebaseNotificationForeground"
    FIREBASE_NOTIFICATION_OPEN = "firebaseNotificationOpen"
    FIREBASE_NOTIFICATION_RECEIVE = "firebaseNotificationReceive"
    FIREBASE_OS_UPDATE = "firebaseOsUpdate"
    FIREBASE_SESSION_START = "firebaseSessionStart"
    FIREBASE_USER_ENGAGEMENT = "firebaseUserEngagement"
    FORM_SUBMISSION = "formSubmission"
    CLICK = "click"
    LINK_CLICK = "linkClick"
    JS_ERROR = "jsError"
    HISTORY_CHANGE = "historyChange"
    TIMER = "timer"
    AMP_CLICK = "ampClick"
    AMP_TIMER = "ampTimer"
    AMP_SCROLL = "ampScroll"
    AMP_VISIBILITY = "ampVisibility"
    YOU_TUBE_VIDEO = "youTubeVideo"
    SCROLL_DEPTH = "scrollDepth"
    ELEMENT_VISIBILITY = "elementVisibility"


class VariableFormatValueCaseConversionType(GtmEnum):
    NONE = "none"
    LOWERCASE = "lowercase"
    "The option to convert a variable value to lowercase."
    UPPERCASE = "uppercase"
    "The option to convert a variable value to uppercase."


class VariableFormatValueConvertToNumber(GtmEnum):
    DECIMAL_SEPARATOR_TYPE_UNSPECIFIED = "decimalSeparatorTypeUnspecified"
    PERIOD = "period"
    "The option to convert a variable value to a number with a period as the decimal separator."
    COMMA = "comma"
    "The option to convert a variable value to a number with a comma as the decimal separator."
    AUTOMATIC = "automatic"
    "The option to convert a variable value to a number with automatic decimal separator detection."


class ContainerVersion(GtmModel):
    "Represents a Google Tag Manager Container Version."

    container: Container | None = Field(
        None, alias="container", description="The container that this version was taken from."
    )
    fingerprint: str | None = Field(
        None,
        alias="fingerprint",
        description="The fingerprint of the GTM Container Version as computed at storage time. This value is recomputed whenever the container version is modified.",
    )
    deleted: bool | None = Field(
        None,
        alias="deleted",
        description="A value of true indicates this container version has been deleted.",
    )
    folder: list[Folder] | None = Field(
        None, alias="folder", description="The folders in the container that this version was taken from."
    )
    built_in_variable: list[BuiltInVariable] | None = Field(
        None,
        alias="builtInVariable",
        description="The built-in variables in the container that this version was taken from.",
    )
    transformation: list[Transformation] | None = Field(
        None,
        alias="transformation",
        description="The transformations in the container that this version was taken from.",
    )
    description: str | None = Field(None, alias="description", description="Container version description.")
    variable: list[Variable] | None = Field(
        None, alias="variable", description="The variables in the container that this version was taken from."
    )
    container_id: str | None = Field(None, alias="containerId", description="GTM Container ID.")
    path: str | None = Field(None, alias="path", description="GTM Container Version's API relative path.")
    zone: list[Zone] | None = Field(
        None, alias="zone", description="The zones in the container that this version was taken from."
    )
    gtag_config: list[GtagConfig] | None = Field(
        None,
        alias="gtagConfig",
        description="The Google tag configs in the container that this version was taken from.",
    )
    tag: list[Tag] | None = Field(
        None, alias="tag", description="The tags in the container that this version was taken from."
    )
    trigger: list[Trigger] | None = Field(
        None, alias="trigger", description="The triggers in the container that this version was taken from."
    )
    account_id: str | None = Field(None, alias="accountId", description="GTM Account ID.")
    name: str | None = Field(None, alias="name", description="Container version display name.")
    custom_template: list[CustomTemplate] | None = Field(
        None,
        alias="customTemplate",
        description="The custom templates in the container that this version was taken from.",
    )
    container_version_id: str | None = Field(
        None,
        alias="containerVersionId",
        description="The Container Version ID uniquely identifies the GTM Container Version.",
    )
    tag_manager_url: str | None = Field(
        None, alias="tagManagerUrl", description="Auto generated link to the tag manager UI"
    )
    client: list[Client] | None = Field(
        None, alias="client", description="The clients in the container that this version was taken from."
    )


class Container(GtmModel):
    "Represents a Google Tag Manager Container, which specifies the platform tags will run on, manages workspaces, and retains container versions."

    fingerprint: str | None = Field(
        None,
        alias="fingerprint",
        description="The fingerprint of the GTM Container as computed at storage time. This value is recomputed whenever the account is modified.",
    )
    public_id: str | None = Field(None, alias="publicId", description="Container Public ID.")
    notes: str | None = Field(None, alias="notes", description="Container Notes.")
    name: str | None = Field(None, alias="name", description="Container display name.")
    tag_ids: list[str] | None = Field(
        None, alias="tagIds", description="All Tag IDs that refer to this Container."
    )
    path: str | None = Field(None, alias="path", description="GTM Container's API relative path.")
    features: ContainerFeatures | None = Field(
        None, alias="features", description="Read-only Container feature set."
    )
    container_id: str | None = Field(
        None, alias="containerId", description="The Container ID uniquely identifies the GTM Container."
    )
    account_id: str | None = Field(None, alias="accountId", description="GTM Account ID.")
    tagging_server_urls: list[str] | None = Field(
        None,
        alias="taggingServerUrls",
        description="List of server-side container URLs for the Container. If multiple URLs are provided, all URL paths must match.",
    )
    domain_name: list[str] | None = Field(
        None, alias="domainName", description="List of domain names associated with the Container."
    )
    tag_manager_url: str | None = Field(
        None, alias="tagManagerUrl", description="Auto generated link to the tag manager UI"
    )
    usage_context: list[ContainerUsageContext] | None = Field(
        None,
        alias="usageContext",
        description="List of Usage Contexts for the Container. Valid values include: web, android, or ios.",
    )


class Folder(GtmModel):
    "Represents a Google Tag Manager Folder."

    workspace_id: str | None = Field(None, alias="workspaceId", description="GTM Workspace ID.")
    account_id: str | None = Field(None, alias="accountId", description="GTM Account ID.")
    folder_id: str | None = Field(
        None, alias="folderId", description="The Folder ID uniquely identifies the GTM Folder."
    )
    notes: str | None = Field(
        None, alias="notes", description="User notes on how to apply this folder in the container."
    )
    name: str | None = Field(None, alias="name", description="Folder display name.")
    path: str | None = Field(None, alias="path", description="GTM Folder's API relative path.")
    container_id: str | None = Field(None, alias="containerId", description="GTM Container ID.")
    fingerprint: str | None = Field(
        None,
        alias="fingerprint",
        description="The fingerprint of the GTM Folder as computed at storage time. This value is recomputed whenever the folder is modified.",
    )
    tag_manager_url: str | None = Field(
        None, alias="tagManagerUrl", description="Auto generated link to the tag manager UI"
    )


class BuiltInVariable(GtmModel):
    "Built-in variables are a special category of variables that are pre-created and non-customizable. They provide common functionality like accessing properties of the gtm data layer, monitoring clicks, or accessing elements of a page URL."

    account_id: str | None = Field(None, alias="accountId", description="GTM Account ID.")
    workspace_id: str | None = Field(None, alias="workspaceId", description="GTM Workspace ID.")
    path: str | None = Field(None, alias="path", description="GTM BuiltInVariable's API relative path.")
    container_id: str | None = Field(None, alias="containerId", description="GTM Container ID.")
    type: BuiltInVariableType | None = Field(None, alias="type", description="Type of built-in variable.")
    name: str | None = Field(
        None,
        alias="name",
        description="Name of the built-in variable to be used to refer to the built-in variable.",
    )


class Transformation(GtmModel):
    "Represents a Google Tag Manager Transformation."

    workspace_id: str | None = Field(None, alias="workspaceId", description="GTM Workspace ID.")
    transformation_id: str | None = Field(
        None,
        alias="transformationId",
        description="The Transformation ID uniquely identifies the GTM transformation.",
    )
    path: str | None = Field(None, alias="path", description="GTM transformation's API relative path.")
    name: str | None = Field(None, alias="name", description="Transformation display name.")
    notes: str | None = Field(
        None, alias="notes", description="User notes on how to apply this transformation in the container."
    )
    parent_folder_id: str | None = Field(None, alias="parentFolderId", description="Parent folder id.")
    parameter: list[Parameter] | None = Field(
        None, alias="parameter", description="The transformation's parameters."
    )
    tag_manager_url: str | None = Field(
        None, alias="tagManagerUrl", description="Auto generated link to the tag manager UI"
    )
    account_id: str | None = Field(None, alias="accountId", description="GTM Account ID.")
    fingerprint: str | None = Field(
        None,
        alias="fingerprint",
        description="The fingerprint of the GTM Transformation as computed at storage time. This value is recomputed whenever the transformation is modified.",
    )
    container_id: str | None = Field(None, alias="containerId", description="GTM Container ID.")
    type: str | None = Field(None, alias="type", description="Transformation type.")


class Variable(GtmModel):
    "Represents a Google Tag Manager Variable."

    path: str | None = Field(None, alias="path", description="GTM Variable's API relative path.")
    variable_id: str | None = Field(
        None, alias="variableId", description="The Variable ID uniquely identifies the GTM Variable."
    )
    name: str | None = Field(None, alias="name", description="Variable display name.")
    parameter: list[Parameter] | None = Field(
        None, alias="parameter", description="The variable's parameters."
    )
    notes: str | None = Field(
        None, alias="notes", description="User notes on how to apply this variable in the container."
    )
    disabling_trigger_id: list[str] | None = Field(
        None,
        alias="disablingTriggerId",
        description="For mobile containers only: A list of trigger IDs for disabling conditional variables; the variable is enabled if one of the enabling trigger is true while all the disabling trigger are false. Treated as an unordered set.",
    )
    fingerprint: str | None = Field(
        None,
        alias="fingerprint",
        description="The fingerprint of the GTM Variable as computed at storage time. This value is recomputed whenever the variable is modified.",
    )
    schedule_start_ms: str | None = Field(
        None,
        alias="scheduleStartMs",
        description="The start timestamp in milliseconds to schedule a variable.",
    )
    account_id: str | None = Field(None, alias="accountId", description="GTM Account ID.")
    tag_manager_url: str | None = Field(
        None, alias="tagManagerUrl", description="Auto generated link to the tag manager UI"
    )
    workspace_id: str | None = Field(None, alias="workspaceId", description="GTM Workspace ID.")
    format_value: VariableFormatValue | None = Field(
        None, alias="formatValue", description="Option to convert a variable value to other value."
    )
    enabling_trigger_id: list[str] | None = Field(
        None,
        alias="enablingTriggerId",
        description="For mobile containers only: A list of trigger IDs for enabling conditional variables; the variable is enabled if one of the enabling triggers is true while all the disabling triggers are false. Treated as an unordered set.",
    )
    schedule_end_ms: str | None = Field(
        None, alias="scheduleEndMs", description="The end timestamp in milliseconds to schedule a variable."
    )
    container_id: str | None = Field(None, alias="containerId", description="GTM Container ID.")
    parent_folder_id: str | None = Field(None, alias="parentFolderId", description="Parent folder id.")
    type: str | None = Field(None, alias="type", description="GTM Variable Type.")


class Zone(GtmModel):
    "Represents a Google Tag Manager Zone's contents."

    account_id: str | None = Field(None, alias="accountId", description="GTM Account ID.")
    name: str | None = Field(None, alias="name", description="Zone display name.")
    workspace_id: str | None = Field(None, alias="workspaceId", description="GTM Workspace ID.")
    child_container: list[ZoneChildContainer] | None = Field(
        None, alias="childContainer", description="Containers that are children of this Zone."
    )
    path: str | None = Field(None, alias="path", description="GTM Zone's API relative path.")
    fingerprint: str | None = Field(
        None,
        alias="fingerprint",
        description="The fingerprint of the GTM Zone as computed at storage time. This value is recomputed whenever the zone is modified.",
    )
    container_id: str | None = Field(None, alias="containerId", description="GTM Container ID.")
    boundary: ZoneBoundary | None = Field(None, alias="boundary", description="This Zone's boundary.")
    notes: str | None = Field(
        None, alias="notes", description="User notes on how to apply this zone in the container."
    )
    zone_id: str | None = Field(
        None, alias="zoneId", description="The Zone ID uniquely identifies the GTM Zone."
    )
    tag_manager_url: str | None = Field(
        None, alias="tagManagerUrl", description="Auto generated link to the tag manager UI"
    )
    type_restriction: ZoneTypeRestriction | None = Field(
        None, alias="typeRestriction", description="This Zone's type restrictions."
    )


class GtagConfig(GtmModel):
    "Represents a Google tag configuration."

    account_id: str | None = Field(None, alias="accountId", description="Google tag account ID.")
    type: str | None = Field(None, alias="type", description="Google tag config type.")
    fingerprint: str | None = Field(
        None,
        alias="fingerprint",
        description="The fingerprint of the Google tag config as computed at storage time. This value is recomputed whenever the config is modified.",
    )
    container_id: str | None = Field(None, alias="containerId", description="Google tag container ID.")
    tag_manager_url: str | None = Field(
        None, alias="tagManagerUrl", description="Auto generated link to the tag manager UI"
    )
    parameter: list[Parameter] | None = Field(
        None, alias="parameter", description="The Google tag config's parameters."
    )
    path: str | None = Field(None, alias="path", description="Google tag config's API relative path.")
    workspace_id: str | None = Field(
        None,
        alias="workspaceId",
        description="Google tag workspace ID. Only used by GTM containers. Set to 0 otherwise.",
    )
    gtag_config_id: str | None = Field(
        None, alias="gtagConfigId", description="The ID uniquely identifies the Google tag config."
    )


class Tag(GtmModel):
    "Represents a Google Tag Manager Tag."

    consent_settings: TagConsentSetting | None = Field(
        None, alias="consentSettings", description="Consent settings of a tag."
    )
    notes: str | None = Field(
        None, alias="notes", description="User notes on how to apply this tag in the container."
    )
    tag_id: str | None = Field(None, alias="tagId", description="The Tag ID uniquely identifies the GTM Tag.")
    schedule_start_ms: str | None = Field(
        None, alias="scheduleStartMs", description="The start timestamp in milliseconds to schedule a tag."
    )
    paused: bool | None = Field(
        None,
        alias="paused",
        description="Indicates whether the tag is paused, which prevents the tag from firing.",
    )
    path: str | None = Field(None, alias="path", description="GTM Tag's API relative path.")
    blocking_trigger_id: list[str] | None = Field(
        None,
        alias="blockingTriggerId",
        description="Blocking trigger IDs. If any of the listed triggers evaluate to true, the tag will not fire.",
    )
    account_id: str | None = Field(None, alias="accountId", description="GTM Account ID.")
    firing_trigger_id: list[str] | None = Field(
        None,
        alias="firingTriggerId",
        description="Firing trigger IDs. A tag will fire when any of the listed triggers are true and all of its blockingTriggerIds (if any specified) are false.",
    )
    tag_firing_option: TagFiringOption | None = Field(
        None, alias="tagFiringOption", description="Option to fire this tag."
    )
    fingerprint: str | None = Field(
        None,
        alias="fingerprint",
        description="The fingerprint of the GTM Tag as computed at storage time. This value is recomputed whenever the tag is modified.",
    )
    monitoring_metadata_tag_name_key: str | None = Field(
        None,
        alias="monitoringMetadataTagNameKey",
        description="If non-empty, then the tag display name will be included in the monitoring metadata map using the key specified.",
    )
    setup_tag: list[SetupTag] | None = Field(
        None, alias="setupTag", description="The list of setup tags. Currently we only allow one."
    )
    priority: Parameter | None = Field(
        None,
        alias="priority",
        description="User defined numeric priority of the tag. Tags are fired asynchronously in order of priority. Tags with higher numeric value fire first. A tag's priority can be a positive or negative value. The default value is 0.",
    )
    name: str | None = Field(None, alias="name", description="Tag display name.")
    monitoring_metadata: Parameter | None = Field(
        None,
        alias="monitoringMetadata",
        description="A map of key-value pairs of tag metadata to be included in the event data for tag monitoring. Notes: - This parameter must be type MAP. - Each parameter in the map are type TEMPLATE, however cannot contain variable references.",
    )
    container_id: str | None = Field(None, alias="containerId", description="GTM Container ID.")
    parameter: list[Parameter] | None = Field(None, alias="parameter", description="The tag's parameters.")
    type: str | None = Field(None, alias="type", description="GTM Tag Type.")
    tag_manager_url: str | None = Field(
        None, alias="tagManagerUrl", description="Auto generated link to the tag manager UI"
    )
    parent_folder_id: str | None = Field(None, alias="parentFolderId", description="Parent folder id.")
    schedule_end_ms: str | None = Field(
        None, alias="scheduleEndMs", description="The end timestamp in milliseconds to schedule a tag."
    )
    teardown_tag: list[TeardownTag] | None = Field(
        None, alias="teardownTag", description="The list of teardown tags. Currently we only allow one."
    )
    workspace_id: str | None = Field(None, alias="workspaceId", description="GTM Workspace ID.")
    live_only: bool | None = Field(
        None,
        alias="liveOnly",
        description="If set to true, this tag will only fire in the live environment (e.g. not in preview or debug mode).",
    )


class Trigger(GtmModel):
    "Represents a Google Tag Manager Trigger"

    name: str | None = Field(None, alias="name", description="Trigger display name.")
    interval: Parameter | None = Field(
        None,
        alias="interval",
        description="Time between triggering recurring Timer Events (in milliseconds). Only valid for Timer triggers.",
    )
    account_id: str | None = Field(None, alias="accountId", description="GTM Account ID.")
    wait_for_tags: Parameter | None = Field(
        None,
        alias="waitForTags",
        description="Whether or not we should delay the form submissions or link opening until all of the tags have fired (by preventing the default action and later simulating the default action). Only valid for Form Submission and Link Click triggers.",
    )
    wait_for_tags_timeout: Parameter | None = Field(
        None,
        alias="waitForTagsTimeout",
        description="How long to wait (in milliseconds) for tags to fire when 'waits_for_tags' above evaluates to true. Only valid for Form Submission and Link Click triggers.",
    )
    container_id: str | None = Field(None, alias="containerId", description="GTM Container ID.")
    limit: Parameter | None = Field(
        None,
        alias="limit",
        description="Limit of the number of GTM events this Timer Trigger will fire. If no limit is set, we will continue to fire GTM events until the user leaves the page. Only valid for Timer triggers.",
    )
    workspace_id: str | None = Field(None, alias="workspaceId", description="GTM Workspace ID.")
    total_time_min_milliseconds: Parameter | None = Field(
        None,
        alias="totalTimeMinMilliseconds",
        description="A visibility trigger minimum total visible time (in milliseconds). Only valid for AMP Visibility trigger.",
    )
    fingerprint: str | None = Field(
        None,
        alias="fingerprint",
        description="The fingerprint of the GTM Trigger as computed at storage time. This value is recomputed whenever the trigger is modified.",
    )
    auto_event_filter: list[Condition] | None = Field(
        None, alias="autoEventFilter", description="Used in the case of auto event tracking."
    )
    unique_trigger_id: Parameter | None = Field(
        None,
        alias="uniqueTriggerId",
        description="Globally unique id of the trigger that auto-generates this (a Form Submit, Link Click or Timer listener) if any. Used to make incompatible auto-events work together with trigger filtering based on trigger ids. This value is populated during output generation since the tags implied by triggers don't exist until then. Only valid for Form Submit, Link Click and Timer triggers.",
    )
    trigger_id: str | None = Field(
        None, alias="triggerId", description="The Trigger ID uniquely identifies the GTM Trigger."
    )
    type: TriggerType | None = Field(
        None, alias="type", description="Defines the data layer event that causes this trigger."
    )
    check_validation: Parameter | None = Field(
        None,
        alias="checkValidation",
        description="Whether or not we should only fire tags if the form submit or link click event is not cancelled by some other event handler (e.g. because of validation). Only valid for Form Submission and Link Click triggers.",
    )
    continuous_time_min_milliseconds: Parameter | None = Field(
        None,
        alias="continuousTimeMinMilliseconds",
        description="A visibility trigger minimum continuous visible time (in milliseconds). Only valid for AMP Visibility trigger.",
    )
    interval_seconds: Parameter | None = Field(
        None,
        alias="intervalSeconds",
        description="Time between Timer Events to fire (in seconds). Only valid for AMP Timer trigger.",
    )
    visible_percentage_max: Parameter | None = Field(
        None,
        alias="visiblePercentageMax",
        description="A visibility trigger maximum percent visibility. Only valid for AMP Visibility trigger.",
    )
    path: str | None = Field(None, alias="path", description="GTM Trigger's API relative path.")
    visible_percentage_min: Parameter | None = Field(
        None,
        alias="visiblePercentageMin",
        description="A visibility trigger minimum percent visibility. Only valid for AMP Visibility trigger.",
    )
    vertical_scroll_percentage_list: Parameter | None = Field(
        None,
        alias="verticalScrollPercentageList",
        description="List of integer percentage values for scroll triggers. The trigger will fire when each percentage is reached when the view is scrolled vertically. Only valid for AMP scroll triggers.",
    )
    custom_event_filter: list[Condition] | None = Field(
        None,
        alias="customEventFilter",
        description="Used in the case of custom event, which is fired iff all Conditions are true.",
    )
    event_name: Parameter | None = Field(
        None,
        alias="eventName",
        description="Name of the GTM event that is fired. Only valid for Timer triggers.",
    )
    visibility_selector: Parameter | None = Field(
        None,
        alias="visibilitySelector",
        description='A visibility trigger CSS selector (i.e. "#id"). Only valid for AMP Visibility trigger.',
    )
    max_timer_length_seconds: Parameter | None = Field(
        None,
        alias="maxTimerLengthSeconds",
        description="Max time to fire Timer Events (in seconds). Only valid for AMP Timer trigger.",
    )
    horizontal_scroll_percentage_list: Parameter | None = Field(
        None,
        alias="horizontalScrollPercentageList",
        description="List of integer percentage values for scroll triggers. The trigger will fire when each percentage is reached when the view is scrolled horizontally. Only valid for AMP scroll triggers.",
    )
    tag_manager_url: str | None = Field(
        None, alias="tagManagerUrl", description="Auto generated link to the tag manager UI"
    )
    filter: list[Condition] | None = Field(
        None, alias="filter", description="The trigger will only fire iff all Conditions are true."
    )
    parameter: list[Parameter] | None = Field(None, alias="parameter", description="Additional parameters.")
    selector: Parameter | None = Field(
        None,
        alias="selector",
        description='A click trigger CSS selector (i.e. "a", "button" etc.). Only valid for AMP Click trigger.',
    )
    notes: str | None = Field(
        None, alias="notes", description="User notes on how to apply this trigger in the container."
    )
    parent_folder_id: str | None = Field(None, alias="parentFolderId", description="Parent folder id.")


class CustomTemplate(GtmModel):
    "Represents a Google Tag Manager Custom Template's contents."

    path: str | None = Field(None, alias="path", description="GTM Custom Template's API relative path.")
    gallery_reference: GalleryReference | None = Field(
        None, alias="galleryReference", description="A reference to the Community Template Gallery entry."
    )
    container_id: str | None = Field(None, alias="containerId", description="GTM Container ID.")
    tag_manager_url: str | None = Field(
        None, alias="tagManagerUrl", description="Auto generated link to the tag manager UI"
    )
    fingerprint: str | None = Field(
        None,
        alias="fingerprint",
        description="The fingerprint of the GTM Custom Template as computed at storage time. This value is recomputed whenever the template is modified.",
    )
    name: str | None = Field(None, alias="name", description="Custom Template display name.")
    template_data: str | None = Field(
        None, alias="templateData", description="The custom template in text format."
    )
    account_id: str | None = Field(None, alias="accountId", description="GTM Account ID.")
    template_id: str | None = Field(
        None,
        alias="templateId",
        description="The Custom Template ID uniquely identifies the GTM custom template.",
    )
    workspace_id: str | None = Field(None, alias="workspaceId", description="GTM Workspace ID.")


class Client(GtmModel):
    "Client"

    client_id: str | None = Field(
        None, alias="clientId", description="The Client ID uniquely identifies the GTM client."
    )
    parameter: list[Parameter] | None = Field(None, alias="parameter", description="The client's parameters.")
    priority: Int32 | None = Field(
        None, alias="priority", description="Priority determines relative firing order."
    )
    fingerprint: str | None = Field(
        None,
        alias="fingerprint",
        description="The fingerprint of the GTM Client as computed at storage time. This value is recomputed whenever the client is modified.",
    )
    type: str | None = Field(None, alias="type", description="Client type.")
    parent_folder_id: str | None = Field(None, alias="parentFolderId", description="Parent folder id.")
    notes: str | None = Field(
        None, alias="notes", description="User notes on how to apply this tag in the container."
    )
    container_id: str | None = Field(None, alias="containerId", description="GTM Container ID.")
    name: str | None = Field(None, alias="name", description="Client display name.")
    path: str | None = Field(None, alias="path", description="GTM client's API relative path.")
    tag_manager_url: str | None = Field(
        None, alias="tagManagerUrl", description="Auto generated link to the tag manager UI"
    )
    workspace_id: str | None = Field(None, alias="workspaceId", description="GTM Workspace ID.")
    account_id: str | None = Field(None, alias="accountId", description="GTM Account ID.")


class ContainerFeatures(GtmModel):
    "ContainerFeatures"

    support_zones: bool | None = Field(
        None, alias="supportZones", description="Whether this Container supports zones."
    )
    support_triggers: bool | None = Field(
        None, alias="supportTriggers", description="Whether this Container supports triggers."
    )
    support_versions: bool | None = Field(
        None, alias="supportVersions", description="Whether this Container supports Container versions."
    )
    support_user_permissions: bool | None = Field(
        None,
        alias="supportUserPermissions",
        description="Whether this Container supports user permissions managed by GTM.",
    )
    support_variables: bool | None = Field(
        None, alias="supportVariables", description="Whether this Container supports variables."
    )
    support_transformations: bool | None = Field(
        None, alias="supportTransformations", description="Whether this Container supports transformations."
    )
    support_templates: bool | None = Field(
        None, alias="supportTemplates", description="Whether this Container supports templates."
    )
    support_folders: bool | None = Field(
        None, alias="supportFolders", description="Whether this Container supports folders."
    )
    support_environments: bool | None = Field(
        None, alias="supportEnvironments", description="Whether this Container supports environments."
    )
    support_clients: bool | None = Field(
        None, alias="supportClients", description="Whether this Container supports clients."
    )
    support_tags: bool | None = Field(
        None, alias="supportTags", description="Whether this Container supports tags."
    )
    support_workspaces: bool | None = Field(
        None, alias="supportWorkspaces", description="Whether this Container supports workspaces."
    )
    support_gtag_configs: bool | None = Field(
        None, alias="supportGtagConfigs", description="Whether this Container supports Google tag config."
    )
    support_built_in_variables: bool | None = Field(
        None,
        alias="supportBuiltInVariables",
        description="Whether this Container supports built-in variables",
    )


class Parameter(GtmModel):
    "Represents a Google Tag Manager Parameter."

    value: str | None = Field(
        None,
        alias="value",
        description="A parameter's value (may contain variable references). as appropriate to the specified type.",
    )
    list_: list[Parameter] | None = Field(
        None, alias="list", description="This list parameter's parameters (keys will be ignored)."
    )
    key: str | None = Field(
        None,
        alias="key",
        description="The named key that uniquely identifies a parameter. Required for top-level parameters, as well as map values. Ignored for list values.",
    )
    map_: list[Parameter] | None = Field(
        None,
        alias="map",
        description="This map parameter's parameters (must have keys; keys must be unique).",
    )
    is_weak_reference: bool | None = Field(
        None,
        alias="isWeakReference",
        description="Whether or not a reference type parameter is strongly or weakly referenced. Only used by Transformations.",
    )
    type: ParameterType | None = Field(
        None,
        alias="type",
        description="The parameter type. Valid values are: - boolean: The value represents a boolean, represented as 'true' or 'false' - integer: The value represents a 64-bit signed integer value, in base 10 - list: A list of parameters should be specified - map: A map of parameters should be specified - template: The value represents any text; this can include variable references (even variable references that might return non-string types) - trigger_reference: The value represents a trigger, represented as the trigger id - tag_reference: The value represents a tag, represented as the tag name",
    )


class VariableFormatValue(GtmModel):
    "VariableFormatValue"

    convert_to_boolean: bool | None = Field(
        None, alias="convertToBoolean", description="The option to convert a variable value to a boolean."
    )
    convert_null_to_value: Parameter | None = Field(
        None, alias="convertNullToValue", description="The value to convert if a variable value is null."
    )
    case_conversion_type: VariableFormatValueCaseConversionType | None = Field(
        None,
        alias="caseConversionType",
        description="The option to convert a string-type variable value to either lowercase or uppercase.",
    )
    convert_undefined_to_value: Parameter | None = Field(
        None,
        alias="convertUndefinedToValue",
        description="The value to convert if a variable value is undefined.",
    )
    convert_to_number: VariableFormatValueConvertToNumber | None = Field(
        None, alias="convertToNumber", description="The option to convert a variable value to a number."
    )
    convert_false_to_value: Parameter | None = Field(
        None, alias="convertFalseToValue", description="The value to convert if a variable value is false."
    )
    convert_true_to_value: Parameter | None = Field(
        None, alias="convertTrueToValue", description="The value to convert if a variable value is true."
    )


class ZoneChildContainer(GtmModel):
    "Represents a child container of a Zone."

    public_id: str | None = Field(None, alias="publicId", description="The child container's public id.")
    nickname: str | None = Field(
        None, alias="nickname", description="The zone's nickname for the child container."
    )


class ZoneBoundary(GtmModel):
    "Represents a Zone's boundaries."

    custom_evaluation_trigger_id: list[str] | None = Field(
        None,
        alias="customEvaluationTriggerId",
        description="Custom evaluation trigger IDs. A zone will evaluate its boundary conditions when any of the listed triggers are true.",
    )
    condition: list[Condition] | None = Field(
        None, alias="condition", description="The conditions that, when conjoined, make up the boundary."
    )


class ZoneTypeRestriction(GtmModel):
    "Represents a Zone's type restrictions."

    enable: bool | None = Field(
        None, alias="enable", description="True if type restrictions have been enabled for this Zone."
    )
    whitelisted_type_id: list[str] | None = Field(
        None,
        alias="whitelistedTypeId",
        description="List of type public ids that have been whitelisted for use in this Zone.",
    )


class TagConsentSetting(GtmModel):
    "TagConsentSetting"

    consent_type: Parameter | None = Field(
        None,
        alias="consentType",
        description="The type of consents to check for during tag firing if in the consent NEEDED state. This parameter must be of type LIST where each list item is of type STRING.",
    )
    consent_status: TagConsentSettingConsentStatus | None = Field(
        None,
        alias="consentStatus",
        description="The tag's consent status. If set to NEEDED, the runtime will check that the consent types specified by the consent_type field have been granted.",
    )


class SetupTag(GtmModel):
    "Represents a reference to atag that fires before another tag in order to set up dependencies."

    stop_on_setup_failure: bool | None = Field(
        None,
        alias="stopOnSetupFailure",
        description="If true, fire the main tag if and only if the setup tag fires successfully. If false, fire the main tag regardless of setup tag firing status.",
    )
    tag_name: str | None = Field(None, alias="tagName", description="The name of the setup tag.")


class TeardownTag(GtmModel):
    "Represents a tag that fires after another tag in order to tear down dependencies."

    tag_name: str | None = Field(None, alias="tagName", description="The name of the teardown tag.")
    stop_teardown_on_failure: bool | None = Field(
        None,
        alias="stopTeardownOnFailure",
        description="If true, fire the teardown tag if and only if the main tag fires successfully. If false, fire the teardown tag regardless of main tag firing status.",
    )


class Condition(GtmModel):
    "Represents a predicate."

    type: ConditionType | None = Field(
        None, alias="type", description="The type of operator for this condition."
    )
    parameter: list[Parameter] | None = Field(
        None,
        alias="parameter",
        description="A list of named parameters (key/value), depending on the condition's type. Notes: - For binary operators, include parameters named arg0 and arg1 for specifying the left and right operands, respectively. - At this time, the left operand (arg0) must be a reference to a variable. - For case-insensitive Regex matching, include a boolean parameter named ignore_case that is set to true. If not specified or set to any other value, the matching will be case sensitive. - To negate an operator, include a boolean parameter named negate boolean parameter that is set to true.",
    )


class GalleryReference(GtmModel):
    "Represents the link between a custom template and an entry on the Community Template Gallery site."

    owner: str | None = Field(
        None, alias="owner", description="The name of the owner for the community gallery template."
    )
    gallery_template_id: str | None = Field(
        None,
        alias="galleryTemplateId",
        description="ID for the gallery template that is generated once during first sync and travels with the template redirects.",
    )
    is_modified: bool | None = Field(
        None, alias="isModified", description="If a user has manually edited the community gallery template."
    )
    host: str | None = Field(
        None, alias="host", description="The name of the host for the community gallery template."
    )
    signature: str | None = Field(
        None,
        alias="signature",
        description="The signature of the community gallery template as computed at import time. This value is recomputed whenever the template is updated from the gallery.",
    )
    version: str | None = Field(
        None, alias="version", description="The version of the community gallery template."
    )
    template_developer_id: str | None = Field(
        None,
        alias="templateDeveloperId",
        description="The developer id of the community gallery template. This value is set whenever the template is created from the gallery.",
    )
    repository: str | None = Field(
        None, alias="repository", description="The name of the repository for the community gallery template."
    )


ContainerVersion.model_rebuild()
Container.model_rebuild()
Folder.model_rebuild()
BuiltInVariable.model_rebuild()
Transformation.model_rebuild()
Variable.model_rebuild()
Zone.model_rebuild()
GtagConfig.model_rebuild()
Tag.model_rebuild()
Trigger.model_rebuild()
CustomTemplate.model_rebuild()
Client.model_rebuild()
ContainerFeatures.model_rebuild()
Parameter.model_rebuild()
VariableFormatValue.model_rebuild()
ZoneChildContainer.model_rebuild()
ZoneBoundary.model_rebuild()
ZoneTypeRestriction.model_rebuild()
TagConsentSetting.model_rebuild()
SetupTag.model_rebuild()
TeardownTag.model_rebuild()
Condition.model_rebuild()
GalleryReference.model_rebuild()
