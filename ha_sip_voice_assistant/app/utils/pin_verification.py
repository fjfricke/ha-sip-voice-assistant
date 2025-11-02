"""PIN verification utilities."""
from typing import Optional
from app.config import Config


class PINVerifier:
    """Handles PIN verification."""
    
    def __init__(self, config: Config):
        self.config = config
    
    def get_expected_pin(self, caller_id: str) -> Optional[int]:
        """Get the expected PIN for a caller."""
        return self.config.get_pin(caller_id)
    
    def reset(self):
        """Reset verification state."""
        pass

