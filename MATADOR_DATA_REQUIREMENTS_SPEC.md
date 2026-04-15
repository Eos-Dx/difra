# DIFRA -> Matador Data Requirements and Transfer Specification

## Purpose

This document describes what data DIFRA needs Matador to accept, store, and process.
It is written for developers and architects who are not directly involved in daily analytics or operator work.

The intent is not to prescribe DIFRA internal acquisition workflow.
DIFRA may collect data in its own way, as long as it can prepare and transfer the required data to Matador according to the agreed contract.

This document defines **what data and relations must be preserved**.
It does **not** define:

- how DIFRA organizes its local acquisition flow
- how DIFRA stores data locally before transfer
- how Matador stores data internally after ingestion
- how Matador implements its internal PostgreSQL / object storage / indexing model

Those are implementation concerns of the respective systems.
The shared concern is the transfer contract and the semantics of the transferred data.

## Responsibility Split

The intended responsibility split is:

- **DIFRA / Sergey** defines what analytical data must be available in Matador
- **Matador / Alex** defines how that data is accepted, validated, and mapped into the Matador platform
- **Validation / Oleksii** verifies that the agreed contract satisfies the business and analytics need

In short:

- requirements = **what must be preserved**
- contract = **how it is transferred**
- implementation = **how each side realizes its own part**

This document therefore focuses on the DIFRA-side data requirements, not on Matador internal storage design.

## Business Trigger

The transfer trigger is:

- each time a sample is measured, DIFRA must be able to send the relevant calibration data and the sample measurement data to Matador

In practice, the same calibration may be sent multiple times together with different sample measurements.
DIFRA must not need to check Matador internal state before upload.
How Matador stores or deduplicates equivalent calibration uploads is a Matador-side implementation decision, but analyst-facing data must remain clear and usable.

## Session Meaning

For the purpose of the DIFRA -> Matador discussion, a session is a logical grouping of:

- one calibration setup
- the related sample measurements acquired against that calibration

Operational actions such as session opening, closing, or reopening are Matador-side workflow decisions.
They may be useful for QC, approval, or recovery handling, but they do not change the underlying DIFRA data requirement:

- calibration and related specimen measurements must remain logically connected

## Validation Scope

At this stage, the required Matador-side validation is primarily transfer and ingestion validation, for example:

- required files are present
- hashes and sizes match
- required metadata is present
- calibration and measurement packages are structurally valid
- pairing between uploaded artifacts is consistent

This document does not require Matador to perform full scientific-quality validation of the data itself.
Scientific QC may remain a hardware / analytics / analyst concern unless explicitly expanded later.

## Main Data Categories

DIFRA transfers two primary analytical categories:

1. Calibration data
2. Sample measurement data

In addition, DIFRA may transfer an HDF5 container as a paired analytical artifact for the same measurement.

The HDF5 container is useful and operator-friendly, but it is not the only trusted source.
The raw measurement and calibration files, together with the structured JSON sidecars, must remain sufficient for ingestion and traceability.

## Transfer Package Layout

The current DIFRA -> Matador transfer model uses separate physical payloads:

1. calibration ZIP
2. measurement ZIP
3. optional paired HDF5 container

The HDF5 container is uploaded as a separate file and is not expected to live inside either ZIP archive.

### Calibration ZIP layout

The calibration ZIP contains files for one calibration distance.
The current packaging model is a flat ZIP root, with no nested folders inside the archive.

The calibration ZIP root contains:

- calibration raw files (`.txt`, `.txt.dsc`)
- derived calibration arrays (`.npy`)
- calibration PONI files (`.poni`) for `AGBH`
- legacy compatibility summary `technical_meta_<day>_<distance>.json`
- `calibrationData.json`
- `metadata.json`

Calibration filenames inside the package must be unique.
In the current DIFRA implementation this uniqueness is achieved by encoding enough context into the filename, including:

- calibration scan type
- distance token
- technical event index
- timestamp token
- integration token
- detector alias

### Measurement ZIP layout

The measurement ZIP contains data for one measured specimen bundle.
The current packaging model is a flat ZIP root, with no nested folders inside the archive.

The measurement ZIP root contains:

- measurement raw files (`.txt`, `.txt.dsc`)
- derived measurement arrays (`.npy`)
- attenuation-related supporting files, where present
- specimen-specific state file `<bundle>_state.json`
- `measurementData.json`
- `metadata.json`

