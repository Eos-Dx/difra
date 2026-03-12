# zone_measurements/logic/file_mixin.py

import json
import logging
import os
from pathlib import Path

import numpy as np
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFileDialog, QListWidgetItem

logger = logging.getLogger(__name__)


class ZoneMeasurementsFileMixin:
    def browse_folder(self):
        """
        Open a dialog to choose the save folder and set it in the UI.
        """
        if (
            hasattr(self, "_is_measurement_output_folder_locked")
            and self._is_measurement_output_folder_locked()
        ):
            locked_folder = ""
            if hasattr(self, "_current_measurement_output_folder"):
                try:
                    locked_folder = str(self._current_measurement_output_folder())
                except Exception:
                    locked_folder = ""
            from PyQt5.QtWidgets import QMessageBox

            QMessageBox.information(
                self,
                "Measurement Folder Locked",
                "Measurement output folder is locked to the active session container.\n\n"
                f"Folder: {locked_folder}",
            )
            if hasattr(self, "_refresh_measurement_output_folder_lock"):
                self._refresh_measurement_output_folder_lock()
            return
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder:
            self.folderLineEdit.setText(folder)

    def process_measurement_result(self, success, result_files, typ):
        """
        Handles measurement files, organizes by alias, converts .txt to .npy,
        and updates the UI list widget. Used for auxiliary or technical measurements.
        """
        if not success:
            logger.warning("[%s] capture failed", typ)
            print(f"[{typ}] capture failed.")
            if hasattr(self, "_aux_timer"):
                self._aux_timer.stop()
            if hasattr(self, "_aux_status"):
                self._aux_status.setText("")
            return {}
        else:
            logger.info("[%s] capture successful: %s", typ, result_files)
            print(f"[{typ}] capture successful: {result_files}")
            if hasattr(self, "_aux_timer"):
                self._aux_timer.stop()
            if hasattr(self, "_aux_status"):
                self._aux_status.setText("Done")

        file_map = {}
        for alias, txt_file in result_files.items():
            txt_path = Path(txt_file)
            dest_folder = txt_path.parent  # single folder (no alias subfolders)
            new_txt_file = dest_folder / txt_path.name
            try:
                if txt_path.resolve() != new_txt_file.resolve():
                    txt_path.replace(new_txt_file)
                else:
                    new_txt_file = txt_path
            except Exception as e:
                logger.warning(
                    "Failed moving file %s -> %s: %s",
                    txt_path,
                    new_txt_file,
                    e,
                    exc_info=True,
                )
                print(f"[ERROR] Moving file {txt_path} → {new_txt_file}: {e}")
                new_txt_file = txt_path
            try:
                data = np.loadtxt(new_txt_file)
                npy = new_txt_file.with_suffix(".npy")
                np.save(npy, data)
            except Exception as e:
                logger.warning("Conversion error for %s: %s", alias, e, exc_info=True)
                print(f"Conversion error for {alias}: {e}")
                npy = new_txt_file
            file_map[alias] = str(npy)
            if hasattr(self, "auxList"):
                item = QListWidgetItem(f"{alias}: {Path(npy).name}")
                item.setData(Qt.UserRole, str(npy))
                self.auxList.addItem(item)
        return file_map

    def handle_add_count(self):
        """
        Appends the count value to the current sample ID in the UI.
        """
        current_filename = self.fileNameLineEdit.text()
        appended_value = "_" + str(self.addCountSpinBox.value())
        self.fileNameLineEdit.setText(current_filename + appended_value)
