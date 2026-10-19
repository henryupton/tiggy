# Third-party container exports

These four files are public GTM container exports, copied byte for byte from their upstream
repositories and **unmodified** (each file's git blob SHA equals the upstream blob at the commit
below). They are redistributed under their own licences, whose full texts are in `licenses/`.
Tiggy's MIT licence does not cover them.

| File | Source | Path | Commit | Licence |
| --- | --- | --- | --- | --- |
| `stape_woocommerce_meta_web.json` | https://github.com/stape-io/woocommerce-gtm-container-templates | `fb-web.json` | `66cdea97c67a467f2eac01ec2622858d45675d87` | Apache-2.0, `licenses/stape-io_woocommerce-gtm-container-templates.LICENSE` |
| `stape_shopify_meta_ga4_ads_server.json` | https://github.com/stape-io/shopify-gtm-container-templates | `fb-ga4-gads-server.json` | `5b909f39e3d7c53df00dee63ef806f1eed80e1d6` | Apache-2.0, `licenses/stape-io_shopify-gtm-container-templates.LICENSE` |
| `stape_magento_meta_web.json` | https://github.com/stape-io/magento-gtm-container-templates | `fb-web.json` | `6013a88a9f51e4aa23ecd7d534c30bb64192b5ec` | Apache-2.0, `licenses/stape-io_magento-gtm-container-templates.LICENSE` |
| `bounteous_youtube_tracking.json` | https://github.com/Bounteous-Inc/youtube-google-analytics | `luna-youtube-tracking.json` | `4a77e280a1663b4cf8d43ad8719e5e1462af2be9` | MIT, `licenses/Bounteous-Inc_youtube-google-analytics.LICENSE.md` |

Each commit is the latest upstream commit touching that path as of 2026-10-19. To re-verify a file:

```bash
git hash-object tests/fixtures/third_party/stape_woocommerce_meta_web.json
gh api 'repos/stape-io/woocommerce-gtm-container-templates/contents/fb-web.json?ref=66cdea97c67a467f2eac01ec2622858d45675d87' --jq .sha
```

The licence files are the upstream `LICENSE` (or `LICENSE.MD`) at the same commits, also unmodified.
None of the upstream repositories carries a NOTICE file.