Measurement filenames inside the package must be unique.
In the current DIFRA implementation this uniqueness is achieved by encoding enough context into the filename, including:

- bundle identifier
- specimen point identity
- measurement-group identity
- point coordinates
- timestamp token
- detector alias

If the term `SpecimenSpecificBundleState.json` is used in discussion or documentation, it should be understood as the logical document class.
The actual current DIFRA filename remains specimen-specific:

- `<bundle>_state.json`

## Current Filename Patterns

This section describes the current DIFRA export naming rules for files placed into calibration and measurement packages.
These rules are informative and reflect the current implementation.

### Calibration file naming

Current calibration files are built from this base pattern:

```text
<tech_prefix>_<distance_token>_<event_index>_<timestamp_token>_<integration_token>_<detector_alias>
```

Meaning of tokens:

- `tech_prefix`: technical scan prefix
- `distance_token`: detector distance token such as `17cm`
- `event_index`: zero-padded technical event index such as `001`
- `timestamp_token`: normalized timestamp such as `20260414_102030`
- `integration_token`: integration/exposure token such as `60s` or `300s`
- `detector_alias`: detector alias such as `PRIMARY` or `SECONDARY`

Current technical scan prefixes are:

- `DARK` -> `DC`
- `EMPTY` -> `Empty`
- `BACKGROUND` -> `Bg`
- `AGBH` -> `AgBH`

Examples:

```text
DC_17cm_001_20260414_102030_60s_PRIMARY.npy
DC_17cm_001_20260414_102030_60s_PRIMARY.txt
DC_17cm_001_20260414_102030_60s_PRIMARY.txt.dsc

AgBH_17cm_002_20260414_102530_300s_PRIMARY.npy
AgBH_17cm_002_20260414_102530_300s_PRIMARY.txt
AgBH_17cm_002_20260414_102530_300s_PRIMARY.txt.dsc
AgBH_17cm_002_20260414_102530_300s_PRIMARY.poni
```

Notes:

- the `.poni` filename must reuse the corresponding exported `AGBH` `.npy` stem and replace `.npy` with `.poni`
- the legacy compatibility JSON is named separately as:

```text
technical_meta_<day_token>_<distance_token>.json
```

### Regular measurement file naming

Current regular specimen measurement files are built from this base pattern:

```text
<measurement_bundle_base>_<point_uid>_<measurement_group>_<x_mm>_<y_mm>_<timestamp_token>_<detector_alias>
```

Meaning of tokens:

- `measurement_bundle_base`: specimen bundle identifier including distance token
- `point_uid`: stable point identity for the specimen measurement point
- `measurement_group`: measurement-group identity from the container, for example `meas_000001`
- `x_mm`, `y_mm`: point coordinates formatted in millimeters with two decimal places
- `timestamp_token`: normalized timestamp such as `20260414_102030`
- `detector_alias`: detector alias such as `PRIMARY` or `SECONDARY`

Examples:

```text
326111__377557_17cm_7ccbcf0e1c85fa4c_meas_000001_1.25_2.50_20260414_102030_PRIMARY.npy
326111__377557_17cm_7ccbcf0e1c85fa4c_meas_000001_1.25_2.50_20260414_102030_PRIMARY.txt
326111__377557_17cm_7ccbcf0e1c85fa4c_meas_000001_1.25_2.50_20260414_102030_PRIMARY.txt.dsc
```

Possible file extensions for regular measurement exports include:

- `.npy`
- `.txt`
- `.txt.dsc`
- `.tiff` / `.tif` where present
- `.gfrm` where present

### Attenuation file naming

Attenuation files use a separate naming pattern from regular specimen measurement files.

#### Attenuation with sample

Current base pattern:

```text
<measurement_bundle_base>_<x_mm>_<y_mm>_<timestamp_token>__<detector_alias>_ATTENUATION
```

Examples:

```text
326111__377557_17cm_1.25_2.50_20260414_103000__PRIMARY_ATTENUATION.npy
326111__377557_17cm_1.25_2.50_20260414_103000__PRIMARY_ATTENUATION.txt
326111__377557_17cm_1.25_2.50_20260414_103000__PRIMARY_ATTENUATION.txt.dsc
```

#### Attenuation without sample

Current base pattern:

```text
<measurement_bundle_base>_<timestamp_token>__<detector_alias>_ATTENUATION0
```

