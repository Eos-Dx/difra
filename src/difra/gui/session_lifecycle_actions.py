"""Higher-level session lifecycle workflows shared by GUI mixins."""

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
    sha256_file,
)
from difra.gui.main_window_ext.technical.helpers import _get_difra_base_folder
from difra.gui.matador_upload_service import build_matador_upload_service
from difra.gui.matador_zip_bundle_exporter import MatadorZipBundleExporter
from difra.gui.session_lifecycle_archive_mixin import SessionLifecycleArchiveMixin
from difra.gui.session_lifecycle_common import (
    SendArchiveResult,
    UploadStubResult,
    _normalize_iso_date,
)
from difra.gui.session_lifecycle_metadata_mixin import SessionLifecycleMetadataMixin
from difra.gui.session_lifecycle_reupload_mixin import SessionLifecycleReuploadMixin
from difra.gui.session_lifecycle_upload_mixin import SessionLifecycleUploadMixin
from difra.gui.session_lifecycle_service import SessionLifecycleService
from difra.gui.session_old_format_exporter import SessionOldFormatExporter

logger = logging.getLogger(__name__)


def build_matador_upload_api(config: Optional[dict] = None):
    return build_matador_upload_service(config=config).api


class SessionLifecycleActions(
    SessionLifecycleReuploadMixin,
    SessionLifecycleArchiveMixin,
    SessionLifecycleUploadMixin,
    SessionLifecycleMetadataMixin,
):
    """Shared lifecycle actions used by session-related GUI flows."""

    DEFAULT_MATADOR_POLL_ATTEMPTS = 24
    DEFAULT_MATADOR_POLL_DELAY_SEC = 5.0
    UPLOAD_STATUS_PENDING_VERIFICATION = "pending_verification"

    SESSION_STATE_ATTR = "session_state"
    SESSION_STATE_REASON_ATTR = "session_state_reason"
    SESSION_STATE_UPDATED_ATTR = "session_state_updated_at"
    TRANSFER_STATUS_ATTR = "transfer_status"
    TRANSFER_STATUS_NOT_COMPLETE = "not_complete"
    TRANSFER_STATUS_REQ_RESEND = "req_resend"
    TRANSFER_STATUS_UNSENT = "unsent"
    COMPLETION_STATUS_ATTR = "session_completion_status"
    COMPLETION_STATUS_COMPLETE = "complete"
    COMPLETION_STATUS_NOT_COMPLETE = "not_complete"

    DEFAULT_MEASUREMENT_CLEANUP_PATTERNS = [
        "*.txt",
        "*.dsc",
        "*.npy",
        "*.t3pa",
        "*.poni",
        "*_state.json",
    ]

# Keep legacy class-name references inside extracted static methods working.
from difra.gui import session_lifecycle_archive_mixin as _archive_mixin
from difra.gui import session_lifecycle_upload_mixin as _upload_mixin
from difra.gui import session_lifecycle_upload_manifest_mixin as _upload_manifest_mixin

_archive_mixin.SessionLifecycleActions = SessionLifecycleActions
_upload_mixin.SessionLifecycleActions = SessionLifecycleActions
_upload_manifest_mixin.SessionLifecycleActions = SessionLifecycleActions
