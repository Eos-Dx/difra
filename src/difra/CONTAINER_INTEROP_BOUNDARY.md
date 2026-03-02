# DIFRA Container Interop Boundary

This document defines the long-term language-neutral adapter contract for writing
and validating DIFRA `v0_2` containers outside the Python GUI codebase.

## Goals

- use a strict JSON manifest as the only control-plane input,
- pass raw arrays or payloads by file path, not embedded binary blobs,
- produce one validated `.nxs.h5` output file,
- keep conformance anchored to golden `v0_2` containers.

## Contract Summary

Input artifacts:

1. `manifest.json`
2. referenced raw files on disk (`.npy`, `.txt`, `.poni`, images, or other payloads)

Output artifact:

1. one DIFRA container file (`.nxs.h5`)

Validation gate:

1. the produced container must pass `scripts/validate_container.py`

## Manifest Envelope

```json
{
  "manifest_version": "1.0",
  "target_schema_version": "0.2",
  "container_kind": "technical",
  "output_path": "/abs/path/to/output/technical_abc.nxs.h5",
  "producer": {
    "software": "difra-adapter-csharp",
    "version": "1.0.0"
  },
  "payload": {}
}
```

Rules:

- `manifest_version` versions the adapter contract, not the HDF5 schema.
- `target_schema_version` must be `0.2`.
- `container_kind` is `technical` or `session`.
- `output_path` is the final container path.
- `payload` contains the schema-specific write inputs.

## Technical Container Payload

```json
{
  "manifest_version": "1.0",
  "target_schema_version": "0.2",
  "container_kind": "technical",
  "output_path": "/abs/path/to/output/technical_abc.nxs.h5",
  "producer": {
    "software": "difra-adapter-csharp",
    "version": "1.0.0"
  },
  "payload": {
    "distance_cm": 17.0,
    "container_id": "abc12345",
    "detectors": [
      {
        "id": "det-01",
        "alias": "primary",
        "type": "pixet",
        "size": {
          "width": 256,
          "height": 256
        },
        "pixel_size_um": [55.0, 55.0],
        "faulty_pixels_path": "/abs/path/to/faulty_pixels.npy"
      }
    ],
    "active_detector_ids": ["det-01"],
    "poni_files": [
      {
        "alias": "primary",
        "path": "/abs/path/to/primary.poni",
        "distance_cm": 17.0,
        "operator_confirmed": true
      }
    ],
    "technical_events": [
      {
        "event_index": 1,
        "technical_type": "DARK",
        "timestamp": "2026-03-02T10:00:00Z",
        "detectors": [
          {
            "alias": "primary",
            "detector_id": "det-01",
            "timestamp": "2026-03-02T10:00:00Z",
            "processed_signal_path": "/abs/path/to/dark_primary.npy",
            "raw_files": [
              "/abs/path/to/dark_primary.txt"
            ]
          }
        ]
      }
    ]
  }
}
```

Rules:

- numeric array payloads should be passed as `.npy` when possible,
- text payloads are passed as file paths and copied into the container,
- file paths must be absolute,
- detector aliases must match the aliases used in event payloads and PONI payloads,
- the validator reads `schema_version` only from container metadata.

## Session Container Payload

```json
{
  "manifest_version": "1.0",
  "target_schema_version": "0.2",
  "container_kind": "session",
  "output_path": "/abs/path/to/output/session_abc.nxs.h5",
  "producer": {
    "software": "difra-adapter-csharp",
    "version": "1.0.0"
  },
  "payload": {
    "session_metadata": {
      "sample_id": "sample-001",
      "study_name": "demo-study",
      "project_id": "proj-01",
      "session_id": "sess-01",
      "acquisition_date": "2026-03-02",
      "operator_id": "op-01",
      "site_id": "site-a",
      "machine_name": "DIFRA-01",
      "beam_energy_keV": 12.4
    },
    "technical_container_path": "/abs/path/to/technical_abc.nxs.h5",
    "images": [
      {
        "image_index": 1,
        "image_type": "sample",
        "data_path": "/abs/path/to/image.npy"
      }
    ],
    "points": [],
    "measurements": [],
    "analytical_measurements": []
  }
}
```

Rules:

- session creation should copy technical calibration from `technical_container_path`,
- points, measurements, and analytical measurements should use stable IDs only after
  the writer creates them,
- measurement arrays still travel by path, not inline JSON blobs.

## Golden Containers

Keep a small fixture set under a stable folder such as:

- `tests/golden_containers/v0_2/technical_valid.nxs.h5`
- `tests/golden_containers/v0_2/session_valid.nxs.h5`

Use them for:

- C# reader conformance,
- Python adapter regression tests,
- version-specific validator checks.

Each golden fixture should include:

- the container file,
- its source manifest,
- a short README describing what is intentionally present.

## Validator Entry Point

Use:

```bash
python3 /Users/sad/dev/xrd-analysis/src/difra/scripts/validate_container.py /path/to/container.nxs.h5
```

Examples:

```bash
python3 /Users/sad/dev/xrd-analysis/src/difra/scripts/validate_container.py /path/to/container.nxs.h5 --kind technical
python3 /Users/sad/dev/xrd-analysis/src/difra/scripts/validate_container.py /path/to/container.nxs.h5 --json
```

For a packaged executable, build:

```bash
/Users/sad/dev/xrd-analysis/src/difra/scripts/build_container_validator_executable.sh
```

This requires `PyInstaller` to be installed locally.

Notes:

- the current validator supports only `v0_2`,
- Windows `.exe` must be built on Windows; PyInstaller does not cross-compile from macOS,
- macOS builds produce a native macOS CLI executable, not a `.exe`.
