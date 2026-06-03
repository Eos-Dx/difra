"""Export session containers into legacy folder layout used by older DIFRA flows."""

import base64
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import h5py
import numpy as np
from PIL import Image
from container import loader
from container.registry import load_version_module


@dataclass
class OldFormatExportSummary:
    """Summary for one legacy export operation."""

    export_dir: Path
    state_path: Path
    raw_file_count: int
    technical_file_count: int


from .session_old_format_attenuation_mixin import SessionOldFormatAttenuationMixin
from .session_old_format_common_mixin import SessionOldFormatCommonMixin
from .session_old_format_image_mixin import SessionOldFormatImageMixin
from .session_old_format_measurement_mixin import SessionOldFormatMeasurementMixin
from .session_old_format_technical_mixin import SessionOldFormatTechnicalMixin


class SessionOldFormatExporter(
    SessionOldFormatCommonMixin,
    SessionOldFormatImageMixin,
    SessionOldFormatTechnicalMixin,
    SessionOldFormatAttenuationMixin,
    SessionOldFormatMeasurementMixin,
):
    """Create old-style folder structure from a session container."""

    TECH_TYPE_FILE_PREFIX = {
        "DARK": "DC",
        "EMPTY": "Empty",
        "AGBH": "AgBH",
        "BACKGROUND": "Bg",
    }
    TECH_TYPE_FILE_ORDER = {
        "DARK": 1,
        "EMPTY": 2,
        "AGBH": 3,
        "BACKGROUND": 4,
    }
    TECH_TYPE_METADATA_NAME = {
        "AGBH": "AgBH",
        "BACKGROUND": "Background",
        "DARK": "DarkCurrent",
        "EMPTY": "EmptyBeam",
    }
    MATRIX_BLOB_PRIORITY = ("txt", "npy", "tif", "tiff", "gfrm")