Examples:

```text
326111__377557_17cm_20260414_102500__PRIMARY_ATTENUATION0.npy
326111__377557_17cm_20260414_102500__PRIMARY_ATTENUATION0.txt
326111__377557_17cm_20260414_102500__PRIMARY_ATTENUATION0.txt.dsc
```

Notes:

- attenuation file references are also carried in the specimen-specific state JSON under `attenuation_files`
- in the state JSON the files are grouped by specimen point, attenuation role, and detector alias
- the filename itself still carries enough context to identify the detector, timestamp, and attenuation mode

## Calibration Data

### Calibration meaning

Calibration data describes the technical detector setup used for subsequent sample measurements.
It is required so that Matador can understand detector geometry, detector-specific calibration, and the relation between measurement data and the calibration used to process it.

### Calibration scan types

Current DIFRA calibration flow contains these technical scan types:

- `DARK`
- `EMPTY`
- `BACKGROUND`
- `AGBH`

For a given distance, each scan type may contain multiple files.

### Two-detector model

DIFRA currently works with two detectors.
For the same calibration scan type, there may be two related detector variants:

- `PRIMARY`
- `SECONDARY`

So, for one logical calibration event, Matador must support multiple files of the same type that differ by detector alias.

### Calibration files

For calibration data, DIFRA may send:

- raw detector output `.txt`
- detector descriptor `.txt.dsc`
- derived NumPy array `.npy`
- PONI file `.poni` for `AGBH` calibration
- structured calibration JSON sidecars

The important point is that there may be multiple files of each type for one calibration category, and those files must be kept associated with:

- calibration scan type
- detector alias (`PRIMARY` / `SECONDARY`)
- detector distance
- acquisition timestamp / exposure

### PONI relation requirement

PONI files are calibration-critical.
For Matador to understand which detector calibration belongs to which raw calibration input, the relation between a PONI file and the corresponding `AGBH` calibration source must be explicit.

Preferred rule:

- the saved PONI filename should unambiguously reference the source `AGBH` calibration file and the detector alias used to create it
- in the current DIFRA export model, the generated PONI filename should reuse the corresponding exported `AGBH` filename stem and replace `.npy` with `.poni`

If naming alone is not sufficient, the calibration metadata must explicitly carry this mapping.

### Calibration metadata that must be preserved

For each calibration entry, Matador must be able to preserve and/or interpret:

- calibration scan type (`DARK`, `EMPTY`, `BACKGROUND`, `AGBH`)
- detector alias
- detector distance
- exposure time for the specific calibration entry, not one shared value for the whole bundle
- acquisition timestamp
- list of files belonging to that calibration entry
- whether the entry was accepted for calibration use or rejected by the operator
- PONI content for detector geometry, where applicable
- calibration grouping / identity needed to relate sample measurements to the calibration used

### `calibrationData.json`

Location:

- root of the calibration ZIP

Purpose:

- structured semantic index of the calibration package
- describes what logical calibration entries are present in the archive
- relates calibration scan type, detector alias, per-entry exposure, timestamp, PONI content, and participating files

Important rule:

- `calibrationData.json` must not imply that one single exposure time applies to the whole calibration ZIP
- exposure, where present, is defined per calibration entry
- the raw `.dsc` file remains the canonical source of acquisition parameters
- JSON sidecars may carry a denormalized copy of selected fields only for indexing, validation, or easier ingestion logic

Current top-level structure:

```json
{
  "distance": "17cm",
  "entries": [
    {
      "scanType": "AgBH",
      "distance": "17cm",
      "exposureTime": 0.5,
      "timestamp": "2026-04-14T10:20:30Z",
      "detectorAlias": "PRIMARY",
      "poniContent": "...",
      "usedForCalibration": true,
      "operatorDecision": "accepted_for_calibration",
      "selectionNote": null,
      "frameFiles": [
        "AgBH_17cm_001_20260414_102030_500ms_PRIMARY.npy",
        "AgBH_17cm_001_20260414_102030_500ms_PRIMARY.txt",
        "AgBH_17cm_001_20260414_102030_500ms_PRIMARY.txt.dsc",
        "AgBH_17cm_001_20260414_102030_500ms_PRIMARY.poni"
      ]
    }
  ]
}
```

Meaning of fields:

