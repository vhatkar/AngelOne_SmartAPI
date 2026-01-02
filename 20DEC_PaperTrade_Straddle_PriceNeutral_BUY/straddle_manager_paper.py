"""
Paper Trading Straddle Manager - COMPLETE VERSION
"""

from leg import Leg
from paper_positions import paper_positions
from market_data_api import market_data
from config_paper import config
from datetime import datetime
from simple_logger import logger  # ✅ ADD LOGGING


class StraddleManager:
    def __init__(self):
        self.ce_leg = None
        self.pe_leg = None
        self.straddle_active = False
        self.strike = None
        self.entry_time = None

    def enter_straddle(self, spot_price, option_chain):
        """Enter straddle - PAPER VERSION"""
        if self.straddle_active:
            print("⚠️  Straddle already active")
            return False

        strike, ce_premium, pe_premium = self._scan_best_straddle(spot_price, option_chain)

        strike_data = option_chain.get(strike)
        if not strike_data:
            print("❌ Strike data not found")
            return False

        ce_symbol = strike_data['CE_symbol']
        pe_symbol = strike_data['PE_symbol']
        ce_security_id = strike_data['CE_security_id']
        pe_security_id = strike_data['PE_security_id']

        print(f"\n{'=' * 80}")
        print(f"[PAPER] ENTERING STRADDLE")
        print(f"{'=' * 80}")
        print(f"Strike: {strike}")
        print(f"CE: {ce_symbol} @ ₹{ce_premium:.2f}")
        print(f"PE: {pe_symbol} @ ₹{pe_premium:.2f}")
        print(f"Total Premium: ₹{ce_premium + pe_premium:.2f}")

        # ✅ EXECUTE PAPER ORDERS
        paper_positions.execute_order('SELL', ce_symbol, ce_security_id, config.LOT_SIZE, ce_premium)
        paper_positions.execute_order('SELL', pe_symbol, pe_security_id, config.LOT_SIZE, pe_premium)

        # ✅ CREATE LEGS
        self.ce_leg = Leg('CE', strike, 'CE', ce_premium, ce_symbol, ce_security_id)
        self.pe_leg = Leg('PE', strike, 'PE', pe_premium, pe_symbol, pe_security_id)

        self.straddle_active = True
        self.strike = strike
        self.entry_time = datetime.now()

        # ✅ LOG ENTRY
        logger.log_entry(strike, ce_premium, pe_premium)

        print(f"✅ PAPER STRADDLE ACTIVE")
        print(f"{'=' * 80}\n")

        return True

    def update_positions(self, option_chain):
        """Update premiums - WITH HEDGE UPDATES"""
        if not self.straddle_active:
            return None

        strike_data = option_chain.get(self.strike)
        if not strike_data:
            print("⚠️  No strike data")
            return None

        ce_premium = strike_data.get('CE', 0)
        pe_premium = strike_data.get('PE', 0)

        if not ce_premium or not pe_premium:
            return None

        # ✅ UPDATE LEG PREMIUMS
        self.ce_leg.update_premium(ce_premium)
        self.pe_leg.update_premium(pe_premium)

        # ✅ UPDATE HEDGE PREMIUMS IF ACTIVE
        if self.ce_leg.hedge_active and self.ce_leg.hedge_strike:
            hedge_data = option_chain.get(self.ce_leg.hedge_strike)
            if hedge_data:
                hedge_premium = hedge_data.get('PE', 0)  # CE hedge is PE
                if hedge_premium:
                    self.ce_leg.update_hedge_premium(hedge_premium)

        if self.pe_leg.hedge_active and self.pe_leg.hedge_strike:
            hedge_data = option_chain.get(self.pe_leg.hedge_strike)
            if hedge_data:
                hedge_premium = hedge_data.get('CE', 0)  # PE hedge is CE
                if hedge_premium:
                    self.pe_leg.update_hedge_premium(hedge_premium)

        # ✅ CHECK EXIT CONDITIONS
        if self._check_premium_ratio_exit():
            return "PREMIUM_RATIO_EXIT"

        if self.ce_leg.is_level3_triggered() or self.pe_leg.is_level3_triggered():
            return "LEVEL_3_HARD_STOP"

        return None

    def exit_straddle(self, reason):
        """Exit straddle - COMPLETE PAPER VERSION"""
        if not self.straddle_active:
            print("⚠️  No straddle to exit")
            return None

        print(f"\n{'=' * 80}")
        print(f"[PAPER] EXITING STRADDLE - {reason}")
        print(f"{'=' * 80}")

        # ✅ CLOSE ALL HEDGES FIRST
        if self.ce_leg.hedge_active:
            print(f"   Closing CE hedge...")
            hedge_data = market_data.get_option_chain([self.ce_leg.hedge_strike])
            if hedge_data and self.ce_leg.hedge_strike in hedge_data:
                hedge_premium = hedge_data[self.ce_leg.hedge_strike].get('PE', self.ce_leg.hedge_current_premium or 0)
                paper_positions.execute_order('BUY', self.ce_leg.hedge_symbol,
                                              self.ce_leg.hedge_security_id,
                                              config.LOT_SIZE, hedge_premium)

        if self.pe_leg.hedge_active:
            print(f"   Closing PE hedge...")
            hedge_data = market_data.get_option_chain([self.pe_leg.hedge_strike])
            if hedge_data and self.pe_leg.hedge_strike in hedge_data:
                hedge_premium = hedge_data[self.pe_leg.hedge_strike].get('CE', self.pe_leg.hedge_current_premium or 0)
                paper_positions.execute_order('BUY', self.pe_leg.hedge_symbol,
                                              self.pe_leg.hedge_security_id,
                                              config.LOT_SIZE, hedge_premium)

        # ✅ CLOSE MAIN LEGS
        ce_exit = self.ce_leg.current_premium
        pe_exit = self.pe_leg.current_premium

        paper_positions.execute_order('BUY', self.ce_leg.symbol, self.ce_leg.security_id, config.LOT_SIZE, ce_exit)
        paper_positions.execute_order('BUY', self.pe_leg.symbol, self.pe_leg.security_id, config.LOT_SIZE, pe_exit)

        # ✅ CALCULATE FINAL P&L
        ce_pnl = self.ce_leg.get_pnl()
        pe_pnl = self.pe_leg.get_pnl()
        total_pnl = ce_pnl + pe_pnl

        # ✅ LOG EXIT
        duration = (datetime.now() - self.entry_time).total_seconds() / 60
        logger.log_exit(self.strike, reason, ce_pnl, pe_pnl, total_pnl, duration)

        print(f"\n📊 FINAL P&L SUMMARY")
        print(f"{'─' * 60}")
        print(f"CE P&L: ₹{ce_pnl:,.2f}")
        print(f"PE P&L: ₹{pe_pnl:+,.2f}")
        print(f"{'─' * 60}")
        print(f"TOTAL P&L: ₹{total_pnl:+,.2f}")
        print(f"Duration: {duration:.1f} minutes")
        print(f"{'=' * 80}\n")

        # ✅ CLEAR POSITIONS
        paper_positions.clear_all()

        # ✅ RESET STATE
        self.straddle_active = False
        self.ce_leg = None
        self.pe_leg = None
        self.strike = None
        self.entry_time = None

        return {'total_pnl': total_pnl, 'exit_time': datetime.now()}

    def _scan_best_straddle(self, spot_price, option_chain):
        """Select best strike - PAPER VERSION"""
        min_diff = float('inf')
        best_strike = None
        best_ce = 0
        best_pe = 0

        for strike, data in option_chain.items():
            ce_prem = data.get('CE', 0)
            pe_prem = data.get('PE', 0)

            if ce_prem > 0 and pe_prem > 0:
                diff = abs(ce_prem - pe_prem)
                if diff < min_diff:
                    min_diff = diff
                    best_strike = strike
                    best_ce = ce_prem
                    best_pe = pe_prem

        if best_strike:
            print(f"✅ Selected ATM: {best_strike} | CE: ₹{best_ce:.2f} | PE: ₹{best_pe:.2f} | Diff: ₹{min_diff:.2f}")
        else:
            print("❌ No valid straddle found")
            best_strike = round(spot_price / config.STRIKE_INTERVAL) * config.STRIKE_INTERVAL
            best_ce = 50.0  # Default for testing
            best_pe = 50.0  # Default for testing

        return best_strike, best_ce, best_pe

    def _check_premium_ratio_exit(self):
        """Check ratio exit"""
        if not self.ce_leg or not self.pe_leg:
            return False

        ce_prem = self.ce_leg.current_premium
        pe_prem = self.pe_leg.current_premium

        if ce_prem <= 0 or pe_prem <= 0:
            return False

        ratio = min(ce_prem, pe_prem) / max(ce_prem, pe_prem)
        should_exit = ratio <= config.FORCE_EXIT_RATIO

        if should_exit:
            print(f"\n⚠️  PREMIUM RATIO EXIT TRIGGERED")
            print(f"   CE: ₹{ce_prem:.2f}, PE: ₹{pe_prem:.2f}")
            print(f"   Ratio: {ratio:.2f} <= {config.FORCE_EXIT_RATIO:.2f}")

        return should_exit


straddle_manager = StraddleManager()
