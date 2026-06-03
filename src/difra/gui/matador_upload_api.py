"""Compatibility façade for Matador upload API helpers."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from difra.gui.matador_upload_models import (
    MatadorCreateSessionRequest,
    MatadorCreateSessionResponse,
    MatadorFileStatusResponse,
    MatadorFindOrCreateSessionRequest,
    MatadorIngestSessionResponse,
    MatadorRegisterFileRequest,
    MatadorRegisteredFileResponse,
    MatadorUploadApi,
    MatadorUploadContainerRequest,
    MatadorUploadContainerResponse,
)
from difra.gui.matador_upload_real import RealMatadorUploadApi
from difra.gui.matador_upload_stub import StubMatadorUploadApi
from difra.gui.matador_upload_utils import (
    _as_text,
    _normalize_iso_date,
    _safe_token,
    _strip_trailing_slash,
    default_matador_cache_path,
    load_matador_reference_cache,
    normalize_matador_base_url,
    normalize_matador_token,
    save_matador_reference_cache,
    sha256_file,
)

DEFAULT_REAL_MATADOR_TIMEOUT_SEC = 90.0


def refresh_matador_reference_cache(
    *,
    base_url: str,
    token: str,
    cache_path: Optional[Path] = None,
    timeout_sec: float = 30.0,
) -> Dict[str, Any]:
    api = RealMatadorUploadApi(
        base_url=normalize_matador_base_url(base_url),
        token=normalize_matador_token(token),
        timeout_sec=timeout_sec,
    )
    studies = api.list_studies()
    machines = api.list_machines()
    saved_path = save_matador_reference_cache(
        studies=studies,
        machines=machines,
        cache_path=cache_path,
    )
    return {
        "studies": studies,
        "machines": machines,
        "savedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cachePath": str(saved_path),
    }


def build_matador_upload_api(config: Optional[dict] = None) -> MatadorUploadApi:
    cfg = config or {}
    base_url = _strip_trailing_slash(
        _as_text(cfg.get("matador_url") or os.environ.get("MATADOR_URL"), "")
    )
    base_url = normalize_matador_base_url(base_url)
    token = normalize_matador_token(
        _as_text(cfg.get("matador_token") or os.environ.get("MATADOR_TOKEN"), "")
    )

    if base_url and token and not bool(cfg.get("matador_force_stub", False)):
        timeout_sec = cfg.get("matador_timeout_sec")
        if timeout_sec is None:
            timeout_sec = DEFAULT_REAL_MATADOR_TIMEOUT_SEC
        return RealMatadorUploadApi(
            base_url=base_url,
            token=token,
            timeout_sec=float(timeout_sec),
        )

    force_failure = bool(cfg.get("upload_stub_force_failure", False))
    failure_probability = cfg.get("upload_stub_failure_probability")
    if failure_probability is None:
        failure_probability = 0.0 if os.environ.get("PYTEST_CURRENT_TEST") else 0.3
    return StubMatadorUploadApi(
        force_failure=force_failure,
        failure_probability=float(failure_probability),
    )


__all__ = [
    "DEFAULT_REAL_MATADOR_TIMEOUT_SEC",
    "MatadorCreateSessionRequest",
    "MatadorCreateSessionResponse",
    "MatadorFileStatusResponse",
    "MatadorFindOrCreateSessionRequest",
    "MatadorIngestSessionResponse",
    "MatadorRegisterFileRequest",
    "MatadorRegisteredFileResponse",
    "MatadorUploadApi",
    "MatadorUploadContainerRequest",
    "MatadorUploadContainerResponse",
    "RealMatadorUploadApi",
    "StubMatadorUploadApi",
    "_as_text",
    "_normalize_iso_date",
    "_safe_token",
    "_strip_trailing_slash",
    "build_matador_upload_api",
    "default_matador_cache_path",
    "load_matador_reference_cache",
    "normalize_matador_base_url",
    "normalize_matador_token",
    "refresh_matador_reference_cache",
    "save_matador_reference_cache",
    "sha256_file",
]
