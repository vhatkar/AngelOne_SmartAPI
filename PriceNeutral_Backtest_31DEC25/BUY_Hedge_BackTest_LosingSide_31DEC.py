"""

Price-Neutral Progressive Hedging Straddle Strategy - BACKTEST VERSION
MODIFIED: BUY HEDGE ON LOSING SIDE

Ganapati Bappa Morya! 🙏

NEW LOGIC (MODIFIED):

- Sell ATM straddle using ±7 strike method (minimum CE-PE difference)

- When losing leg reaches configured % from original price:

  * Level 1: 20% loss → BUY hedge on LOSING side to protect
  * Level 2: 40% loss → Square off L1 hedge, BUY new hedge on losing side
  * Level 3: 60% loss → Square off all, sell fresh straddle

- Exit hedges when losing leg returns to trigger price

- Both CE and PE directions supported

- Hedges BOUGHT (not sold) to provide protection on losing side

- Hedge size based on difference between both legs

Version: 2.0_BUY_HEDGE_LOSING_SIDE

✅ MODIFIED: Changed from SELL hedge on profit side to BUY hedge on losing side
✅ MODIFIED: Hedge strike selection now finds OTM strike on losing side
✅ MODIFIED: P&L calculations adjusted for bought hedges (we pay premium)

"""

import os
import json
import pandas as pd
import numpy as np
from datetime import datetime, time as dtime
from pathlib import Path
import glob
import warnings
warnings.filterwarnings('ignore')

# ============================================================================
# USER CONFIGURATION
# ============================================================================

EXPIRY_DATE = "2026-01-06"
START_DATE = "2025-12-16"
END_DATE = "2026-01-01"

class BacktestConfig:
    """Configuration for backtesting"""

    # Strategy Parameters - BUY Hedge on Losing Side
    LEVEL1_TRIGGER_PCT = 20  # Level 1: Losing leg moves 20% from original
    LEVEL2_TRIGGER_PCT = 40  # Level 2: Losing leg moves 40% from original
    LEVEL3_TRIGGER_PCT = 60  # Level 3: Losing leg moves 60% from original (exit all)
    HEDGE_REVERSAL_EXIT_PCT = 20  # Exit hedge when loss retraces by this % from entry

    # Trading Parameters
    LOT_SIZE = 65
    STRIKE_INTERVAL = 50
    ATM_RANGE = 15  # ±7 strikes for ATM selection

    # Market Timing
    MARKET_OPEN_TIME = dtime(9, 15)
    ENTRY_WINDOW_START = dtime(9, 59)
    ENTRY_WINDOW_END = dtime(14, 55)
    SQUAREOFF_TIME = dtime(15, 19)
    MARKET_CLOSE_TIME = dtime(15, 30)

    # Re-entry Logic
    REENTRY_WAIT_CANDLES = 1

    # Files (set in main)
    NIFTY_CSV = None
    OPTIONS_FOLDER = None
    MASTER_JSON = None
    EXPIRY_DATE = None

    # Debug/Logging
    VERBOSE_LOGGING = True

config = BacktestConfig()

# ============================================================================
# TRANSACTION LOGGER
# ============================================================================

class TransactionLogger:
    """Logs all transactions for audit trail"""

    def __init__(self):
        self.transactions = []

    def log(self, timestamp, action, details):
        """Log a transaction"""
        self.transactions.append({
            'timestamp': timestamp,
            'action': action,
            **details
        })

    def to_dataframe(self):
        """Convert to DataFrame"""
        if not self.transactions:
            return pd.DataFrame()
        return pd.DataFrame(self.transactions)

# ============================================================================
# HISTORICAL DATA MANAGER
# ============================================================================

