"""Operator-selection helpers for the new-session dialog."""

from __future__ import annotations

from difra.gui.qt_compat import QDialog


class NewSessionDialogOperatorMixin:
    def _populate_operator_combo(self):
        """Populate operator combo box."""
        self.operator_combo.clear()

        operators = self.operator_manager.get_all_operators()

        if not operators:
            self.operator_combo.addItem("No operators defined", None)
            return

        current_id = self.operator_manager.get_current_operator_id()
        current_index = 0

        for i, (op_id, _op_info) in enumerate(sorted(operators.items())):
            display_name = self.operator_manager.get_operator_display_name(op_id)
            self.operator_combo.addItem(display_name, op_id)
            if op_id == current_id:
                current_index = i

        if current_id and current_index < self.operator_combo.count():
            self.operator_combo.setCurrentIndex(current_index)

    def _update_operator_details(self):
        """Update operator details display."""
        operator_id = self.operator_combo.currentData()

        if not operator_id:
            self.operator_details_label.setText("No operator selected")
            return

        operator = self.operator_manager.get_operator(operator_id)
        if not operator:
            self.operator_details_label.setText("Operator not found")
            return

        details = f"{operator['name']} {operator['surname']} | {operator.get('email', 'N/A')}"
        if operator.get("institution"):
            details += f" | {operator['institution']}"

        self.operator_details_label.setText(details)

    def _restore_last_operator_selection(self) -> None:
        """Restore the last confirmed operator for the next DIFRA run."""
        last_operator_id = str(
            self.settings.value("matador/last_operator_id", "", type=str) or ""
        ).strip()
        if last_operator_id:
            self.operator_manager.set_current_operator(last_operator_id)

    def _on_operator_changed(self):
        """Handle operator selection change."""
        self._update_operator_details()

    def _on_add_new_operator(self):
        """Handle add new operator button."""
        from difra.gui.operator_manager import NewOperatorDialog

        dialog = NewOperatorDialog(self.operator_manager, self)

        if dialog.exec_() == QDialog.Accepted:
            new_operator_id = dialog.get_operator_id()
            self._populate_operator_combo()
            for i in range(self.operator_combo.count()):
                if self.operator_combo.itemData(i) == new_operator_id:
                    self.operator_combo.setCurrentIndex(i)
                    break
