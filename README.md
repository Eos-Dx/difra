# eosdx-difra

Standalone `hardware.difra` package extracted from `xrd-analysis`.

## Layout

- `src/` contains the installable `hardware.difra` package.
- `tests/` contains standalone smoke tests.

## Scope

This repo contains the DiFRA GUI, session workflow, hardware integration, and bundled resources.

`hardware.difra` still expects the sibling namespace packages `hardware.container` and `hardware.protocol` to be available when you run the full application.

## Development

Install in editable mode:

```bash
pip install -e .
pytest tests
```
