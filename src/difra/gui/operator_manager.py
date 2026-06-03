"""Operator Management for DIFRA.

Manages operator information stored in JSON format with contact details.
Provides dialog for operator selection/creation on startup.
"""

import json
import hashlib
import hmac
import logging
from pathlib import Path
from typing import Dict, Optional

from PyQt5.QtWidgets import QInputDialog, QMessageBox  # noqa: F401

logger = logging.getLogger(__name__)

DEFAULT_MODIFICATION_PASSWORD_HASH = (
    "64ae5ac9f98ac4a2bb67a66cc913909022d4d0bb7d673fcf76d1999c33debd93"
)


def _load_json_with_encoding_fallback(path: Path) -> dict:
    last_error = None
    for encoding in ("utf-8", "utf-8-sig", "utf-16"):
        try:
            with open(path, "r", encoding=encoding) as file_handle:
                payload = json.load(file_handle)
            return payload if isinstance(payload, dict) else {}
        except UnicodeError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    return {}


def _hash_password(password: str) -> str:
    return hashlib.sha256(str(password).encode("utf-8")).hexdigest()


class OperatorManager:
    """Manages operator database and selection."""
    
    def __init__(self, config_path: Optional[Path] = None):
        """Initialize operator manager.
        
        Args:
            config_path: Path to operator JSON file. If None, uses default location.
        """
        if config_path is None:
            # Default to config directory in resources
            config_path = Path(__file__).parent.parent / "resources" / "config" / "operators.json"
        
        self.config_path = Path(config_path)
        self.operators: Dict[str, Dict[str, str]] = {}
        self.current_operator_id: Optional[str] = None
        self.operator_modify_password_hash: str = DEFAULT_MODIFICATION_PASSWORD_HASH
        
        # Load operators from file
        self.load_operators()
    
    def load_operators(self) -> None:
        """Load operators from JSON file."""
        if not self.config_path.exists():
            logger.info(f"Operator config not found, creating default: {self.config_path}")
            self.create_default_operators()
            return
        
        try:
            data = _load_json_with_encoding_fallback(self.config_path)
            self.operators = data.get('operators', {})
            self.current_operator_id = data.get('current_operator_id')
            loaded_hash = data.get("operator_modify_password_hash")
            if isinstance(loaded_hash, str) and loaded_hash.strip():
                self.operator_modify_password_hash = loaded_hash.strip()
            else:
                self.operator_modify_password_hash = DEFAULT_MODIFICATION_PASSWORD_HASH
                # Persist upgraded config format (without plaintext password).
                self.save_operators()
            
            logger.info(f"Loaded {len(self.operators)} operators from {self.config_path}")
        
        except Exception as e:
            logger.error(f"Failed to load operators: {e}", exc_info=True)
            QMessageBox.warning(
                None,
                "Operator Config Error",
                f"Failed to load operator configuration:\n{e}\n\nCreating default config.",
            )
            self.create_default_operators()
    
    def create_default_operators(self) -> None:
        """Create default operators file with example operator."""
        self.operators = {
            "default_operator": {
                "name": "Default Operator",
                "surname": "User",
                "email": "operator@example.com",
                "phone": "",
                "institution": "",
            }
        }
        self.current_operator_id = "default_operator"
        self.operator_modify_password_hash = DEFAULT_MODIFICATION_PASSWORD_HASH
        
        # Ensure directory exists
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Save to file
        self.save_operators()
        logger.info(f"Created default operator config: {self.config_path}")
    
    def save_operators(self) -> None:
        """Save operators to JSON file."""
        try:
            data = {
                "operators": self.operators,
                "current_operator_id": self.current_operator_id,
                "operator_modify_password_hash": self.operator_modify_password_hash,
            }
            
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            logger.debug(f"Saved operators to {self.config_path}")
        
        except Exception as e:
            logger.error(f"Failed to save operators: {e}", exc_info=True)
            raise
    
    def get_operator(self, operator_id: str) -> Optional[Dict[str, str]]:
        """Get operator information by ID.
        
        Args:
            operator_id: Operator identifier
            
        Returns:
            Operator dict with name, surname, email, etc., or None if not found
        """
        return self.operators.get(operator_id)
    
    def get_all_operators(self) -> Dict[str, Dict[str, str]]:
        """Get all operators.
        
        Returns:
            Dict mapping operator_id to operator info
        """
        return self.operators.copy()
    
    def add_operator(
        self,
        operator_id: str,
        name: str,
        surname: str,
        email: str,
        phone: str = "",
        institution: str = "",
    ) -> None:
        """Add or update an operator.
        
        Args:
            operator_id: Unique operator identifier (e.g., username)
            name: First name
            surname: Last name
            email: Email address
            phone: Phone number (optional)
            institution: Institution/organization (optional)
        """
        self.operators[operator_id] = {
            "name": name,
            "surname": surname,
            "email": email,
            "phone": phone,
            "institution": institution,
        }
        
        self.save_operators()
        logger.info(f"Added/updated operator: {operator_id} ({name} {surname})")
    
    def remove_operator(self, operator_id: str) -> bool:
        """Remove an operator.
        
        Args:
            operator_id: Operator to remove
            
        Returns:
            True if removed, False if not found
        """
        if operator_id in self.operators:
            del self.operators[operator_id]
            
            # Clear current if it was this operator
            if self.current_operator_id == operator_id:
                self.current_operator_id = None
            
            self.save_operators()
            logger.info(f"Removed operator: {operator_id}")
            return True
        
        return False
    
    def set_current_operator(self, operator_id: str) -> bool:
        """Set the current operator.
        
        Args:
            operator_id: Operator ID to set as current
            
        Returns:
            True if set successfully, False if operator not found
        """
        if operator_id not in self.operators:
            logger.warning(f"Cannot set current operator - not found: {operator_id}")
            return False
        
        self.current_operator_id = operator_id
        self.save_operators()
        logger.info(f"Set current operator: {operator_id}")
        return True
    
    def get_current_operator(self) -> Optional[Dict[str, str]]:
        """Get current operator information.
        
        Returns:
            Current operator dict, or None if not set
        """
        if self.current_operator_id:
            return self.operators.get(self.current_operator_id)
        return None
    
    def get_current_operator_id(self) -> Optional[str]:
        """Get current operator ID.
        
        Returns:
            Current operator ID, or None if not set
        """
        return self.current_operator_id
    
    def get_operator_display_name(self, operator_id: str) -> str:
        """Get display name for operator.
        
        Args:
            operator_id: Operator ID
            
        Returns:
            Formatted name (e.g., "John Doe (john@example.com)")
        """
        operator = self.get_operator(operator_id)
        if operator:
            name = f"{operator['name']} {operator['surname']}"
            email = operator.get('email', '')
            if email:
                return f"{name} ({email})"
            return name
        return operator_id

    def verify_modify_password(self, password: str) -> bool:
        if not password:
            return False
        expected = str(self.operator_modify_password_hash or "").strip()
        provided = _hash_password(password)
        return bool(expected) and hmac.compare_digest(expected, provided)


from difra.gui.operator_dialogs import NewOperatorDialog, OperatorSelectionDialog  # noqa: E402
