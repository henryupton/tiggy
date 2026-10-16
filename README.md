# Tiggy

Declarative Google Tag Manager. Describe a GTM container in YAML, then validate, plan and apply it the way
[Tundra](https://github.com/henryupton/tundra) does for Snowflake permissions.

Early days: nothing works yet.

```bash
pip install tiggy-cli

tiggy validate spec/       # every reference between tags, triggers and variables resolves
tiggy spec-test spec/      # the spec matches the live container
tiggy run spec/ --dry      # the GTM changes that would make it match
tiggy run spec/            # make them, in a new workspace
tiggy import <container>   # write a live container out as a spec
```
