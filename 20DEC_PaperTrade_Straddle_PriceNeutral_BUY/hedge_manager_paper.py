"""

Paper Trading Hedge Manager - BUY HEDGE ON LOSING SIDE

🔥 MODIFIED: Changed from SELL to BUY hedge strategy
🔥 MODIFIED: Hedge placed on LOSING side (same option type)
🔥 MODIFIED: Strike selection finds OTM on losing side
🔥 MODIFIED: Order types changed (BUY to enter, SELL to exit)

"""

from paper_positions import paper_positions
from market_data_api import market_data
from config_paper import config
from simple_logger import logger

class HedgeManager:
    def __init__(self):
        self.hedge_events = []

    def process_leg_hedging(self, losing_leg, profit_leg, option_chain):
        """✅ MODIFIED: Process hedging with BUY strategy - WITH ENTRY AND EXIT LOGIC"""

        # ✅ 1. FIRST CHECK FOR HEDGE EXITS (REVERSALS)
        exit_event = self._check_hedge_exit(losing_leg, option_chain)
        if exit_event:
            return exit_event

        # ✅ 2. CHECK FOR NEW HEDGE ENTRIES
        if not losing_leg.should_buy_hedge():
            return None

        level = losing_leg.get_next_level()
        if not level or level > 2:
            return None

        # ✅ 3. CALCULATE OTM STRIKE ON LOSING SIDE
        hedge_strike, target_premium = self._calculate_hedge_strike_losing_side(
            losing_leg, profit_leg, option_chain
        )

        # ✅ 4. FIND HEDGE IN CHAIN (SAME TYPE AS LOSING LEG)
        hedge_data = self._find_hedge_in_chain(option_chain, hedge_strike, losing_leg.option_type)

        if not hedge_data:
            print(f"❌ No hedge found at strike {hedge_strike}")
            return None

        hedge_symbol, hedge_security_id, hedge_premium = hedge_data

        # ✅ 5. EXECUTE PAPER ORDER (BUY HEDGE)
        print(f"\n[PAPER] BUYING HEDGE L{level}: {hedge_symbol} @ ₹{hedge_premium:.2f}")
        paper_positions.execute_order('BUY', hedge_symbol, hedge_security_id, 
                                     config.LOT_SIZE, hedge_premium)

        # ✅ 6. UPDATE LEG STATE
        event = losing_leg.buy_hedge(hedge_symbol, hedge_security_id, 
                                     hedge_strike, hedge_premium, level)
        self.hedge_events.append(event)

        # ✅ LOG HEDGE ENTRY
        logger.log_hedge(losing_leg.name, level, hedge_strike, hedge_premium, 'ENTRY')

        return event

    def _check_hedge_exit(self, losing_leg, option_chain):
        """✅ CHECK IF HEDGE SHOULD EXIT DUE TO REVERSAL"""
        if not losing_leg.hedge_active:
            return None

        # Get current hedge premium (SAME type as losing leg)
        hedge_data = self._find_hedge_in_chain(
            option_chain,
            losing_leg.hedge_strike,
            losing_leg.option_type  # ✅ SAME type, not opposite
        )

        if not hedge_data:
            print(f"⚠️  Could not find hedge data for strike {losing_leg.hedge_strike}")
            return None

        _, _, current_hedge_premium = hedge_data
        losing_leg.update_hedge_premium(current_hedge_premium)

        # ✅ CALCULATE REVERSAL EXIT THRESHOLD
        exit_threshold = None
        current_loss = losing_leg.current_loss_pct

        if losing_leg.hedge_level == 1:
            # L1 entered at 20%, exit at 10% (10% retrace)
            entry_pct = config.PROGRESSIVE_HEDGING_LEVELS[0]  # 20%
            exit_threshold = entry_pct - config.HEDGE_REVERSAL_EXIT_PCT  # 10%

            if current_loss <= exit_threshold:
                print(f"\n🔄 L1 REVERSAL EXIT TRIGGERED")
                print(f"   Entry: {entry_pct:.0f}%, Exit: {exit_threshold:.0f}%, Current: {current_loss:.1f}%")

        elif losing_leg.hedge_level == 2:
            # L2 entered at 40%, exit at 30% (10% retrace)
            entry_pct = config.PROGRESSIVE_HEDGING_LEVELS[1]  # 40%
            exit_threshold = entry_pct - config.HEDGE_REVERSAL_EXIT_PCT  # 30%

            if current_loss <= exit_threshold:
                print(f"\n🔄 L2 REVERSAL EXIT TRIGGERED")
                print(f"   Entry: {entry_pct:.0f}%, Exit: {exit_threshold:.0f}%, Current: {current_loss:.1f}%")

        # ✅ EXECUTE HEDGE EXIT (SELL THE BOUGHT HEDGE)
        if exit_threshold is not None and current_loss <= exit_threshold:
            print(f"[PAPER] SELLING HEDGE: {losing_leg.hedge_symbol} @ ₹{current_hedge_premium:.2f}")
            paper_positions.execute_order('SELL', losing_leg.hedge_symbol,
                                        losing_leg.hedge_security_id,
                                        config.LOT_SIZE, current_hedge_premium)

            exit_event = losing_leg.close_hedge(current_hedge_premium)
            self.hedge_events.append(exit_event)

            # ✅ LOG HEDGE EXIT
            logger.log_hedge(losing_leg.name, exit_event['level'],
                           losing_leg.hedge_strike, current_hedge_premium, 'EXIT')

            return exit_event

        return None

    def _calculate_hedge_strike_losing_side(self, losing_leg, profit_leg, option_chain):
        """
        ✅ MODIFIED: Find OTM strike on LOSING side

        Logic:
        1. Calculate difference between both legs
        2. Find OTM strike on LOSING side with premium ≈ difference
        3. Direction constraint:
           - CE losing (market UP) → BUY OTM CE (above current strike)
           - PE losing (market DOWN) → BUY OTM PE (below current strike)
        """

        # Step 1: Calculate difference
        losing_total = losing_leg.get_total_side_premium(include_hedge=False)
        profit_total = profit_leg.get_total_side_premium(include_hedge=False)
        target_premium = abs(losing_total - profit_total)

        print(f"\n🎯 HEDGE CALCULATION (BUY ON LOSING SIDE):")
        print(f"   {losing_leg.option_type} (losing): ₹{losing_total:.2f}")
        print(f"   {profit_leg.option_type} (profit): ₹{profit_total:.2f}")
        print(f"   → Need to BUY {losing_leg.option_type} @ ₹{target_premium:.2f}")

        # Step 2: Get current spot and straddle strike
        spot = market_data.get_spot_price()
        if not spot:
            spot = losing_leg.strike

        straddle_strike = losing_leg.strike
        losing_type = losing_leg.option_type

        # Step 3: CRITICAL - Directional constraint for OTM on LOSING side
        if losing_leg.option_type == 'CE':
            # CE losing → market moved UP
            # Buy OTM CE (above current strike, cheaper premium)
            valid_strikes = [s for s in option_chain.keys() 
                           if s > straddle_strike]
            direction = "ABOVE strike (OTM CE)"
        else:
            # PE losing → market moved DOWN
            # Buy OTM PE (below current strike, cheaper premium)
            valid_strikes = [s for s in option_chain.keys() 
                           if s < straddle_strike]
            direction = "BELOW strike (OTM PE)"

        print(f"   Market Direction: {losing_leg.option_type} losing → {'UP' if losing_leg.option_type == 'CE' else 'DOWN'}")
        print(f"   Hedge Direction: {direction}")

        if not valid_strikes:
            print(f"   ⚠️  WARNING: No valid OTM strikes found!")
            # Emergency fallback
            valid_strikes = [s for s in option_chain.keys() if s != straddle_strike]

        # Step 4: Find best premium match within valid strikes
        best_strike = None
        best_diff = float('inf')
        best_premium = 0

        for strike in valid_strikes:
            data = option_chain.get(strike, {})
            premium = data.get(losing_type, 0)

            if premium > 0:
                diff = abs(premium - target_premium)
                if diff < best_diff:
                    best_diff = diff
                    best_strike = strike
                    best_premium = premium

        # Final fallback
        if not best_strike:
            print(f"   🚨 EMERGENCY: No hedge found in valid range!")
            best_strike = valid_strikes[0] if valid_strikes else straddle_strike + config.STRIKE_INTERVAL
            data = option_chain.get(best_strike, {})
            best_premium = data.get(losing_type, target_premium)

        print(f"   ✅ Selected: {best_strike} {losing_type} @ ₹{best_premium:.2f}")
        print(f"   Target Premium: ₹{target_premium:.2f}")
        print(f"   Difference: ₹{abs(best_premium - target_premium):.2f}")

        return best_strike, target_premium

    def _find_hedge_in_chain(self, option_chain, strike, losing_leg_type):
        """
        ✅ MODIFIED: Find hedge in chain - SAME type as losing leg

        Args:
            losing_leg_type: 'CE' or 'PE' (the LOSING leg type)

        Returns:
            (symbol, security_id, premium) for SAME type
        """
        # ✅ MODIFIED: Get SAME option type (CE if CE losing, PE if PE losing)
        same_type = losing_leg_type

        data = option_chain.get(strike)
        if not data:
            return None

        premium = data.get(same_type)
        symbol = data.get(f'{same_type}_symbol')
        security_id = data.get(f'{same_type}_security_id')

        if premium and symbol and security_id:
            return (symbol, security_id, premium)

        return None

hedge_manager = HedgeManager()
