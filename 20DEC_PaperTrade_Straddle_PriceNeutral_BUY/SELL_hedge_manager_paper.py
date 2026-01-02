"""
Paper Trading Hedge Manager - COMPLETE WITH EXIT LOGIC
"""

from paper_positions import paper_positions
from market_data_api import market_data
from config_paper import config
from simple_logger import logger  # ✅ ADD LOGGING


class HedgeManager:
    def __init__(self):
        self.hedge_events = []

    def process_leg_hedging(self, losing_leg, profit_leg, option_chain):
        """Process hedging - WITH ENTRY AND EXIT LOGIC"""

        # ✅ 1. FIRST CHECK FOR HEDGE EXITS (REVERSALS)
        exit_event = self._check_hedge_exit(losing_leg, option_chain)
        if exit_event:
            return exit_event

        # ✅ 2. CHECK FOR NEW HEDGE ENTRIES
        if not losing_leg.should_sell_hedge():
            return None

        level = losing_leg.get_next_level()
        if not level or level > 2:
            return None

        # ✅ 3. CALCULATE PRICE-NEUTRAL STRIKE
        hedge_strike, target_premium = self._calculate_price_neutral_strike(
            losing_leg, profit_leg, option_chain
        )

        # ✅ 4. FIND HEDGE IN CHAIN
        hedge_data = self._find_hedge_in_chain(option_chain, hedge_strike, losing_leg.option_type)
        if not hedge_data:
            print(f"❌ No hedge found at strike {hedge_strike}")
            return None

        hedge_symbol, hedge_security_id, hedge_premium = hedge_data

        # ✅ 5. EXECUTE PAPER ORDER
        print(f"\n[PAPER] SELLING HEDGE L{level}: {hedge_symbol} @ ₹{hedge_premium:.2f}")

        paper_positions.execute_order('SELL', hedge_symbol, hedge_security_id, config.LOT_SIZE, hedge_premium)

        # ✅ 6. UPDATE LEG STATE
        event = losing_leg.sell_hedge(hedge_symbol, hedge_security_id, hedge_strike, hedge_premium, level)
        self.hedge_events.append(event)

        # ✅ LOG HEDGE ENTRY
        logger.log_hedge(losing_leg.name, level, hedge_strike, hedge_premium, 'ENTRY')

        return event

    def _check_hedge_exit(self, losing_leg, option_chain):
        """✅ CHECK IF HEDGE SHOULD EXIT DUE TO REVERSAL"""
        if not losing_leg.hedge_active:
            return None

        # Get current hedge premium
        hedge_data = self._find_hedge_in_chain(
            option_chain,
            losing_leg.hedge_strike,
            losing_leg.option_type
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

        # ✅ EXECUTE HEDGE EXIT
        if exit_threshold is not None and current_loss <= exit_threshold:
            print(f"[PAPER] BUYING BACK HEDGE: {losing_leg.hedge_symbol} @ ₹{current_hedge_premium:.2f}")

            paper_positions.execute_order('BUY', losing_leg.hedge_symbol,
                                          losing_leg.hedge_security_id,
                                          config.LOT_SIZE, current_hedge_premium)

            exit_event = losing_leg.close_hedge(current_hedge_premium)
            self.hedge_events.append(exit_event)

            # ✅ LOG HEDGE EXIT
            logger.log_hedge(losing_leg.name, exit_event['level'],
                             losing_leg.hedge_strike, current_hedge_premium, 'EXIT')

            return exit_event

        return None

    def _calculate_price_neutral_strike(self, losing_leg, profit_leg, option_chain):
        """Calculate price-neutral strike"""
        losing_total = losing_leg.get_total_side_premium(include_hedge=False)
        profit_total = profit_leg.get_total_side_premium(include_hedge=False)
        target_premium = abs(losing_total - profit_total)

        spot = market_data.get_spot_price()
        if not spot:
            spot = losing_leg.strike

        opposing_type = profit_leg.option_type

        # Directional constraint
        if losing_leg.option_type == 'CE':
            # For CE leg losing, hedge with OTM PE (strike < spot)
            valid_strikes = [s for s in option_chain.keys() if s < spot]
        else:
            # For PE leg losing, hedge with OTM CE (strike > spot)
            valid_strikes = [s for s in option_chain.keys() if s > spot]

        if not valid_strikes:
            valid_strikes = list(option_chain.keys())

        # Find best match to target premium
        best_strike = None
        best_diff = float('inf')
        best_premium = 0

        for strike in valid_strikes:
            data = option_chain.get(strike, {})
            premium = data.get(opposing_type, 0)

            if premium > 0:
                diff = abs(premium - target_premium)
                if diff < best_diff:
                    best_diff = diff
                    best_strike = strike
                    best_premium = premium

        if best_strike:
            print(
                f"   Price-Neutral: Target ₹{target_premium:.2f} → Selected {opposing_type} {best_strike} @ ₹{best_premium:.2f}")
        else:
            # Fallback: nearest available strike
            best_strike = valid_strikes[0] if valid_strikes else losing_leg.strike + config.STRIKE_INTERVAL
            data = option_chain.get(best_strike, {})
            best_premium = data.get(opposing_type, target_premium)
            print(f"   Price-Neutral (fallback): {opposing_type} {best_strike} @ ₹{best_premium:.2f}")

        return best_strike, target_premium

    def _find_hedge_in_chain(self, option_chain, strike, losing_leg_type):
        """Find hedge in chain"""
        opposing_type = 'PE' if losing_leg_type == 'CE' else 'CE'

        data = option_chain.get(strike)
        if not data:
            return None

        premium = data.get(opposing_type)
        symbol = data.get(f'{opposing_type}_symbol')
        security_id = data.get(f'{opposing_type}_security_id')

        if premium and symbol and security_id:
            return (symbol, security_id, premium)
        return None


hedge_manager = HedgeManager()
