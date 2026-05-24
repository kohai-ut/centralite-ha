---
name: Bug report
about: Something is broken or not working as expected
title: ''
labels: bug
assignees: ''
---

## What happened

A clear description of the bug.

## Expected behavior

What you expected to happen instead.

## Reproduction steps

1. ...
2. ...
3. ...

## Environment

- **Centralite Lighting version**: (e.g. v2.0.0a1)
- **Home Assistant Core version**: (e.g. 2026.5.4)
- **HA install type**: (HA OS / Container / Core / Supervised)
- **System type**: Elegance / JetStream / eLite
- **Bridge firmware** (if known):
- **Serial adapter** (e.g. Prolific PL2303, FTDI FT232R):

## Logs

Enable debug logging in `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.centralite: debug
```

Restart HA, reproduce the issue, then paste the relevant log excerpt here (redact any secrets):

```
<paste logs>
```

## Additional context

Anything else helpful — screenshots, related Repairs entries, etc.