class HistoricalDataManager:
    """Manages historical data loading and retrieval"""

    def __init__(self):
        self.nifty_data = None
        self.options_data = {}
        self.instruments_master = None
        self.expiry_date = None

    def load_data(self):
        """Load all required data files"""
        print("\n" + "="*80)
        print("[LOAD] LOADING HISTORICAL DATA")
        print("="*80)

        # Load NIFTY spot data
        if os.path.exists(config.NIFTY_CSV):
            self.nifty_data = pd.read_csv(config.NIFTY_CSV)
            self.nifty_data['datetime'] = pd.to_datetime(self.nifty_data['datetime'])
            self.nifty_data.set_index('datetime', inplace=True)
            print(f"[OK] Loaded NIFTY data: {len(self.nifty_data):,} records")
            print(f"     Date range: {self.nifty_data.index.min()} to {self.nifty_data.index.max()}")
        else:
            raise FileNotFoundError(f"NIFTY CSV not found: {config.NIFTY_CSV}")

        # Load instruments master
        if os.path.exists(config.MASTER_JSON):
            with open(config.MASTER_JSON, 'r') as f:
                self.instruments_master = json.load(f)
            print(f"[OK] Loaded instruments master: {len(self.instruments_master):,} instruments")
        else:
            print(f"[WARN] Master JSON not found: {config.MASTER_JSON}")

        # Load option data
        if os.path.exists(config.OPTIONS_FOLDER):
            option_files = glob.glob(os.path.join(config.OPTIONS_FOLDER, "*.csv"))
            print(f"[OK] Found {len(option_files):,} option CSV files")

            for file_path in option_files:
                filename = os.path.basename(file_path)
                symbol = filename.replace('.csv', '').upper()

                try:
                    df = pd.read_csv(file_path)
                    df['datetime'] = pd.to_datetime(df['datetime'])
                    df.set_index('datetime', inplace=True)
                    self.options_data[symbol] = df
                except Exception as e:
                    print(f"[WARN] Could not load {filename}: {e}")

            print(f"[OK] Loaded {len(self.options_data):,} option instruments")
        else:
            raise FileNotFoundError(f"Options folder not found: {config.OPTIONS_FOLDER}")

        print("="*80)

    def get_nifty_price(self, timestamp):
        """Get NIFTY spot price at timestamp"""
        try:
            if timestamp in self.nifty_data.index:
                return self.nifty_data.loc[timestamp, 'close']
            return None
        except:
            return None

    def get_option_price(self, symbol, timestamp):
        """Get option price at timestamp"""
        try:
            if symbol in self.options_data:
                if timestamp in self.options_data[symbol].index:
                    return self.options_data[symbol].loc[timestamp, 'close']
            return None
        except:
            return None

    def find_atm_strike(self, spot_price, timestamp):
        """Find ATM strike using ±7 method with minimum CE-PE difference"""
        base_strike = round(spot_price / config.STRIKE_INTERVAL) * config.STRIKE_INTERVAL

        print(f"\n[SCAN] Scanning straddles within +/-{config.ATM_RANGE} strikes of {int(base_strike)}...")

        best_strike = None
        min_difference = float('inf')

        # Check ±7 strikes
        for offset in range(-config.ATM_RANGE, config.ATM_RANGE + 1):
            strike = base_strike + (offset * config.STRIKE_INTERVAL)

            # Build symbols
            ce_symbol = self._build_option_symbol(strike, 'CE')
            pe_symbol = self._build_option_symbol(strike, 'PE')

            # Get premiums
            ce_price = self.get_option_price(ce_symbol, timestamp)
            pe_price = self.get_option_price(pe_symbol, timestamp)

            if ce_price and pe_price and ce_price > 0 and pe_price > 0:
                difference = abs(ce_price - pe_price)
                print(f"     Strike {int(strike)}: CE={ce_price:.2f}, PE={pe_price:.2f}, Diff={difference:.2f}")

                if difference < min_difference:
                    min_difference = difference
                    best_strike = strike

        if best_strike:
            print(f"     [OK] Selected: {int(best_strike)} (Diff: {min_difference:.2f})")

        return best_strike

    def _build_option_symbol(self, strike, option_type):
        """Build option symbol from strike and type matching CSV naming convention"""
        # Format: NIFTY02DEC2526000CE_ONE_MINUTE
        expiry_str = datetime.strptime(config.EXPIRY_DATE, '%Y-%m-%d').strftime('%d%b%y').upper()
        return f"NIFTY{expiry_str}{int(strike)}{option_type}_ONE_MINUTE"

    def find_hedge_strike_by_premium(self, target_premium, option_type, timestamp, current_strike):
        """Find OTM strike on LOSING side with premium closest to target"""
        if target_premium <= 0:
            return None, None

        # Get base spot price
        spot = self.get_nifty_price(timestamp)
        if not spot:
            return None, None

        base_strike = round(spot / config.STRIKE_INTERVAL) * config.STRIKE_INTERVAL

        # Search range: 500 points in each direction
        search_range = 10  # 10 strikes = 500 points

        best_strike = None
        best_symbol = None
        min_diff = float('inf')

        # ✅ MODIFIED: For losing CE (market up), buy OTM CE (above current)
        # For losing PE (market down), buy OTM PE (below current)
        if option_type == 'CE':
            # Search above current strike (OTM calls - protection when market rises)
            start_strike = current_strike + config.STRIKE_INTERVAL
            end_strike = base_strike + (search_range * config.STRIKE_INTERVAL)
        else:  # PE
            # Search below current strike (OTM puts - protection when market falls)
            start_strike = base_strike - (search_range * config.STRIKE_INTERVAL)
            end_strike = current_strike - config.STRIKE_INTERVAL

        strike = start_strike
        while strike <= end_strike:
            symbol = self._build_option_symbol(strike, option_type)
            price = self.get_option_price(symbol, timestamp)

            if price and price > 0:
                diff = abs(price - target_premium)
                if diff < min_diff:
                    min_diff = diff
                    best_strike = strike
                    best_symbol = symbol

            strike += config.STRIKE_INTERVAL

        return best_strike, best_symbol

