from __future__ import annotations

from collections import Counter
from fnmatch import fnmatch
import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Any, Dict, Iterable, List, Optional, Set
import zipfile

import h5py

from difra.gui.matador_upload_api import (
    MatadorFindOrCreateSessionRequest,
    MatadorRegisterFileRequest,
    MatadorUploadContainerRequest,
    build_matador_upload_api,
    sha256_file,
)
from difra.gui.main_window_ext.technical.helpers import _get_difra_base_folder
from difra.gui.matador_zip_bundle_exporter import MatadorZipBundleExporter
from difra.gui.session_lifecycle_common import (
    SendArchiveResult,
    UploadStubResult,
    _normalize_iso_date,
)
from difra.gui.session_reupload_service import SessionReuploadService
from difra.gui.session_lifecycle_service import SessionLifecycleService
from difra.gui.session_old_format_exporter import SessionOldFormatExporter

logger = logging.getLogger(__name__)
SessionLifecycleActions = None


def _actions_module():
    from difra.gui import session_lifecycle_actions as actions

    return actions


def _build_matador_upload_api(*args, **kwargs):
    return _actions_module().build_matador_upload_api(*args, **kwargs)


def _session_old_format_exporter():
    return _actions_module().SessionOldFormatExporter


def _session_lifecycle_service():
    return _actions_module().SessionLifecycleService


class SessionLifecycleReuploadMixin:
    @classmethod
    def reupload_archived_session_containers(
        cls,
        container_paths: Iterable[Path],
        *,
        container_manager: Any,
        uploader_id: Optional[str] = None,
        lock_user: Optional[str] = None,
        simulate_upload_failure: bool = False,
        config: Optional[Dict[str, Any]] = None,
        export_old_format: Optional[bool] = None,
        progress_callback: Optional[Any] = None,
        specimen_overrides: Optional[Dict[str, int]] = None,
    ) -> SendArchiveResult:
        """Upload archived session containers again without moving them."""
        service = SessionReuploadService(
            actions_cls=cls,
            build_upload_api=_build_matador_upload_api,
        )
        return service.reupload_archived_session_containers(
            container_paths,
            container_manager=container_manager,
            uploader_id=uploader_id,
            lock_user=lock_user,
            simulate_upload_failure=simulate_upload_failure,
            config=config,
            export_old_format=export_old_format,
            progress_callback=progress_callback,
            specimen_overrides=specimen_overrides,
        )
