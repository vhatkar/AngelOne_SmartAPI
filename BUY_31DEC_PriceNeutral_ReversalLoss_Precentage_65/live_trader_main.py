# live_trader_main.py - PURE PRICE-NEUTRAL HEDGING VERSION
"""
Angel One Live Trader - Main trading loop
Progressive Hedging Straddle Strategy - PURE PRICE-NEUTRAL
✅ REACTIVE AUTHENTICATION: One login per day
✅ RE-LOGIN ONLY ON AB1007 (reactive)
✅ NO PROACTIVE REFRESHES
✅ NO JWT REFRESH THREADS
✅ SIMPLIFIED & STABLE
✅ SIMPLIFIED KEYBOARD: Ctrl+C only
✅ FIXED: Removed conflicting signal handler
✅ FIXED: Corrected display bug for next level when hedge active
✅ FIXED: Instant resume from menu (no 2-minute delay)
"""

import time
import sys
from datetime import datetime, timedelta
from typing import List
from config import config
from angelone_api import api
from straddle_manager import StraddleManager
from excel_logger import ExcelLogger
from position_reconciler import PositionReconciler
from bot_controller import BotController


class LiveTrader:
    """Main live trading system with simplified reactive authentication"""
    
    def __init__(self):
        self.running = False
        self.excel_logger = ExcelLogger()  
        self.straddle_manager = StraddleManager(excel_logger=self.excel_logger)
        self.position_reconciler = PositionReconciler()
        self.candles_to_wait = 0
        self.last_exit_time = None
        self.candle_count = 0
        self.is_executing_trade = False
        self.last_reconcile_time = None
        self.interrupt_received = False
        self.menu_active = False
        
        # 🔥 NEW: Track first entry for manual strike logic
        self.first_entry_done = False
        
        # ✅ FIX 1: Use BotController - no signal conflicts
        self.controller = BotController(self)
    
    def interruptible_sleep(self, sleep_seconds: int):
        """
        ✅ FIXED: Sleep that responds to Ctrl+C immediately
        Checks every 0.1 seconds for interrupts
        """
        check_interval = 0.1
        total_checks = int(sleep_seconds / check_interval)
        
        for i in range(total_checks):
            if self.interrupt_received:
                return
            try:
                time.sleep(check_interval)
            except KeyboardInterrupt:
                # ✅ NEW: Catch Ctrl+C during sleep
                print("\n⚠️ Interrupt detected during sleep!")
                raise  # Re-raise to trigger main handler
        
        # Sleep remaining time
        remaining = sleep_seconds - (total_checks * check_interval)
        if remaining > 0 and not self.interrupt_received:
            try:
                time.sleep(remaining)
            except KeyboardInterrupt:
                print("\n⚠️ Interrupt detected during remaining sleep!")
                raise

    def _get_next_candle_time(self) -> datetime:
        """Calculate next aligned candle time (NO DRIFT)"""
        current = config.get_current_ist_time()
        interval_seconds = config.CANDLE_INTERVAL_SECONDS
        
        # Calculate seconds since midnight
        seconds_since_midnight = (
            current.hour * 3600 + 
            current.minute * 60 + 
            current.second
        )
        
        # Round up to next interval boundary
        next_interval = ((seconds_since_midnight // interval_seconds) + 1) * interval_seconds
        
        # Convert back to time
        next_hour = next_interval // 3600
        next_minute = (next_interval % 3600) // 60
        next_second = next_interval % 60
        
        next_time = current.replace(
            hour=next_hour % 24,
            minute=next_minute,
            second=next_second,
            microsecond=0
        )
        
        # Handle day rollover
        if next_time <= current:
            next_time += timedelta(days=1)
        
        return next_time

    def _wait_for_next_candle(self):
        """Sleep until next aligned candle time (NO DRIFT)"""
        next_candle = self._get_next_candle_time()
        current = config.get_current_ist_time()
        
        wait_seconds = (next_candle - current).total_seconds()
        
        if wait_seconds > 0:
            print(f"⏰ Waiting {wait_seconds:.1f}s until next candle at {next_candle.strftime('%H:%M:%S')}")
            self.interruptible_sleep(int(wait_seconds))

    def _generate_strikes_for_option_chain(self, spot_price: float) -> List[int]:
        """
        Generate list of strikes to fetch option chain
        ✅ BALANCED: 17 strikes (±8) for optimal hedge coverage
        ✅ CRITICAL FIX: Always includes active hedge strikes even if out of ATM range
        """
        base_atm = round(spot_price / config.STRIKE_INTERVAL) * config.STRIKE_INTERVAL
        
        strikes = []
        for i in range(-8, 9):  # ±8 = 17 strikes ✅ BALANCED COVERAGE
            strike = int(base_atm + (i * config.STRIKE_INTERVAL))
            strikes.append(strike)
        
        # ⭐ CRITICAL FIX: Force-add active hedge strikes if they exist
        if self.straddle_manager.straddle_active:
            ce_leg = self.straddle_manager.ce_leg
            pe_leg = self.straddle_manager.pe_leg
            
            if ce_leg and ce_leg.hedge_active and ce_leg.hedge_strike:
                if ce_leg.hedge_strike not in strikes:
                    strikes.append(ce_leg.hedge_strike)
                    print(f"🔧 Added CE hedge strike {ce_leg.hedge_strike} (out of ATM range)")
            
            if pe_leg and pe_leg.hedge_active and pe_leg.hedge_strike:
                if pe_leg.hedge_strike not in strikes:
                    strikes.append(pe_leg.hedge_strike)
                    print(f"🔧 Added PE hedge strike {pe_leg.hedge_strike} (out of ATM range)")
        
        return sorted(strikes)

    def _generate_strikes_around_strike(self, center_strike: int) -> List[int]:
        """
        🎯 NEW: Generate strikes around a CENTER strike (not spot)
        Used for both MANUAL and AUTO modes
        """
        strikes = []
        for i in range(-8, 9):  # ✅ 17 strikes
            strike = int(center_strike + (i * config.STRIKE_INTERVAL))
            strikes.append(strike)
        
        return strikes
    
    def _safe_reconcile(self, reason: str):
        """
        Safe reconciliation with trade lock check + MANUAL SYNC
        OPTIMIZED: Only called when actually needed
        """
        if self.is_executing_trade:
            print(f"   [WARN] Skipping reconciliation (trade in progress)")
            return
        
        try:
            print(f"\n[RECONCILING] {reason}")
            reconciliation_result = self.position_reconciler.reconcile(
                self.straddle_manager,
                self.straddle_manager.hedge_manager
            )
            
            # ✅ NEW: Check if all positions were manually closed
            if reconciliation_result and reconciliation_result.get('all_positions_closed', False):
                print(f"🚨 EMERGENCY: All positions manually closed!")
                print(f"Creating emergency stop flag...")
                config.create_emergency_stop("All positions manually closed from broker terminal")
                self.running = False  # Stop the trading loop
                return
            
            # 🔥 FIX #2: Log manual hedge changes (CORRECTED)
            manual_changes = reconciliation_result.get('manual_hedge_changes', [])
            if manual_changes:
                print(f"\n🔧 AUTO-SYNCED {len(manual_changes)} manual broker changes:")
                for change in manual_changes:
                    print(f"   {change['leg']} hedge L{change['level']} {change['action']}")
                    
                    # Log to Excel if available
                    if self.excel_logger:
                        self.excel_logger.log_manual_intervention(
                            intervention_type=f"BROKER_{change['action']}",
                            leg_type=change['leg'],
                            level=change['level'],
                            time=config.get_current_ist_time(),
                            notes=f"Auto-detected from broker, synced to script"
                        )
            
            self.last_reconcile_time = config.get_current_ist_time()
        except Exception as e:
            print(f"   [WARN] Reconciliation failed (non-critical): {e}")
    
    def _try_periodic_reconcile(self):
        """
        Periodic reconciliation (fallback safety net)
        OPTIMIZED: Only triggers if event-driven reconciliation missed
        """
        if self.is_executing_trade:
            return
        
        if not self.straddle_manager.straddle_active:
            return
        
        # NEW: Check if reconciliation is needed (smart logic)
        if self.position_reconciler.should_reconcile():
            self._safe_reconcile("Periodic Check (Fallback)")
    
    def initialize_system(self) -> bool:
        """Initialize trading system - ONE LOGIN PER DAY"""
        try:
            print("\n" + "=" * 80)
            print("ANGEL ONE LIVE TRADING SYSTEM")
            print("PROGRESSIVE HEDGING STRADDLE STRATEGY - PURE PRICE-NEUTRAL")
            print("✅ REACTIVE AUTHENTICATION MODE")
            print("✅ ONE LOGIN AT START")
            print("✅ RE-LOGIN ONLY ON AB1007")
            print("✅ NO PROACTIVE REFRESHES")
            print("✅ SIMPLIFIED KEYBOARD: Ctrl+C only")
            print("=" * 80)
            
            config.display_config()
            
            # Single login attempt - valid for entire trading session
            if not api.login():
                print("[ERROR] Failed to login to Angel One")
                return False
            
            print("[OK] System initialized - One login for entire day\n")
            return True
            
        except Exception as e:
            print(f"[ERROR] Initialization error: {str(e)}")
            return False
    
    def check_force_exit_conditions(self) -> tuple:
        """CORE LOGIC: Check force exit (Priority 1) WITHOUT token refresh"""
        if config.should_force_squareoff():
            return True, "EOD Force Square-off"
        
        return False, None
    
    def process_force_exit_and_reentry(self, reason: str):
        """Process force exit WITHOUT any token logic"""
        self.is_executing_trade = True
        
        try:
            print(f"\n{'='*80}")
            print(f"{'EOD SQUARE-OFF' if 'EOD' in reason else 'EXITING POSITIONS'}")
            print(f"{'='*80}")
            
            # Direct exit - no token refresh
            exit_details = self.straddle_manager.exit_straddle(reason)
            
            if not exit_details:
                print("⚠️ Exit failed - possible auth error")
                # If auth error, it will be caught by error handler on next API call
            
            self.last_exit_time = config.get_current_ist_time()
            
            # Wait for broker settlement
            self.interruptible_sleep(5)
            
            # NEW: Mark orders filled - triggers reconciliation
            self.position_reconciler.mark_order_filled()
            
        except Exception as e:
            print(f"\n🚨 Error during exit: {e}")
        
        finally:
            self.is_executing_trade = False
        
        # Handle post-exit logic
        if 'EOD' in reason:
            print("\n🏁 EOD complete - stopping script")
            self.running = False
            return
        
        if not config.is_entry_window_open():
            print("[STOP] Entry window closed")
            return
        
        # CORE LOGIC: Wait 1 candle
        self.candles_to_wait = config.RE_ENTRY_WAIT_CANDLES
        print(f"\n[WAITING] {self.candles_to_wait} candle(s) before re-entry...\n")
    
    def process_candle(self):
        """Process one candle with MARKET HOURS CHECK"""
        try:
            # CRITICAL FIX: Check if market is open first
            if not config.is_market_open():
                if self.straddle_manager.straddle_active:
                    print(f"[MARKET CLOSED] Market is closed but positions active - monitoring for exit")
                    # Still process monitoring for active positions
                    self._process_monitoring()
                else:
                    current_time = config.get_current_ist_time()
                    print(f"[MARKET CLOSED] Skipping candle - Market closed - {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
                return
            
            # ✅ NEW: Add emergency stop check
            if config.is_emergency_stop():
                print(f"⏸️ Skipping candle processing - emergency stop active")
                return

            self.candle_count += 1
            current_time = config.get_current_ist_time()
            
            print(f"\n{'-' * 80}")
            print(f"[CANDLE] Candle #{self.candle_count} | {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"{'-' * 80}")
            
            # PRIORITY 1: Force Exit Check
            should_exit, reason = self.check_force_exit_conditions()
            if should_exit:
                if self.straddle_manager.straddle_active:
                    self.process_force_exit_and_reentry(reason)
                return
            
            # RE-ENTRY COOLDOWN
            if self.candles_to_wait > 0:
                self.candles_to_wait -= 1
                print(f"[WAITING] Cooldown: {self.candles_to_wait} candle(s) remaining")
                if self.candles_to_wait == 0:
                    print("[OK] Cooldown complete - Ready for re-entry\n")
                return
            
            # ENTRY LOGIC
            if not self.straddle_manager.straddle_active:
                if config.is_entry_window_open():
                    self._process_entry()
                else:
                    print("[STOP] Entry window closed")
                return
            
            # MONITORING & HEDGING
            self._process_monitoring()
            
            # OPTIMIZED RECONCILIATION (event-driven + fallback)
            self._try_periodic_reconcile()
        
        except Exception as e:
            print(f"[ERROR] Error processing candle: {str(e)}")
            import traceback
            traceback.print_exc()
    
    def _process_entry(self):
        """
        Safe straddle entry with AUTO scanning or MANUAL strike selection
        MANUAL mode: Uses manual strike ONLY for first entry, then AUTO for re-entries
        BALANCED: Uses 17 strikes (±8) for optimal hedge coverage
        """
        if config.is_emergency_stop():
            print("[STOP] Emergency stop active - skipping entry")
            return
        
        if not config.is_market_open():
            print("[STOP] Market is closed - skipping entry")
            return

        if not config.is_entry_window_open():
            print("[STOP] Entry window closed - skipping entry")
            return

        self.is_executing_trade = True
        success = False
        
        try:
            spot_price = api.get_spot_price()
            if not spot_price:
                print("[ERROR] Could not fetch spot price")
                return
            
            # 🎯 Determine strike selection
            strike_mode = getattr(config, 'STRIKE_SELECTION_MODE', 'AUTO').upper()
            manual_strike = getattr(config, 'MANUAL_STRIKE', None)
            
            # Check if this is first entry
            is_first_entry = (self.last_exit_time is None)
            
            # Decide strike selection
            use_manual_strike = False
            selected_strike = None
            
            if strike_mode == 'MANUAL' and manual_strike and is_first_entry:
                # MANUAL mode: Use manual strike ONLY for first entry
                use_manual_strike = True
                selected_strike = int(manual_strike)
                print(f"\n{'='*80}")
                print(f"📍 MANUAL MODE - FIRST ENTRY")
                print(f"{'='*80}")
                print(f"   Using Manual Strike: {selected_strike}")
                print(f"   Current NIFTY Spot: {spot_price:.2f}")
                print(f"   (Re-entries will use AUTO scanning)")
                print(f"{'='*80}\n")
            
            elif strike_mode == 'MANUAL' and not is_first_entry:
                # Re-entry: Switch to AUTO mode
                print(f"\n{'='*80}")
                print(f"🔄 RE-ENTRY - AUTO MODE")
                print(f"{'='*80}")
                print(f"   First entry used manual strike: {manual_strike}")
                print(f"   Now scanning for best strike...")
                print(f"   Current NIFTY Spot: {spot_price:.2f}")
                print(f"{'='*80}\n")
            
            else:
                # AUTO mode from start
                print(f"\n{'='*80}")
                print(f"🔍 AUTO MODE - SCANNING FOR BEST STRADDLE")
                print(f"{'='*80}")
                print(f"   Current NIFTY Spot: {spot_price:.2f}")
                print(f"{'='*80}\n")
            
            # 🔥 BALANCED: Generate 17 strikes (±8) for both modes
            if use_manual_strike:
                # For manual mode, fetch ±8 strikes around manual strike
                base_strike = selected_strike
                strikes_to_fetch = []
                for i in range(-8, 9):  # ✅ 17 strikes (±8)
                    strike = int(base_strike + (i * config.STRIKE_INTERVAL))
                    strikes_to_fetch.append(strike)
                print(f"[FETCHING] Fetching 17 strikes around manual strike {selected_strike}...")
            else:
                # For auto mode, fetch ±8 strikes around ATM
                strikes_to_fetch = self._generate_strikes_for_option_chain(spot_price)
                print(f"[FETCHING] Scanning 17 strikes for minimum CE-PE difference...")
            
            print(f"   Strike range: {min(strikes_to_fetch)} to {max(strikes_to_fetch)}")
            
            option_chain = api.get_option_chain(strikes_to_fetch)
            if not option_chain:
                print("[ERROR] Could not fetch option chain")
                return
            
            # Validate option chain has reasonable premiums
            valid_strikes = 0
            for strike, data in option_chain.items():
                ce_premium = data.get('CE', 0)
                pe_premium = data.get('PE', 0)
                if ce_premium > 5 and pe_premium > 5:
                    valid_strikes += 1
            
            if valid_strikes < 3:
                print(f"[WARN] Insufficient valid strikes ({valid_strikes}), skipping entry")
                return
            
            # ✅ Validate manual strike if using manual mode
            if use_manual_strike:
                validate_strike = getattr(config, 'VALIDATE_STRIKE_IN_CHAIN', True)
                min_premium = getattr(config, 'MIN_PREMIUM_THRESHOLD', 5.0)
                
                if validate_strike:
                    if selected_strike not in option_chain:
                        print(f"[ERROR] Manual strike {selected_strike} not found in option chain!")
                        print(f"   Available strikes: {sorted(option_chain.keys())}")
                        return
                    
                    strike_data = option_chain[selected_strike]
                    ce_premium = strike_data.get('CE', 0)
                    pe_premium = strike_data.get('PE', 0)
                    
                    if ce_premium < min_premium or pe_premium < min_premium:
                        print(f"[ERROR] Manual strike {selected_strike} has invalid premiums!")
                        print(f"   CE: ₹{ce_premium:.2f}, PE: ₹{pe_premium:.2f}")
                        print(f"   Minimum required: ₹{min_premium:.2f}")
                        return
                    
                    print(f"[OK] Manual strike {selected_strike} validated")
                    print(f"   CE Premium: ₹{ce_premium:.2f}")
                    print(f"   PE Premium: ₹{pe_premium:.2f}")
                    print(f"   Total Premium: ₹{ce_premium + pe_premium:.2f}\n")
            
            # ✅ Enter straddle
            if use_manual_strike:
                success = self.straddle_manager.enter_straddle(
                    spot_price, 
                    option_chain, 
                    manual_strike=selected_strike
                )
            else:
                success = self.straddle_manager.enter_straddle(
                    spot_price, 
                    option_chain
                )
            
            if not success:
                print("\n[WARN] Straddle entry returned False")
                print("[ACTION] Checking for orphaned positions...")
                self.interruptible_sleep(3)
                
                if self.position_reconciler:
                    actual_positions = self.position_reconciler.get_actual_positions()
                    if actual_positions:
                        print(f"\n🚨 FOUND {len(actual_positions)} OPEN POSITIONS!")
                        print("="*80)
                        for pos in actual_positions:
                            print(f"   Security ID: {pos.get('securityId')}")
                            print(f"   Quantity: {pos.get('netQty')}")
                            print(f"   Symbol: {pos.get('tradingsymbol')}")
                        print("="*80)
                        print("\n⚠️ MANUAL ACTION REQUIRED:")
                        print("   1. Go to Angel One terminal")
                        print("   2. Square off ALL positions listed above")
                        print("   3. Then restart the script")
                        print("="*80 + "\n")
                return
            
            if success:
                self.interruptible_sleep(5)
                self.position_reconciler.mark_order_filled()
        
        except Exception as e:
            print(f"\n[CRITICAL ERROR] Exception in _process_entry: {e}")
            import traceback
            traceback.print_exc()
            
            print("\n[EMERGENCY] Checking for open positions...")
            self.interruptible_sleep(2)
            try:
                actual_positions = api.get_positions()
                if actual_positions:
                    print(f"\n🚨 EMERGENCY: FOUND {len(actual_positions)} OPEN POSITIONS!")
                    print("="*80)
                    for pos in actual_positions:
                        print(f"   Security ID: {pos.get('securityId')}")
                        print(f"   Quantity: {pos.get('netQty')}")
                        print(f"   Symbol: {pos.get('tradingsymbol')}")
                    print("="*80)
                    print("\n⚠️ SCRIPT WILL PAUSE - SQUARE OFF MANUALLY FIRST!")
                    input("Press ENTER after squaring off positions...")
            except:
                pass
        
        finally:
            self.is_executing_trade = False
        
        if success and self.position_reconciler.should_reconcile():
            self._safe_reconcile("After Straddle Entry")
    
    def _process_monitoring(self):
        """
        ✅ FIXED: Safe monitoring with WebSocket health check added
        ✅ CRITICAL FIX: Includes hedge strikes even if out of ATM range
        ✅ FIX 3: Added WebSocket health check and resubscription
        """
        # ✅ FIX: Added null checks for straddle legs
        if not self.straddle_manager.straddle_active or not self.straddle_manager.ce_leg or not self.straddle_manager.pe_leg:
            return
        
        spot_price = api.get_spot_price()
        if not spot_price:
            print("[ERROR] Could not fetch spot price")
            return
        
        # ✅ FIX 3: Check WebSocket health every candle
        websocket_healthy = api.check_websocket_health()
        if not websocket_healthy:
            print("⚠️ WebSocket unhealthy - attempting to resubscribe instruments...")
            
            # Resubscribe to active straddle
            try:
                ce_id = self.straddle_manager.ce_leg.security_id
                pe_id = self.straddle_manager.pe_leg.security_id
                
                token_list = [{
                    "exchangeType": 2,
                    "tokens": [ce_id, pe_id]
                }]
                
                if api.market_ws:
                    api.market_ws.subscribe(
                        correlation_id=f"resubscribe_{int(time.time())}",
                        mode=1,
                        token_list=token_list
                    )
                    print("✅ Resubscribed to straddle legs")
            except Exception as e:
                print(f"⚠️ Resubscribe failed: {e}")
        
        # ⭐ CRITICAL FIX: Generate strikes WITH hedge strike protection
        strikes_to_fetch = self._generate_strikes_for_option_chain(spot_price)
        
        # Show debugging info about included strikes
        if config.ENABLE_DEBUG_MODE:
            ce_leg = self.straddle_manager.ce_leg
            pe_leg = self.straddle_manager.pe_leg
            
            if ce_leg and ce_leg.hedge_active and ce_leg.hedge_strike:
                in_range = ce_leg.hedge_strike in strikes_to_fetch
                print(f"🔍 CE Hedge Strike {ce_leg.hedge_strike}: {'✅ IN range' if in_range else '❌ OUT of range'}")
            
            if pe_leg and pe_leg.hedge_active and pe_leg.hedge_strike:
                in_range = pe_leg.hedge_strike in strikes_to_fetch
                print(f"🔍 PE Hedge Strike {pe_leg.hedge_strike}: {'✅ IN range' if in_range else '❌ OUT of range'}")
        
        option_chain = api.get_option_chain(strikes_to_fetch)
        if not option_chain:
            print("[ERROR] Could not fetch option chain")
            return
        
        # Track if hedges exist before update
        ce_had_hedge = self.straddle_manager.ce_leg.hedge_active
        pe_had_hedge = self.straddle_manager.pe_leg.hedge_active
        
        # This handles Priority 2-6 (Premium Ratio, Level 3, Hedging)
        force_exit_reason = self.straddle_manager.update_positions(option_chain)
        
        # Check if NEW hedges were added
        ce_added_hedge = (not ce_had_hedge and self.straddle_manager.ce_leg.hedge_active)
        pe_added_hedge = (not pe_had_hedge and self.straddle_manager.pe_leg.hedge_active)
        
        if ce_added_hedge or pe_added_hedge:
            # Wait for broker settlement
            self.interruptible_sleep(3)
            
            # NEW: Mark orders filled - triggers reconciliation
            self.position_reconciler.mark_order_filled()
            
            # Reconcile after hedge (event-driven)
            if self.position_reconciler.should_reconcile():
                self._safe_reconcile("After Hedge Entry")
        
        if force_exit_reason:
            self.process_force_exit_and_reentry(force_exit_reason)
            return
        
        self._display_status()
    
    def _display_status(self):
        """ULTIMATE DISPLAY - Shows all stop loss states dynamically - PURE PRICE-NEUTRAL
        🆕 ENHANCED: Shows next target + exit zone for ALL levels on BOTH legs
        ✅ CORRECT EXIT LOGIC:
           - L1 exits at 0% (break-even)
           - L2 exits at 20% (L1 trigger level)
        ✅ FIXED: Correct next level display when hedge active
        """
        if not self.straddle_manager.straddle_active or not self.straddle_manager.ce_leg or not self.straddle_manager.pe_leg:
            return
        
        ce = self.straddle_manager.ce_leg
        pe = self.straddle_manager.pe_leg
        
        print(f"🎯 POSITION STATUS - PURE PRICE-NEUTRAL:")
        print(f"{'─'*60}")
        
        for leg, name in [(ce, "CE"), (pe, "PE")]:
            status_icon = "🛡️" if leg.hedge_active else "✅"
            print(f"   {name}: {status_icon} ₹{leg.current_premium:.2f} | Loss: {leg.current_loss_pct:+.1f}%")
            
            if leg.hedge_active:
                # ============================================================
                # ACTIVE HEDGE - Show next level + exit zone
                # ============================================================
                print(f"        ✅ L{leg.hedge_level} HEDGE ACTIVE: {leg.hedge_symbol}")
                
                # ✅ FIX: Determine next level based on CURRENT hedge level
                # L1 active → next is L2 at 40%
                # L2 active → next is L3 at 60%
                if leg.hedge_level == 1:
                    # L1 hedge active → show L2 as next
                    next_sl_pct = config.PROGRESSIVE_HEDGING_LEVELS[1]  # 40%
                    next_sl_price = leg.entry_premium * (1 + next_sl_pct / 100)
                    distance_to_next = next_sl_pct - leg.current_loss_pct
                    print(f"        ⏫ NEXT: L2 at {next_sl_pct:.0f}% (₹{next_sl_price:.2f})")
                    print(f"        📏 Distance UP: {distance_to_next:+.1f}%")
                    
                elif leg.hedge_level == 2:
                    # L2 hedge active → show L3 as next
                    l3_pct = config.LEVEL_3_HARD_STOP
                    l3_price = leg.entry_premium * (1 + l3_pct / 100)
                    distance_to_l3 = l3_pct - leg.current_loss_pct
                    print(f"        🚨 NEXT: L3 HARD STOP at {l3_pct:.0f}% (₹{l3_price:.2f})")
                    print(f"        📏 Distance UP: {distance_to_l3:+.1f}%")
                
                # ✅ CORRECT EXIT LOGIC:
                # L1 exits at 0% (break-even)
                # L2 exits at 20% (L1 trigger level)
                if leg.hedge_level == 1:
                    exit_zone_pct = 0  # L1 exits at break-even
                elif leg.hedge_level == 2:
                    exit_zone_pct = config.PROGRESSIVE_HEDGING_LEVELS[0]  # L2 exits at L1 trigger (20%)
                else:
                    exit_zone_pct = 0  # Fallback
                
                exit_zone_price = leg.entry_premium * (1 + exit_zone_pct / 100)
                distance_to_exit = exit_zone_pct - leg.current_loss_pct
                print(f"        📉 Exit zone: {exit_zone_pct:.0f}% (₹{exit_zone_price:.2f})")
                print(f"        📏 Distance DOWN: {distance_to_exit:+.1f}%")
                
                # 🆕 SHOW HEDGE P&L if available
                if leg.hedge_current_premium:
                    hedge_pnl = (leg.hedge_entry_premium - leg.hedge_current_premium) * leg.lot_size
                    hedge_icon = "💚" if hedge_pnl >= 0 else "❌"
                    print(f"        {hedge_icon} Hedge P&L: ₹{hedge_pnl:+.2f}")
                
            else:
                # ============================================================
                # NO HEDGE - Show next hedge trigger
                # ============================================================
                next_level = leg.get_next_level()
                if next_level:
                    next_sl_pct = leg.next_stop_loss_pct
                    next_sl_price = leg.entry_premium * (1 + next_sl_pct / 100)
                    distance_to_hedge = next_sl_pct - leg.current_loss_pct
                    print(f"        ⏫ NEXT: L{next_level} at {next_sl_pct:.0f}% (₹{next_sl_price:.2f})")
                    print(f"        📏 Distance: {distance_to_hedge:+.1f}%")
                else:
                    # All levels completed - show L3 hard stop
                    l3_pct = config.LEVEL_3_HARD_STOP
                    l3_price = leg.entry_premium * (1 + l3_pct / 100)
                    distance_to_l3 = l3_pct - leg.current_loss_pct
                    print(f"        🚨 NEXT: L3 HARD STOP at {l3_pct:.0f}% (₹{l3_price:.2f})")
                    print(f"        📏 Distance: {distance_to_l3:+.1f}%")
            
            print(f"{'─'*60}")
        
        # Show P&L summary
        try:
            ce_pnl = ce.get_pnl()
            pe_pnl = pe.get_pnl()
            total_pnl = ce_pnl + pe_pnl
            pnl_icon = "💰" if total_pnl >= 0 else "💸"
            print(f"   {pnl_icon} P&L: CE ₹{ce_pnl:+.2f} | PE ₹{pe_pnl:+.2f} | TOTAL ₹{total_pnl:+.2f}")
        except Exception as e:
            print(f"   💰 P&L: Calculation Error: {e}")
        
        # WebSocket status
        if api.ws_enabled and api.market_ws:
            print(f"   📡 WebSocket: [ONLINE]")
    
    def _action_skip_ce_level(self):
        """Skip CE's next level"""
        leg = self.straddle_manager.ce_leg
        if not leg:
            print("❌ CE leg not found")
            return
        
        next_level = self._get_next_level_for_leg(leg)
        
        if not next_level:
            print("❌ No more levels to skip (all completed or at Level 3)")
            return
        
        if leg.hedge_active:
            print(f"⚠️ CE hedge L{leg.hedge_level} is ACTIVE")
            print(f"   Cannot skip while hedge active - exit it first")
            return
        
        print(f"\n⚠️ CONFIRM: Skip CE Level {next_level}?")
        print(f"   Current Loss: {leg.current_loss_pct:+.1f}%")
        print(f"   Level {next_level} trigger: {config.PROGRESSIVE_HEDGING_LEVELS[next_level-1]:.1f}%")
        confirm = input("   Type 'YES' to confirm: ").strip().upper()
        
        if confirm != 'YES':
            print("❌ Cancelled")
            return
        
        leg.skip_level(next_level)
        
        if self.excel_logger:
            self.excel_logger.log_manual_intervention(
                intervention_type='LEVEL_SKIP',
                leg_type='CE',
                level=next_level,
                time=config.get_current_ist_time(),
                notes=f"Skipped Level {next_level} via menu"
            )
        
        print(f"✅ CE Level {next_level} SKIPPED!")
    
    def _action_skip_pe_level(self):
        """Skip PE's next level"""
        leg = self.straddle_manager.pe_leg
        if not leg:
            print("❌ PE leg not found")
            return
        
        next_level = self._get_next_level_for_leg(leg)
        
        if not next_level:
            print("❌ No more levels to skip (all completed or at Level 3)")
            return
        
        if leg.hedge_active:
            print(f"⚠️ PE hedge L{leg.hedge_level} is ACTIVE")
            print(f"   Cannot skip while hedge active - exit it first")
            return
        
        print(f"\n⚠️ CONFIRM: Skip PE Level {next_level}?")
        print(f"   Current Loss: {leg.current_loss_pct:+.1f}%")
        print(f"   Level {next_level} trigger: {config.PROGRESSIVE_HEDGING_LEVELS[next_level-1]:.1f}%")
        confirm = input("   Type 'YES' to confirm: ").strip().upper()
        
        if confirm != 'YES':
            print("❌ Cancelled")
            return
        
        leg.skip_level(next_level)
        
        if self.excel_logger:
            self.excel_logger.log_manual_intervention(
                intervention_type='LEVEL_SKIP',
                leg_type='PE',
                level=next_level,
                time=config.get_current_ist_time(),
                notes=f"Skipped Level {next_level} via menu"
            )
        
        print(f"✅ PE Level {next_level} SKIPPED!")
    
    def _action_force_buy_ce(self):
        """Force BUY CE hedge at next level (protective)"""
        leg = self.straddle_manager.ce_leg
        if not leg:
            print("❌ CE leg not found")
            return
        
        if leg.hedge_active:
            print(f"❌ CE already has active hedge at Level {leg.hedge_level}")
            return
        
        next_level = self._get_next_level_for_leg(leg)
        
        if not next_level:
            print("❌ No more levels available")
            return
        
        print(f"\n⚠️ CONFIRM: Force BUY CE hedge at Level {next_level}?")
        print(f"   Current Loss: {leg.current_loss_pct:.1f}%")
        print(f"   Level threshold: {config.PROGRESSIVE_HEDGING_LEVELS[next_level-1]:.1f}%")
        print(f"   ⚠️ Will BUY hedge on SAME type at MARKET PRICE")
        confirm = input("   Type 'YES' to confirm: ").strip().upper()
        
        if confirm != 'YES':
            print("❌ Cancelled")
            return
        
        self._execute_force_buy_hedge(leg, 'CE', next_level)
    
    def _action_force_buy_pe(self):
        """Force BUY PE hedge at next level (protective)"""
        leg = self.straddle_manager.pe_leg
        if not leg:
            print("❌ PE leg not found")
            return
        
        if leg.hedge_active:
            print(f"❌ PE already has active hedge at Level {leg.hedge_level}")
            return
        
        next_level = self._get_next_level_for_leg(leg)
        
        if not next_level:
            print("❌ No more levels available")
            return
        
        print(f"\n⚠️ CONFIRM: Force BUY PE hedge at Level {next_level}?")
        print(f"   Current Loss: {leg.current_loss_pct:.1f}%")
        print(f"   Level threshold: {config.PROGRESSIVE_HEDGING_LEVELS[next_level-1]:.1f}%")
        print(f"   ⚠️ Will BUY hedge on SAME type at MARKET PRICE")
        confirm = input("   Type 'YES' to confirm: ").strip().upper()
        
        if confirm != 'YES':
            print("❌ Cancelled")
            return
        
        self._execute_force_buy_hedge(leg, 'PE', next_level)
    
    def _action_force_exit_ce(self):
        """Force exit CE hedge"""
        leg = self.straddle_manager.ce_leg
        if not leg:
            print("❌ CE leg not found")
            return
        
        if not leg.hedge_active:
            print("❌ No active CE hedge to exit")
            return
        
        print(f"\n⚠️ CONFIRM: Force exit CE hedge Level {leg.hedge_level}?")
        print(f"   Hedge: {leg.hedge_symbol}")
        print(f"   Entry: ₹{leg.hedge_entry_premium:.2f}")
        print(f"   Current: ₹{leg.hedge_current_premium:.2f}")
        print(f"   ⚠️ Will SELL at MARKET PRICE (close BUY position)")
        confirm = input("   Type 'YES' to confirm: ").strip().upper()
        
        if confirm != 'YES':
            print("❌ Cancelled")
            return
        
        self._execute_force_exit_hedge(leg, 'CE')
    
    def _action_force_exit_pe(self):
        """Force exit PE hedge"""
        leg = self.straddle_manager.pe_leg
        if not leg:
            print("❌ PE leg not found")
            return
        
        if not leg.hedge_active:
            print("❌ No active PE hedge to exit")
            return
        
        print(f"\n⚠️ CONFIRM: Force exit PE hedge Level {leg.hedge_level}?")
        print(f"   Hedge: {leg.hedge_symbol}")
        print(f"   Entry: ₹{leg.hedge_entry_premium:.2f}")
        print(f"   Current: ₹{leg.hedge_current_premium:.2f}")
        print(f"   ⚠️ Will SELL at MARKET PRICE (close BUY position)")
        confirm = input("   Type 'YES' to confirm: ").strip().upper()
        
        if confirm != 'YES':
            print("❌ Cancelled")
            return
        
        self._execute_force_exit_hedge(leg, 'PE')
    
    def _get_next_level_for_leg(self, leg) -> int:
        """
        Get the next level that would trigger for this leg
        Returns None if all levels completed
        """
        available_levels = [i+1 for i in range(len(config.PROGRESSIVE_HEDGING_LEVELS))
                           if (i+1) not in leg.completed_levels]
        
        return available_levels[0] if available_levels else None
    
    def _display_hedge_status(self):
        """Display current hedge status"""
        print("\n" + "="*80)
        print("📊 CURRENT HEDGE STATUS")
        print("="*80)
        
        if not self.straddle_manager.straddle_active:
            print("No active straddle")
            print("="*80)
            return
        
        for leg, name in [(self.straddle_manager.ce_leg, "CE"), 
                          (self.straddle_manager.pe_leg, "PE")]:
            if not leg:
                continue
            
            print(f"\n{name} LEG:")
            print(f"  Current Loss: {leg.current_loss_pct:+.1f}%")
            print(f"  Completed Levels: {leg.completed_levels if leg.completed_levels else 'None'}")
            
            # Show next level
            next_level = self._get_next_level_for_leg(leg)
            if next_level:
                print(f"  ⏭️ Next Level: {next_level} (triggers at {config.PROGRESSIVE_HEDGING_LEVELS[next_level-1]:.1f}%)")
            else:
                # 🔥 HYBRID: Updated for Level 3 hard stop
                print(f"  ⚠️ All levels completed - At Level 3 ({config.LEVEL_3_HARD_STOP:.1f}%)")
            
            if leg.hedge_active:
                print(f"\n  🛡️ ACTIVE HEDGE:")
                print(f"     Level: {leg.hedge_level}")
                print(f"     Symbol: {leg.hedge_symbol}")
                print(f"     Entry: ₹{leg.hedge_entry_premium:.2f} (at {leg.current_loss_pct:.1f}% loss)")
                print(f"     Current: ₹{leg.hedge_current_premium:.2f}")
                
                # Calculate hedge P&L
                hedge_pnl = (leg.hedge_entry_premium - leg.hedge_current_premium) * leg.lot_size
                pnl_icon = "💰" if hedge_pnl >= 0 else "💸"
                print(f"     {pnl_icon} Hedge P&L: ₹{hedge_pnl:+.2f}")
            else:
                print(f"\n  ❌ No active hedge")
            
            print(f"  {'-'*76}")
        
        # Total P&L
        try:
            ce_pnl = self.straddle_manager.ce_leg.get_pnl()
            pe_pnl = self.straddle_manager.pe_leg.get_pnl()
            total_pnl = ce_pnl + pe_pnl
            
            print(f"\n💰 TOTAL P&L:")
            print(f"  CE: ₹{ce_pnl:+.2f} | PE: ₹{pe_pnl:+.2f} | TOTAL: ₹{total_pnl:+.2f}")
        except:
            pass
        
        print("="*80)
    
    def _execute_force_buy_hedge(self, leg, leg_type: str, level: int):
        """Execute force BUY hedge - protective strategy"""
        self.is_executing_trade = True
        
        try:
            print(f"\n{'='*80}")
            print(f"🛡️ FORCE BUYING {leg_type} HEDGE LEVEL {level}")
            print(f"{'='*80}")
            
            spot_price = api.get_spot_price()
            if not spot_price:
                print("❌ Could not fetch spot price")
                return
            
            # ⭐ CRITICAL FIX: Use hedge strike protection method
            strikes_to_fetch = self._generate_strikes_for_option_chain(spot_price)
            option_chain = api.get_option_chain(strikes_to_fetch)
            
            if not option_chain:
                print("❌ Could not fetch option chain")
                return
            
            # 🔥 FIXED: Determine losing_leg and profit_leg for price-neutral calculation
            if leg_type == 'CE':
                losing_leg = self.straddle_manager.ce_leg
                profit_leg = self.straddle_manager.pe_leg
            else:
                losing_leg = self.straddle_manager.pe_leg
                profit_leg = self.straddle_manager.ce_leg
            
            # 🔥 FIXED: Use corrected hedge manager method with BOTH legs
            hm = self.straddle_manager.hedge_manager
            hedge_strike, target_premium = hm._calculate_hedge_strike_losing_side(
                losing_leg, profit_leg, option_chain, level
            )
            
            hedge_symbol, hedge_security_id, hedge_premium = hm._get_hedge_from_chain(
                option_chain, hedge_strike, losing_leg.option_type
            )
            
            if not hedge_symbol:
                print(f"❌ Could not find hedge in option chain")
                return
            
            print(f"   Hedge: {hedge_symbol} @ ₹{hedge_premium:.2f}")
            
            success, order_id = hm._place_hedge_order(
                hedge_symbol, hedge_security_id, leg.lot_size
            )
            
            if not success:
                print(f"❌ Failed to place hedge order")
                return
            
            if order_id:
                time.sleep(1)
                actual_hedge_price = api.get_order_fill_price(order_id)
                if actual_hedge_price:
                    hedge_premium = actual_hedge_price
                    print(f"   💰 Actual fill: ₹{actual_hedge_price:.2f}")
            
            leg.buy_hedge(hedge_symbol, hedge_security_id, hedge_strike, hedge_premium, level)
            
            if self.excel_logger:
                self.excel_logger.log_manual_intervention(
                    intervention_type='FORCE_BUY',
                    leg_type=leg_type,
                    level=level,
                    time=config.get_current_ist_time(),
                    notes=f"Force buy via menu at {leg.current_loss_pct:.1f}% loss"
                )
                
                self.excel_logger.log_hedge_entry(
                    leg_type=leg_type,
                    level=level,
                    strike=hedge_strike,
                    entry_time=config.get_current_ist_time(),
                    entry_premium=hedge_premium,
                    entry_loss_pct=leg.current_loss_pct,
                    buffer_target_pct=0  # No buffer in pure system
                )
            
            print(f"✅ {leg_type} hedge Level {level} BOUGHT!")
            print(f"{'='*80}")
            
            # 🔥 FIX #1: Invalidate cache + trigger reconciliation
            api.invalidate_position_cache()
            self.position_reconciler.mark_order_filled()
            
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.is_executing_trade = False
    
    def _execute_force_exit_hedge(self, leg, leg_type: str):
        """Execute force exit hedge with CORRECTED P&L calculation"""
        self.is_executing_trade = True
        
        try:
            print(f"\n{'='*80}")
            print(f"🚪 FORCE EXITING {leg_type} HEDGE LEVEL {leg.hedge_level}")
            print(f"{'='*80}")
            
            # ⭐ CRITICAL: Ensure we fetch this hedge strike (even if out of ATM range)
            # First, get current spot to generate strikes WITH hedge protection
            spot_price = api.get_spot_price()
            if spot_price:
                strikes_to_fetch = self._generate_strikes_for_option_chain(spot_price)
                print(f"🔍 Monitoring {len(strikes_to_fetch)} strikes including hedge protection")
            
            exit_premium = api.get_ltp_with_retry(leg.hedge_security_id, max_retries=3)
            
            if not exit_premium:
                print(f"⚠️ Using last known premium")
                exit_premium = leg.hedge_current_premium
            
            print(f"   Exit Premium: ₹{exit_premium:.2f}")
            
            # ✅ FIX: Reuse existing hedge manager
            hm = self.straddle_manager.hedge_manager
            success, order_id = hm._place_hedge_exit_order(
                leg.hedge_symbol, leg.hedge_security_id, leg.lot_size
            )
            
            if not success:
                print(f"❌ Failed to place exit order")
                return
            
            if order_id:
                time.sleep(1)
                actual_exit_price = api.get_order_fill_price(order_id)
                if actual_exit_price:
                    exit_premium = actual_exit_price
                    print(f"   💰 Actual exit: ₹{actual_exit_price:.2f}")
            
            # 🔥 FIXED: Use leg.close_hedge() return value for correct P&L
            exited_level = leg.hedge_level
            exit_event = leg.close_hedge(exit_premium)  # Returns dict with correct P&L
            hedge_pnl = exit_event['pnl']  # Use the CORRECT P&L from close_hedge()
            
            # 🔥 NEW: Set flag to prevent immediate re-entry in same candle
            leg.manual_exit_timestamp = config.get_current_ist_time()
            
            if self.excel_logger:
                self.excel_logger.log_manual_intervention(
                    intervention_type='FORCE_EXIT',
                    leg_type=leg_type,
                    level=exited_level,
                    time=config.get_current_ist_time(),
                    notes=f"Force exit via menu, P&L: ₹{hedge_pnl:.2f}"
                )
                
                self.excel_logger.log_hedge_exit(
                    leg_type=leg_type,
                    level=exited_level,
                    exit_time=config.get_current_ist_time(),
                    exit_premium=exit_premium,
                    exit_loss_pct=leg.current_loss_pct,
                    trail_activated=False,
                    hedge_pnl=hedge_pnl,
                    exit_reason="Manual Force Exit"
                )
            
            print(f"✅ {leg_type} hedge Level {exited_level} EXITED!")
            print(f"   Hedge P&L: ₹{hedge_pnl:+.2f}")
            print(f"{'='*80}")
            
            # 🔥 FIX #1: Invalidate cache + trigger reconciliation
            api.invalidate_position_cache()
            self.position_reconciler.mark_order_filled()
            
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.is_executing_trade = False
    
    def _display_keyboard_help(self):
        """Display keyboard commands"""
        print("\n" + "="*80)
        print("⌨️ KEYBOARD COMMANDS")
        print("="*80)
        print("MASTER CONTROL:")
        print("  Press Ctrl+C anytime → Opens Control Menu")
        print("")
        print("FROM MENU YOU CAN:")
        print("  • Check P&L [P]")
        print("  • Sync positions [S]")
        print("  • Skip hedge levels [1,2]")
        print("  • Force BUY hedges [3,4]")
        print("  • Force exit hedges [5,6]")
        print("  • View hedge status [7]")
        print("  • Exit trading [0]")
        print("  • Continue trading [C]")
        print("")
        print("💡 Everything in ONE menu - no nested navigation!")
        print("="*80 + "\n")
    
    def _display_exit_summary(self):
        """Display detailed position summary before exit"""
        print("\n" + "="*80)
        print("POSITION SUMMARY - BEFORE EXIT")
        print("="*80)
        
        if not self.straddle_manager.straddle_active:
            print("[OK] No active positions")
            print("="*80 + "\n")
            return
        
        # ✅ FIX: Added comprehensive null checks
        if not self.straddle_manager.ce_leg or not self.straddle_manager.pe_leg:
            print("[ERROR] Legs not properly initialized")
            print("="*80 + "\n")
            return
        
        ce = self.straddle_manager.ce_leg
        pe = self.straddle_manager.pe_leg
        
        print(f"\n[STRADDLE] Straddle Details:")
        print(f"   Strike: {self.straddle_manager.strike}")
        print(f"   Entry Time: {self.straddle_manager.entry_time.strftime('%H:%M:%S') if self.straddle_manager.entry_time else 'N/A'}")
        
        print(f"\n[CE LEG] CE Leg: {ce.symbol}")
        print(f"   Security ID: {ce.security_id}")
        print(f"   Entry Premium: Rs.{ce.entry_premium:.2f}")
        print(f"   Current Premium: Rs.{ce.current_premium:.2f}")
        print(f"   Loss: {ce.current_loss_pct:+.1f}%")
        
        try:
            ce_pnl_breakdown = ce.get_pnl_breakdown()
            print(f"   Leg P&L: Rs.{ce_pnl_breakdown['leg_pnl']:,.2f}")
        except:
            print(f"   Leg P&L: [Calculation Error]")
        
        if ce.hedge_active:
            print(f"\n   [HEDGE] CE Hedge Level {ce.hedge_level}: {ce.hedge_symbol}")
            print(f"      Security ID: {ce.hedge_security_id}")
            print(f"      Entry: Rs.{ce.hedge_entry_premium:.2f} | Current: Rs.{ce.hedge_current_premium:.2f}")
            try:
                print(f"      Hedge P&L: Rs.{ce.get_pnl_breakdown()['current_hedge_pnl']:,.2f}")
            except:
                print(f"      Hedge P&L: [Calculation Error]")
        
        if getattr(ce, 'realized_hedge_pnl', 0) != 0:
            print(f"   [PNL] Realized Hedge P&L: Rs.{ce.realized_hedge_pnl:,.2f}")
        
        print(f"\n[PE LEG] PE Leg: {pe.symbol}")
        print(f"   Security ID: {pe.security_id}")
        print(f"   Entry Premium: Rs.{pe.entry_premium:.2f}")
        print(f"   Current Premium: Rs.{pe.current_premium:.2f}")
        print(f"   Loss: {pe.current_loss_pct:+.1f}%")
        
        try:
            pe_pnl_breakdown = pe.get_pnl_breakdown()
            print(f"   Leg P&L: Rs.{pe_pnl_breakdown['leg_pnl']:,.2f}")
        except:
            print(f"   Leg P&L: [Calculation Error]")
        
        if pe.hedge_active:
            print(f"\n   [HEDGE] PE Hedge Level {pe.hedge_level}: {pe.hedge_symbol}")
            print(f"      Security ID: {pe.hedge_security_id}")
            print(f"      Entry: Rs.{pe.hedge_entry_premium:.2f} | Current: Rs.{pe.hedge_current_premium:.2f}")
            try:
                print(f"      Hedge P&L: Rs.{pe.get_pnl_breakdown()['current_hedge_pnl']:,.2f}")
            except:
                print(f"      Hedge P&L: [Calculation Error]")
        
        if getattr(pe, 'realized_hedge_pnl', 0) != 0:
            print(f"   [PNL] Realized Hedge P&L: Rs.{pe.realized_hedge_pnl:,.2f}")
        
        try:
            total_pnl = ce.get_pnl() + pe.get_pnl()
            print(f"\n{'-'*76}")
            print(f"  [TOTAL] TOTAL P&L: Rs.{total_pnl:,.2f}")
            print(f"{'-'*76}")
        except:
            print(f"\n{'-'*76}")
            print(f"  [TOTAL] TOTAL P&L: [Calculation Error]")
            print(f"{'-'*76}")
            
        print("="*80 + "\n")
    
    def _handle_interactive_exit(self):
        """Handle interactive exit - ask user what to do"""
        self._display_exit_summary()
        
        print("="*80)
        print("EXIT OPTIONS")
        print("="*80)
        print("Choose how to handle open positions:\n")
        print("  [1] AUTO SQUARE-OFF - Let script close all positions now")
        print("      -> Immediate exit with retry logic")
        print("      -> Full Excel logging")
        print("      -> Best for fast exit\n")
        print("  [2] SAFE EXIT - I'll close manually from broker terminal")
        print("      -> Full control over exit timing & prices")
        print("      -> Can leg out strategically")
        print("      -> Must manually close ALL positions (see summary above)")
        print("="*80)
        
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                choice = input(f"\nEnter choice (1 or 2): ").strip()
                
                if choice == '1':
                    print("\n" + "="*80)
                    print("[AUTO SQUAREOFF] AUTO SQUAREOFF INITIATED")
                    print("="*80)
                    self.process_force_exit_and_reentry("Manual Stop - Auto Square-Off")
                    print("[OK] All positions closed successfully")
                    return True
                
                elif choice == '2':
                    print("\n" + "="*80)
                    print("[SAFE EXIT] SAFE EXIT MODE - Positions remain open")
                    print("="*80)
                    print("\n[IMPORTANT] IMPORTANT REMINDERS:")
                    print("   1. All positions are STILL OPEN in your broker account")
                    print("   2. You MUST manually close them from broker terminal:")
                    
                    ce = self.straddle_manager.ce_leg
                    pe = self.straddle_manager.pe_leg
                    
                    print("\n   [CHECKLIST] CHECKLIST:")
                    print(f"      [ ] Close CE: {ce.symbol} (Security ID: {ce.security_id})")
                    print(f"      [ ] Close PE: {pe.symbol} (Security ID: {pe.security_id})")
                    if ce.hedge_active:
                        print(f"      [ ] Close CE Hedge L{ce.hedge_level}: {ce.hedge_symbol}")
                    if pe.hedge_active:
                        print(f"      [ ] Close PE Hedge L{pe.hedge_level}: {pe.hedge_symbol}")
                    
                    try:
                        total_pnl = ce.get_pnl() + pe.get_pnl()
                        print(f"\n   [PNL] Last Known P&L: Rs.{total_pnl:,.2f}")
                    except:
                        print(f"\n   [PNL] Last Known P&L: [Calculation Error]")
                        
                    print("\n   [WARN] Market is moving - prices will change!")
                    print("="*80 + "\n")
                    return False
                
                else:
                    print(f"[ERROR] Invalid choice '{choice}'. Please enter 1 or 2.")
                    if attempt < max_attempts - 1:
                        print(f"   ({max_attempts - attempt - 1} attempt(s) remaining)")
            
            except EOFError:
                # Input stream closed (e.g., running in background)
                print("\n\n[WARN] Cannot read input - defaulting to SAFE EXIT mode")
                print("[OK] Positions remain open - please manually square off")
                return False
            
            except Exception as e:
                print(f"\n[ERROR] Input error: {e}")
                if attempt < max_attempts - 1:
                    print(f"   ({max_attempts - attempt - 1} attempt(s) remaining)")
        
        # Failed all attempts - default to SAFE EXIT
        print("\n\n[WARN] Max input attempts reached - defaulting to SAFE EXIT mode")
        print("[OK] Positions remain open - please manually square off from broker terminal\n")
        return False
    
    def main_loop(self):
        """Main trading loop with ALIGNED CANDLES + KEYBOARD COMMANDS"""
        self.running = True
        
        # ✅ NEW: Pre-flight check for market hours
        if not config.is_market_open():
            print("\n" + "="*80)
            print("⚠️ MARKET IS CURRENTLY CLOSED")
            print("="*80)
            print(f"Current Time: {config.get_current_ist_time().strftime('%Y-%m-%d %H:%M:%S IST')}")
            print(f"Market Hours: {config.MARKET_OPEN_TIME} - {config.MARKET_CLOSE_TIME}")
            print("\nOptions:")
            print("  [1] Wait for market to open (script will idle)")
            print("  [2] Exit now and restart when market opens")
            print("="*80)
            
            try:
                choice = input("\nEnter choice (1 or 2): ").strip()
                if choice == '2':
                    print("✅ Exiting - Restart during market hours")
                    return
            except KeyboardInterrupt:
                print("\n✅ Exiting")
                return
        
        print("\n[STARTING] Trading loop started")
        print(f"[CONFIG] Press Ctrl+C to open Control Menu")
        print(f"[CONFIG] Candle Interval: {config.CANDLE_INTERVAL_SECONDS} seconds")
        print(f"[CONFIG] ⏰ CANDLES ALIGNED TO WALL CLOCK")
        print(f"[CONFIG] Production-Safe Reconciliation: Enabled")
        print(f"[CONFIG] Event-Driven Reconciliation: Enabled")
        print(f"[CONFIG] Interactive Exit: Enabled")
        print(f"[CONFIG] ✅ SIMPLIFIED AUTHENTICATION: One login per day")
        print(f"[CONFIG] ✅ REACTIVE RE-LOGIN: Only on AB1007 errors")
        print(f"[CONFIG] ✅ NO PROACTIVE REFRESHES")
        print(f"[CONFIG] ✅ SIMPLIFIED KEYBOARD: Ctrl+C only")
        print(f"[CONFIG] HYBRID: 2-Level Price-Neutral + Level 3 Hard Stop")
        print(f"[CONFIG] PURE: NO BUFFER/NO TRAILING - Hold SELL hedges until Level 3")
        print(f"[CONFIG] BALANCED: 17 strikes (±8) coverage")
        print(f"[CONFIG] 🛡️ HEDGE STRIKE PROTECTION: ACTIVE ✅")
        print(f"[CONFIG] ✅ WebSocket Health Check: Reactive only")
        print(f"[CONFIG] ✅ FIXED: Instant resume from menu (no 2-minute delay)\n")
        
        try:
            while self.running:
                try:
                    # 🔥 FIX: Proper menu active check - no race condition
                    if self.menu_active:
                        time.sleep(0.1)
                        continue
                    
                    # Check for interrupts
                    if self.interrupt_received:
                        self._handle_interactive_exit()
                        if not self.running:
                            break

                    self.process_candle()
                    
                    # 🔥 NEW: Wait for next ALIGNED candle (not just fixed sleep)
                    self._wait_for_next_candle()
                
                except KeyboardInterrupt:
                    # This catches Ctrl+C in main loop
                    print("\n\n" + "="*80)
                    print("[INTERRUPT] KEYBOARD INTERRUPT RECEIVED (Ctrl+C)")
                    print("="*80)
                    
                    if self.straddle_manager.straddle_active:
                        auto_squared_off = self._handle_interactive_exit()
                        
                        if not auto_squared_off:
                            # User chose manual exit - positions still open
                            print("\n[STOP] Script stopping - positions remain OPEN in broker")
                            print("[WARN] DON'T FORGET to manually square off!\n")
                    else:
                        print("\n[OK] No active positions - safe to exit")
                        print("="*80 + "\n")
                    
                    break
                
                except Exception as e:
                    print(f"[ERROR] Error in main loop: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    
                    # 🔥 NEW: Don't continue if there's an error with open positions
                    if self.straddle_manager.straddle_active:
                        print("\n🚨 ERROR WITH ACTIVE POSITIONS!")
                        print("Pausing for safety...")
                        print("Press Ctrl+C to handle positions, or wait 30s to continue")
                        self.interruptible_sleep(30)
                    else:
                        self.interruptible_sleep(5)
        
        except Exception as e:
            print(f"\n[CRITICAL] Unhandled exception in main loop: {e}")
            import traceback
            traceback.print_exc()
        
        finally:
            print("\n[STOP] Trading loop stopped")
    
    def run(self):
        """Run the trading system"""
        if not self.initialize_system():
            return
        
        try:
            self.main_loop()
        finally:
            print("\n" + "=" * 80)
            print("SYSTEM SHUTDOWN")
            print("=" * 80)
            self.excel_logger.close()
            print("[OK] Logs saved")


def main():
    """Entry point"""
    trader = LiveTrader()
    trader.run()


if __name__ == "__main__":
    main()