import logging
import shutil
from pathlib import Path


def _tm():
    from difra.gui.main_window_ext.technical import capture_auto_poni_workflow_mixin

    return capture_auto_poni_workflow_mixin._tm()


logger = logging.getLogger(__name__)


class TechnicalCaptureAutoPoniSourcesMixin:
    def _collect_auto_poni_agbh_sources(self) -> dict:
        tm = _tm()
        sources = {}
        if not hasattr(self, "auxTable") or self.auxTable is None:
            return sources

        for row in range(self.auxTable.rowCount()):
            if self._aux_row_type(row) != "AGBH":
                continue
            alias = self._aux_row_alias(row)
            if not alias:
                continue
            file_item = self.auxTable.item(row, self.AUX_COL_FILE)
            source_ref = (
                str(file_item.data(tm.Qt.UserRole) or "").strip()
                if file_item is not None
                else ""
            )
            if not source_ref:
                continue
            sources.setdefault(alias, source_ref)
        return sources

    @staticmethod
    def _auto_poni_output_path_for_source(source_path, fallback_poni_path):
        fallback = Path(fallback_poni_path)
        try:
            source = Path(source_path) if source_path else None
        except (TypeError, ValueError):
            source = None
        if source is not None and str(source).strip():
            return fallback.parent / f"{source.stem}.poni"
        return fallback

    def _autopony_output_dir(self) -> Path:
        return Path(self._current_technical_output_folder()) / "autopony"

    def _reset_autopony_output_dir(self) -> Path:
        output_dir = self._autopony_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        for child in output_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        return output_dir

    def _auto_poni_source_path_from_h5ref(self, source_ref: str):
        parser = getattr(self, "_parse_h5ref", None)
        if callable(parser):
            container_path, dataset_path = parser(source_ref)
        else:
            raw = str(source_ref or "")
            payload = raw[len("h5ref://") :] if raw.startswith("h5ref://") else ""
            container_path, sep, dataset_path = payload.partition("#")
            if not sep:
                container_path, dataset_path = None, None
        if not container_path or not dataset_path:
            return None
        try:
            import h5py

            with h5py.File(container_path, "r") as h5f:
                if dataset_path not in h5f:
                    return None
                obj = h5f[dataset_path]
                candidates = []
                for item in (obj, getattr(obj, "parent", None)):
                    if item is None:
                        continue
                    attrs = getattr(item, "attrs", {})
                    for key in ("source_file", "source_path", "source_ref"):
                        value = attrs.get(key)
                        if isinstance(value, bytes):
                            value = value.decode("utf-8", errors="replace")
                        if value:
                            candidates.append(str(value))
                for value in candidates:
                    if value.startswith("h5ref://"):
                        continue
                    path = Path(value)
                    if path.is_absolute():
                        return path
                    return Path(container_path).parent / path
        except Exception:
            logger.debug("Failed to resolve Auto PONI h5ref source path", exc_info=True)
        return None

    def _auto_poni_output_dir_for_source(self, source_ref: str):
        text = str(source_ref or "").strip()
        if text.startswith("h5ref://"):
            source_path = self._auto_poni_source_path_from_h5ref(text)
            if source_path is not None:
                return self._autopony_output_dir(), source_path
            parser = getattr(self, "_parse_h5ref", None)
            if callable(parser):
                container_path, _dataset_path = parser(text)
                if container_path:
                    return self._autopony_output_dir(), None
            payload = text[len("h5ref://") :]
            container_path, sep, _dataset_path = payload.partition("#")
            if sep and container_path:
                return self._autopony_output_dir(), None
            return self._autopony_output_dir(), None
        source_path = Path(text)
        return self._autopony_output_dir(), source_path