# ============================================================================
# LEG CLASS - Represents one side of straddle (CE or PE)
# ============================================================================

class Leg:
    """Represents one leg of the straddle"""

    def __init__(self, symbol, strike, option_type, entry_premium):
        self.symbol = symbol
        self.strike = strike
        self.option_type = option_type  # 'CE' or 'PE'
        self.entry_premium = entry_premium
        self.original_premium = entry_premium  # Store for trigger calculations
        self.current_premium = entry_premium

        # Hedge tracking
        self.hedge_active = False
        self.hedge_level = 0  # 0, 1, 2 (Level 3 exits all)
        self.hedge_symbol = None
        self.hedge_strike = None
        self.hedge_entry_premium = None
        self.hedge_current_premium = None

        # P&L tracking
        self.realized_hedge_pnl = 0  # Accumulated from closed hedges
        self.total_hedges_bought = 0  # ✅ MODIFIED: Changed from sold to bought

        # Loading state for triggers
        self.l1_loaded = True  # L1 ready to trigger initially
        self.l2_loaded = True  # L2 ready to trigger initially

    def get_loss_pct(self):
        """Calculate loss percentage from original premium"""
        if self.original_premium == 0:
            return 0
        return ((self.current_premium - self.original_premium) / self.original_premium) * 100

    def get_pnl(self):
        """Get P&L for main leg (sold straddle)"""
        return (self.entry_premium - self.current_premium) * config.LOT_SIZE

    def get_hedge_pnl(self):
        """Get P&L for active hedge"""
        if not self.hedge_active or not self.hedge_current_premium:
            return 0

        # ✅ MODIFIED: We BOUGHT the hedge, so profit when it goes UP
        return (self.hedge_current_premium - self.hedge_entry_premium) * config.LOT_SIZE

    def get_total_side_premium(self, include_hedge=True):
        """Get total premium on this side (leg + hedge if active)"""
        total = self.current_premium
        if include_hedge and self.hedge_active and self.hedge_current_premium:
            total += self.hedge_current_premium
        return total

# ============================================================================
# STRADDLE MANAGER
# ============================================================================

class StraddleManager:
    """Manages the straddle position"""

    def __init__(self, data_manager, tx_logger):
        self.data_manager = data_manager
        self.tx_logger = tx_logger
        self.straddle_active = False
        self.entry_time = None
        self.strike = None
        self.ce_leg = None
        self.pe_leg = None

    def enter_straddle(self, timestamp):
        """Enter a new straddle position"""
        spot = self.data_manager.get_nifty_price(timestamp)
        if not spot:
            print(f"[{timestamp.strftime('%H:%M:%S')}] [ERROR] No spot price available")
            return False

        # Find ATM strike
        atm_strike = self.data_manager.find_atm_strike(spot, timestamp)
        if not atm_strike:
            print(f"[{timestamp.strftime('%H:%M:%S')}] [ERROR] Could not find ATM strike")
            return False

        # Build symbols
        ce_symbol = self.data_manager._build_option_symbol(atm_strike, 'CE')
        pe_symbol = self.data_manager._build_option_symbol(atm_strike, 'PE')

        # Get premiums
        ce_price = self.data_manager.get_option_price(ce_symbol, timestamp)
        pe_price = self.data_manager.get_option_price(pe_symbol, timestamp)

        if not ce_price or not pe_price or ce_price <= 0 or pe_price <= 0:
            print(f"[{timestamp.strftime('%H:%M:%S')}] [ERROR] Invalid premiums")
            return False

        # Create legs
        self.ce_leg = Leg(ce_symbol, atm_strike, 'CE', ce_price)
        self.pe_leg = Leg(pe_symbol, atm_strike, 'PE', pe_price)
        self.strike = atm_strike
        self.entry_time = timestamp
        self.straddle_active = True

        return True

    def log_entry(self, timestamp, session_number):
        """Log straddle entry"""
        # Log transaction
        self.tx_logger.log(timestamp, 'STRADDLE_ENTRY', {
            'strike': self.strike,
            'ce_price': self.ce_leg.entry_premium,
            'pe_price': self.pe_leg.entry_premium,
            'spot': self.data_manager.get_nifty_price(timestamp),
            'total_premium': self.ce_leg.entry_premium + self.pe_leg.entry_premium,
            'session': session_number
        })

        total_collected = (self.ce_leg.entry_premium + self.pe_leg.entry_premium) * config.LOT_SIZE

        print(f"\n{'='*80}")
        print(f"[{timestamp.strftime('%H:%M:%S')}] [ENTRY] Session {session_number}")
        print(f"{'='*80}")
        print(f"  Strike: {self.strike}")
        print(f"  CE Premium: Rs.{self.ce_leg.entry_premium:.2f}")
        print(f"  PE Premium: Rs.{self.pe_leg.entry_premium:.2f}")
        print(f"  Total Collected: Rs.{total_collected:,.2f}")
        print(f"{'='*80}")

    def update_prices(self, timestamp):
        """Update current prices for all legs"""
        if not self.straddle_active:
            return

        # Update CE leg
        ce_price = self.data_manager.get_option_price(self.ce_leg.symbol, timestamp)
        if ce_price and ce_price > 0:
            self.ce_leg.current_premium = ce_price

        # Update PE leg
        pe_price = self.data_manager.get_option_price(self.pe_leg.symbol, timestamp)
        if pe_price and pe_price > 0:
            self.pe_leg.current_premium = pe_price

        # Update CE hedge if active
        if self.ce_leg.hedge_active and self.ce_leg.hedge_symbol:
            hedge_price = self.data_manager.get_option_price(self.ce_leg.hedge_symbol, timestamp)
            if hedge_price and hedge_price > 0:
                self.ce_leg.hedge_current_premium = hedge_price

        # Update PE hedge if active
        if self.pe_leg.hedge_active and self.pe_leg.hedge_symbol:
            hedge_price = self.data_manager.get_option_price(self.pe_leg.hedge_symbol, timestamp)
            if hedge_price and hedge_price > 0:
                self.pe_leg.hedge_current_premium = hedge_price

