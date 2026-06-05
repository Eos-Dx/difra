# PIXet 1.8.5 API Migration Notes

## Summary

The `new-pixet-api` branch changes DiFRA PIXet control from the legacy vendor
Python binding to a direct C API bridge.

Old path:

- SDK/binding: `pypixet.pyd`
- Python runtime: legacy sidecar env, originally Python 3.7/3.8 compatible
- Main call: `doSimpleIntegralAcquisition(...)`
- File output: PIXet wrote acquisition files directly

New path:

- SDK/API: Advacam PIXet Pro GUI/API `1.8.5` x64
- Runtime: `eosdx_pixet`, Python `3.12` x64
- Bridge: `ctypes` against `pxcore.dll`
- Main call: `pxcMeasureSingleFrame(...)`
- File output: DiFRA receives frame arrays and writes `.txt`, `.dsc`, `.npy`

The important change is not only the SDK version. The important change is that
DiFRA now controls the low-level frame acquisition path itself.

## Runtime Bootstrapping

On Windows, `src/difra/bin/run_difra.bat` calls:

```bat
src\difra\bin\ensure_pixet_sidecar_runtime.bat
```

That script:

- reads `sidecar_conda` from `main_win.json`
- defaults to `eosdx_pixet`
- creates/updates the env from `environment-eosdx-pixet.yml`
- verifies Python `3.12` x64
- installs `python=3.12 pip numpy`
- downloads PIXet Pro GUI/API `1.8.5`
- unpacks it to `D:\API_PIXet_Pro_1.8.5_Windows_x64` when `D:\` exists
- finds `pxcore.dll`
- exports `PIXET_SDK_PATH`

## C API Initialization

The new bridge is implemented in:

```text
src/difra/hardware/pixet_ctypes_api.py
src/difra/hardware/detector_pixet_ctypes_controller.py
```

Key details:

- `pxcInitialize` signature is `pxcInitialize(const char *iniFile)`.
- DiFRA passes the absolute path to `pixet.ini`.
- DiFRA temporarily changes CWD to the SDK directory before initialization,
  because `pxcore.dll` resolves `hwlibs/...` paths relative to CWD.
- `PixetCtypesAPI` is process-wide singleton.
- All detector controllers share the same initialized `pxcore.dll`.
- Individual detector `deinit_detector()` must not call `pxcExit()`, because
  that would break other controllers in the same sidecar process.

## Capture Behavior

Legacy behavior:

```text
pypixet device -> doSimpleIntegralAcquisition(Nframes, exposure, output_file)
PIXet writes output file
DiFRA converts output to container format
```

Current new behavior:

```text
for each frame:
    frame = pxcMeasureSingleFrame(device_index, exposure)
    integrated += frame

DiFRA writes integrated .txt
DiFRA writes generated .dsc
DiFRA converts .txt to .npy for container v0.2
```

This means DiFRA now has direct access to each raw frame before summing.

## Why Frame Saving Is Now Possible

Because the new backend calls `pxcMeasureSingleFrame(...)` directly, DiFRA can
store every individual frame instead of only the final integrated image.

Possible future capture mode:

```text
for frame_index in range(Nframes):
    frame = pxcMeasureSingleFrame(device_index, exposure)
    save frame_index as .npy/.txt
    integrated += frame

save integrated image
write metadata:
    detector alias
    frame count
    exposure per frame
    total exposure
    frame timestamps
```

This would allow:

- frame-by-frame quality control
- spike/outlier detection
- detector drift checks
- time-resolved signal checks
- debugging bad integrations before summing hides the problem

Recommended config flag:

```json
{
  "pixet_save_individual_frames": false
}
```

Default should stay `false`, because saving every frame can significantly
increase container size.

## Known Differences And Risks

The new C API path is lower-level than `pypixet`.

Benefits:

- Python 3.12 compatible
- no dependency on vendor `pypixet.pyd`
- controlled SDK download/unpack
- direct frame access
- easier future frame-level QC

Risks:

- wrong `ctypes` signatures can crash Python
- SDK initialization depends on correct `pixet.ini`
- SDK relative paths require CWD handling
- `pxcore.dll` is process-level state, so shutdown must be controlled carefully

## Legacy Fallback

Legacy `pypixet` backend still exists:

```text
src/difra/hardware/detector_pixet_legacy_controller.py
```

It can be forced with:

```bat
set PIXET_SIDECAR_BACKEND=pypixet
```

This should be treated as fallback only.

