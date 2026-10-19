___TERMS_OF_SERVICE___

Stand-in for the notice the Template Editor writes at the top of every gallery
template, pointing at Google Tag Manager's Community Template Gallery Developer
Terms of Service (https://developers.google.com/tag-manager/gallery-tos). Real
templates carry Google's own wording here.


___INFO___

{
  "type": "TAG",
  "id": "cvt_temp_public_id",
  "version": 1,
  "displayName": "Cookiebot CMP",
  "categories": [
    "UTILITY"
  ],
  "brand": {
    "id": "github.com_example-cmp",
    "displayName": "example-cmp",
    "thumbnail": "data:image/png;base64,iVBORw0KGgo="
  },
  "description": "Loads the consent banner and sets Google Consent Mode defaults before any other tag fires.",
  "containerContexts": [
    "WEB"
  ]
}


___TEMPLATE_PARAMETERS___

[
  {
    "type": "TEXT",
    "name": "serial",
    "displayName": "Domain group ID",
    "simpleValueType": true,
    "valueValidators": [
      {
        "type": "NON_EMPTY"
      }
    ]
  },
  {
    "type": "CHECKBOX",
    "name": "enableConsentMode",
    "displayName": "Enable Google Consent Mode",
    "simpleValueType": true
  },
  {
    "type": "TEXT",
    "name": "waitForUpdate",
    "displayName": "Wait for update (ms)",
    "simpleValueType": true,
    "defaultValue": "500"
  },
  {
    "type": "PARAM_TABLE",
    "name": "defaultConsent",
    "displayName": "Default consent state by region",
    "paramTableColumns": [
      {
        "param": {
          "type": "TEXT",
          "name": "region",
          "displayName": "region",
          "simpleValueType": true
        },
        "isUnique": true
      },
      {
        "param": {
          "type": "TEXT",
          "name": "ad_storage",
          "displayName": "ad_storage",
          "simpleValueType": true
        },
        "isUnique": false
      },
      {
        "param": {
          "type": "TEXT",
          "name": "analytics_storage",
          "displayName": "analytics_storage",
          "simpleValueType": true
        },
        "isUnique": false
      },
      {
        "param": {
          "type": "TEXT",
          "name": "ad_user_data",
          "displayName": "ad_user_data",
          "simpleValueType": true
        },
        "isUnique": false
      },
      {
        "param": {
          "type": "TEXT",
          "name": "ad_personalization",
          "displayName": "ad_personalization",
          "simpleValueType": true
        },
        "isUnique": false
      }
    ]
  },
  {
    "type": "CHECKBOX",
    "name": "adsDataRedaction",
    "displayName": "Redact ads data",
    "simpleValueType": true
  },
  {
    "type": "CHECKBOX",
    "name": "urlPassthrough",
    "displayName": "Pass through URL parameters",
    "simpleValueType": true
  }
]


___SANDBOXED_JS_FOR_WEB_TEMPLATE___

const gtagSet = require('gtagSet');
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


___WEB_PERMISSIONS___

[
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
                    "boolean": false
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
                    "string": "analytics_storage"
                  },
                  {
                    "type": 8,
                    "boolean": false
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
                    "string": "ad_user_data"
                  },
                  {
                    "type": 8,
                    "boolean": false
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
                    "string": "ad_personalization"
                  },
                  {
                    "type": 8,
                    "boolean": false
                  },
                  {
                    "type": 8,
                    "boolean": true
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
        "publicId": "write_data_layer",
        "versionId": "1"
      },
      "param": [
        {
          "key": "keyPatterns",
          "value": {
            "type": 2,
            "listItem": [
              {
                "type": 1,
                "string": "ads_data_redaction"
              },
              {
                "type": 1,
                "string": "url_passthrough"
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
                "string": "https://consent.example.net/*"
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

Created on 02/02/2026, 16:40:05