# ============================================================================
# HEDGE MANAGER - MODIFIED: BUY HEDGE ON LOSING SIDE
# ============================================================================

class HedgeManager:
    """Manages hedging strategy - BUY hedge on LOSING side"""

    def __init__(self, data_manager, tx_logger):
        self.data_manager = data_manager
        self.tx_logger = tx_logger

    def check_and_manage_hedges(self, timestamp, ce_leg, pe_leg):
        """Check both legs for hedge entry/exit triggers"""
        # Check CE leg (market moving up)
        self._manage_leg_hedge(timestamp, ce_leg, pe_leg, 'CE')

        # Check PE leg (market moving down)
        self._manage_leg_hedge(timestamp, pe_leg, ce_leg, 'PE')

    def _manage_leg_hedge(self, timestamp, losing_leg, profit_leg, leg_type):
        """Manage hedge for one leg"""
        loss_pct = losing_leg.get_loss_pct()

        # CASE 1: Hedge is ACTIVE - Check for exit OR upgrade
        if losing_leg.hedge_active:
            # First check if we need to upgrade to next level
            should_upgrade = False

            if losing_leg.hedge_level == 1 and loss_pct >= config.LEVEL2_TRIGGER_PCT:
                # L1 active, but loss reached L2 threshold
                should_upgrade = True
                target_level = 2
            elif losing_leg.hedge_level == 2 and loss_pct >= config.LEVEL3_TRIGGER_PCT:
                # L2 active, but loss reached L3 threshold (handled in backtester)
                should_upgrade = False  # L3 is complete exit, not upgrade

            if should_upgrade:
                # Square off current hedge
                current_hedge_pnl = losing_leg.get_hedge_pnl()
                losing_leg.realized_hedge_pnl += current_hedge_pnl

                self.tx_logger.log(timestamp, f'HEDGE_SQUAREOFF_L{losing_leg.hedge_level}', {
                    'leg': leg_type,
                    'level': losing_leg.hedge_level,
                    'strike': losing_leg.hedge_strike,
                    'pnl': current_hedge_pnl
                })

                print(f"     [{timestamp.strftime('%H:%M:%S')}] [L{losing_leg.hedge_level} SQUAREOFF] {leg_type}: Rs.{current_hedge_pnl:,.0f}")
                print(f"     Upgrading to L{target_level}...")

                # Reset hedge state
                old_level = losing_leg.hedge_level
                losing_leg.hedge_active = False
                losing_leg.hedge_symbol = None
                losing_leg.hedge_strike = None
                losing_leg.hedge_entry_premium = None
                losing_leg.hedge_current_premium = None

                # Now enter new level
                self._enter_hedge_at_level(timestamp, losing_leg, profit_leg, loss_pct, leg_type, target_level)
            else:
                # Check for normal exit (return to trigger)
                self._check_hedge_exit(timestamp, losing_leg, loss_pct, leg_type)

        # CASE 2: No hedge active - Check for entry trigger
        else:
            self._check_hedge_entry(timestamp, losing_leg, profit_leg, loss_pct, leg_type)

    def _check_hedge_exit(self, timestamp, losing_leg, current_loss_pct, leg_type):
        """Check if hedge should be exited"""
        if losing_leg.hedge_level == 1:
            exit_trigger = config.LEVEL1_TRIGGER_PCT - config.HEDGE_REVERSAL_EXIT_PCT
        elif losing_leg.hedge_level == 2:
            exit_trigger = config.LEVEL2_TRIGGER_PCT - config.HEDGE_REVERSAL_EXIT_PCT
        else:
            return

        # Exit if loss has reduced to trigger level
        if current_loss_pct <= exit_trigger:
            hedge_pnl = losing_leg.get_hedge_pnl()

            # Log transaction
            self.tx_logger.log(timestamp, 'HEDGE_EXIT', {
                'leg': leg_type,
                'level': losing_leg.hedge_level,
                'strike': losing_leg.hedge_strike,
                'entry_price': losing_leg.hedge_entry_premium,
                'exit_price': losing_leg.hedge_current_premium,
                'pnl': hedge_pnl,
                'exit_trigger': exit_trigger
            })

            # Determine next trigger message
            if losing_leg.hedge_level == 1:
                next_trigger_msg = f"L1 @ {config.LEVEL1_TRIGGER_PCT}%"
            elif losing_leg.hedge_level == 2:
                next_trigger_msg = f"L2 @ {config.LEVEL2_TRIGGER_PCT}%"
            else:
                next_trigger_msg = "MAX"

            print(f"     [{timestamp.strftime('%H:%M:%S')}] [EXIT] {leg_type} L{losing_leg.hedge_level}: Rs.{hedge_pnl:,.0f} | Exit at {current_loss_pct:.1f}%")
            print(f"         Next trigger: {next_trigger_msg}")

            # Update realized P&L
            losing_leg.realized_hedge_pnl += hedge_pnl

            # Reload logic on reversal exit
            if losing_leg.hedge_level == 1:
                losing_leg.l1_loaded = True
            elif losing_leg.hedge_level == 2:
                losing_leg.l2_loaded = True

            # Reset hedge state
            losing_leg.hedge_active = False
            losing_leg.hedge_symbol = None
            losing_leg.hedge_strike = None
            losing_leg.hedge_entry_premium = None
            losing_leg.hedge_current_premium = None

    def _check_hedge_entry(self, timestamp, losing_leg, profit_leg, current_loss_pct, leg_type):
        """Check if hedge should be entered"""
        # Determine which level would trigger
        if current_loss_pct >= config.LEVEL3_TRIGGER_PCT:
            return
        elif current_loss_pct >= config.LEVEL2_TRIGGER_PCT:
            candidate_level = 2
        elif current_loss_pct >= config.LEVEL1_TRIGGER_PCT:
            candidate_level = 1
        else:
            return

        # Check if the level is loaded
        if candidate_level == 1:
            if not losing_leg.l1_loaded:
                return
        elif candidate_level == 2:
            if not losing_leg.l2_loaded:
                return

        # Level is loaded, proceed
        self._enter_hedge_at_level(timestamp, losing_leg, profit_leg,
                                   current_loss_pct, leg_type, candidate_level)

    def _enter_hedge_at_level(self, timestamp, losing_leg, profit_leg, current_loss_pct, leg_type, target_level):
        """Enter a hedge at specified level"""
        # Calculate difference for hedge selection
        losing_total = losing_leg.current_premium
        profit_total = profit_leg.get_total_side_premium(include_hedge=False)
        difference = abs(losing_total - profit_total)

        # ✅ MODIFIED: Find hedge strike on LOSING side (same option type)
        losing_option_type = losing_leg.option_type
        hedge_strike, hedge_symbol = self.data_manager.find_hedge_strike_by_premium(
            difference, losing_option_type, timestamp, losing_leg.strike
        )

        if not hedge_strike or not hedge_symbol:
            print(f"[{timestamp.strftime('%H:%M:%S')}] [WARN] Could not find hedge strike for {leg_type} L{target_level}")
            return

        # Get hedge price
        hedge_price = self.data_manager.get_option_price(hedge_symbol, timestamp)
        if not hedge_price or hedge_price <= 0:
            print(f"[{timestamp.strftime('%H:%M:%S')}] [WARN] Invalid hedge price for {leg_type} L{target_level}")
            return

        # ✅ MODIFIED: BUY the hedge (we pay premium)
        losing_leg.hedge_active = True
        losing_leg.hedge_level = target_level
        losing_leg.hedge_symbol = hedge_symbol
        losing_leg.hedge_strike = hedge_strike
        losing_leg.hedge_entry_premium = hedge_price
        losing_leg.hedge_current_premium = hedge_price
        losing_leg.total_hedges_bought += 1  # ✅ MODIFIED

        # Unload the level that just triggered
        if target_level == 1:
            losing_leg.l1_loaded = False
        elif target_level == 2:
            losing_leg.l2_loaded = False

        # Log transaction
        self.tx_logger.log(timestamp, 'HEDGE_ENTRY', {
            'leg': leg_type,
            'level': target_level,
            'losing_leg_loss': current_loss_pct,
            'hedge_strike': hedge_strike,
            'hedge_price': hedge_price,
            'hedge_type': losing_option_type,  # ✅ MODIFIED: Same as losing leg
            'difference': difference
        })

        # ✅ MODIFIED: Display hedge on LOSING side
        print(f"     [{timestamp.strftime('%H:%M:%S')}] [HEDGE] {leg_type} L{target_level}: BUY {losing_option_type} {int(hedge_strike)} @ Rs.{hedge_price:.2f}")
        print(f"         Entry Loss: {current_loss_pct:.1f}%")
        print(f"         Difference used: Rs.{difference:.2f}")

