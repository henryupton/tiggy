___TERMS_OF_SERVICE___

Stand-in for the notice the Template Editor writes at the top of every gallery
template, pointing at Google Tag Manager's Community Template Gallery Developer
Terms of Service (https://developers.google.com/tag-manager/gallery-tos). Real
templates carry Google's own wording here.


___INFO___

{
  "type": "TAG",
  "id": "cvt_T3STC",
  "version": 1,
  "displayName": "Meta Conversions API",
  "categories": [
    "ADVERTISING",
    "CONVERSIONS"
  ],
  "brand": {
    "id": "github.com_example-pixels",
    "displayName": "example-pixels",
    "thumbnail": "data:image/png;base64,iVBORw0KGgo="
  },
  "description": "Sends server events to the Meta Conversions API.",
  "containerContexts": [
    "SERVER"
  ]
}


___TEMPLATE_PARAMETERS___

[
  {
    "type": "SELECT",
    "name": "inheritEventName",
    "displayName": "Event name",
    "selectItems": [
      {
        "value": "inherit",
        "displayValue": "Inherit from client"
      },
      {
        "value": "override",
        "displayValue": "Override"
      }
    ],
    "simpleValueType": true,
    "subParams": [
      {
        "type": "RADIO",
        "name": "eventName",
        "displayName": "Event type",
        "radioItems": [
          {
            "value": "standard",
            "displayValue": "Standard",
            "subParams": [
              {
                "type": "SELECT",
                "name": "eventNameStandard",
                "displayName": "",
                "selectItems": [
                  {
                    "value": "PageView",
                    "displayValue": "PageView"
                  },
                  {
                    "value": "Purchase",
                    "displayValue": "Purchase"
                  },
                  {
                    "value": "CompleteRegistration",
                    "displayValue": "CompleteRegistration"
                  },
                  {
                    "value": "Lead",
                    "displayValue": "Lead"
                  },
                  {
                    "value": "ViewContent",
                    "displayValue": "ViewContent"
                  }
                ],
                "simpleValueType": true
              }
            ]
          },
          {
            "value": "custom",
            "displayValue": "Custom",
            "subParams": [
              {
                "type": "TEXT",
                "name": "eventNameCustom",
                "displayName": "",
                "simpleValueType": true
              }
            ]
          }
        ],
        "simpleValueType": true,
        "enablingConditions": [
          {
            "paramName": "inheritEventName",
            "paramValue": "override",
            "type": "EQUALS"
          }
        ]
      }
    ]
  },
  {
    "type": "SELECT",
    "name": "actionSource",
    "displayName": "Action source",
    "selectItems": [
      {
        "value": "website",
        "displayValue": "website"
      },
      {
        "value": "app",
        "displayValue": "app"
      },
      {
        "value": "email",
        "displayValue": "email"
      },
      {
        "value": "other",
        "displayValue": "other"
      }
    ],
    "simpleValueType": true,
    "defaultValue": "website"
  },
  {
    "type": "TEXT",
    "name": "accessToken",
    "displayName": "API access token",
    "simpleValueType": true,
    "valueValidators": [
      {
        "type": "NON_EMPTY"
      }
    ]
  },
  {
    "type": "TEXT",
    "name": "pixelId",
    "displayName": "Pixel ID",
    "simpleValueType": true,
    "valueValidators": [
      {
        "type": "NON_EMPTY"
      }
    ]
  },
  {
    "type": "CHECKBOX",
    "name": "enableMultipixelSetup",
    "displayName": "",
    "checkboxText": "Send to more than one pixel",
    "simpleValueType": true
  },
  {
    "type": "TEXT",
    "name": "testId",
    "displayName": "Test event code",
    "simpleValueType": true
  },
  {
    "type": "TEXT",
    "name": "itemIdKey",
    "displayName": "Item ID key",
    "simpleValueType": true,
    "canBeEmptyString": true
  },
  {
    "type": "CHECKBOX",
    "name": "generateFbp",
    "displayName": "",
    "checkboxText": "Set _fbp when it is missing",
    "simpleValueType": true,
    "defaultValue": true
  },
  {
    "type": "CHECKBOX",
    "name": "useHttpOnlyCookie",
    "displayName": "",
    "checkboxText": "HttpOnly cookies",
    "simpleValueType": true
  },
  {
    "type": "CHECKBOX",
    "name": "enableEventEnhancement",
    "displayName": "",
    "checkboxText": "Fill user data from the event",
    "simpleValueType": true,
    "defaultValue": true
  },
  {
    "type": "CHECKBOX",
    "name": "useOptimisticScenario",
    "displayName": "",
    "checkboxText": "Succeed without waiting for Meta",
    "simpleValueType": true
  },
  {
    "type": "GROUP",
    "name": "serverEventDataListGroup",
    "displayName": "Server event data",
    "groupStyle": "ZIPPY_CLOSED",
    "subParams": [
      {
        "type": "SIMPLE_TABLE",
        "name": "serverEventDataList",
        "displayName": "",
        "simpleTableColumns": [
          {
            "defaultValue": "",
            "displayName": "Name",
            "name": "name",
            "type": "TEXT"
          },
          {
            "defaultValue": "",
            "displayName": "Value",
            "name": "value",
            "type": "TEXT"
          }
        ]
      }
    ]
  },
  {
    "type": "GROUP",
    "name": "userDataListGroup",
    "displayName": "User data",
    "groupStyle": "ZIPPY_CLOSED",
    "subParams": [
      {
        "type": "SIMPLE_TABLE",
        "name": "userDataList",
        "displayName": "",
        "simpleTableColumns": [
          {
            "defaultValue": "",
            "displayName": "Name",
            "name": "name",
            "type": "TEXT"
          },
          {
            "defaultValue": "",
            "displayName": "Value",
            "name": "value",
            "type": "TEXT"
          }
        ]
      }
    ]
  },
  {
    "type": "GROUP",
    "name": "customDataListGroup",
    "displayName": "Custom data",
    "groupStyle": "ZIPPY_CLOSED",
    "subParams": [
      {
        "type": "SIMPLE_TABLE",
        "name": "customDataList",
        "displayName": "",
        "simpleTableColumns": [
          {
            "defaultValue": "",
            "displayName": "Name",
            "name": "name",
            "type": "TEXT"
          },
          {
            "defaultValue": "",
            "displayName": "Value",
            "name": "value",
            "type": "TEXT"
          }
        ]
      }
    ]
  },
  {
    "type": "GROUP",
    "name": "consentSettingsGroup",
    "displayName": "Consent",
    "groupStyle": "ZIPPY_CLOSED",
    "subParams": [
      {
        "type": "RADIO",
        "name": "adStorageConsent",
        "displayName": "",
        "radioItems": [
          {
            "value": "optional",
            "displayValue": "optional"
          },
          {
            "value": "required",
            "displayValue": "required"
          }
        ],
        "simpleValueType": true,
        "defaultValue": "optional"
      }
    ]
  },
  {
    "type": "GROUP",
    "name": "logsGroup",
    "displayName": "Logging",
    "groupStyle": "ZIPPY_CLOSED",
    "subParams": [
      {
        "type": "RADIO",
        "name": "logType",
        "displayName": "",
        "radioItems": [
          {
            "value": "no",
            "displayValue": "no"
          },
          {
            "value": "debug",
            "displayValue": "debug"
          },
          {
            "value": "always",
            "displayValue": "always"
          }
        ],
        "simpleValueType": true,
        "defaultValue": "debug"
      }
    ]
  }
]


___SANDBOXED_JS_FOR_SERVER___

const JSON = require('JSON');
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


___SERVER_PERMISSIONS___

[
  {
    "instance": {
      "key": {
        "publicId": "read_event_data",
        "versionId": "1"
      },
      "param": [
        {
          "key": "eventDataAccess",
          "value": {
            "type": 1,
            "string": "any"
          }
        }
      ]
    },
    "clientAnnotations": {
      "isEditedByUser": true
    },
    "isRequired": true
  },
  {
    "instance": {
      "key": {
        "publicId": "get_cookies",
        "versionId": "1"
      },
      "param": [
        {
          "key": "cookieAccess",
          "value": {
            "type": 1,
            "string": "specific"
          }
        },
        {
          "key": "cookieNames",
          "value": {
            "type": 2,
            "listItem": [
              {
                "type": 1,
                "string": "_fbp"
              },
              {
                "type": 1,
                "string": "_fbc"
              }
            ]
          }
        }
      ]
    },
    "clientAnnotations": {
      "isEditedByUser": true
    },
    "isRequired": true
  },
  {
    "instance": {
      "key": {
        "publicId": "set_cookies",
        "versionId": "1"
      },
      "param": [
        {
          "key": "allowedCookies",
          "value": {
            "type": 2,
            "listItem": [
              {
                "type": 3,
                "mapKey": [
                  {
                    "type": 1,
                    "string": "name"
                  },
                  {
                    "type": 1,
                    "string": "domain"
                  },
                  {
                    "type": 1,
                    "string": "path"
                  },
                  {
                    "type": 1,
                    "string": "secure"
                  },
                  {
                    "type": 1,
                    "string": "session"
                  }
                ],
                "mapValue": [
                  {
                    "type": 1,
                    "string": "_fbp"
                  },
                  {
                    "type": 1,
                    "string": "*"
                  },
                  {
                    "type": 1,
                    "string": "*"
                  },
                  {
                    "type": 1,
                    "string": "any"
                  },
                  {
                    "type": 1,
                    "string": "any"
                  }
                ]
              }
            ]
          }
        }
      ]
    },
    "clientAnnotations": {
      "isEditedByUser": true
    },
    "isRequired": true
  },
  {
    "instance": {
      "key": {
        "publicId": "send_http",
        "versionId": "1"
      },
      "param": [
        {
          "key": "allowedUrls",
          "value": {
            "type": 1,
            "string": "specific"
          }
        },
        {
          "key": "urls",
          "value": {
            "type": 2,
            "listItem": [
              {
                "type": 1,
                "string": "https://graph.example.net/"
              }
            ]
          }
        }
      ]
    },
    "clientAnnotations": {
      "isEditedByUser": true
    },
    "isRequired": true
  },
  {
    "instance": {
      "key": {
        "publicId": "logging",
        "versionId": "1"
      },
      "param": [
        {
          "key": "environments",
          "value": {
            "type": 1,
            "string": "all"
          }
        }
      ]
    },
    "clientAnnotations": {
      "isEditedByUser": true
    },
    "isRequired": true
  },
  {
    "instance": {
      "key": {
        "publicId": "read_container_data",
        "versionId": "1"
      },
      "param": []
    },
    "isRequired": true
  }
]


___TESTS___

scenarios: []


___NOTES___

Created on 09/09/2026, 13:27:52


