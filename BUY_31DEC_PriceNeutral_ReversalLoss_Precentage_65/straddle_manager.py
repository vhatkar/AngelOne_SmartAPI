"""
Straddle Manager - PURE PRICE-NEUTRAL HEDGING
NO BUFFER/NO TRAILING - Hold SELL hedges until Level 3
Logs every leg and hedge action to Excel with timestamps
FIXED: Straddle leg exits now use verified orders with retry logic
TRUE SIMULTANEOUS ORDER FIRING: Uses threading for instant execution
✅ BATCH API OPTIMIZATIONS ADDED
✅ FIXED: WebSocket-only verification for order fills
✅ FIXED: Race condition protection
✅ FIXED: Actual fill price tracking - CRITICAL FIX APPLIED
✅ MANUAL HEDGE SUPPORT ADDED
✅ ADDED: Manual strike selection support
✅ HYBRID: Updated for 2-level price-neutral + Level 3 hard stop
✅ CORRECTED: Price-neutral hedging with both legs passed to hedge manager
✅ FIXED: Hedge premium update with correct option types
✅ FIXED: Added critical operation locks
"""

from typing import Optional, Dict, Tuple, List
from datetime import datetime
from leg import Leg
from hedge_manager import HedgeManager
from angelone_api import api
from config import config
import time
import threading