# ============================================================================
# BACKTESTER
# ============================================================================

class PriceNeutralBacktester:
    """Main backtester"""

    def __init__(self, data_manager):
        self.data_manager = data_manager
        self.tx_logger = TransactionLogger()
        self.straddle_manager = StraddleManager(data_manager, self.tx_logger)
        self.hedge_manager = HedgeManager(data_manager, self.tx_logger)
        self.sessions = []
        self.session_number = 0
        self.candles_to_wait = 0

        # Daily tracking
        self.daily_stats = {}
        self.current_day_sessions = 0
        self.current_day_pnl = 0

    def run_backtest(self, start_date, end_date):
        """Run the backtest"""
        print("\n" + "="*80)
        print("[START] BUY HEDGE ON LOSING SIDE - BACKTEST")
        print("="*80)
        print(f"Period: {start_date} to {end_date}")
        print(f"Expiry: {config.EXPIRY_DATE}")
        print(f"Hedge Levels: L1={config.LEVEL1_TRIGGER_PCT}%, L2={config.LEVEL2_TRIGGER_PCT}%, L3={config.LEVEL3_TRIGGER_PCT}%")
        print(f"Hedge Reversal Exit: {config.HEDGE_REVERSAL_EXIT_PCT}%")
        print(f"Strategy: BUY hedge on LOSING side")
        print("="*80)

        # Get date range
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)

        # Filter NIFTY data
        mask = (self.data_manager.nifty_data.index >= start_dt) & \
               (self.data_manager.nifty_data.index <= end_dt)
        backtest_data = self.data_manager.nifty_data[mask]

        current_date = None

        # Process each candle
        for timestamp, row in backtest_data.iterrows():
            trade_date = timestamp.date()
            trade_time = timestamp.time()

            # New day initialization
            if trade_date != current_date:
                # Print end of previous day
                if current_date is not None:
                    self._print_end_of_day(current_date)

                current_date = trade_date
                self.candles_to_wait = 0
                self.current_day_sessions = 0
                self.current_day_pnl = 0

                print(f"\n{'='*80}")
                print(f"[DATE] {trade_date} - DAILY ISOLATED RUN")
                print(f"{'='*80}")

            # Check if market is open
            if trade_time < config.MARKET_OPEN_TIME or trade_time >= config.MARKET_CLOSE_TIME:
                continue

            # EOD square-off
            if trade_time >= config.SQUAREOFF_TIME:
                if self.straddle_manager.straddle_active:
                    self.exit_straddle(timestamp, "EOD Time Reached")
                continue

            # Re-entry wait
            if self.candles_to_wait > 0:
                self.candles_to_wait -= 1
                continue

            # Entry logic
            if not self.straddle_manager.straddle_active:
                if config.ENTRY_WINDOW_START <= trade_time <= config.ENTRY_WINDOW_END:
                    self.session_number += 1
                    self.current_day_sessions += 1

                    if self.straddle_manager.enter_straddle(timestamp):
                        self.straddle_manager.log_entry(timestamp, self.session_number)
                continue

            # Update prices
            self.straddle_manager.update_prices(timestamp)

            ce = self.straddle_manager.ce_leg
            pe = self.straddle_manager.pe_leg

            # Check for Level 3 trigger (complete exit)
            ce_loss = ce.get_loss_pct()
            pe_loss = pe.get_loss_pct()

            if ce_loss >= config.LEVEL3_TRIGGER_PCT:
                self.exit_straddle(timestamp, f"Level 3 Trigger - CE at {ce_loss:.1f}%")
                continue

            if pe_loss >= config.LEVEL3_TRIGGER_PCT:
                self.exit_straddle(timestamp, f"Level 3 Trigger - PE at {pe_loss:.1f}%")
                continue

            # Manage hedges
            self.hedge_manager.check_and_manage_hedges(timestamp, ce, pe)

        # Final day summary
        if current_date is not None:
            self._print_end_of_day(current_date)

        # Final square-off if still active
        if self.straddle_manager.straddle_active:
            last_timestamp = backtest_data.index[-1]
            self.exit_straddle(last_timestamp, "Backtest End")

        print(f"\n[INFO] Total candles processed: {len(backtest_data):,}")

        # Generate results
        return self.generate_results()

    def _print_end_of_day(self, date):
        """Print end of day summary"""
        if date in self.daily_stats:
            stats = self.daily_stats[date]
            print(f"[END OF DAY] {date} | Sessions started: {stats['sessions']}")

    def exit_straddle(self, timestamp, reason):
        """Exit current straddle and all hedges"""
        if not self.straddle_manager.straddle_active:
            return

        ce = self.straddle_manager.ce_leg
        pe = self.straddle_manager.pe_leg

        # Calculate P&L
        ce_pnl = ce.get_pnl()
        pe_pnl = pe.get_pnl()

        # Get hedge P&L (accumulated + active)
        ce_hedge_pnl = ce.realized_hedge_pnl
        pe_hedge_pnl = pe.realized_hedge_pnl

        # Add active hedge P&L
        if ce.hedge_active:
            active_ce_pnl = ce.get_hedge_pnl()
            ce_hedge_pnl += active_ce_pnl

            if config.VERBOSE_LOGGING and active_ce_pnl != 0:
                print(f"     [ACTIVE HEDGE] CE L{ce.hedge_level}: Rs.{active_ce_pnl:,.0f}")

            self.tx_logger.log(timestamp, 'HEDGE_FORCE_EXIT', {
                'leg': 'CE',
                'level': ce.hedge_level,
                'strike': ce.hedge_strike,
                'pnl': active_ce_pnl
            })

        if pe.hedge_active:
            active_pe_pnl = pe.get_hedge_pnl()
            pe_hedge_pnl += active_pe_pnl

            if config.VERBOSE_LOGGING and active_pe_pnl != 0:
                print(f"     [ACTIVE HEDGE] PE L{pe.hedge_level}: Rs.{active_pe_pnl:,.0f}")

            self.tx_logger.log(timestamp, 'HEDGE_FORCE_EXIT', {
                'leg': 'PE',
                'level': pe.hedge_level,
                'strike': pe.hedge_strike,
                'pnl': active_pe_pnl
            })

        hedge_pnl = ce_hedge_pnl + pe_hedge_pnl
        total_pnl = ce_pnl + pe_pnl + hedge_pnl

        duration_minutes = (timestamp - self.straddle_manager.entry_time).total_seconds() / 60

        # Store session data
        session_data = {
            'session': self.session_number,
            'entry_time': self.straddle_manager.entry_time,
            'exit_time': timestamp,
            'duration_minutes': duration_minutes,
            'strike': self.straddle_manager.strike,
            'ce_entry': ce.entry_premium,
            'pe_entry': pe.entry_premium,
            'ce_exit': ce.current_premium,
            'pe_exit': pe.current_premium,
            'ce_pnl': ce_pnl,
            'pe_pnl': pe_pnl,
            'hedge_pnl': hedge_pnl,
            'total_pnl': total_pnl,
            'exit_reason': reason,
            'ce_hedges': ce.total_hedges_bought,  # ✅ MODIFIED
            'pe_hedges': pe.total_hedges_bought,  # ✅ MODIFIED
            'ce_hedge_active': ce.hedge_active,
            'pe_hedge_active': pe.hedge_active
        }

        self.sessions.append(session_data)

        # Update daily stats
        trade_date = timestamp.date()
        if trade_date not in self.daily_stats:
            self.daily_stats[trade_date] = {
                'sessions': 0,
                'pnl': 0,
                'ce_hedges': 0,
                'pe_hedges': 0
            }

        self.daily_stats[trade_date]['sessions'] = self.current_day_sessions
        self.daily_stats[trade_date]['pnl'] += total_pnl
        self.daily_stats[trade_date]['ce_hedges'] += ce.total_hedges_bought  # ✅ MODIFIED
        self.daily_stats[trade_date]['pe_hedges'] += pe.total_hedges_bought  # ✅ MODIFIED

        # Log transaction
        self.tx_logger.log(timestamp, 'STRADDLE_EXIT', {
            'session': self.session_number,
            'reason': reason,
            'ce_pnl': ce_pnl,
            'pe_pnl': pe_pnl,
            'hedge_pnl': hedge_pnl,
            'total_pnl': total_pnl
        })

        print(f"\n[{timestamp.strftime('%H:%M:%S')}] [EXIT] {reason}")
        print(f"  Duration: {duration_minutes:.0f} minutes")
        print(f"  P&L: CE=Rs.{ce_pnl:,.0f}, PE=Rs.{pe_pnl:,.0f}, Hedge=Rs.{hedge_pnl:,.0f}")
        print(f"  [TOTAL] Rs.{total_pnl:,.0f}")

        # Reset
        self.straddle_manager.straddle_active = False

        # Re-entry wait (if not EOD)
        if reason not in ["EOD Time Reached", "Backtest End"]:
            self.candles_to_wait = config.REENTRY_WAIT_CANDLES

            if config.VERBOSE_LOGGING:
                print(f"     Re-entry wait: {self.candles_to_wait} candles")

    def generate_results(self):
        """Generate results summary"""
        if not self.sessions:
            print("\n[WARN] No sessions completed")
            return None

        sessions_df = pd.DataFrame(self.sessions)

        total_pnl = sessions_df['total_pnl'].sum()
        total_sessions = len(sessions_df)
        winning_sessions = len(sessions_df[sessions_df['total_pnl'] > 0])
        losing_sessions = len(sessions_df[sessions_df['total_pnl'] < 0])
        win_rate = (winning_sessions / total_sessions * 100) if total_sessions > 0 else 0

        avg_win = sessions_df[sessions_df['total_pnl'] > 0]['total_pnl'].mean() if winning_sessions > 0 else 0
        avg_loss = sessions_df[sessions_df['total_pnl'] < 0]['total_pnl'].mean() if losing_sessions > 0 else 0
        max_win = sessions_df['total_pnl'].max()
        max_loss = sessions_df['total_pnl'].min()

        total_ce_hedges = sessions_df['ce_hedges'].sum()
        total_pe_hedges = sessions_df['pe_hedges'].sum()

        # Print Daily P&L Summary
        print("\n" + "="*80)
        print("DAILY P&L SUMMARY WITH HEDGE COUNTS:")
        print("="*80)

        for date in sorted(self.daily_stats.keys()):
            stats = self.daily_stats[date]
            pnl = stats['pnl']
            pnl_sign = '+' if pnl >= 0 else ''
            ce_h = stats['ce_hedges']
            pe_h = stats['pe_hedges']
            total_h = ce_h + pe_h
            print(f"📅 {date} | Sessions: {stats['sessions']:2d} | P&L: {pnl_sign}Rs.{pnl:,.2f} | Hedges BOUGHT: {total_h:2d} (CE:{ce_h}, PE:{pe_h})")

        print("="*80)
        print(f"✅ Total P&L ({min(self.daily_stats.keys())} → {max(self.daily_stats.keys())}): Rs.{total_pnl:,.2f}")
        print(f"📊 Total Sessions: {total_sessions} | Total Hedges BOUGHT: {total_ce_hedges + total_pe_hedges} (CE:{total_ce_hedges}, PE:{total_pe_hedges})")

        if total_sessions > 0:
            print(f"📈 Avg Hedges/Session: {(total_ce_hedges + total_pe_hedges) / total_sessions:.1f}")

        print("="*80)

        # Print detailed results
        print("\n" + "="*80)
        print("[RESULTS] BACKTEST SUMMARY - BUY HEDGE ON LOSING SIDE")
        print("="*80)
        print(f"Total P&L: Rs.{total_pnl:,.2f}")
        print(f"Total Sessions: {total_sessions}")
        print(f"  Winning: {winning_sessions} ({win_rate:.1f}%)")
        print(f"  Losing: {losing_sessions}")
        print(f"Average Win: Rs.{avg_win:,.2f}")
        print(f"Average Loss: Rs.{avg_loss:,.2f}")
        print(f"Max Win: Rs.{max_win:,.2f}")
        print(f"Max Loss: Rs.{max_loss:,.2f}")
        print(f"Total Hedges BOUGHT: CE={total_ce_hedges}, PE={total_pe_hedges}")

        if avg_loss != 0:
            profit_factor = abs(avg_win / avg_loss) if avg_loss < 0 else 0
            print(f"Profit Factor: {profit_factor:.2f}")

        print("="*80)

        return sessions_df

