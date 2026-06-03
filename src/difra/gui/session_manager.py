"""Session Manager for DIFRA GUI.

Handles HDF5 session container lifecycle:
- Creating new sessions
- Managing active session state
- Writing measurements to containers
- Tracking measurement counters
"""

from difra.gui.session_manager_common_mixin import (
    DEFAULT_BEAM_ENERGY_KEV as DEFAULT_BEAM_ENERGY_KEV,
    SessionManagerCommonMixin,
)
from difra.gui.session_manager_lifecycle_mixin import SessionManagerLifecycleMixin
from difra.gui.session_manager_manifest_mixin import SessionManagerManifestMixin
from difra.gui.session_manager_measurement_ops_mixin import (
    SessionManagerMeasurementOpsMixin,
)
from difra.gui.session_manager_metadata_mixin import SessionManagerMetadataMixin
from difra.gui.session_manager_recovery_mixin import SessionManagerRecoveryMixin
from difra.gui.session_manager_state_mixin import SessionManagerStateMixin


class SessionManager(
    SessionManagerCommonMixin,
    SessionManagerManifestMixin,
    SessionManagerStateMixin,
    SessionManagerLifecycleMixin,
    SessionManagerMetadataMixin,
    SessionManagerRecoveryMixin,
    SessionManagerMeasurementOpsMixin,
):
    """Manages HDF5 session containers for DIFRA measurements."""

    SESSION_STATE_ATTR = "session_state"
    SESSION_STATE_REASON_ATTR = "session_state_reason"
    SESSION_STATE_UPDATED_ATTR = "session_state_updated_at"

    SESSION_STATE_DRAFT = "draft"
    SESSION_STATE_PREPARED = "prepared"
    SESSION_STATE_MEASURING = "measuring"
    SESSION_STATE_RECOVERY_REQUIRED = "recovery_required"
    SESSION_STATE_LOCKED = "locked"
    SESSION_STATE_ARCHIVED = "archived"

    VALID_SESSION_STATES = {
        SESSION_STATE_DRAFT,
        SESSION_STATE_PREPARED,
        SESSION_STATE_MEASURING,
        SESSION_STATE_RECOVERY_REQUIRED,
        SESSION_STATE_LOCKED,
        SESSION_STATE_ARCHIVED,
    }

    CAPTURE_MANIFEST_ATTR = "capture_manifest_json"
    CAPTURE_MANIFEST_VERSION = 1