class StraddleManager:
    """Manages straddle positions with enhanced detailed logging"""

    def __init__(self, excel_logger=None):
        """Initialize straddle manager"""
        self.ce_leg: Optional[Leg] = None
        self.pe_leg: Optional[Leg] = None
        self.hedge_manager = HedgeManager()
        self.excel_logger = excel_logger
        self.straddle_active = False
        self.strike = None
        self.entry_time = None
        self.session_id = 0

        # Track number of hedges used per leg
        self.ce_hedges_count = 0
        self.pe_hedges_count = 0
        
        # 🔥 HYBRID: Cache for price-neutral hedge calculation
        self._global_option_chain = None

    def enter_straddle(self, spot_price: float, option_chain: Dict, manual_strike: Optional[int] = None) -> bool:
        """✅ FIX 4: Enter new straddle with CRITICAL LOCK"""
        # ✅ CRITICAL FIX: Initialize all variables BEFORE try block to avoid undefined references
        ce_result = None
        pe_result = None
        ce_symbol = None
        pe_symbol = None
        ce_security_id = None
        pe_security_id = None
        success = False
        
        if self.straddle_active:
            print("[WARN] Straddle already active")
            return False

        try:
            # 🎯 Strike selection: Manual or Auto scan
            if manual_strike is not None:
                # MANUAL MODE: Use provided strike
                strike = manual_strike
                
                # Get premiums from option chain
                strike_data = option_chain.get(strike, {})
                ce_premium = strike_data.get('CE', 0)
                pe_premium = strike_data.get('PE', 0)
                
                if not ce_premium or not pe_premium:
                    print(f"[ERROR] Manual strike {strike} has invalid premiums in option chain")
                    return False
                
                print(f"\n[MANUAL] Using manual strike: {strike}")
                print(f"   CE Premium: ₹{ce_premium:.2f}")
                print(f"   PE Premium: ₹{pe_premium:.2f}")
                print(f"   Total: ₹{ce_premium + pe_premium:.2f}")
            else:
                # AUTO MODE: Scan for best strike
                strike, ce_premium, pe_premium = self.scan_best_straddle(spot_price, option_chain)
                print(f"🎯 AUTO selected strike: {strike}")

            # CRITICAL FIX: Enhanced premium validation with better error handling
            max_reasonable_premium = spot_price * 0.10  # 10% of spot as upper limit
            min_reasonable_premium = config.MIN_PREMIUM_THRESHOLD  # Use config value
            
            # Check for unreasonably high premiums
            if ce_premium > max_reasonable_premium:
                print(f"[WARN] CE premium too high: Rs.{ce_premium:.2f} > Rs.{max_reasonable_premium:.2f}, skipping entry")
                return False
                
            if pe_premium > max_reasonable_premium:
                print(f"[WARN] PE premium too high: Rs.{pe_premium:.2f} > Rs.{max_reasonable_premium:.2f}, skipping entry")
                return False
                
            # Check for unreasonably low premiums (likely bad data)
            if ce_premium < min_reasonable_premium:
                print(f"[WARN] CE premium too low: Rs.{ce_premium:.2f} < Rs.{min_reasonable_premium:.2f}, skipping entry")
                return False
                
            if pe_premium < min_reasonable_premium:
                print(f"[WARN] PE premium too low: Rs.{pe_premium:.2f} < Rs.{min_reasonable_premium:.2f}, skipping entry")
                return False

            # Check if premiums are too far apart (indication of bad data or wrong strike)
            if ce_premium > 0 and pe_premium > 0:
                premium_ratio = min(ce_premium, pe_premium) / max(ce_premium, pe_premium)
                if premium_ratio < 0.3:
                    print(f"[WARN] Premiums too imbalanced - ratio: {premium_ratio:.2f}, CE: Rs.{ce_premium:.2f}, PE: Rs.{pe_premium:.2f}, skipping entry")
                    return False
            else:
                print(f"[WARN] Invalid premiums - CE: {ce_premium}, PE: {pe_premium}, skipping entry")
                return False

            # Get option details
            ce_data = self._find_straddle_in_chain(option_chain, strike, 'CE')
            pe_data = self._find_straddle_in_chain(option_chain, strike, 'PE')

            if not ce_data or not pe_data:
                print(f"[ERROR] Option data not found for strike {strike}")
                return False

            ce_symbol, ce_security_id = ce_data
            pe_symbol, pe_security_id = pe_data

            print(f"\n{'=' * 80}")
            print(f"ENTERING STRADDLE - TRUE SIMULTANEOUS FIRING")
            print(f"{'=' * 80}")
            print(f"Strike: {strike}")
            print(f"CE: {ce_symbol} (Rs.{ce_premium:.2f})")
            print(f"PE: {pe_symbol} (Rs.{pe_premium:.2f})")
            print(f"Total Premium: Rs.{ce_premium + pe_premium:.2f}")

            entry_time = config.get_current_ist_time()

            # 🔥 HYBRID: Cache option chain for price-neutral hedge calculation
            self._global_option_chain = option_chain
            StraddleManager._global_option_chain = option_chain

            # 🔥 FIX 4: Acquire critical lock
            api.acquire_critical_lock(f"STRADDLE_ENTRY_{strike}")

            # FIRE BOTH ORDERS SIMULTANEOUSLY USING THREADS
            print(f"\n[FIRING] BOTH LEGS SIMULTANEOUSLY...")

            # Use threads to place orders simultaneously
            order_results = {}

            ce_thread = threading.Thread(
                target=self._place_order_thread,
                args=('CE', ce_symbol, ce_security_id, order_results)
            )

            pe_thread = threading.Thread(
                target=self._place_order_thread,
                args=('PE', pe_symbol, pe_security_id, order_results)
            )

            # Start both threads
            ce_thread.start()
            pe_thread.start()

            # Wait for both threads to complete
            ce_thread.join()
            pe_thread.join()

            # Check results
            ce_result = order_results.get('CE', {'success': False, 'message': 'No result'})
            pe_result = order_results.get('PE', {'success': False, 'message': 'No result'})

            # Log failed attempts
            if not ce_result.get('success'):
                error_message = ce_result.get('message', 'Unknown error')
                print(f"   [ERROR] CE Order failed: {error_message}")
                if self.excel_logger:
                    self.excel_logger.log_leg_action(
                        leg_type="CE", action="SELL", time=entry_time,
                        strike=strike, symbol=ce_symbol, security_id=ce_security_id,
                        premium=ce_premium, quantity=config.LOT_SIZE,
                        order_status="FAILED", notes=error_message
                    )

            if not pe_result.get('success'):
                error_message = pe_result.get('message', 'Unknown error')
                print(f"   [ERROR] PE Order failed: {error_message}")
                if self.excel_logger:
                    self.excel_logger.log_leg_action(
                        leg_type="PE", action="SELL", time=entry_time,
                        strike=strike, symbol=pe_symbol, security_id=pe_security_id,
                        premium=pe_premium, quantity=config.LOT_SIZE,
                        order_status="FAILED", notes=error_message
                    )

            # If both failed or one failed, rollback
            if not ce_result.get('success') or not pe_result.get('success'):
                print(f"\n[ROLLBACK] Rolling back successful orders...")

                # Rollback CE if it was successful but PE failed
                if ce_result.get('success') and not pe_result.get('success'):
                    print(f"   [ROLLBACK] Rolling back CE order...")
                    api.place_order(
                        transaction_type='BUY',
                        symbol=ce_symbol,
                        security_id=ce_security_id,
                        quantity=config.LOT_SIZE
                    )

                # Rollback PE if it was successful but CE failed
                if pe_result.get('success') and not ce_result.get('success'):
                    print(f"   [ROLLBACK] Rolling back PE order...")
                    api.place_order(
                        transaction_type='BUY',
                        symbol=pe_symbol,
                        security_id=pe_security_id,
                        quantity=config.LOT_SIZE
                    )

                print(f"[ERROR] Straddle entry aborted - simultaneous execution failed")
                return False

            # Both orders placed successfully, get order IDs
            ce_order_id = ce_result.get('order_id')
            pe_order_id = pe_result.get('order_id')

            if not ce_order_id or not pe_order_id:
                print(f"[ERROR] Missing order IDs - CE: {ce_order_id}, PE: {pe_order_id}")
                return False

            # ✅ FIXED: Use WebSocket-only verification (REST fallback disabled)
            print(f"\n[VERIFYING] Verifying order execution via WebSocket...")
            time.sleep(2)

            # 🔥 FIXED: Use WebSocket-only verification with race condition protection
            ce_filled = api.verify_order_fill_websocket(ce_order_id, max_wait=30)
            time.sleep(0.5)
            pe_filled = api.verify_order_fill_websocket(pe_order_id, max_wait=30)

            # Rollback logic if verification fails
            if not ce_filled or not pe_filled:
                print(f"\n[WARN] ORDER VERIFICATION FAILED")
                print(f"   CE Filled: {ce_filled}")
                print(f"   PE Filled: {pe_filled}")

                if ce_filled and not pe_filled:
                    print(f"[ROLLBACK] Rolling back CE order...")
                    api.place_order(
                        transaction_type='BUY',
                        symbol=ce_symbol,
                        security_id=ce_security_id,
                        quantity=config.LOT_SIZE
                    )

                if pe_filled and not ce_filled:
                    print(f"[ROLLBACK] Rolling back PE order...")
                    api.place_order(
                        transaction_type='BUY',
                        symbol=pe_symbol,
                        security_id=pe_security_id,
                        quantity=config.LOT_SIZE
                    )

                print(f"[ERROR] Straddle entry aborted - verification failed")
                return False

            # 🔥 CRITICAL FIX: Get ACTUAL fill prices from order book
            print(f"\n[VERIFYING] Fetching actual fill prices from broker...")

            # Get actual fill price for CE
            actual_ce_price = api.get_order_fill_price(ce_order_id)
            if actual_ce_price:
                ce_slippage = actual_ce_price - ce_premium
                print(f"   📊 CE: Decision ₹{ce_premium:.2f} → Fill ₹{actual_ce_price:.2f} (Slippage: {ce_slippage:+.2f})")
                ce_premium = actual_ce_price  # Use actual fill price
            else:
                print(f"   ⚠️ Could not fetch CE fill price, using market price")
                # Fallback to current market price
                actual_ce_price = api.get_ltp_with_retry(ce_security_id, max_retries=3)
                if actual_ce_price:
                    ce_premium = actual_ce_price

            time.sleep(0.5)

            # Get actual fill price for PE
            actual_pe_price = api.get_order_fill_price(pe_order_id)
            if actual_pe_price:
                pe_slippage = actual_pe_price - pe_premium
                print(f"   📊 PE: Decision ₹{pe_premium:.2f} → Fill ₹{actual_pe_price:.2f} (Slippage: {pe_slippage:+.2f})")
                pe_premium = actual_pe_price  # Use actual fill price
            else:
                print(f"   ⚠️ Could not fetch PE fill price, using market price")
                # Fallback to current market price
                actual_pe_price = api.get_ltp_with_retry(pe_security_id, max_retries=3)
                if actual_pe_price:
                    pe_premium = actual_pe_price

            # Calculate total slippage
            if actual_ce_price and actual_pe_price:
                total_slippage = (actual_ce_price - ce_premium) + (actual_pe_price - pe_premium)
                print(f"   💰 Total Entry Premium (ACTUAL): ₹{ce_premium + pe_premium:.2f}")
                if abs(total_slippage) > 1.0:
                    print(f"   ⚠️ Total Slippage: ₹{total_slippage:+.2f}")

            # Create leg objects with ACTUAL fill premiums
            self.ce_leg = Leg(
                name="CE",
                strike=strike,
                option_type="CE",
                entry_premium=ce_premium,
                symbol=ce_symbol,
                security_id=ce_security_id
            )

            self.pe_leg = Leg(
                name="PE",
                strike=strike,
                option_type="PE",
                entry_premium=pe_premium,
                symbol=pe_symbol,
                security_id=pe_security_id
            )

            # Update state
            self.straddle_active = True
            self.entry_time = entry_time
            self.strike = strike
            self.session_id += 1
            self.ce_hedges_count = 0
            self.pe_hedges_count = 0

            # ✅ FIXED: Subscribe to WebSocket using market_ws with correct format
            if api.ws_enabled and api.market_ws:
                try:
                    # Subscribe to straddle legs
                    token_list = [{
                        "exchangeType": 2,  # NSE_FO
                        "tokens": [ce_security_id, pe_security_id]
                    }]
                    
                    api.market_ws.subscribe(
                        correlation_id=f"straddle_{int(time.time())}",
                        mode=1,  # LTP mode
                        token_list=token_list
                    )
                    print("[OK] Subscribed straddle legs to WebSocket")
                except Exception as e:
                    print(f"[WARN] WebSocket subscription failed: {e}")

            # LOG TO EXCEL
            if self.excel_logger:
                # Log main trade entry
                self.excel_logger.log_entry(
                    entry_time=entry_time,
                    atm_strike=strike,
                    ce_premium=ce_premium,
                    pe_premium=pe_premium,
                    spot_price=spot_price
                )

                # Log CE leg action
                self.excel_logger.log_leg_action(
                    leg_type="CE", action="SELL", time=entry_time,
                    strike=strike, symbol=ce_symbol, security_id=ce_security_id,
                    premium=ce_premium, quantity=config.LOT_SIZE,
                    order_status="FILLED", notes="Straddle Entry"
                )

                # Log PE leg action
                self.excel_logger.log_leg_action(
                    leg_type="PE", action="SELL", time=entry_time,
                    strike=strike, symbol=pe_symbol, security_id=pe_security_id,
                    premium=pe_premium, quantity=config.LOT_SIZE,
                    order_status="FILLED", notes="Straddle Entry"
                )

            print(f"\n[OK] STRADDLE ENTRY SUCCESSFUL")
            print(f"   Session ID: {self.session_id}")
            print(f"   Strike: {strike}")
            print(f"   Entry Time: {entry_time.strftime('%H:%M:%S')}")
            print(f"   [OK] BOTH LEGS EXECUTED SIMULTANEOUSLY")
            print(f"{'=' * 80}\n")

            success = True
            return True

        except Exception as e:
            print(f"[ERROR] Straddle entry error: {e}")
            import traceback
            traceback.print_exc()
            
            # 🔥 CRITICAL: Clean up on error
            print("\n" + "="*80)
            print("🚨 STRADDLE ENTRY FAILED - POSITIONS MAY BE OPEN!")
            print("="*80)
            print(f"   CE Order: {ce_result.get('order_id') if ce_result else 'Unknown'}")
            print(f"   PE Order: {pe_result.get('order_id') if pe_result else 'Unknown'}")
            print("\n⚠️ CHECK YOUR BROKER TERMINAL AND SQUARE OFF MANUALLY IF NEEDED!")
            print("="*80 + "\n")
            
            return False  # ✅ Return False, don't crash completely
        finally:
            # 🔥 FIX 4: Always release lock
            api.release_critical_lock(f"STRADDLE_ENTRY_{strike}")

    def scan_best_straddle(self, spot_price: float, option_chain: Dict) -> Tuple[int, float, float]:
        """FIXED: Select straddle with minimum CE-PE premium difference from ALL available strikes"""
        base_atm = round(spot_price / config.STRIKE_INTERVAL) * config.STRIKE_INTERVAL

        # CRITICAL FIX: Use ALL strikes from the option chain that was fetched
        available_strikes = list(option_chain.keys())
        
        if not available_strikes:
            print("[ERROR] No strikes available in option chain")
            return base_atm, 0, 0

        min_diff = float('inf')
        best_strike = base_atm
        best_ce_premium = 0
        best_pe_premium = 0
        strikes_considered = 0

        for strike in available_strikes:
            strike_data = option_chain[strike]
            ce_premium = strike_data.get('CE', 0)
            pe_premium = strike_data.get('PE', 0)

            if ce_premium > 0 and pe_premium > 0:
                diff = abs(ce_premium - pe_premium)
                if diff < min_diff:
                    min_diff = diff
                    best_strike = strike
                    best_ce_premium = ce_premium
                    best_pe_premium = pe_premium
                strikes_considered += 1

        print(f"\n[OK] Scanned {strikes_considered} strikes | Best: {best_strike} | CE: Rs.{best_ce_premium:.2f} | PE: Rs.{best_pe_premium:.2f} | Diff: Rs.{min_diff:.2f}")

        return best_strike, best_ce_premium, best_pe_premium

    def _place_order_thread(self, order_type: str, symbol: str, security_id: str, result_dict: dict):
        """
        Place order in a separate thread
        🔥 UPDATED: Handles new response format with order_status
        """
        try:
            order_result = api.place_order(
                transaction_type='SELL',
                symbol=symbol,
                security_id=security_id,
                quantity=config.LOT_SIZE
            )
            result_dict[order_type] = order_result
            
            # 🔥 NEW: Log immediate order status if available
            if order_result.get('success') and order_result.get('order_status'):
                print(f"   📊 {order_type} immediate status: {order_result['order_status']}")
                
        except Exception as e:
            result_dict[order_type] = {
                'success': False,
                'message': str(e),
                'order_status': None
            }

    def update_positions(self, option_chain: Dict) -> Optional[str]:
        """SIMPLE: Premiums → Hedges → Level 3"""
        if not self.straddle_active:
            return None

        # FIX: Added null checks for legs
        if not self.ce_leg or not self.pe_leg:
            print("[ERROR] Legs not initialized")
            return None

        # 🔥 OPTIMIZED: Get all premiums from option chain first
        ce_premium = self._get_premium_from_chain(option_chain, self.strike, 'CE')
        pe_premium = self._get_premium_from_chain(option_chain, self.strike, 'PE')
        
        # If option chain fetch failed, try batch API directly
        if not ce_premium or not pe_premium:
            print("[WARN] Premium missing from chain, trying batch API...")
            
            # 🔥 Use batch API as fallback
            ce_hedge_id = self.ce_leg.hedge_security_id if self.ce_leg.hedge_active else None
            pe_hedge_id = self.pe_leg.hedge_security_id if self.pe_leg.hedge_active else None
            
            premiums = api.update_straddle_premiums_batch(
                self.ce_leg.security_id,
                self.pe_leg.security_id,
                ce_hedge_id,
                pe_hedge_id
            )
            
            if premiums:
                ce_premium = premiums.get('ce')
                pe_premium = premiums.get('pe')
                ce_hedge_premium = premiums.get('ce_hedge')
                pe_hedge_premium = premiums.get('pe_hedge')
                
                if not ce_premium or not pe_premium:
                    print("[ERROR] Could not get current premiums from batch API")
                    return None
                
                # Update all at once
                self.ce_leg.update_premium(ce_premium)
                self.pe_leg.update_premium(pe_premium)
                
                if ce_hedge_premium and self.ce_leg.hedge_active:
                    self.ce_leg.update_hedge_premium(ce_hedge_premium)
                if pe_hedge_premium and self.pe_leg.hedge_active:
                    self.pe_leg.update_hedge_premium(pe_hedge_premium)
            else:
                print("[ERROR] Could not get current premiums")
                return None
        else:
            # Got premiums from chain, update legs
            self.ce_leg.update_premium(ce_premium)
            self.pe_leg.update_premium(pe_premium)

            # 🔥 FIXED: Update hedge premiums with CORRECT option types
            if self.ce_leg.hedge_active and hasattr(self.ce_leg, 'hedge_strike'):
                # 🔥 FIX: CE hedge is PE on profit side
                opposing_type = 'PE' if self.ce_leg.option_type == 'CE' else 'CE'
                ce_hedge_premium = self._get_premium_from_chain(
                    option_chain, self.ce_leg.hedge_strike, opposing_type)
                if ce_hedge_premium:
                    self.ce_leg.update_hedge_premium(ce_hedge_premium)

            if self.pe_leg.hedge_active and hasattr(self.pe_leg, 'hedge_strike'):
                # 🔥 FIX: PE hedge is CE on profit side
                opposing_type = 'CE' if self.pe_leg.option_type == 'PE' else 'PE'
                pe_hedge_premium = self._get_premium_from_chain(
                    option_chain, self.pe_leg.hedge_strike, opposing_type)
                if pe_hedge_premium:
                    self.pe_leg.update_hedge_premium(pe_hedge_premium)

        # Check premium ratio exit FIRST
        if self.check_premium_ratio_force_exit():
            return "Premium Ratio Force Exit"

        # 🔥 FIXED: Pass BOTH legs for true price neutrality
        self.hedge_manager.process_leg_hedging(self.ce_leg, self.pe_leg, option_chain)
        self.hedge_manager.process_leg_hedging(self.pe_leg, self.ce_leg, option_chain)
        
        # Level 3 ONLY
        if self.ce_leg.is_level_3_triggered():
            return "Level 3 Hard Stop - CE"

        if self.pe_leg.is_level_3_triggered():
            return "Level 3 Hard Stop - PE"

        return None

    def _log_hedge_event(self, event: Dict, leg_type: str):
        """Log hedge event to Excel"""
        if not self.excel_logger:
            return

        action = event.get('action')

        if action == 'HEDGE_SELL':
            self.excel_logger.log_hedge_entry(
                leg_type=leg_type,
                level=event['level'],
                strike=event['strike'],
                entry_time=config.get_current_ist_time(),
                entry_premium=event['premium'],
                entry_loss_pct=event['entry_loss_pct'],
                buffer_target_pct=0  # No buffer in pure system
            )

            # Log leg action
            self.excel_logger.log_leg_action(
                leg_type=leg_type,
                action="SELL",
                time=config.get_current_ist_time(),
                strike=event['strike'],
                symbol=f"{leg_type} Hedge L{event['level']}",
                security_id="",
                premium=event['premium'],
                quantity=config.LOT_SIZE,
                order_status="FILLED",
                notes=f"Hedge Level {event['level']} @ {event['entry_loss_pct']:.1f}% loss"
            )

            # Increment hedge counter
            if leg_type == "CE":
                self.ce_hedges_count += 1
            else:
                self.pe_hedges_count += 1

        elif action == 'HEDGE_CLOSE':
            self.excel_logger.log_hedge_exit(
                leg_type=leg_type,
                level=event['level'],
                exit_time=config.get_current_ist_time(),
                exit_premium=event['exit_premium'],
                exit_loss_pct=0,
                trail_activated=False,
                hedge_pnl=event['pnl'],
                exit_reason="Force Exit"
            )

            # Log leg action
            self.excel_logger.log_leg_action(
                leg_type=leg_type,
                action="BUY",
                time=config.get_current_ist_time(),
                strike=0,
                symbol=f"{leg_type} Hedge L{event['level']}",
                security_id="",
                premium=event['exit_premium'],
                quantity=config.LOT_SIZE,
                order_status="FILLED",
                notes=f"Hedge Exit | P&L: Rs.{event['pnl']:.2f}"
            )

    def check_premium_ratio_force_exit(self) -> bool:
        """Check if premium ratio <= 0.30"""
        # FIX: Added null checks
        if not self.ce_leg or not self.pe_leg:
            return False

        ce_premium = self.ce_leg.current_premium
        pe_premium = self.pe_leg.current_premium

        if ce_premium <= 0 or pe_premium <= 0:
            return False

        ratio = min(ce_premium, pe_premium) / max(ce_premium, pe_premium)

        if ratio <= config.FORCE_EXIT_RATIO:
            print(f"\n{'=' * 80}")
            print(f"PREMIUM RATIO EXIT TRIGGERED")
            print(f"{'=' * 80}")
            print(f"   Ratio: {ratio:.3f} <= {config.FORCE_EXIT_RATIO}")
            print(f"   CE: Rs.{ce_premium:.2f} | PE: Rs.{pe_premium:.2f}")
            print(f"{'=' * 80}")
            return True

        return False

    def exit_straddle(self, reason: str) -> Dict:
        """✅ FIX 4: Exit complete straddle with CRITICAL LOCK"""
        if not self.straddle_active:
            print("[WARN] No active straddle to exit")
            return None

        # FIX: Added comprehensive null checks
        if not self.ce_leg or not self.pe_leg:
            print("[ERROR] Legs not initialized properly")
            return None

        try:
            # 🔥 FIX 4: Acquire critical lock
            api.acquire_critical_lock(f"STRADDLE_EXIT_{self.strike}")
            
            print(f"\n{'=' * 80}")
            print(f"EXITING STRADDLE - {reason}")
            print(f"{'=' * 80}")

            exit_time = config.get_current_ist_time()

            # Calculate hedge P&L
            ce_hedge_pnl = getattr(self.ce_leg, 'realized_hedge_pnl', 0)
            pe_hedge_pnl = getattr(self.pe_leg, 'realized_hedge_pnl', 0)

            # Check for ACTIVE hedges
            if self.ce_leg.hedge_active:
                print(f"\n[FETCHING] Fetching active CE hedge price...")
                ce_hedge_premium = api.get_ltp_with_retry(
                    self.ce_leg.hedge_security_id, max_retries=3)

                if ce_hedge_premium and hasattr(self.ce_leg, 'hedge_entry_premium'):
                    self.ce_leg.hedge_current_premium = ce_hedge_premium
                    active_ce_hedge_pnl = (self.ce_leg.hedge_entry_premium - ce_hedge_premium) * config.LOT_SIZE
                    ce_hedge_pnl += active_ce_hedge_pnl
                    print(f"   [OK] Active CE hedge L{self.ce_leg.hedge_level}: Rs.{active_ce_hedge_pnl:,.2f}")

            if self.pe_leg.hedge_active:
                print(f"\n[FETCHING] Fetching active PE hedge price...")
                pe_hedge_premium = api.get_ltp_with_retry(
                    self.pe_leg.hedge_security_id, max_retries=3)

                if pe_hedge_premium and hasattr(self.pe_leg, 'hedge_entry_premium'):
                    self.pe_leg.hedge_current_premium = pe_hedge_premium
                    active_pe_hedge_pnl = (self.pe_leg.hedge_entry_premium - pe_hedge_premium) * config.LOT_SIZE
                    pe_hedge_pnl += active_pe_hedge_pnl
                    print(f"   [OK] Active PE hedge L{self.pe_leg.hedge_level}: Rs.{active_pe_hedge_pnl:,.2f}")

            # Get straddle leg P&L
            ce_breakdown = self.ce_leg.get_pnl_breakdown()
            pe_breakdown = self.pe_leg.get_pnl_breakdown()

            total_pnl = ce_breakdown['leg_pnl'] + pe_breakdown['leg_pnl'] + ce_hedge_pnl + pe_hedge_pnl

            print(f"\nP&L BREAKDOWN:")
            print(f"{'-' * 60}")
            print(f"   CE Straddle: Rs.{ce_breakdown['leg_pnl']:,.2f}")
            print(f"   PE Straddle: Rs.{pe_breakdown['leg_pnl']:,.2f}")
            print(f"   CE Hedges:   Rs.{ce_hedge_pnl:,.2f}")
            print(f"   PE Hedges:   Rs.{pe_hedge_pnl:,.2f}")
            print(f"{'-' * 60}")
            print(f"   TOTAL P&L: Rs.{total_pnl:,.2f}")
            print(f"{'-' * 60}")

            # Exit hedges
            self.hedge_manager.exit_all_hedges(self.ce_leg, self.pe_leg)

            # FIXED: Buy back straddle legs - CRITICAL ORDERS with ACTUAL FILL PRICE TRACKING
            print(f"\n[SQUARING OFF] Buying back straddle legs (with ACTUAL fill price tracking)...")

            try:
                # CE Leg - CRITICAL order with verification
                print(f"   [SQUARING OFF] Squaring off CE leg...")
                ce_result = api.place_order_with_verification(
                    transaction_type='BUY',
                    symbol=self.ce_leg.symbol,
                    security_id=self.ce_leg.security_id,
                    quantity=config.LOT_SIZE,
                    is_critical=True  # CRITICAL: 3 attempts with 20s pause
                )
                
                # 🔥 NEW: Get actual exit fill price
                if ce_result and ce_result.get('success') and ce_result.get('order_id'):
                    time.sleep(1)  # Wait for order book update
                    actual_ce_exit = api.get_order_fill_price(ce_result['order_id'])
                    if actual_ce_exit:
                        print(f"   💰 CE actual exit price: ₹{actual_ce_exit:.2f}")
                        self.ce_leg.current_premium = actual_ce_exit  # Update with actual
                
                if not ce_result or not ce_result.get('success'):
                    print(f"   [ERROR] CE leg exit FAILED - MANUAL INTERVENTION REQUIRED!")
                    # Still log the attempt
                    if self.excel_logger:
                        self.excel_logger.log_leg_action(
                            leg_type="CE", action="BUY", time=exit_time,
                            strike=self.strike, symbol=self.ce_leg.symbol,
                            security_id=self.ce_leg.security_id,
                            premium=self.ce_leg.current_premium,
                            quantity=config.LOT_SIZE,
                            order_status="FAILED", 
                            notes=f"Straddle Exit Failed: {ce_result.get('message', 'Unknown error') if ce_result else 'No response'}"
                        )
                else:
                    # Log successful CE leg exit
                    if self.excel_logger:
                        self.excel_logger.log_leg_action(
                            leg_type="CE", action="BUY", time=exit_time,
                            strike=self.strike, symbol=self.ce_leg.symbol,
                            security_id=self.ce_leg.security_id,
                            premium=self.ce_leg.current_premium,
                            quantity=config.LOT_SIZE,
                            order_status="FILLED" if ce_result.get('filled') else "PENDING",
                            notes="Straddle Exit"
                        )

                time.sleep(0.5)

                # PE Leg - CRITICAL order with verification
                print(f"   [SQUARING OFF] Squaring off PE leg...")
                pe_result = api.place_order_with_verification(
                    transaction_type='BUY',
                    symbol=self.pe_leg.symbol,
                    security_id=self.pe_leg.security_id,
                    quantity=config.LOT_SIZE,
                    is_critical=True  # CRITICAL: 3 attempts with 20s pause
                )
                
                # 🔥 NEW: Get actual exit fill price
                if pe_result and pe_result.get('success') and pe_result.get('order_id'):
                    time.sleep(1)  # Wait for order book update
                    actual_pe_exit = api.get_order_fill_price(pe_result['order_id'])
                    if actual_pe_exit:
                        print(f"   💰 PE actual exit price: ₹{actual_pe_exit:.2f}")
                        self.pe_leg.current_premium = actual_pe_exit  # Update with actual
                
                if not pe_result or not pe_result.get('success'):
                    print(f"   [ERROR] PE leg exit FAILED - MANUAL INTERVENTION REQUIRED!")
                    # Still log the attempt
                    if self.excel_logger:
                        self.excel_logger.log_leg_action(
                            leg_type="PE", action="BUY", time=exit_time,
                            strike=self.strike, symbol=self.pe_leg.symbol,
                            security_id=self.pe_leg.security_id,
                            premium=self.pe_leg.current_premium,
                            quantity=config.LOT_SIZE,
                            order_status="FAILED",
                            notes=f"Straddle Exit Failed: {pe_result.get('message', 'Unknown error') if pe_result else 'No response'}"
                        )
                else:
                    # Log successful PE leg exit
                    if self.excel_logger:
                        self.excel_logger.log_leg_action(
                            leg_type="PE", action="BUY", time=exit_time,
                            strike=self.strike, symbol=self.pe_leg.symbol,
                            security_id=self.pe_leg.security_id,
                            premium=self.pe_leg.current_premium,
                            quantity=config.LOT_SIZE,
                            order_status="FILLED" if pe_result.get('filled') else "PENDING",
                            notes="Straddle Exit"
                        )

            except Exception as e:
                print(f"\n{'=' * 60}")
                print(f"CRITICAL ERROR EXITING STRADDLE LEGS")
                print(f"{'=' * 60}")
                print(f"   Exception: {e}")
                print(f"   CE Symbol: {self.ce_leg.symbol}")
                print(f"   PE Symbol: {self.pe_leg.symbol}")
                print(f"   MANUAL INTERVENTION REQUIRED!")
                print(f"{'=' * 60}\n")
                import traceback
                traceback.print_exc()

            # LOG COMPLETE EXIT TO EXCEL
            if self.excel_logger:
                self.excel_logger.log_exit(
                    exit_time=exit_time,
                    ce_exit_premium=self.ce_leg.current_premium,
                    pe_exit_premium=self.pe_leg.current_premium,
                    ce_pnl=ce_breakdown['leg_pnl'],
                    pe_pnl=pe_breakdown['leg_pnl'],
                    ce_hedge_pnl=ce_hedge_pnl,
                    pe_hedge_pnl=pe_hedge_pnl,
                    total_pnl=total_pnl,
                    exit_reason=reason,
                    ce_hedges_used=self.ce_hedges_count,
                    pe_hedges_used=self.pe_hedges_count
                )

            # Reset state
            self.straddle_active = False
            self.ce_leg = None
            self.pe_leg = None
            self.strike = None
            self.entry_time = None

            print(f"\n[OK] STRADDLE EXITED SUCCESSFULLY")
            print(f"{'=' * 80}\n")

            return {
                'exit_time': exit_time,
                'total_pnl': total_pnl,
                'exit_reason': reason
            }
            
        except Exception as e:
            print(f"[ERROR] Straddle exit error: {e}")
            return None
        finally:
            # 🔥 FIX 4: Always release lock
            api.release_critical_lock(f"STRADDLE_EXIT_{self.strike}")

    def _find_straddle_in_chain(self, option_chain: Dict, strike: int,
                                option_type: str) -> Optional[Tuple[str, str]]:
        """Find option details in chain"""
        if strike in option_chain:
            strike_data = option_chain[strike]
            symbol = strike_data.get(f'{option_type}_symbol', '')
            security_id = strike_data.get(f'{option_type}_security_id', '')

            if symbol and security_id:
                return (symbol, security_id)

        return None

    def _get_premium_from_chain(self, option_chain: Dict, strike: int,
                                option_type: str) -> Optional[float]:
        """Get premium from option chain"""
        if strike in option_chain:
            premium = option_chain[strike].get(option_type, 0)
            if premium > 0:
                return premium

        return None

    def log_manual_hedge_changes(self, manual_changes: List[Dict]):
        """
        🔥 NEW: Log manual hedge changes to Excel
        
        Args:
            manual_changes: List of manual change dictionaries
        """
        if not self.excel_logger or not manual_changes:
            return
        
        for change in manual_changes:
            action = change['action']
            leg_type = change['leg_type']
            level = change['level']
            
            if action == 'MANUAL_HEDGE_ADDITION':
                self.excel_logger.log_manual_intervention(
                    intervention_type='MANUAL_ADD',
                    leg_type=leg_type,
                    level=level,
                    time=config.get_current_ist_time(),
                    notes=f"Manual hedge addition detected at {change['current_loss_pct']:.1f}% loss"
                )
                
                # Increment hedge counter
                if leg_type == "CE":
                    self.ce_hedges_count += 1
                else:
                    self.pe_hedges_count += 1
                    
            elif action == 'MANUAL_HEDGE_EXIT':
                self.excel_logger.log_manual_intervention(
                    intervention_type='MANUAL_EXIT',
                    leg_type=leg_type,
                    level=level,
                    time=config.get_current_ist_time(),
                    notes=f"Manual hedge exit detected"
                )