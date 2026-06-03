from __future__ import annotations

from pathlib import Path

from difra.gui.session_reupload_service import SessionReuploadService


class _FakeUploadApi:
    pass


class _FakeActions:
    TRANSFER_STATUS_NOT_COMPLETE = "not_complete"
    TRANSFER_STATUS_REQ_RESEND = "req_resend"
    progress_events = []

    @staticmethod
    def _resolve_uploader_id(*, explicit_uploader_id=None, lock_user=None):
        return explicit_uploader_id or lock_user or "tester"

    @staticmethod
    def _order_paths_by_matador_group(paths, *, config=None, uploader_id=None):
        return list(paths)

    @staticmethod
    def _current_transfer_status(path, *, container_manager):
        return container_manager.get_transfer_status(path)

    @classmethod
    def _notify_progress(cls, progress_callback, **payload):
        cls.progress_events.append(payload)
        if callable(progress_callback):
            progress_callback(payload)


class _FakeContainerManager:
    def __init__(self, status: str = "unsent"):
        self.status = status

    def get_transfer_status(self, _path: Path) -> str:
        return self.status


def _service() -> SessionReuploadService:
    return SessionReuploadService(
        actions_cls=_FakeActions,
        build_upload_api=lambda *, config=None: _FakeUploadApi(),
    )


def test_session_reupload_service_reports_missing_container(tmp_path: Path):
    result = _service().reupload_archived_session_containers(
        [tmp_path / "missing.nxs.h5"],
        container_manager=_FakeContainerManager(),
        export_old_format=False,
    )

    assert result.upload_failed == 1
    assert result.failed == ["missing.nxs.h5: container not found"]


def test_session_reupload_service_blocks_not_complete_container(tmp_path: Path):
    path = tmp_path / "session.nxs.h5"
    path.write_bytes(b"placeholder")
    progress_events = []

    result = _service().reupload_archived_session_containers(
        [path],
        container_manager=_FakeContainerManager(status="not_complete"),
        export_old_format=False,
        progress_callback=progress_events.append,
    )

    assert result.upload_failed == 1
    assert "NOT_COMPLETE" in result.failed[0]
    assert progress_events[-1]["kind"] == "container_failed"