# ============================================================================
# MAIN EXECUTION
# ============================================================================

if __name__ == "__main__":
    print("\n🙏 Ganapati Bappa Morya! 🙏")
    print("Starting BUY HEDGE ON LOSING SIDE Backtest...")

    # Configuration
    config.NIFTY_CSV = "nifty_1_min.csv"
    config.OPTIONS_FOLDER = "options_data/"
    config.MASTER_JSON = "OpenAPIScripMaster.json"
    config.EXPIRY_DATE = EXPIRY_DATE
    config.VERBOSE_LOGGING = True

    try:
        # Initialize
        data_manager = HistoricalDataManager()
        data_manager.load_data()

        # Run backtest
        backtester = PriceNeutralBacktester(data_manager)
        results = backtester.run_backtest(START_DATE, END_DATE)

        # Save results
        if results is not None:
            output_file = f"BUY_HEDGE_Backtest_{START_DATE}_to_{END_DATE}.xlsx"

            # Save sessions
            results.to_excel(output_file, sheet_name='Sessions', index=False)

            # Save transactions
            tx_df = backtester.tx_logger.to_dataframe()
            if not tx_df.empty:
                with pd.ExcelWriter(output_file, mode='a', engine='openpyxl') as writer:
                    tx_df.to_excel(writer, sheet_name='Transactions', index=False)

            print(f"\n[OK] Results saved to: {output_file}")

            # Display session details
            print("\n[INFO] Session Details:")
            for idx, row in results.iterrows():
                pnl_sign = '+' if row['total_pnl'] >= 0 else ''
                print(f"  S{row['session']:02d}: {row['entry_time'].strftime('%Y-%m-%d %H:%M')} | " +
                      f"{pnl_sign}Rs.{row['total_pnl']:,.0f} | {row['exit_reason']}")

    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()

    print("\n🙏 Ganapati Bappa Morya! 🙏")