- `distance`: calibration distance token for the whole package
- `entries`: list of semantic calibration entries
- `entries[].scanType`: calibration category such as `Dark`, `Empty`, `Background`, `AgBH`
- `entries[].distance`: distance token for this entry
- `entries[].exposureTime`: exposure / integration time for this entry only
- `entries[].timestamp`: acquisition timestamp of this calibration entry
- `entries[].detectorAlias`: detector identity such as `PRIMARY` or `SECONDARY`
- `entries[].poniContent`: embedded PONI text, used where applicable
- `entries[].usedForCalibration`: whether this entry should be used for calibration
- `entries[].operatorDecision`: explicit calibration-use decision such as `accepted_for_calibration` or `rejected_for_calibration`
- `entries[].selectionNote`: optional operator / workflow note explaining why an entry was not selected
- `entries[].frameFiles`: exact archive filenames that belong to this calibration entry

Practical note:

- if Matador already reads and trusts the `.dsc` files directly, `entries[].exposureTime` is not strictly required as the single source of truth
- in that case it should be treated as a convenience field or omitted from the semantic contract
- the minimal requirement is that Matador can unambiguously determine which `.dsc` belongs to which calibration entry

### Calibration `metadata.json`

Location:

- root of the calibration ZIP

Purpose:

- package manifest
- file inventory
- simple size/count metadata for integrity and inspection

Current structure:

```json
{
  "key": "calibration_17cm",
  "fileCount": 12,
  "totalSize": 123456,
  "createdAt": "2026-04-14T10:20:30Z",
  "fileNames": [
    "AgBH_17cm_001_20260414_102030_500ms_PRIMARY.npy",
    "AgBH_17cm_001_20260414_102030_500ms_PRIMARY.poni",
    "calibrationData.json",
    "metadata.json",
    "technical_meta_20260414_17cm.json"
  ]
}
```

Meaning of fields:

- `key`: logical package identifier
- `fileCount`: number of files in the package including `metadata.json`
- `totalSize`: total byte size of the package content
- `createdAt`: manifest creation timestamp
- `fileNames`: exact filenames present in the ZIP

`metadata.json` is intentionally generic.
It is not the semantic calibration description.
The semantic description lives in `calibrationData.json`.

## Sample Measurement Data

### Measurement meaning

Sample measurement data describes the actual measured specimen and the spatially resolved measurement points acquired for that specimen.

The important thing for Matador is not just storing files, but preserving the relation between:

- the specimen
- the measurement points
- the detector-specific files
- the calibration used
- the image / coordinate context used to interpret the points

### Measurement files

For each sample measurement, DIFRA may send:

- raw detector output `.txt`
- detector descriptor `.txt.dsc`
- derived NumPy array `.npy`
- structured measurement JSON sidecars
- specimen state JSON

In current practice:

- the raw `.txt` is the detector output
- `.txt.dsc` is the associated descriptor file
- `.npy` is derived from the measurement data and is used as a structured numerical artifact

Each logical sample measurement may contain multiple measurement points, and for each point there may be multiple detector files.

### Two-detector model for measurements

As with calibration, measurement data may exist for two detectors:

- `PRIMARY`
- `SECONDARY`

Therefore Matador must preserve the relation between each measurement point and the detector-specific files that belong to that point.

### State JSON requirement

The specimen-specific state file is mandatory for correct interpretation of sample measurement data.
It must remain specimen-specific and should not be reduced to a generic shared filename.

Expected form:

- `<bundle>_state.json`

This state JSON is required because it carries the spatial and interpretive context needed to understand the measurement.

### What is important inside the state JSON

The exact internal schema may evolve, but the following kinds of information are essential:

- embedded sample image
- measurement point definitions
- X/Y coordinates of measurement points
- detector-to-calibration relation
- attenuation-related files
- geometric / spatial interpretation fields used to place points on the image

In current DIFRA packaging, the state JSON may include data such as:

- `measurement_points`
- `measurements_meta`
- `attenuation_files`
- `detector_poni`
- `technical_aux`
- `real_center`
- `pixel_to_mm_ratio`
- `rotation_angle`
- `crop_rect`
- `shapes`
- `zone_points`
- embedded image payload (`image_base64`)

This file is important because Matador relies on it to understand where measurements were taken and how to interpret them spatially.
Without this information, the measurement data is incomplete from an analytics point of view.

### Attenuation data

