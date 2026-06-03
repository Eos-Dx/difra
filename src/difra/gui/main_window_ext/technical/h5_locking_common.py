"""Shared imports/proxies for technical H5 locking mixins."""

from __future__ import annotations

import sys

from . import h5_management_mixin as _module
from .poni_center_validation import (
    normalize_alias_mapping_to_rule_aliases,
    parse_poni_center_px,
    resolve_poni_rule_alias,
    validate_poni_centers,
    validate_poni_metadata,
)
from .poni_agbh_peak_qc import evaluate_agbh_peak_qc_for_h5
from .poni_distance_validation import parse_poni_distance_cm, validate_poni_distances
from difra.gui.main_window_ext.technical import h5_management_lock_actions

os = _module.os
logger = _module.logger
QFileDialog = _module.QFileDialog
Path = _module.Path
shutil = _module.shutil
time = _module.time


_LOCKING_MODULE = "difra.gui.main_window_ext.technical.h5_management_locking_mixin"


class _ObjectProxy:
    def __init__(self, name: str, fallback):
        self._name = name
        self._fallback = fallback

    def _target(self):
        module = sys.modules.get(_LOCKING_MODULE)
        if module is None:
            return self._fallback
        target = getattr(module, self._name, self._fallback)
        if target is self:
            return self._fallback
        return target

    def __getattr__(self, attr: str):
        return getattr(self._target(), attr)


class _FunctionProxy(_ObjectProxy):
    def __call__(self, *args, **kwargs):
        return self._target()(*args, **kwargs)


QInputDialog = _ObjectProxy("QInputDialog", _module.QInputDialog)
QMessageBox = _ObjectProxy("QMessageBox", _module.QMessageBox)
get_container_manager = _FunctionProxy(
    "get_container_manager",
    _module.get_container_manager,
)
get_schema = _FunctionProxy("get_schema", _module.get_schema)
get_technical_validator = _FunctionProxy(
    "get_technical_validator",
    _module.get_technical_validator,
)
