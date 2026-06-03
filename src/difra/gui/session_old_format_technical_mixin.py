"""Export session containers into legacy folder layout used by older DIFRA flows."""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import h5py
import numpy as np


@dataclass
class OldFormatExportSummary:
    """Summary for one legacy export operation."""

    export_dir: Path
    state_path: Path
    raw_file_count: int
    technical_file_count: int




class SessionOldFormatTechnicalMixin:
    """Old-format export behavior split from SessionOldFormatExporter."""

    @classmethod
    def _collect_technical_events(
        cls,
        *,
        h5f: h5py.File,
        day_token: str,
        default_distance_int: int,
    ) -> Tuple[List[Dict[str, Any]], int]:
        schema = cls._schema_for_h5(h5f)
        technical_group = h5f.get(getattr(schema, "GROUP_TECHNICAL", "/entry/technical"))
        if technical_group is None:
            return [], int(default_distance_int)

        poni_by_path: Dict[str, str] = {}
        poni_by_alias: Dict[str, str] = {}
        poni_group = h5f.get(
            getattr(schema, "GROUP_TECHNICAL_PONI", f"{technical_group.name}/poni")
        )
        if poni_group is not None:
            for poni_name in sorted(poni_group.keys()):
                poni_ds = poni_group[poni_name]
                text = cls._as_text(poni_ds[()], "")
                ds_path = f"{poni_group.name}/{poni_name}"
                poni_by_path[ds_path] = text
                alias = cls._as_text(
                    poni_ds.attrs.get(getattr(schema, "ATTR_DETECTOR_ALIAS", "detector_alias")),
                    "",
                ).upper()
                if alias:
                    poni_by_alias[alias] = text

        distance_values: List[float] = []
        events: List[Dict[str, Any]] = []

        for event_name in sorted(technical_group.keys()):
            if not str(event_name).startswith("tech_evt_"):
                continue
            event_group = technical_group[event_name]
            event_type = cls._as_text(
                event_group.attrs.get(
                    "type",
                    event_group.attrs.get(getattr(schema, "ATTR_TECHNICAL_TYPE", "technical_type")),
                ),
                "UNKNOWN",
            ).upper()
            event_ts = cls._normalize_timestamp_token(
                event_group.attrs.get(getattr(schema, "ATTR_TIMESTAMP", "timestamp")),
                day_token,
            )
            event_is_primary = bool(event_group.attrs.get("is_primary", False))
            event_distance = cls._extract_distance_from_attrs(event_group.attrs)
            if event_distance is not None:
                distance_values.append(event_distance)

            event_idx_match = re.search(r"(\d+)$", str(event_name))
            if event_idx_match:
                event_index = int(event_idx_match.group(1))
            else:
                event_index = len(events) + 1

            for det_name in sorted(event_group.keys()):
                if not str(det_name).startswith("det_"):
                    continue
                det_group = event_group[det_name]
                dataset_processed_signal = getattr(schema, "DATASET_PROCESSED_SIGNAL", "processed_signal")
                if dataset_processed_signal not in det_group:
                    continue

                alias = cls._as_text(
                    det_group.attrs.get(getattr(schema, "ATTR_DETECTOR_ALIAS", "detector_alias")),
                    str(det_name).replace("det_", "").upper(),
                ).upper()
                detector_id = cls._as_text(
                    det_group.attrs.get(getattr(schema, "ATTR_DETECTOR_ID", "detector_id")),
                    alias,
                )
                integration_ms = cls._to_float(det_group.attrs.get("integration_time_ms"))
                integration_s = None
                if integration_ms is not None:
                    integration_s = integration_ms / 1000.0
                n_frames = cls._safe_int(
                    det_group.attrs.get(getattr(schema, "ATTR_N_FRAMES", "n_frames"))
                )

                det_distance = cls._extract_distance_from_attrs(det_group.attrs)
                resolved_distance = det_distance if det_distance is not None else event_distance
                if resolved_distance is not None:
                    distance_values.append(resolved_distance)

                raw_blobs: Dict[str, bytes] = {}
                blob_group = det_group.get("blob")
                if blob_group is not None:
                    for blob_name in sorted(blob_group.keys()):
                        if not str(blob_name).startswith("raw_"):
                            continue
                        raw_blobs[str(blob_name)] = cls._read_blob_bytes(blob_group[blob_name])

                poni_text = None
                poni_path = cls._as_text(
                    det_group.attrs.get(getattr(schema, "ATTR_PONI_REF", "poni_ref"))
                    or det_group.attrs.get("poni_path"),
                    "",
                )
                if poni_path:
                    poni_text = poni_by_path.get(poni_path)
                if poni_text is None:
                    poni_text = poni_by_alias.get(alias)

                events.append(
                    {
                        "type": event_type,
                        "event_index": int(event_index),
                        "timestamp_token": event_ts,
                        "alias": alias,
                        "detector_id": detector_id,
                        "integration_s": integration_s,
                        "n_frames": n_frames or 1,
                        "is_primary": bool(event_is_primary),
                        "selection_note": cls._as_text(
                            event_group.attrs.get("supplementary_note", ""),
                            "",
                        ).strip(),
                        "processed_signal": np.asarray(det_group[dataset_processed_signal][()]),
                        "raw_blobs": raw_blobs,
                        "poni_text": poni_text,
                    }
                )

        if distance_values:
            rounded = [int(round(v)) for v in distance_values]
            mode_value = Counter(rounded).most_common(1)[0][0]
            canonical_distance_int = max(1, int(mode_value))
        else:
            canonical_distance_int = int(default_distance_int)

        return events, canonical_distance_int

    @classmethod
    def _build_technical_data_files(
        cls,
        *,
        events: List[Dict[str, Any]],
        distance_token: str,
    ) -> Tuple[Dict[str, bytes], Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
        files: Dict[str, bytes] = {}
        selected: Dict[Tuple[str, str], Dict[str, Any]] = {}
        calibration_entries: List[Dict[str, Any]] = []

        for event in events:
            event_type = cls._as_text(event.get("type"), "UNKNOWN").upper()
            alias = cls._as_text(event.get("alias"), "DETECTOR").upper()
            alias_token = cls._safe_token(alias, "DETECTOR").upper()
            event_idx_token = cls._technical_type_order_token(
                event_type,
                event.get("event_index"),
            )
            ts_token = cls._as_text(event.get("timestamp_token"), "")
            integration_token = cls._integration_token(event.get("integration_s"), event_type)
            frames_token = cls._frames_token(event.get("n_frames"))
            prefix = cls.TECH_TYPE_FILE_PREFIX.get(event_type, event_type.title() or "Tech")

            base = f"{prefix}_{distance_token}_{event_idx_token}_{ts_token}_{integration_token}_{frames_token}_{alias_token}"
            raw_blobs = event.get("raw_blobs") or {}
            matrix_name, matrix_payload, _matrix_ext, dsc_payload = cls._select_matrix_payload(
                base=base,
                processed_signal=event.get("processed_signal"),
                raw_blobs=raw_blobs,
            )
            files[matrix_name] = matrix_payload
            frame_files = [matrix_name]
            if dsc_payload:
                dsc_name = cls._measurement_dsc_sidecar_name(matrix_name)
                files[dsc_name] = dsc_payload
                frame_files.append(dsc_name)

            poni_name = None
            poni_text = event.get("poni_text")
            if event_type == "AGBH" and poni_text is not None:
                poni_name = f"{Path(matrix_name).stem}.poni"
                files[poni_name] = cls._as_text(poni_text, "").encode("utf-8")
                frame_files.append(poni_name)

            calibration_entries.append(
                {
                    "event_type": event_type,
                    "alias": alias,
                    "distance": distance_token,
                    "integration_s": event.get("integration_s"),
                    "timestamp_token": ts_token,
                    "poni_text": cls._as_text(poni_text, "") if poni_text is not None else None,
                    "frame_files": sorted(frame_files),
                    "is_primary": bool(event.get("is_primary")),
                    "selection_note": cls._as_text(event.get("selection_note"), "").strip(),
                }
            )

            rank = (1 if bool(event.get("is_primary")) else 0, int(event.get("event_index") or 0))
            key = (event_type, alias)
            existing = selected.get(key)
            if existing is None or rank > existing["rank"]:
                selected[key] = {
                    "rank": rank,
                    "matrix_name": matrix_name,
                    "poni_name": poni_name,
                    "poni_text": cls._as_text(poni_text, "") if poni_text is not None else None,
                    "integration_s": event.get("integration_s"),
                    "timestamp_token": ts_token,
                }

        return files, selected, calibration_entries

    @staticmethod
    def _iso_utc_now() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @classmethod
    def _timestamp_token_to_iso8601(cls, timestamp_token: str) -> str:
        text = cls._as_text(timestamp_token, "").strip()
        if not text:
            return cls._iso_utc_now()
        for fmt in ("%Y%m%d_%H%M%S", "%Y%m%d%H%M%S"):
            try:
                return datetime.strptime(text, fmt).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                continue
        return text

    @classmethod
    def _distance_mm_from_token(cls, distance_token: str) -> int:
        match = re.match(r"^\s*(\d+)\s*cm\s*$", str(distance_token or ""), re.IGNORECASE)
        if match:
            return int(match.group(1)) * 10
        return 0

    @classmethod
    def _created_at_from_day_token(cls, day_token: str) -> str:
        text = cls._as_text(day_token, "").strip()
        for fmt in ("%Y%m%d", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
            except Exception:
                continue
        return cls._iso_utc_now()

    @classmethod
    def _build_metadata_json(
        cls,
        *,
        bundle_key: str,
        file_payloads: Dict[str, bytes],
    ) -> bytes:
        file_names = sorted(file_payloads.keys()) + ["metadata.json"]
        base_size = sum(len(payload) for payload in file_payloads.values())
        created_at = cls._iso_utc_now()

        previous_payload = b""
        while True:
            candidate = {
                "key": bundle_key,
                "fileCount": len(file_names),
                "totalSize": int(base_size + len(previous_payload)),
                "createdAt": created_at,
                "fileNames": file_names,
            }
            payload = json.dumps(candidate, indent=2).encode("utf-8")
            if len(payload) == len(previous_payload):
                return payload
            previous_payload = payload

    @classmethod
    def _build_calibration_data_payload(
        cls,
        *,
        distance_token: str,
        day_token: str,
        entries: List[Dict[str, Any]],
        calibration_group_hash: str,
        machine_id: Optional[int] = None,
        machine_name: str = "",
    ) -> Dict[str, Any]:
        payload_entries: List[Dict[str, Any]] = []
        filetype_map: Dict[str, Any] = {}
        primary_poni_text = ""
        exposure_times = [
            float(info["integration_s"])
            for info in entries
            if info.get("integration_s") is not None
        ]
        for info in entries:
            event_type = cls._as_text(info.get("event_type"), "UNKNOWN").upper()
            alias = cls._as_text(info.get("alias"), "UNKNOWN").upper()
            selection_note = cls._as_text(info.get("selection_note"), "").strip()
            used_for_calibration = bool(info.get("is_primary"))
            poni_text = cls._as_text(info.get("poni_text"), "")
            if used_for_calibration and poni_text and not primary_poni_text:
                primary_poni_text = poni_text
            elif poni_text and not primary_poni_text:
                primary_poni_text = poni_text
            for file_name in list(info.get("frame_files") or []):
                ext = Path(str(file_name)).suffix.lower()
                if ext == ".poni":
                    calibration_file_type = "LAB_PONI"
                elif ext == ".dsc":
                    calibration_file_type = f"{event_type}_DSC"
                else:
                    calibration_file_type = event_type
                filetype_map[str(file_name)] = {
                    "calibrationFileType": calibration_file_type,
                    "detectorType": str(alias),
                }
            payload_entries.append(
                {
                    "scanType": cls.TECH_TYPE_METADATA_NAME.get(
                        str(event_type).upper(),
                        str(event_type).title() or "Unknown",
                    ),
                    "distance": distance_token,
                    "exposureTime": info.get("integration_s"),
                    "timestamp": cls._timestamp_token_to_iso8601(
                        cls._as_text(info.get("timestamp_token"), "")
                    ),
                    "detectorAlias": str(alias),
                    "poniContent": info.get("poni_text"),
                    "frameFiles": list(info.get("frame_files") or []),
                    "usedForCalibration": used_for_calibration,
                    "operatorDecision": (
                        "accepted_for_calibration"
                        if used_for_calibration
                        else "rejected_for_calibration"
                    ),
                    "selectionNote": selection_note or None,
                }
            )
        if calibration_group_hash:
            filetype_map["CALIBRATION_GROUP_HASH"] = calibration_group_hash
        distance_mm = cls._distance_mm_from_token(distance_token)
        created_at = cls._created_at_from_day_token(day_token)
        return {
            "id": None,
            "name": f"{distance_token}_calibration",
            "exposureTime": exposure_times[0] if exposure_times else 0,
            "distanceInMM": distance_mm,
            "processingStatus": "REQUEST_FOR_VALIDATION",
            "distance": distance_token,
            "entries": payload_entries,
            "filetypeMap": filetype_map,
            "ponifile": primary_poni_text,
            "machine": {
                "id": machine_id,
                "machineName": machine_name or None,
            },
            "createdAt": created_at,
            "CALIBRATION_GROUP_HASH": calibration_group_hash,
            "quantity": len([key for key in filetype_map if key != "CALIBRATION_GROUP_HASH"]),
        }

    @classmethod
    def _folder_matches_data_files(cls, folder: Path, files: Dict[str, bytes]) -> bool:
        folder = Path(folder)
        if not folder.exists() or not folder.is_dir():
            return False

        try:
            existing_files = {
                p.name
                for p in folder.iterdir()
                if p.is_file()
                and not p.name.startswith("technical_meta_")
                and p.name not in {"calibrationData.json", "metadata.json"}
            }
        except Exception:
            return False

        if existing_files != set(files.keys()):
            return False

        for name, payload in files.items():
            candidate = folder / name
            try:
                if not candidate.exists() or candidate.read_bytes() != payload:
                    return False
            except Exception:
                return False

        return True

    @classmethod
    def _next_distance_token(cls, calibration_root: Path, start_distance: int) -> Tuple[str, int]:
        used: set = set()
        pattern = re.compile(r"^(\d+)cm$", re.IGNORECASE)
        for child in calibration_root.iterdir() if calibration_root.exists() else []:
            if not child.is_dir():
                continue
            match = pattern.match(child.name)
            if match:
                used.add(int(match.group(1)))

        candidate = max(1, int(start_distance))
        while candidate in used:
            candidate += 1
        return cls._distance_token(candidate), candidate

    @classmethod
    def _choose_technical_distance_token(
        cls,
        *,
        calibration_root: Path,
        events: List[Dict[str, Any]],
        canonical_distance_int: int,
    ) -> Tuple[str, Dict[str, bytes], Dict[str, Dict[str, Any]], bool]:
        calibration_root.mkdir(parents=True, exist_ok=True)

        existing_tokens: List[Tuple[int, str]] = []
        token_re = re.compile(r"^(\d+)cm$", re.IGNORECASE)
        for child in sorted(calibration_root.iterdir()):
            if not child.is_dir():
                continue
            match = token_re.match(child.name)
            if match:
                existing_tokens.append((int(match.group(1)), child.name))

        # Prefer exact payload match in any already existing distance folder.
        for _distance_value, token in sorted(existing_tokens):
            candidate_files, candidate_selected, _candidate_entries = cls._build_technical_data_files(
                events=events,
                distance_token=token,
            )
            if cls._folder_matches_data_files(calibration_root / token, candidate_files):
                return token, candidate_files, candidate_selected, True

        canonical_token = cls._distance_token(canonical_distance_int)
        canonical_files, canonical_selected, _canonical_entries = cls._build_technical_data_files(
            events=events,
            distance_token=canonical_token,
        )
        canonical_folder = calibration_root / canonical_token

        if not canonical_folder.exists():
            return canonical_token, canonical_files, canonical_selected, False

        if cls._folder_matches_data_files(canonical_folder, canonical_files):
            return canonical_token, canonical_files, canonical_selected, True

        next_token, _next_int = cls._next_distance_token(calibration_root, canonical_distance_int + 1)
        next_files, next_selected, _next_entries = cls._build_technical_data_files(
            events=events,
            distance_token=next_token,
        )
        return next_token, next_files, next_selected, False

    @classmethod
    def _export_technical_measurements(
        cls,
        *,
        h5f: h5py.File,
        day_dir: Path,
        day_token: str,
        default_distance_int: int,
        calibration_group_hash: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, str, Dict[Tuple[str, str], str], Dict[str, Dict[str, Any]], Optional[Path]]:
        cfg = config or {}
        calibration_root = day_dir / "calibration"
        events, canonical_distance_int = cls._collect_technical_events(
            h5f=h5f,
            day_token=day_token,
            default_distance_int=default_distance_int,
        )
        root_distance = cls._extract_distance_from_attrs(h5f.attrs)
        if root_distance is not None:
            canonical_distance_int = cls._distance_int(
                root_distance,
                default=default_distance_int,
            )

        distance_token, data_files, selected, reused_existing = cls._choose_technical_distance_token(
            calibration_root=calibration_root,
            events=events,
            canonical_distance_int=canonical_distance_int,
        )
        _, _, calibration_entries = cls._build_technical_data_files(
            events=events,
            distance_token=distance_token,
        )
        tech_dir = calibration_root / distance_token
        tech_dir.mkdir(parents=True, exist_ok=True)

        if data_files:
            for name, payload in sorted(data_files.items()):
                cls._write_bytes_if_changed(tech_dir / name, payload)

        # Build technical meta payload in old format style.
        by_type_alias: Dict[str, Dict[str, str]] = {}
        for (event_type, alias), info in sorted(selected.items()):
            by_type_alias.setdefault(event_type, {})[alias] = info["matrix_name"]

        detector_poni: Dict[str, Dict[str, Any]] = {}
        poni_lab: Dict[str, str] = {}
        poni_lab_path: Dict[str, str] = {}
        poni_lab_values: Dict[str, str] = {}

        for (event_type, alias), info in sorted(selected.items()):
            if event_type != "AGBH":
                continue
            poni_name = info.get("poni_name")
            poni_text = info.get("poni_text")
            if not poni_name:
                continue
            poni_path = str((tech_dir / poni_name).resolve())
            detector_poni[alias] = {
                "poni_filename": poni_name,
                "poni_path": poni_path,
                "poni_value": cls._as_text(poni_text, ""),
            }
            poni_lab[alias] = poni_name
            poni_lab_path[alias] = poni_path
            poni_lab_values[alias] = cls._as_text(poni_text, "")

        meta_payload: Dict[str, Any] = {}
        preferred_order = ["DARK", "EMPTY", "AGBH", "BACKGROUND"]
        for typ in preferred_order:
            if typ in by_type_alias:
                meta_payload[typ] = by_type_alias[typ]
        for typ in sorted(by_type_alias.keys()):
            if typ not in meta_payload:
                meta_payload[typ] = by_type_alias[typ]
        if poni_lab:
            meta_payload["PONI_LAB"] = poni_lab
        if poni_lab_path:
            meta_payload["PONI_LAB_PATH"] = poni_lab_path
        if poni_lab_values:
            meta_payload["PONI_LAB_VALUES"] = poni_lab_values
        if calibration_group_hash:
            meta_payload["CALIBRATION_GROUP_HASH"] = calibration_group_hash

        meta_name = f"technical_meta_{day_token}_{distance_token}.json"
        meta_path = tech_dir / meta_name
        meta_bytes = json.dumps(meta_payload, indent=2).encode("utf-8")
        cls._write_bytes_if_changed(meta_path, meta_bytes)

        calibration_data_payload = cls._build_calibration_data_payload(
            distance_token=distance_token,
            day_token=day_token,
            entries=calibration_entries,
            calibration_group_hash=calibration_group_hash,
            machine_id=cls._safe_int(
                h5f.attrs.get("matadorMachineId", cfg.get("matador_machine_id"))
            ),
            machine_name=cls._as_text(
                h5f.attrs.get("machine_name", cfg.get("machine_name")),
                "",
            ).strip(),
        )
        calibration_data_path = tech_dir / "calibrationData.json"
        calibration_data_bytes = json.dumps(
            calibration_data_payload,
            indent=2,
        ).encode("utf-8")
        cls._write_bytes_if_changed(calibration_data_path, calibration_data_bytes)

        metadata_bytes = cls._build_metadata_json(
            bundle_key=f"calibration_{distance_token}",
            file_payloads={
                **data_files,
                meta_name: meta_bytes,
                "calibrationData.json": calibration_data_bytes,
            },
        )
        metadata_path = tech_dir / "metadata.json"
        cls._write_bytes_if_changed(metadata_path, metadata_bytes)

        technical_aux_map: Dict[Tuple[str, str], str] = {}
        for event_type, alias_map in by_type_alias.items():
            for alias, npy_name in alias_map.items():
                technical_aux_map[(event_type, alias)] = str((tech_dir / npy_name).resolve())

        technical_count = len(data_files) + 3
        # Keep a deterministic count even when folder was reused (for reporting/tests).
        if reused_existing:
            technical_count = len(data_files) + 3

        return technical_count, distance_token, technical_aux_map, detector_poni, meta_path