Attenuation measurements are part of the transferred measurement context.
They must remain associated with the correct specimen, point, detector, and attenuation role.

The relation of attenuation files is currently represented through the state JSON.
From Matador's perspective, attenuation files are not independent orphan files; they are supporting measurement artifacts tied to the specimen measurement context.

### Measurement metadata that must be preserved

For sample measurement data, Matador must be able to preserve and/or interpret:

- specimen identifier
- patient identifier, if present
- measurement name / bundle identifier
- detector distance
- detector alias
- measurement point coordinates
- unique measurement point identity
- integration time / exposure where relevant, but at the correct granularity
- machine and operator context
- calibration-group relation
- spatial image context from the state JSON

### `measurementData.json`

Location:

- root of the measurement ZIP

Purpose:

- structured registration-level description of the specimen measurement bundle
- identifies the measurement in business / system terms
- provides study, machine, specimen, user, and detector context

Important rule:

- `measurementData.json` describes the measurement bundle as an entity
- it should not try to flatten all per-point or per-detector acquisition parameters into one bundle-level field
- if exposure differs across points, detectors, or files, that information belongs in per-file / per-point metadata and in the raw `.dsc` source, not as one top-level measurement exposure field

Current top-level structure:

```json
{
  "id": null,
  "distanceInMM": 170,
  "study": { "id": 1701 },
  "machineMeasur": {
    "id": 1751,
    "machineName": "MOLI",
    "wavelength": 1.5406,
    "pixelSize": 55,
    "source": "Cu",
    "sourceType": "CU_K_ALPHA",
    "matrixResolution": "M256X256",
    "detectorModel": "ADVACAM MiniPix Timepix Standard",
    "organization": {
      "id": 10,
      "name": "Example Org",
      "country": "UK"
    },
    "createdAt": "2026-04-14T10:20:30.000Z",
    "updatedAt": "2026-04-14T10:20:30.000Z"
  },
  "user": { "id": 42 },
  "measurementName": "326111_17cm",
  "patient": { "id": 123 },
  "specimen": { "id": 326111 },
  "createdAt": "2026-04-14T10:20:30.000Z",
  "measurementM": { "id": 7 }
}
```

Meaning of fields:

- `distanceInMM`: detector distance for this measurement bundle
- `study`: study reference
- `machineMeasur`: machine and detector descriptive context
- `user`: uploader / operator reference
- `measurementName`: bundle-level measurement name
- `patient`: patient reference where applicable
- `specimen`: specimen reference
- `createdAt`: measurement record timestamp
- `measurementM`: measurement-module reference

`measurementData.json` is not the spatial interpretation file.
It describes the measurement bundle as an entity.
Spatial interpretation lives in the specimen-specific state JSON.
Raw acquisition specifics remain traceable through the measurement files and associated `.dsc` descriptors.

### Measurement `metadata.json`

Location:

- root of the measurement ZIP

Purpose:

- package manifest
- file inventory
- simple size/count metadata for integrity and inspection

Current structure:

```json
{
  "key": "326111_17cm",
  "fileCount": 24,
  "totalSize": 234567,
  "createdAt": "2026-04-14T10:20:30Z",
  "fileNames": [
    "326111_17cm_000100_000200_20260414_102030_PRIMARY.npy",
    "326111_17cm_state.json",
    "measurementData.json",
    "metadata.json"
  ]
}
```

The meaning of fields is the same as for calibration `metadata.json`:

- `key`
- `fileCount`
- `totalSize`
- `createdAt`
- `fileNames`

### Specimen-specific bundle state JSON

Location:

- root of the measurement ZIP

Current physical filename:

- `<bundle>_state.json`

Logical purpose:

- specimen-specific spatial and interpretive state for the whole measurement bundle
- defines how raw and derived measurement files should be understood in specimen coordinates
- carries the image and geometric context required to render and interpret measurement points

This is the closest thing to the semantic core of the measurement package.
Without it, the raw files are present, but the spatial meaning of the measurement is incomplete.

Current content categories:

- specimen measurement point definitions
- coordinate system and geometry information
- detector-to-calibration references
- attenuation file references
- per-file measurement metadata
- embedded specimen image

Example realistic exported payload:

If no points were skipped, `skipped_points` may be omitted or left as an empty list.

