"""
Paper Trading Configuration
Reads all parameters from .env file
"""

import os
from datetime import time, datetime
from dotenv import load_dotenv
import pytz

load_dotenv(override=True)


class Config:
    def __init__(self):
        # API CREDENTIALS
        self.API_KEY = os.getenv('API_KEY')
        self.CLIENT_ID = os.getenv('CLIENT_ID')
        self.PASSWORD = os.getenv('PASSWORD')
        self.TOTP_SECRET = os.getenv('TOTP_SECRET')

        if not all([self.API_KEY, self.CLIENT_ID, self.PASSWORD, self.TOTP_SECRET]):
            raise ValueError("❌ Missing API credentials in .env file")

        # TRADING PARAMETERS
        self.UNDERLYING = os.getenv('UNDERLYING', 'NIFTY')
        self.LOT_SIZE = int(os.getenv('LOT_SIZE', '75'))
        self.STRIKE_INTERVAL = int(os.getenv('STRIKE_INTERVAL', '50'))
        self.EXPIRY_DATE = os.getenv('EXPIRY_DATE', '24DEC25')

        # ✅ VALIDATE EXPIRY DATE FORMAT
        if not self.EXPIRY_DATE or len(self.EXPIRY_DATE) != 7:
            raise ValueError(f"❌ Invalid EXPIRY_DATE format: {self.EXPIRY_DATE}. Expected: DDMMMYY (e.g., 24DEC25)")

        # MARKET TIMING
        self.ENTRY_WINDOW_START = self._parse_time(os.getenv('ENTRY_WINDOW_START', '09:30'))
        self.ENTRY_WINDOW_END = self._parse_time(os.getenv('ENTRY_WINDOW_END', '14:30'))
        self.SQUARE_OFF_TIME = self._parse_time(os.getenv('SQUARE_OFF_TIME', '15:20'))
        self.MARKET_OPEN_TIME = self._parse_time(os.getenv('MARKET_OPEN_TIME', '09:15'))
        self.MARKET_CLOSE_TIME = self._parse_time(os.getenv('MARKET_CLOSE_TIME', '15:30'))

        # HEDGING LEVELS
        levels_str = os.getenv('PROGRESSIVE_HEDGING_LEVELS', '20,40')
        self.PROGRESSIVE_HEDGING_LEVELS = [float(x.strip()) for x in levels_str.split(',')]
        self.LEVEL_3_HARD_STOP = float(os.getenv('LEVEL_3_HARD_STOP', '60'))
        self.HEDGE_REVERSAL_EXIT_PCT = float(os.getenv('HEDGE_REVERSAL_EXIT_PCT', '10'))
        self.FORCE_EXIT_RATIO = float(os.getenv('FORCE_EXIT_RATIO', '0.33'))

        # MONITORING
        self.CANDLE_INTERVAL_SECONDS = int(os.getenv('CANDLE_INTERVAL_SECONDS', '60'))

        # STRIKE SELECTION
        self.STRIKE_SELECTION_MODE = os.getenv('STRIKE_SELECTION_MODE', 'AUTO').upper()
        self.MIN_PREMIUM_THRESHOLD = float(os.getenv('MIN_PREMIUM_THRESHOLD', '5.0'))

        # TIMEZONE
        self.TIMEZONE = pytz.timezone('Asia/Kolkata')

        print("✅ Paper Trading Config Loaded")
        self._display_config()

    def _parse_time(self, time_str):
        """Parse time string to time object"""
        try:
            hour, minute = map(int, time_str.split(':'))
            return time(hour, minute)
        except:
            return time(9, 15)

    def get_current_ist_time(self):
        """Get current IST time"""
        utc_now = datetime.now(pytz.UTC)
        ist_now = utc_now.astimezone(self.TIMEZONE)
        return ist_now

    def is_entry_window_open(self):
        """Check if entry window is open"""
        current_time = self.get_current_ist_time().time()
        return self.ENTRY_WINDOW_START <= current_time <= self.ENTRY_WINDOW_END

    def should_force_squareoff(self):
        """Check if should square off"""
        current_time = self.get_current_ist_time().time()
        return current_time >= self.SQUARE_OFF_TIME

    def _display_config(self):
        """Display loaded configuration"""
        print(f"\n{'=' * 80}")
        print(f"🧪 PAPER TRADING CONFIGURATION 🧪")
        print(f"{'=' * 80}")
        print(f"Underlying: {self.UNDERLYING}")
        print(f"Lot Size: {self.LOT_SIZE}")
        print(f"Expiry: {self.EXPIRY_DATE}")
        print(f"Strike Interval: {self.STRIKE_INTERVAL}")
        print(f"\n⏰ Timing:")
        print(f"  Entry Window: {self.ENTRY_WINDOW_START} - {self.ENTRY_WINDOW_END}")
        print(f"  Square Off: {self.SQUARE_OFF_TIME}")
        print(f"\n📊 Hedging Levels:")
        for i, level in enumerate(self.PROGRESSIVE_HEDGING_LEVELS, 1):
            print(f"  Level {i}: {level}%")
        print(f"  Level 3 Hard Stop: {self.LEVEL_3_HARD_STOP}%")
        print(f"\n🔍 Monitoring:")
        print(f"  Candle Interval: {self.CANDLE_INTERVAL_SECONDS}s")
        print(f"{'=' * 80}\n")


config = Config()
