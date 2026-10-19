___TERMS_OF_SERVICE___

Stand-in for the notice the Template Editor writes at the top of every gallery
template, pointing at Google Tag Manager's Community Template Gallery Developer
Terms of Service (https://developers.google.com/tag-manager/gallery-tos). Real
templates carry Google's own wording here.


___INFO___

{
  "type": "TAG",
  "id": "cvt_T3STP",
  "version": 1,
  "displayName": "Meta Pixel",
  "categories": [
    "ADVERTISING",
    "CONVERSIONS"
  ],
  "brand": {
    "id": "github.com_example-pixels",
    "displayName": "example-pixels",
    "thumbnail": "data:image/png;base64,iVBORw0KGgo="
  },
  "description": "Loads the Meta Pixel and sends a standard or custom event.",
  "containerContexts": [
    "WEB"
  ]
}


___TEMPLATE_PARAMETERS___

[
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
    "type": "RADIO",
    "name": "eventName",
    "displayName": "Event",
    "radioItems": [
      {
        "value": "standard",
        "displayValue": "Standard"
      },
      {
        "value": "custom",
        "displayValue": "Custom"
      }
    ],
    "simpleValueType": true
  },
  {
    "type": "SELECT",
    "name": "standardEventName",
    "displayName": "Standard event",
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
      }
    ],
    "simpleValueType": true
  },
  {
    "type": "TEXT",
    "name": "customEventName",
    "displayName": "Custom event",
    "simpleValueType": true
  },
  {
    "type": "SIMPLE_TABLE",
    "name": "objectPropertyList",
    "displayName": "Object properties",
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
  },
  {
    "type": "CHECKBOX",
    "name": "consent",
    "displayName": "Honour ad_storage consent",
    "simpleValueType": true
  },
  {
    "type": "CHECKBOX",
    "name": "advancedMatching",
    "displayName": "Advanced matching",
    "simpleValueType": true
  }
]


___SANDBOXED_JS_FOR_WEB_TEMPLATE___

const callInWindow = require('callInWindow');
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


___WEB_PERMISSIONS___

[
  {
    "instance": {
      "key": {
        "publicId": "access_globals",
        "versionId": "1"
      },
      "param": [
        {
          "key": "keys",
          "value": {
            "type": 2,
            "listItem": [
              {
                "type": 3,
                "mapKey": [
                  {
                    "type": 1,
                    "string": "key"
                  },
                  {
                    "type": 1,
                    "string": "read"
                  },
                  {
                    "type": 1,
                    "string": "write"
                  },
                  {
                    "type": 1,
                    "string": "execute"
                  }
                ],
                "mapValue": [
                  {
                    "type": 1,
                    "string": "fbq"
                  },
                  {
                    "type": 8,
                    "boolean": true
                  },
                  {
                    "type": 8,
                    "boolean": true
                  },
                  {
                    "type": 8,
                    "boolean": true
                  }
                ]
              },
              {
                "type": 3,
                "mapKey": [
                  {
                    "type": 1,
                    "string": "key"
                  },
                  {
                    "type": 1,
                    "string": "read"
                  },
                  {
                    "type": 1,
                    "string": "write"
                  },
                  {
                    "type": 1,
                    "string": "execute"
                  }
                ],
                "mapValue": [
                  {
                    "type": 1,
                    "string": "_fbq"
                  },
                  {
                    "type": 8,
                    "boolean": true
                  },
                  {
                    "type": 8,
                    "boolean": true
                  },
                  {
                    "type": 8,
                    "boolean": false
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
        "publicId": "access_consent",
        "versionId": "1"
      },
      "param": [
        {
          "key": "consentTypes",
          "value": {
            "type": 2,
            "listItem": [
              {
                "type": 3,
                "mapKey": [
                  {
                    "type": 1,
                    "string": "consentType"
                  },
                  {
                    "type": 1,
                    "string": "read"
                  },
                  {
                    "type": 1,
                    "string": "write"
                  }
                ],
                "mapValue": [
                  {
                    "type": 1,
                    "string": "ad_storage"
                  },
                  {
                    "type": 8,
                    "boolean": true
                  },
                  {
                    "type": 8,
                    "boolean": false
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
        "publicId": "inject_script",
        "versionId": "1"
      },
      "param": [
        {
          "key": "urls",
          "value": {
            "type": 2,
            "listItem": [
              {
                "type": 1,
                "string": "https://pixel.example.net/events.js"
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
            "string": "debug"
          }
        }
      ]
    },
    "isRequired": true
  }
]


___TESTS___

scenarios: []


___NOTES___

Created on 21/11/2025, 08:03:19


