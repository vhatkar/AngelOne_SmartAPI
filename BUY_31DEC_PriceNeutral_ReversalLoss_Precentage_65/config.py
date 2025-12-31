"""
Configuration Manager for Progressive Hedging Straddle Strategy
100% CORE LOGIC COMPLIANT - PURE PRICE-NEUTRAL HEDGING
Angel One specific configuration
Date format: DDMMMYY (e.g., 04NOV25)
Reads from .env file with changeable parameters
SIMPLIFIED: Weekend + Time checks only (user monitors holidays manually)
✅ UPDATED: Added sub-minute candle support (30-second candles)
✅ ADDED: Manual strike selection for first entry
✅ HYBRID: Updated for 2-level price-neutral hedging + Level 3 hard stop
✅ REMOVED: All buffer/trailing settings
✅ CORRECTED: Removed obsolete HEDGE_TYPE parameter
✅ CORRECTED: Removed obsolete HEDGE_OFFSET_STRIKES parameter
✅ UPDATED: Strike range display to 17 strikes (±8)
✅ ADDED: HEDGE_REVERSAL_EXIT_PCT parameter for adjustable hedge exit thresholds
"""

import os
from datetime import datetime, time
from typing import List
from dotenv import load_dotenv
import pytz

load_dotenv(override=True)


