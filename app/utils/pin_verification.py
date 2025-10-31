"""PIN verification utilities for voice and DTMF input."""
import re
from typing import Optional, Callable, Awaitable
from app.config import Config


class PINVerifier:
    """Handles PIN verification via voice and DTMF."""
    
    def __init__(self, config: Config):
        self.config = config
        self.dtmf_digits: list[str] = []
        self.voice_input_buffer: str = ""
    
    def get_expected_pin(self, caller_id: str) -> Optional[str]:
        """Get the expected PIN for a caller."""
        return self.config.get_pin(caller_id)
    
    def verify_voice_pin(self, text: str, expected_pin: str) -> bool:
        """
        Verify PIN from voice input.
        Tries to extract numeric sequences from the text.
        """
        # Extract all numeric sequences
        numbers = re.findall(r'\d+', text)
        
        # Check if any sequence matches the PIN
        for num_str in numbers:
            if num_str == expected_pin:
                return True
        
        # Also check if PIN is spelled out (e.g., "one two three four")
        # This is a simple implementation - could be enhanced
        text_lower = text.lower()
        pin_digits = list(expected_pin)
        words = text_lower.split()
        
        # Map common number words
        number_map = {
            "zero": "0", "oh": "0", "o": "0",
            "one": "1", "won": "1",
            "two": "2", "to": "2", "too": "2",
            "three": "3", "tree": "3",
            "four": "4", "for": "4", "fore": "4",
            "five": "5",
            "six": "6", "sicks": "6",
            "seven": "7",
            "eight": "8", "ate": "8",
            "nine": "9", "nein": "9",
        }
        
        extracted_digits = []
        for word in words:
            if word in number_map:
                extracted_digits.append(number_map[word])
            elif word.isdigit():
                extracted_digits.append(word)
        
        extracted_pin = "".join(extracted_digits)
        if expected_pin in extracted_pin:
            return True
        
        return False
    
    def add_dtmf_digit(self, digit: str):
        """Add a DTMF digit to the buffer."""
        if digit.isdigit():
            self.dtmf_digits.append(digit)
            # Keep only last 10 digits to prevent buffer overflow
            if len(self.dtmf_digits) > 10:
                self.dtmf_digits.pop(0)
    
    def verify_dtmf_pin(self, expected_pin: str) -> bool:
        """Verify PIN from DTMF digits."""
        if len(self.dtmf_digits) < len(expected_pin):
            return False
        
        # Check if the last N digits match (where N is PIN length)
        pin_length = len(expected_pin)
        last_digits = "".join(self.dtmf_digits[-pin_length:])
        return last_digits == expected_pin
    
    def reset(self):
        """Reset verification state."""
        self.dtmf_digits = []
        self.voice_input_buffer = ""
    
    async def verify_pin(
        self,
        caller_id: str,
        voice_text: Optional[str] = None,
        dtmf_digit: Optional[str] = None,
        get_voice_input: Optional[Callable[[], Awaitable[str]]] = None,
        prompt_for_dtmf: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> bool:
        """
        Verify PIN using voice and/or DTMF.
        
        Args:
            caller_id: The caller's phone number
            voice_text: Optional voice input text to check
            dtmf_digit: Optional DTMF digit received
            get_voice_input: Async function to get voice input if needed
            prompt_for_dtmf: Async function to prompt for DTMF input
        
        Returns:
            True if PIN is verified, False otherwise
        """
        expected_pin = self.get_expected_pin(caller_id)
        if not expected_pin:
            # No PIN required
            return True
        
        # Handle DTMF digit
        if dtmf_digit:
            self.add_dtmf_digit(dtmf_digit)
            if self.verify_dtmf_pin(expected_pin):
                self.reset()
                return True
        
        # Handle voice input
        if voice_text:
            if self.verify_voice_pin(voice_text, expected_pin):
                self.reset()
                return True
        
        # Try to get voice input if not provided
        if get_voice_input and not voice_text:
            try:
                voice_input = await get_voice_input()
                if self.verify_voice_pin(voice_input, expected_pin):
                    self.reset()
                    return True
            except Exception:
                pass
        
        # If DTMF verification is available, check current buffer
        if self.verify_dtmf_pin(expected_pin):
            self.reset()
            return True
        
        return False

