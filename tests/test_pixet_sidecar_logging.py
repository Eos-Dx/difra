import logging

from difra.scripts import pixet_sidecar_server


def test_pixet_sidecar_configures_file_logging(tmp_path, monkeypatch):
    log_path = tmp_path / "pixet_sidecar.log"
    monkeypatch.setenv("DIFRA_SIDECAR_LOG_PATH", str(log_path))

    try:
        resolved = pixet_sidecar_server._configure_sidecar_logging()
        pixet_sidecar_server.LOGGER.info("sidecar log smoke")
        for handler in logging.getLogger().handlers:
            if getattr(handler, "_difra_pixet_sidecar", False):
                handler.flush()

        assert resolved == log_path
        assert "sidecar log smoke" in log_path.read_text(encoding="utf-8")
    finally:
        root = logging.getLogger()
        for handler in list(root.handlers):
            if getattr(handler, "_difra_pixet_sidecar", False):
                root.removeHandler(handler)
                handler.close()
