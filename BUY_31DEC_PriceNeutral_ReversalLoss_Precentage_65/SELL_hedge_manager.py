"""
Hedge Manager - CORRECTED PRICE-NEUTRAL HEDGING WITH DIRECTIONAL CONSTRAINT
WITH REVERSAL EXIT LOGIC + UPGRADE LOGIC
Exit L1 at 0%, Exit L2 at 20%
🔥 FIXED: True price-neutral calculation with DIRECTIONAL strike selection
🔥 FIXED: Added upgrade logic (L1→L2 when loss reaches 40%)
🔥 FIXED: Added reversal exit logic for L1 and L2 hedges
🔥 FIXED: Added critical operation locks
🔥 FIXED: L1 reloading bug during L1→L2 upgrade
🔥 ADDED: HEDGE_REVERSAL_EXIT_PCT parameter for adjustable hedge exit thresholds
"""

from typing import Optional, Dict
from leg import Leg
from angelone_api import api
from config import config
import time


class HedgeManager:
    """Manages hedge selling and holding logic WITH REVERSAL EXITS + UPGRADES"""

    def __init__(self):
        """Initialize hedge manager"""
        self.hedge_events = []

    def process_leg_hedging(self, losing_leg: Leg, profit_leg: Leg, option_chain: Dict) -> Optional[Dict]:
        """
        ✅ FIXED: Process hedging with UPGRADE + REVERSAL EXIT logic
        
        Args:
            losing_leg: The leg that's losing (CE or PE)
            profit_leg: The opposing leg that's in profit  
            option_chain: Current option chain data
        """
        
        loss_pct = losing_leg.current_loss_pct
        
        # ✅ CRITICAL FIX: REMOVED all auto-reload logic from here
        # Auto-reloading is ONLY handled by leg.close_hedge() during reversal exits
        # This prevents state corruption during upgrades
        
        if losing_leg.hedge_active:
            # ✅ CASE 1: Hedge is ACTIVE - Check for UPGRADE or EXIT
            should_upgrade = False
            target_level = None
            
            # ✅ CHECK FOR UPGRADE CONDITIONS
            if losing_leg.hedge_level == 1 and loss_pct >= config.PROGRESSIVE_HEDGING_LEVELS[1]:  # 40%
                # L1 active, but loss reached L2 threshold (40%)
                should_upgrade = True
                target_level = 2
                print(f"⚠️ [{losing_leg.name}] L1 → L2 UPGRADE TRIGGERED!")
                print(f"   Current Loss: {loss_pct:.1f}% | L2 Trigger: {config.PROGRESSIVE_HEDGING_LEVELS[1]:.0f}%")
                
            elif losing_leg.hedge_level == 2 and loss_pct >= config.LEVEL_3_HARD_STOP:  # 60%
                # L2 active, but loss reached L3 threshold (60%) - Complete exit handled by main script
                should_upgrade = False  # L3 is complete exit, not upgrade
                print(f"🚨 [{losing_leg.name}] LEVEL 3 TRIGGER - Complete exit required!")
            
            # ✅ EXECUTE UPGRADE
            if should_upgrade and target_level:
                print(f"📊 Squaring off L{losing_leg.hedge_level} hedge...")
                
                # Calculate P&L before closing
                current_hedge_pnl = 0
                if losing_leg.hedge_entry_premium and losing_leg.hedge_current_premium:
                    current_hedge_pnl = (losing_leg.hedge_entry_premium - losing_leg.hedge_current_premium) * losing_leg.lot_size
                    losing_leg.realized_hedge_pnl += current_hedge_pnl
                    print(f"   L{losing_leg.hedge_level} Hedge P&L: ₹{current_hedge_pnl:,.0f}")
                
                # Get current hedge premium for exit
                exit_premium = api.get_ltp_with_retry(losing_leg.hedge_security_id)
                if not exit_premium:
                    print(f"⚠️  Using last known premium for exit")
                    exit_premium = losing_leg.hedge_current_premium
                
                # Place exit order (BUY back the sold hedge)
                success, order_id = self._place_hedge_exit_order(
                    losing_leg.hedge_symbol,
                    losing_leg.hedge_security_id,
                    losing_leg.lot_size
                )
                
                if success:
                    print(f"✅ L{losing_leg.hedge_level} squared off successfully")
                    
                    # Get actual fill price if available
                    if order_id:
                        time.sleep(1)
                        actual_exit_price = api.get_order_fill_price(order_id)
                        if actual_exit_price:
                            exit_premium = actual_exit_price
                            print(f"   💰 Actual exit price: ₹{actual_exit_price:.2f}")
                    
                    # Save old level for logging
                    old_level = losing_leg.hedge_level
                    
                    # Reset hedge state (but preserve loading states)
                    losing_leg.hedge_active = False
                    losing_leg.hedge_symbol = None
                    losing_leg.hedge_security_id = None
                    losing_leg.hedge_strike = None
                    losing_leg.hedge_entry_premium = None
                    losing_leg.hedge_current_premium = None
                    
                    # ✅ CRITICAL FIX: Do NOT modify loading states here
                    # The sell_hedge() method will correctly unload the new level
                    # Previous levels remain in their current state (L1 stays UNLOADED)
                    
                    print(f"🎯 Entering L{target_level} hedge immediately...")
                    print(f"   Loading State: L1={'LOADED' if losing_leg.l1_loaded else 'UNLOADED'}, L2={'LOADED' if losing_leg.l2_loaded else 'UNLOADED'}")
                    
                    # ✅ IMMEDIATELY ENTER NEW LEVEL
                    # Force the new level entry by calling _check_hedge_entry with force_level parameter
                    # sell_hedge() will handle unloading L2 when it's entered
                    return self._check_hedge_entry(losing_leg, profit_leg, option_chain, force_level=target_level)
                else:
                    print(f"❌ Failed to square off L{losing_leg.hedge_level} - Cannot upgrade!")
                    return None
            else:
                # ✅ No upgrade needed, check for reversal exit
                return self._check_hedge_exit(losing_leg)
        else:
            # ✅ CASE 2: No hedge active - Check for entry trigger
            return self._check_hedge_entry(losing_leg, profit_leg, option_chain)

    def _check_hedge_exit(self, losing_leg: Leg) -> Optional[Dict]:
        """Check if active hedge should be exited due to reversal"""
        
        # Get the hedge level
        if not hasattr(losing_leg, 'hedge_level') or losing_leg.hedge_level is None:
            return None  # No hedge level info, hold hedge
        
        exit_triggered = False
        exit_threshold = None
        
        if losing_leg.hedge_level == 1:
            # L1 hedge: Exit when loss retraces by configured % from L1 entry
            exit_threshold = config.PROGRESSIVE_HEDGING_LEVELS[0] - config.HEDGE_REVERSAL_EXIT_PCT
            if losing_leg.current_loss_pct <= exit_threshold:
                print(f"\n🔄 L1 REVERSAL EXIT - {losing_leg.name} retraced to {losing_leg.current_loss_pct:.1f}%")
                print(f"   (Entry: {config.PROGRESSIVE_HEDGING_LEVELS[0]:.0f}%, Exit: {exit_threshold:.0f}%, Retrace: {config.HEDGE_REVERSAL_EXIT_PCT:.0f}%)")
                exit_triggered = True
        
        elif losing_leg.hedge_level == 2:
            # L2 hedge: Exit when loss retraces by configured % from L2 entry
            exit_threshold = config.PROGRESSIVE_HEDGING_LEVELS[1] - config.HEDGE_REVERSAL_EXIT_PCT
            if losing_leg.current_loss_pct <= exit_threshold:
                print(f"\n🔄 L2 REVERSAL EXIT - {losing_leg.name} retraced to {losing_leg.current_loss_pct:.1f}%")
                print(f"   (Entry: {config.PROGRESSIVE_HEDGING_LEVELS[1]:.0f}%, Exit: {exit_threshold:.0f}%, Retrace: {config.HEDGE_REVERSAL_EXIT_PCT:.0f}%)")
                exit_triggered = True
        
        if exit_triggered:
            # Close the hedge
            exit_premium = api.get_ltp_with_retry(losing_leg.hedge_security_id)
            if not exit_premium:
                print(f"⚠️ Using last known premium for reversal exit")
                exit_premium = losing_leg.hedge_current_premium
            
            success, order_id = self._place_hedge_exit_order(
                losing_leg.hedge_symbol,
                losing_leg.hedge_security_id,
                losing_leg.lot_size
            )
            
            if success:
                # Get actual exit price if available
                if order_id:
                    time.sleep(1)
                    actual_exit_price = api.get_order_fill_price(order_id)
                    if actual_exit_price:
                        exit_premium = actual_exit_price
                        print(f"   💰 Hedge actual exit: ₹{actual_exit_price:.2f}")
                
                # Close hedge in leg
                losing_leg.close_hedge(exit_premium)
                
                # Record exit event
                exit_event = {
                    'type': 'HEDGE_EXIT',
                    'leg': losing_leg.name,
                    'level': losing_leg.hedge_level,
                    'exit_premium': exit_premium,
                    'current_loss_pct': losing_leg.current_loss_pct,
                    'timestamp': time.time()
                }
                self.hedge_events.append(exit_event)
                return exit_event
        
        # No exit triggered, hold hedge
        return None

    def _check_hedge_entry(self, losing_leg: Leg, profit_leg: Leg, option_chain: Dict, force_level: int = None) -> Optional[Dict]:
        """✅ Check if hedge should be entered (supports forced level for upgrades)"""
        
        # ✅ Determine which level to enter
        if force_level is not None:
            # Forced level (used for upgrades)
            level = force_level
            print(f"   Using FORCED level: L{level} (upgrade path)")
        else:
            # Normal entry - check if should sell hedge
            if not losing_leg.should_sell_hedge():
                return None
            
            # Determine level from next stop loss
            level = self._get_level_from_stop_loss(losing_leg.next_stop_loss_pct)
        
        # 🔥 HYBRID: Level check for 2-level system
        if level > 2:
            print(f"\n🚨 {losing_leg.name} at Level 3 ({losing_leg.current_loss_pct:.1f}%) - NO HEDGE AVAILABLE!")
            return None

        # 🔥 FIXED: Calculate ACTUAL premium difference with DIRECTIONAL constraint
        hedge_strike, target_premium = self._calculate_price_neutral_strike(
            losing_leg, profit_leg, option_chain, level
        )
        
        hedge_symbol, hedge_security_id, hedge_premium = self._get_hedge_from_chain(
            option_chain, hedge_strike, losing_leg.option_type
        )

        if not hedge_symbol:
            print(f"❌ Could not find hedge in option chain for {losing_leg.name}")
            return None

        success, order_id = self._place_hedge_order(hedge_symbol, hedge_security_id, losing_leg.lot_size)

        if not success:
            print(f"❌ Failed to place hedge order for {losing_leg.name}")
            return None

        # 🔥 NEW: Get actual hedge fill price
        if order_id:
            time.sleep(1)
            actual_hedge_price = api.get_order_fill_price(order_id)
            if actual_hedge_price:
                hedge_premium = actual_hedge_price
                print(f"   💰 Hedge actual fill: ₹{actual_hedge_price:.2f}")

        # ✅ ADDED: Subscribe hedge to WebSocket using market_ws
        if api.ws_enabled and api.market_ws:
            try:
                token_list = [{
                    "exchangeType": 2,  # NSE_FO
                    "tokens": [hedge_security_id]
                }]
                
                api.market_ws.subscribe(
                    correlation_id=f"hedge_{int(time.time())}",
                    mode=1,  # LTP mode
                    token_list=token_list
                )
                print(f"✅ Subscribed hedge to WebSocket: {hedge_symbol}")
            except Exception as e:
                print(f"⚠️ Hedge WebSocket subscription failed: {e}")

        event = losing_leg.sell_hedge(hedge_symbol, hedge_security_id, hedge_strike, hedge_premium, level)
        self.hedge_events.append(event)

        return event

    def _calculate_price_neutral_strike(self, losing_leg: Leg, profit_leg: Leg, 
                                       option_chain: Dict, level: int) -> tuple:
        """
        🔥 FIXED: TRUE PRICE-NEUTRAL with DIRECTIONAL CONSTRAINT
        
        Logic:
        1. Calculate actual difference: |losing_side_total - profit_side_total|
        2. Find opposing strike with premium ≈ difference
        3. CRITICAL: Direction constraint based on market move
           - CE losing (market UP) → SELL ITM PE (below spot, gaining value)
           - PE losing (market DOWN) → SELL ITM CE (above spot, gaining value)
        
        Returns:
            (hedge_strike, target_premium)
        """
        # Step 1: Calculate ACTUAL difference
        losing_total = losing_leg.get_total_side_premium(include_hedge=False)
        profit_total = profit_leg.get_total_side_premium(include_hedge=False)  # ✅ FIXED: Changed from True to False
        
        target_premium = abs(losing_total - profit_total)
        
        print(f"\n🎯 PRICE-NEUTRAL CALCULATION:")
        print(f"   {losing_leg.option_type} (losing): ₹{losing_total:.2f}")
        print(f"   {profit_leg.option_type} (profit): ₹{profit_total:.2f}")
        print(f"   → Need to SELL {profit_leg.option_type} @ ₹{target_premium:.2f}")
        
        # Step 2: Get current spot and straddle strike
        spot = api.get_spot_price()
        if not spot:
            spot = losing_leg.strike  # Fallback to straddle strike
        
        straddle_strike = losing_leg.strike
        opposing_type = profit_leg.option_type
        
        # Step 3: CRITICAL - Directional constraint
        if losing_leg.option_type == 'CE':
            # CE losing → market moved UP
            # Sell ITM PE (below spot, on profit side)
            # Higher premium PE = lower strike (more ITM)
            valid_strikes = [s for s in option_chain.keys() 
                            if s <= spot and s != straddle_strike]
            direction = "BELOW spot (ITM PE)"
        else:
            # PE losing → market moved DOWN  
            # Sell ITM CE (above spot, on profit side)
            # Higher premium CE = higher strike (more ITM)
            valid_strikes = [s for s in option_chain.keys() 
                            if s >= spot and s != straddle_strike]
            direction = "ABOVE spot (ITM CE)"
        
        print(f"   Market Direction: {losing_leg.option_type} losing → {'UP' if losing_leg.option_type == 'CE' else 'DOWN'}")
        print(f"   Hedge Direction: {direction}")
        print(f"   Valid strikes: {len(valid_strikes)} from {len(option_chain)} total")
        
        if not valid_strikes:
            print(f"   ⚠️ WARNING: No valid directional strikes found!")
            print(f"   Spot: {spot:.2f}, Straddle: {straddle_strike}")
            # Emergency fallback - use all strikes except straddle
            valid_strikes = [s for s in option_chain.keys() if s != straddle_strike]
        
        # Step 4: Find best premium match within valid strikes
        best_strike = None
        best_premium_diff = float('inf')
        best_premium = 0
        
        for strike in valid_strikes:
            strike_data = option_chain.get(strike, {})
            premium = strike_data.get(opposing_type, 0)
            
            if premium > 0:
                diff = abs(premium - target_premium)
                if diff < best_premium_diff:
                    best_premium_diff = diff
                    best_strike = strike
                    best_premium = premium
        
        # Final fallback if no valid strike found
        if not best_strike:
            print(f"   🚨 EMERGENCY: No hedge found in valid range!")
            print(f"   Falling back to closest premium match (ignoring direction)")
            best_strike = min(
                [s for s in option_chain.keys() if s != straddle_strike],
                key=lambda s: abs(option_chain.get(s, {}).get(opposing_type, 0) - target_premium),
                default=straddle_strike
            )
            best_premium = option_chain.get(best_strike, {}).get(opposing_type, 0)
        
        print(f"   ✅ Selected: {best_strike} {opposing_type} @ ₹{best_premium:.2f}")
        print(f"   Target Premium: ₹{target_premium:.2f}")
        print(f"   Difference: ₹{abs(best_premium - target_premium):.2f}")
        
        return best_strike, target_premium

    def _get_hedge_from_chain(self, option_chain: Dict, hedge_strike: int,
                              losing_leg_type: str) -> tuple:
        """
        🔥 FIXED: Get hedge details - OPPOSING type from losing leg
        
        Args:
            losing_leg_type: 'CE' or 'PE' (the LOSING leg type)
        
        Returns:
            (symbol, security_id, premium) for OPPOSING type
        """
        try:
            chain_data = option_chain.get(hedge_strike, {})

            if not chain_data:
                print(f"   ❌ No data found for hedge strike {hedge_strike}")
                return None, None, None

            # Get OPPOSING option type (PE if CE losing, CE if PE losing)
            opposing_type = 'PE' if losing_leg_type == 'CE' else 'CE'
            
            premium = chain_data.get(opposing_type)
            symbol_key = f'{opposing_type}_symbol'
            security_id_key = f'{opposing_type}_security_id'

            symbol = chain_data.get(symbol_key)
            security_id = chain_data.get(security_id_key)

            if premium and symbol and security_id:
                print(f"   ✅ Found hedge: {symbol} @ ₹{premium:.2f}")
                return symbol, security_id, premium
            else:
                print(f"   ❌ Hedge data incomplete for {opposing_type} {hedge_strike}")
                print(f"      Premium: {premium}, Symbol: {symbol}, Security ID: {security_id}")
                return None, None, None

        except Exception as e:
            print(f"❌ Error finding hedge in chain: {str(e)}")
            return None, None, None

    def _place_hedge_order(self, symbol: str, security_id: str, quantity: int) -> tuple:
        """🔥 FIXED: Place hedge SELL order with CRITICAL flag + LOCK"""
        try:
            # 🔥 FIX 4: Acquire critical lock
            api.acquire_critical_lock(f"HEDGE_ENTRY_{symbol}")
            
            print(f"   📤 Placing SELL order: {symbol}")

            response = api.place_order_with_verification(
                transaction_type='SELL',
                symbol=symbol,
                security_id=security_id,
                quantity=quantity,
                is_critical=True,  # CRITICAL - 3 attempts + handles token recovery
                max_wait=60  # LONGER TIMEOUT - handles volatile markets
            )

            # ✅ Check 'success' first, then 'filled'
            if not response or not response.get('success'):
                error_msg = response.get('message', 'Unknown error') if response else 'No response'
                print(f"   ❌ Hedge order placement failed: {error_msg}")
                return False, None

            if response.get('filled'):
                print(f"   ✅ Hedge order filled")
                api.invalidate_position_cache()
                return True, response.get('order_id')
            else:
                print(f"   ❌ Hedge order not filled after all retries")
                return False, response.get('order_id')

        except Exception as e:
            print(f"   ❌ Error placing hedge order: {str(e)}")
            return False, None
        finally:
            # 🔥 FIX 4: Always release lock
            api.release_critical_lock(f"HEDGE_ENTRY_{symbol}")

    def _place_hedge_exit_order(self, symbol: str, security_id: str, quantity: int) -> tuple:
        """🔥 FIXED: Place hedge BUY order with CRITICAL flag + LOCK"""
        try:
            # 🔥 FIX 4: Acquire critical lock
            api.acquire_critical_lock(f"HEDGE_EXIT_{symbol}")
            
            print(f"   📤 Placing BUY order: {symbol}")

            response = api.place_order_with_verification(
                transaction_type='BUY',
                symbol=symbol,
                security_id=security_id,
                quantity=quantity,
                is_critical=True,  # CRITICAL - 3 attempts + handles token recovery
                max_wait=60  # LONGER TIMEOUT - handles volatile markets
            )

            # ✅ Check 'success' first, then 'filled'
            if not response or not response.get('success'):
                error_msg = response.get('message', 'Unknown error') if response else 'No response'
                print(f"   ❌ Hedge exit order placement failed: {error_msg}")
                return False, None

            if response.get('filled'):
                print(f"   ✅ Hedge exit order filled")
                api.invalidate_position_cache()
                return True, response.get('order_id')
            else:
                print(f"   ❌ Hedge exit order not filled after all retries")
                return False, response.get('order_id')

        except Exception as e:
            print(f"   ❌ Error exiting hedge: {str(e)}")
            return False, None
        finally:
            # 🔥 FIX 4: Always release lock
            api.release_critical_lock(f"HEDGE_EXIT_{symbol}")

    def _get_level_from_stop_loss(self, stop_loss_pct: float) -> int:
        """Get level number from stop loss percentage - HYBRID: Updated for 3 levels"""
        # 🔥 HYBRID: 2 levels + Level 3 hard stop
        levels = config.PROGRESSIVE_HEDGING_LEVELS + [config.LEVEL_3_HARD_STOP]
        for i, level_pct in enumerate(levels, 1):
            if abs(stop_loss_pct - level_pct) < 0.1:
                return i
        return 3  # Level 3 hard stop

    def check_level_3_trigger(self, leg: Leg) -> bool:
        """Check if Level 3 hard stop triggered - HYBRID: Level 3"""
        if leg and leg.is_level_3_triggered():
            print(f"\n🚨 LEVEL 3 TRIGGERED - {leg.name}")
            print(f"   Loss: {leg.current_loss_pct:.1f}%")
            print(f"   Hedge Active: {leg.hedge_active}")
            return True
        return False

    def exit_all_hedges(self, ce_leg: Leg, pe_leg: Leg):
        """Exit all active hedges (for force exits)"""
        if ce_leg and ce_leg.hedge_active:
            print(f"\n🚪 Force exiting CE hedge...")
            self._force_close_hedge(ce_leg)

        if pe_leg and pe_leg.hedge_active:
            print(f"\n🚪 Force exiting PE hedge...")
            self._force_close_hedge(pe_leg)

    def _force_close_hedge(self, leg: Leg):
        """Force close a hedge"""
        exit_premium = api.get_ltp_with_retry(leg.hedge_security_id)
        if not exit_premium:
            print(f"⚠️ Using last known premium for force exit")
            exit_premium = leg.hedge_current_premium

        success, order_id = self._place_hedge_exit_order(leg.hedge_symbol, leg.hedge_security_id, leg.lot_size)

        if success:
            # Get actual exit price if available
            if order_id:
                time.sleep(1)
                actual_exit_price = api.get_order_fill_price(order_id)
                if actual_exit_price:
                    exit_premium = actual_exit_price
                    print(f"   💰 Hedge actual exit: ₹{actual_exit_price:.2f}")

            leg.close_hedge(exit_premium)
        else:
            print(f"❌ Failed to force exit hedge for {leg.name}")

    def reset_for_new_session(self):
        """Reset for new session"""
        self.hedge_events = []