```json
{
  "measurement_points": [
    {
      "unique_id": "7ccbcf0e1c85fa4c",
      "index": 0,
      "point_index": 1,
      "x": 1.25,
      "y": 2.5
    },
    {
      "unique_id": "3b29d2f1a4e77c11",
      "index": 1,
      "point_index": 2,
      "x": 3.75,
      "y": 2.5
    }
  ],
  "active_detectors_aliases": ["PRIMARY", "SECONDARY"],
  "CALIBRATION_GROUP_HASH": "5f7d2c4a91e83b10",
  "detector_poni": {
    "PRIMARY": {
      "poni_filename": "AgBH_17cm_001_20260414_102530_300s_PRIMARY.poni",
      "poni_value": "Distance: 0.17\nPoni1: 0.001234\nPoni2: 0.002345\nWavelength: 1.5406e-10\n"
    },
    "SECONDARY": {
      "poni_filename": "AgBH_17cm_001_20260414_102530_300s_SECONDARY.poni",
      "poni_value": "Distance: 0.17\nPoni1: 0.001198\nPoni2: 0.002301\nWavelength: 1.5406e-10\n"
    }
  },
  "technical_aux": [
    { "type": "DARK", "alias": "PRIMARY" },
    { "type": "EMPTY", "alias": "PRIMARY" },
    { "type": "BACKGROUND", "alias": "PRIMARY" },
    { "type": "AGBH", "alias": "PRIMARY" },
    { "type": "DARK", "alias": "SECONDARY" },
    { "type": "EMPTY", "alias": "SECONDARY" },
    { "type": "BACKGROUND", "alias": "SECONDARY" },
    { "type": "AGBH", "alias": "SECONDARY" }
  ],
  "measurements_meta": {
    "326111__377557_17cm_7ccbcf0e1c85fa4c_meas_000001_1.25_2.50_20260414_103000_PRIMARY.npy": {
      "x": 1.25,
      "y": 2.5,
      "unique_id": "7ccbcf0e1c85fa4c",
      "base_file": "326111__377557_17cm",
      "integration_time": 300.0,
      "detector_alias": "PRIMARY",
      "detector_id": "DET-PRIMARY",
      "CALIBRATION_GROUP_HASH": "5f7d2c4a91e83b10",
      "detector_type": "Pixet",
      "detector_size": { "width": 512, "height": 512 },
      "pixel_size_um": [55.0, 55.0],
      "faulty_pixels": []
    },
    "326111__377557_17cm_7ccbcf0e1c85fa4c_meas_000001_1.25_2.50_20260414_103000_PRIMARY.txt": {
      "x": 1.25,
      "y": 2.5,
      "unique_id": "7ccbcf0e1c85fa4c",
      "base_file": "326111__377557_17cm",
      "integration_time": 300.0,
      "detector_alias": "PRIMARY",
      "detector_id": "DET-PRIMARY",
      "CALIBRATION_GROUP_HASH": "5f7d2c4a91e83b10",
      "detector_type": "Pixet",
      "detector_size": { "width": 512, "height": 512 },
      "pixel_size_um": [55.0, 55.0],
      "faulty_pixels": []
    },
    "326111__377557_17cm_3b29d2f1a4e77c11_meas_000002_3.75_2.50_20260414_103245_SECONDARY.npy": {
      "x": 3.75,
      "y": 2.5,
      "unique_id": "3b29d2f1a4e77c11",
      "base_file": "326111__377557_17cm",
      "integration_time": 300.0,
      "detector_alias": "SECONDARY",
      "detector_id": "DET-SECONDARY",
      "CALIBRATION_GROUP_HASH": "5f7d2c4a91e83b10",
      "detector_type": "Pixet",
      "detector_size": { "width": 512, "height": 512 },
      "pixel_size_um": [55.0, 55.0],
      "faulty_pixels": []
    }
  ],
  "attenuation_files": {
    "7ccbcf0e1c85fa4c": {
      "without_sample": {
        "PRIMARY": "326111__377557_17cm_20260414_102500__PRIMARY_ATTENUATION0.npy"
      },
      "with_sample": {
        "PRIMARY": "326111__377557_17cm_1.25_2.50_20260414_103100__PRIMARY_ATTENUATION.npy"
      }
    },
    "3b29d2f1a4e77c11": {
      "without_sample": {
        "SECONDARY": "326111__377557_17cm_20260414_102500__SECONDARY_ATTENUATION0.npy"
      },
      "with_sample": {
        "SECONDARY": "326111__377557_17cm_3.75_2.50_20260414_103300__SECONDARY_ATTENUATION.npy"
      }
    }
  },
  "real_center": { "x": 512.0, "y": 384.0 },
  "pixel_to_mm_ratio": 42.7,
  "rotation_angle": 5.0,
  "crop_rect": {
    "x": 120.0,
    "y": 80.0,
    "width": 1024.0,
    "height": 768.0
  },
  "shapes": [
    {
      "id": "zone_outline_001",
      "type": "polygon",
      "label": "Specimen Boundary",
      "points": [[140.0, 120.0], [980.0, 120.0], [980.0, 700.0], [140.0, 700.0]]
    }
  ],
  "zone_points": [
    {
      "id": "zone_pt_001",
      "x": 1.25,
      "y": 2.5
    },
    {
      "id": "zone_pt_002",
      "x": 3.75,
      "y": 2.5
    }
  ],
  "image_base64": "/9j/4AAQSkZJRgABAQAAAQABAAD..."
}
```

