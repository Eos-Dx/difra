# eosdx-difra

Standalone `difra` package extracted from `xrd-analysis`.

## Layout

- `src/` contains the installable `difra` package.
- `tests/` contains standalone smoke tests.

## Scope

This repo contains the DiFRA GUI, session workflow, hardware integration, and bundled resources.

The launcher scripts in `src/difra/bin/` automatically ensure
`eosdx-container` and `eosdx-protocol` are installed in the target Python
environment. If either package is missing, the launchers install it from the
official GitHub repositories with `pip` before starting DiFRA.

## Development

Install in editable mode:

```bash
pip install -e .
pytest
```
