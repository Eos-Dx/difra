# eosdx-difra

Primary standalone DiFRA application repository for Eos-Dx.

This repo contains the installable `difra` package, the desktop GUI, hardware
integration layers, and the gRPC sidecar used to drive the instrument workflow.
It is intentionally split out from the old `xrd-analysis` tree so it can be
developed and released on its own.

## Repository Role

- `difra` is the application layer.
- It depends on the standalone `container` package for HDF5 container handling.
- It depends on the standalone `protocol` package for command schemas and gRPC
  protocol assets.

## Layout

- `src/difra/` contains the installable Python package.
- `src/difra/gui/` contains the desktop application.
- `src/difra/grpc_server/` contains the Python gRPC sidecar.
- `src/difra/bin/` contains launch scripts for the dual-environment runtime.
- `tests/` contains standalone smoke and guardrail tests.

## Runtime Expectations

The launcher scripts in `src/difra/bin/` force-refresh the runtime dependency
packages from GitHub on every run so the environment is always brought up to
date before DiFRA starts.

Managed runtime packages:

- `container`
- `protocol`
- `xrdanalysis`

The refresh step uses `pip install --upgrade --force-reinstall --no-cache-dir`
against the GitHub source archives by default.

For direct source imports outside the launcher, local sibling checkouts can
still be resolved during development.

The default launcher expects:

- a GUI conda environment such as `eosdx13`
- a legacy detector sidecar environment such as `ulster37`

## Development

Install in editable mode and run the standalone checks:

```bash
pip install -e .
pytest
```

For a full local startup smoke test, launch:

```bash
bash src/difra/bin/run_difra_dual_env.sh
```
