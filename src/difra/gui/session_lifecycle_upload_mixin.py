from difra.gui.session_lifecycle_upload_execute_mixin import (
    SessionLifecycleUploadExecuteMixin,
)
from difra.gui.session_lifecycle_upload_manifest_mixin import (
    SessionLifecycleUploadManifestMixin,
)
from difra.gui.session_lifecycle_upload_metadata_mixin import (
    SessionLifecycleUploadMetadataMixin,
)
from difra.gui.session_lifecycle_upload_old_format_mixin import (
    SessionLifecycleUploadOldFormatMixin,
)
from difra.gui.session_lifecycle_upload_verify_mixin import (
    SessionLifecycleUploadVerifyMixin,
)


class SessionLifecycleUploadMixin(
    SessionLifecycleUploadVerifyMixin,
    SessionLifecycleUploadExecuteMixin,
    SessionLifecycleUploadManifestMixin,
    SessionLifecycleUploadMetadataMixin,
    SessionLifecycleUploadOldFormatMixin,
):
    pass