Meaning of important fields:

- `measurement_points`: point definitions for the specimen measurement
- `skipped_points`: points intentionally not measured
- `active_detectors_aliases`: detectors active in this bundle
- `CALIBRATION_GROUP_HASH`: link between specimen measurement and the calibration group used
- `detector_poni`: detector geometry context embedded as portable PONI data
- `technical_aux`: calibration-support references by type and detector alias
- `measurements_meta`: per exported measurement-file metadata, including coordinates, detector identity, and point identity
- `attenuation_files`: attenuation-support files grouped by point, role, and detector alias
- `real_center`, `pixel_to_mm_ratio`, `rotation_angle`, `crop_rect`, `shapes`, `zone_points`: geometric and image-placement context
- `image_base64`: embedded specimen image payload used for portable rendering / interpretation

Important portability rule:

- the transferred state JSON should contain portable content and portable references only
- machine-local file paths should not be relied on by Matador
- where files are referenced from the state JSON, archive-relative filenames or embedded content should be used

Important raw-data rule:

- raw `.dsc` files remain the canonical source for detailed acquisition parameters
- JSON sidecars should not duplicate raw descriptor content unless that duplication serves a clear ingestion or indexing need
- when a field exists both in JSON and in `.dsc`, the `.dsc` should be treated as the lower-level acquisition source, while JSON acts as the portable transfer index

## Overall Metadata Scope

The data to be stored is not all at the same level.
For implementation purposes, it is important to distinguish at least these levels:

- overall transfer / session level
- calibration level
- sample measurement level
- measurement-point level
- detector-specific file level

Examples of overall or session-level metadata:

- machine
- operator / user
- acquisition date
- study

Examples of calibration-level metadata:

- scan type
- detector alias
- detector distance
- exposure time
- PONI mapping

Examples of sample-measurement-level metadata:

- specimen ID
- patient ID, if present
- bundle name
- image and spatial context

Examples of point-level metadata:

- X and Y coordinates
- point unique ID
- point-specific attenuation relation

Examples of detector-file-level metadata:

- detector alias
- raw vs derived file role
- descriptor relation
- timestamp

## File and Relation Expectations

The key implementation expectation is not just "store files".
It is:

- preserve which files belong to calibration vs sample measurement
- preserve which files belong to which detector
- preserve which files belong to which measurement point
- preserve which calibration was used for which sample measurement
- preserve the spatial meaning described by the specimen state JSON

This is the minimum needed for later analytics, troubleshooting, reproducibility, and auditability.

## Practical Transfer Expectation

For each measured sample, DIFRA should be able to send:

1. the relevant calibration package for the current setup
2. the sample measurement package
3. the paired HDF5 container, where used

Repeated sending of the same calibration package is acceptable from the DIFRA side.
Matador should handle that safely without requiring DIFRA to pre-check Matador state.

## Summary

In short, Matador must accept and preserve:

- calibration data with multiple technical scan categories
- multiple files per category
- two-detector variants (`PRIMARY` / `SECONDARY`)
- PONI relations for `AGBH`
- sample measurements with raw, descriptor, and derived files
- specimen-specific state JSON with image and coordinates
- attenuation-related supporting files
- enough metadata to reconstruct how files relate to specimen, detector, point, and calibration

This is the practical data-storage and transfer requirement from the DIFRA side.