class Config:
    """Configuration class for strategy parameters"""

    def __init__(self):
        """Initialize configuration"""
        self.load_api_credentials()
        self.load_timing_parameters()
        self.load_trading_parameters()
        self.load_hedging_parameters()
        self.load_monitoring_settings()
        self.load_file_settings()
        self.validate_config()

    def load_api_credentials(self):
        """Load Angel One API credentials"""
        self.API_KEY = os.getenv('API_KEY')
        self.CLIENT_ID = os.getenv('CLIENT_ID')
        self.PASSWORD = os.getenv('PASSWORD')
        self.TOTP_SECRET = os.getenv('TOTP_SECRET')
        
        # JWT refresh credentials (for fast token refresh)
        self.CLIENT_LOCAL_IP = os.getenv('CLIENT_LOCAL_IP', '127.0.0.1')
        self.CLIENT_PUBLIC_IP = os.getenv('CLIENT_PUBLIC_IP', '106.193.147.98')
        self.MAC_ADDRESS = os.getenv('MAC_ADDRESS', '34:e6:d7:58:a9:aa')

    def load_timing_parameters(self):
        """Load market timing parameters"""
        # Expiry Date (Angel One format: DDMMMYY - e.g., "04NOV25")
        self.EXPIRY_DATE = os.getenv('EXPIRY_DATE', '04NOV25')

        # Market Timing
        self.MARKET_OPEN_TIME = self.parse_time(os.getenv('MARKET_OPEN_TIME', '09:15'))
        self.ENTRY_WINDOW_START = self.parse_time(os.getenv('ENTRY_WINDOW_START', '09:20'))
        self.ENTRY_WINDOW_END = self.parse_time(os.getenv('ENTRY_WINDOW_END', '14:30'))
        self.SQUARE_OFF_TIME = self.parse_time(os.getenv('SQUARE_OFF_TIME', '15:25'))
        self.MARKET_CLOSE_TIME = self.parse_time(os.getenv('MARKET_CLOSE_TIME', '15:30'))

        # Set timezone to IST
        self.TIMEZONE = pytz.timezone('Asia/Kolkata')

    def load_trading_parameters(self):
        """Load trading parameters"""
        self.UNDERLYING = os.getenv('UNDERLYING', 'NIFTY')
        self.LOT_SIZE = int(os.getenv('LOT_SIZE', '65'))
        self.STRIKE_INTERVAL = int(os.getenv('STRIKE_INTERVAL', '50'))

        # ✅ CORRECTED: Removed obsolete HEDGE_OFFSET_STRIKES
        
        # 🎯 Strike selection (MANUAL = first entry only, then AUTO)
        self.STRIKE_SELECTION_MODE = os.getenv('STRIKE_SELECTION_MODE', 'AUTO').upper()
        self.MANUAL_STRIKE = int(os.getenv('MANUAL_STRIKE', '0')) if os.getenv('MANUAL_STRIKE') else None
        self.VALIDATE_STRIKE_IN_CHAIN = os.getenv('VALIDATE_STRIKE_IN_CHAIN', 'true').lower() == 'true'
        self.MIN_PREMIUM_THRESHOLD = float(os.getenv('MIN_PREMIUM_THRESHOLD', '5.0'))

    def load_hedging_parameters(self):
        """CORE LOGIC: Load hedging parameters - PURE PRICE-NEUTRAL"""
        # 🔥 HYBRID: 2 levels instead of 4
        levels_str = os.getenv('PROGRESSIVE_HEDGING_LEVELS', '20,40')
        self.PROGRESSIVE_HEDGING_LEVELS = [float(x.strip()) for x in levels_str.split(',')]

        # 🔥 HYBRID: Level 3 hard stop (60%) instead of Level 5
        self.LEVEL_3_HARD_STOP = float(os.getenv('LEVEL_3_HARD_STOP', '60'))
        
        # 🔥 NEW: Hedge reversal exit percentage
        self.HEDGE_REVERSAL_EXIT_PCT = float(os.getenv('HEDGE_REVERSAL_EXIT_PCT', '10'))

        # ✅ CORRECTED: Removed HEDGE_STRIKE_DISTANCE calculation
        # Dynamic strike selection based on premium matching - no fixed distances

        # CORE LOGIC: Premium ratio threshold (0.33)
        self.FORCE_EXIT_RATIO = float(os.getenv('FORCE_EXIT_RATIO', '0.33'))

    def load_monitoring_settings(self):
        """✅ UPDATED: Load monitoring settings with sub-minute candle support"""
        # ✅ NEW: Candle interval in seconds (supports sub-minute)
        candle_interval_seconds = int(os.getenv('CANDLE_INTERVAL_SECONDS', '60'))
        
        # Validate minimum interval
        if candle_interval_seconds < 30:
            raise ValueError("Minimum candle interval is 30 seconds for API rate limit safety")
        
        if candle_interval_seconds > 300:  # 5 minutes
            print("⚠️ Warning: Candle interval > 5 minutes may miss rapid price movements")
        
        self.CANDLE_INTERVAL_SECONDS = candle_interval_seconds
        self.CANDLE_TIMEFRAME_MINUTES = candle_interval_seconds / 60

        # Re-entry Logic
        self.RE_ENTRY_WAIT_CANDLES = int(os.getenv('RE_ENTRY_WAIT_CANDLES', '1'))

        # Monitoring
        self.POLL_INTERVAL_SECONDS = int(os.getenv('POLL_INTERVAL_SECONDS', '30'))
        self.POSITION_RECONCILE_FREQUENCY = int(os.getenv('POSITION_RECONCILE_FREQUENCY', '5'))

        # 🔥 NEW: Debug mode (optional)
        self.ENABLE_DEBUG_MODE = os.getenv('ENABLE_DEBUG_MODE', 'False').lower() == 'true'

    def load_file_settings(self):
        """Load file paths"""
        self.EXCEL_LOG_PATH = os.getenv('EXCEL_FILE', 'angelone_live_trades.xlsx')
        
        # Emergency stop system
        self.EMERGENCY_STOP_FILE = "EMERGENCY_STOP.flag"

    def parse_time(self, time_str: str) -> time:
        """Parse time string to time object"""
        try:
            hour, minute = map(int, time_str.split(':'))
            return time(hour, minute)
        except:
            return time(9, 15)

    def get_current_ist_time(self) -> datetime:
        """Get current IST time - FIXED VERSION"""
        # Get current UTC time and convert to IST
        utc_now = datetime.now(pytz.UTC)
        ist_tz = pytz.timezone('Asia/Kolkata')
        ist_now = utc_now.astimezone(ist_tz)
        return ist_now

    def is_entry_window_open(self) -> bool:
        """Check if entry window is open"""
        current_time = self.get_current_ist_time().time()
        return self.ENTRY_WINDOW_START <= current_time < self.ENTRY_WINDOW_END

    def should_force_squareoff(self) -> bool:
        """Check if force square-off time reached"""
        current_time = self.get_current_ist_time().time()
        return current_time >= self.SQUARE_OFF_TIME

    def is_market_open(self) -> bool:
        """
        SIMPLIFIED: Check if market is open (weekends + time only)
        User monitors holidays manually for flexibility
        """
        current_time = self.get_current_ist_time()
        current_weekday = current_time.weekday()  # Monday=0, Sunday=6
        
        # Check if weekend (Saturday=5, Sunday=6)
        if current_weekday >= 5:
            print(f"[MARKET CLOSED] Weekend - {current_time.strftime('%A, %d %b %Y')}")
            return False
        
        # Check time window (9:15 AM to 3:30 PM)
        current_time_only = current_time.time()
        is_open = self.MARKET_OPEN_TIME <= current_time_only <= self.MARKET_CLOSE_TIME
        
        if not is_open:
            print(f"[MARKET CLOSED] Outside trading hours - {current_time_only.strftime('%H:%M:%S')}")
            print(f"   Trading hours: {self.MARKET_OPEN_TIME.strftime('%H:%M')} - {self.MARKET_CLOSE_TIME.strftime('%H:%M')}")
        
        return is_open

    def should_skip_trading(self) -> bool:
        """
        SIMPLIFIED: Check if we should skip trading
        Only checks weekends + time (user handles holidays manually)
        """
        return not self.is_market_open()

    def validate_expiry_format(self) -> bool:
        """Validate Angel One expiry format (DDMMMYY)"""
        try:
            if len(self.EXPIRY_DATE) != 7:
                return False

            day = self.EXPIRY_DATE[:2]
            month = self.EXPIRY_DATE[2:5]
            year = self.EXPIRY_DATE[5:]

            # Check day is numeric
            if not day.isdigit() or int(day) < 1 or int(day) > 31:
                return False

            # Check month is valid
            valid_months = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
                            'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
            if month.upper() not in valid_months:
                return False

            # Check year is numeric
            if not year.isdigit():
                return False

            return True

        except Exception as e:
            print(f"[ERROR] Error validating expiry format: {e}")
            return False

    def validate_config(self):
        """Validate configuration - HYBRID: Updated for 2 levels"""
        # Validate API credentials
        if not self.API_KEY or not self.CLIENT_ID or not self.PASSWORD or not self.TOTP_SECRET:
            raise ValueError("[ERROR] Angel One credentials not configured in .env file")

        # Validate expiry format
        if not self.validate_expiry_format():
            raise ValueError(f"[ERROR] Invalid expiry format: '{self.EXPIRY_DATE}'. Expected DDMMMYY (e.g., '04NOV25')")

        # 🔥 HYBRID: Validate 2 hedging levels instead of 4
        if len(self.PROGRESSIVE_HEDGING_LEVELS) != 2:
            raise ValueError(f"[ERROR] Must have exactly 2 hedging levels, found {len(self.PROGRESSIVE_HEDGING_LEVELS)}")

        # ✅ CORRECTED: Removed HEDGE_STRIKE_DISTANCE validation

        # Validate hedging levels are in ascending order
        for i in range(len(self.PROGRESSIVE_HEDGING_LEVELS) - 1):
            if self.PROGRESSIVE_HEDGING_LEVELS[i] >= self.PROGRESSIVE_HEDGING_LEVELS[i + 1]:
                raise ValueError("[ERROR] Hedging levels must be in ascending order")

        # 🔥 HYBRID: Validate Level 3 is higher than Level 2
        if self.LEVEL_3_HARD_STOP <= self.PROGRESSIVE_HEDGING_LEVELS[-1]:
            raise ValueError("Level 3 hard stop must be higher than Level 2")

        # Validate HEDGE_REVERSAL_EXIT_PCT is positive and reasonable
        if self.HEDGE_REVERSAL_EXIT_PCT <= 0 or self.HEDGE_REVERSAL_EXIT_PCT >= 30:
            raise ValueError("[ERROR] HEDGE_REVERSAL_EXIT_PCT must be between 0 and 30")

        # Validate time sequence
        if not (self.MARKET_OPEN_TIME < self.ENTRY_WINDOW_START <
                self.ENTRY_WINDOW_END < self.SQUARE_OFF_TIME < self.MARKET_CLOSE_TIME):
            raise ValueError("[ERROR] Invalid time sequence in configuration")

        print("[OK] Configuration validated successfully")

    def display_config(self):
        """✅ UPDATED: Display configuration with manual strike selection + PURE PRICE-NEUTRAL"""
        print(f"\n{'=' * 80}")
        print(f"PROGRESSIVE HEDGING STRADDLE - ANGEL ONE CONFIGURATION")
        print(f"{'=' * 80}")
        print(f"Underlying: {self.UNDERLYING}")
        print(f"Lot Size: {self.LOT_SIZE}")
        print(f"Expiry Date: {self.EXPIRY_DATE} (Angel One format: DDMMMYY)")
        print(f"Strike Interval: {self.STRIKE_INTERVAL}")

        # 🎯 NEW: Display strike selection mode
        print(f"\n🎯 STRIKE SELECTION:")
        if self.STRIKE_SELECTION_MODE == 'MANUAL' and self.MANUAL_STRIKE:
            print(f"   Mode: MANUAL")
            print(f"   Strike: {self.MANUAL_STRIKE}")
            print(f"   Scope: FIRST ENTRY ONLY")
            print(f"   ✅ First entry: {self.MANUAL_STRIKE}")
            print(f"   ✅ Re-entries: AUTO ATM (smart re-entry)")
        else:
            print(f"   Mode: AUTO (ATM calculated)")
            print(f"   System calculates ATM for all entries")

        print(f"\nMarket Timing:")
        print(f"   Market Open: {self.MARKET_OPEN_TIME}")
        print(f"   Entry Window: {self.ENTRY_WINDOW_START} - {self.ENTRY_WINDOW_END}")
        print(f"   Force Square-off: {self.SQUARE_OFF_TIME}")
        print(f"   Market Close: {self.MARKET_CLOSE_TIME}")

        print(f"\nMarket Status:")
        current_time = self.get_current_ist_time()
        print(f"   Current Time: {current_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"   Day: {current_time.strftime('%A')}")
        print(f"   Market Open: {'[YES]' if self.is_market_open() else '[NO]'}")
        print(f"   Entry Window Open: {'[YES]' if self.is_entry_window_open() else '[NO]'}")
        
        print(f"\n   NOTE: Holiday monitoring is MANUAL (user responsibility)")
        print(f"         Only weekends are auto-blocked")

        print(f"\nMonitoring:")
        # ✅ UPDATED: Show in seconds for sub-minute intervals
        if self.CANDLE_INTERVAL_SECONDS < 60:
            print(f"   Candle Timeframe: {self.CANDLE_INTERVAL_SECONDS} seconds")
        else:
            print(f"   Candle Timeframe: {self.CANDLE_TIMEFRAME_MINUTES} minute(s)")
        print(f"   Poll Interval: {self.POLL_INTERVAL_SECONDS} seconds")
        print(f"   Re-entry Wait: {self.RE_ENTRY_WAIT_CANDLES} candle(s)")

        # 🔥 PURE LOGIC: Updated for 2-level price-neutral display
        print(f"\nCORE LOGIC - Progressive Hedging Levels (PURE PRICE-NEUTRAL):")
        for i, level in enumerate(self.PROGRESSIVE_HEDGING_LEVELS, 1):
            exit_level = level - self.HEDGE_REVERSAL_EXIT_PCT
            print(f"   Level {i}: Entry @ {level}% loss, Exit @ {exit_level}% loss → SELL Hedge")
        print(f"   Level 3: {self.LEVEL_3_HARD_STOP}% loss → HARD STOP (No Hedge)")
        print(f"\nHedge Reversal Exit: {self.HEDGE_REVERSAL_EXIT_PCT}% retrace from entry level")

        print(f"\nCORE LOGIC - Exit Parameters:")
        print(f"   Premium Ratio Threshold: <= {self.FORCE_EXIT_RATIO} (1:{1 / self.FORCE_EXIT_RATIO:.1f})")
        print(f"   🚫 NO BUFFER/NO TRAILING - Pure SELL hedge strategy")

        print(f"\nOption Chain Configuration:")
        print(f"   ✅ BALANCED: 17 strikes (±8) for optimal hedge coverage")  # ✅ UPDATED: Changed from 13 to 17 strikes

        print(f"\nHedge Configuration:")
        # ✅ CORRECTED: Removed HEDGE_TYPE and HEDGE_OFFSET_STRIKES display
        print(f"   🔥 DYNAMIC Strike Selection: Based on premium matching")
        print(f"   🔥 FALLBACK: Sell opposing leg at SAME strike (profit leg)")
        print(f"   🔥 NO FIXED OFFSET - Pure price-neutral logic")

        print(f"\nFiles:")
        print(f"   Excel Log: {self.EXCEL_LOG_PATH}")
        print(f"{'=' * 80}\n")

        # Display AMX session info
        self.display_amx_session_info()

    def display_amx_session_info(self):
        """Display AMX session management info"""
        print(f"\n⚡ AMX SESSION MANAGEMENT:")
        print(f"   AMX Gateway Timeout: ~25 minutes")
        print(f"   Proactive Refresh: Every 20 minutes")
        print(f"   Error Codes Handled:")
        print(f"     • AB1007 = AMX Error (session expired)")
        print(f"     • AB1010 = AMX Session Expired")
        print(f"     • AG8001 = Invalid Token (JWT)")
        print(f"     • AG8002 = Token Expired (JWT)")
        print(f"   Recovery: Full re-login (creates new AMX session)")

    def is_emergency_stop(self) -> bool:
        """Check if emergency stop flag exists"""
        return os.path.exists(self.EMERGENCY_STOP_FILE)

    def create_emergency_stop(self, reason: str = "Manual emergency stop"):
        """Create emergency stop flag to prevent auto re-entry"""
        timestamp = self.get_current_ist_time().strftime('%Y-%m-%d %H:%M:%S')
        with open(self.EMERGENCY_STOP_FILE, 'w') as f:
            f.write(f"Emergency Stop Activated\n")
            f.write(f"Time: {timestamp}\n")
            f.write(f"Reason: {reason}\n")
        print("\n" + "="*80)
        print("🚨 EMERGENCY STOP FLAG CREATED!")
        print("="*80)
        print(f"Reason: {reason}")
        print(f"Time: {timestamp}")
        print("\nScript will NOT auto-enter positions on restart.")
        print("\nTo resume trading:")
        print(f"  rm {self.EMERGENCY_STOP_FILE}")
        print("="*80 + "\n")

    def remove_emergency_stop(self):
        """Remove emergency stop flag"""
        if os.path.exists(self.EMERGENCY_STOP_FILE):
            os.remove(self.EMERGENCY_STOP_FILE)
            print("✅ Emergency stop flag removed - trading re-enabled")


# Global config instance
config = Config()