import logging
import queue

import matplotlib.pyplot as plt
import numpy as np

LOGGER = logging.getLogger(__name__)


def _tm():
    from difra.gui.main_window_ext import technical_measurements as tm

    return tm


class TechnicalRealtimeMixin:
    def _toggle_realtime(self, checked: bool):
        if checked:
            self._log_technical_event("Starting real-time measurement display")
            try:
                self._start_realtime()
            except Exception as exc:
                LOGGER.exception("Failed to start real-time display")
                self._log_technical_event(f"Real-time display failed: {exc}")
                self.rtBtn.blockSignals(True)
                self.rtBtn.setChecked(False)
                self.rtBtn.setText("Real-time")
                self.rtBtn.blockSignals(False)
                msg_box = getattr(_tm(), "QMessageBox", None)
                if msg_box is not None:
                    msg_box.warning(self, "Real-time", str(exc))
                return
            else:
                self.rtBtn.setText("Stop RT")
        else:
            self._log_technical_event("Stopping real-time measurement display")
            self._stop_realtime()
            self.rtBtn.setText("Real-time")

    def _realtime_detector_controllers(self):
        controllers = getattr(self, "detector_controller", None) or {}
        if controllers:
            return dict(controllers)

        client = getattr(self, "hardware_client", None)
        if client is None:
            ensure_client = getattr(self, "_ensure_hardware_client", None)
            if callable(ensure_client):
                client = ensure_client()
        if client is None:
            return {}

        controllers = getattr(client, "detector_controllers", None) or {}
        if controllers:
            self.detector_controller = dict(controllers)
            return dict(controllers)

        initialize_detector = getattr(client, "initialize_detector", None)
        if callable(initialize_detector):
            try:
                if initialize_detector():
                    controllers = getattr(client, "detector_controllers", None) or {}
            except Exception:
                LOGGER.exception("Failed to initialize detector for real-time display")
                controllers = {}
            if controllers:
                self.detector_controller = dict(controllers)
                return dict(controllers)

        return {}

    def _start_realtime(self):
        tm = _tm()
        exposure = float(self.integrationTimeSpin.value())
        frames_spin = getattr(self, "framesSpin", None)
        frames = int(frames_spin.value()) if frames_spin is not None else 1
        frames = max(frames, 1)
        self._rt_queue = queue.Queue()

        plt.ion()
        detector_controllers = self._realtime_detector_controllers()
        self._rt_detector_controllers = detector_controllers
        detector_aliases = list(detector_controllers.keys())
        n_det = len(detector_aliases)
        self._rt_img = {}
        self._rt_last_frame = {}

        if n_det == 0:
            fig, ax = plt.subplots(1, 1, figsize=(5, 5))
            ax.text(
                0.5,
                0.5,
                "No detector initialized",
                ha="center",
                va="center",
                transform=ax.transAxes,
            )
            ax.set_axis_off()
            self._rt_fig = fig
            plt.show(block=False)
            self._log_technical_event("Real-time display opened without active detectors")
            return

        fig, axes = plt.subplots(1, n_det, figsize=(5 * n_det, 5))
        axes = np.atleast_1d(axes).ravel()

        for ax, alias in zip(axes, detector_aliases):
            size = getattr(detector_controllers[alias], "size", (256, 256))
            self._rt_img[alias] = ax.imshow(
                np.zeros(size), origin="lower", interpolation="none"
            )
            ax.set_title(alias)
        self._rt_fig = fig
        plt.show(block=False)

        self._plot_timer = tm.QTimer(self)
        self._plot_timer.setInterval(50)
        self._plot_timer.timeout.connect(self._rt_plot_tick)
        self._plot_timer.start()

        def callback(frames_dict):
            for alias, frame in frames_dict.items():
                self._rt_last_frame[alias] = frame
            self._rt_queue.put(True)

        for controller in detector_controllers.values():
            controller.start_stream(
                callback=callback,
                exposure=exposure,
                interval=0.0,
                frames=frames,
            )

    def _rt_plot_tick(self):
        while True:
            try:
                _ = self._rt_queue.get_nowait()
            except queue.Empty:
                break

        for alias in self._rt_img:
            frame = self._rt_last_frame.get(alias)
            if frame is not None:
                self._rt_img[alias].set_data(frame)
                self._rt_img[alias].set_clim(frame.min(), frame.max())
        self._rt_fig.canvas.draw_idle()

    def _stop_realtime(self):
        for controller in getattr(self, "_rt_detector_controllers", {}).values():
            stop_stream = getattr(controller, "stop_stream", None)
            if callable(stop_stream):
                stop_stream()
        if hasattr(self, "_plot_timer"):
            self._plot_timer.stop()
            del self._plot_timer
        if hasattr(self, "_rt_fig"):
            plt.close(self._rt_fig)
            del self._rt_fig
        for attr in (
            "_rt_queue",
            "_rt_last_frame",
            "_rt_img",
            "_rt_detector_controllers",
        ):
            if hasattr(self, attr):
                delattr(self, attr)
