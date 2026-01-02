"""
Paper Trading Main Script
Run this file to start paper trading
"""

import time
from datetime import datetime, timedelta
from market_data_api import market_data
from straddle_manager_paper import straddle_manager
from hedge_manager_paper import hedge_manager
from paper_positions import paper_positions
from config_paper import config


class PaperTrader:
    def __init__(self):
        self.running = False
        self.candle_count = 0

    def initialize(self):
        """Initialize system"""
        print("\n" + "=" * 80)
        print("🧪 PAPER TRADING SYSTEM 🧪")
        print("NO REAL ORDERS - SIMULATION ONLY")
        print("=" * 80 + "\n")

        if not market_data.login():
            print("❌ Login failed")
            return False

        return True

    def _get_next_candle_time(self):
        """Next aligned candle time, same as live trader."""
        current = config.get_current_ist_time()
        interval = config.CANDLE_INTERVAL_SECONDS

        seconds_since_midnight = current.hour * 3600 + current.minute * 60 + current.second
        next_interval = ((seconds_since_midnight // interval) + 1) * interval

        next_hour = next_interval // 3600
        next_minute = (next_interval % 3600) // 60
        next_second = next_interval % 60

        next_time = current.replace(
            hour=next_hour % 24,
            minute=next_minute,
            second=next_second,
            microsecond=0,
        )
        if next_time <= current:
            next_time += timedelta(days=1)
        return next_time

    def _wait_for_next_candle(self):
        """Sleep until next aligned candle."""
        next_candle = self._get_next_candle_time()
        current = config.get_current_ist_time()
        wait_seconds = (next_candle - current).total_seconds()
        if wait_seconds > 0:
            print(f"⏰ Waiting {wait_seconds:.1f}s until next candle at {next_candle.strftime('%H:%M:%S')}")
            time.sleep(wait_seconds)

    def run(self):
        """Main loop"""
        if not self.initialize():
            return

        # ✅ WARM-UP DELAY: let WebSocket connect and receive initial ticks
        print("⏳ Waiting 5s for WebSocket warm-up before first candle...")
        time.sleep(5)

        self.running = True

        try:
            while self.running:
                self.candle_count += 1
                self.process_candle()

                # Check EOD
                if config.should_force_squareoff():
                    print("\n🔔 EOD Square-off time reached")
                    if straddle_manager.straddle_active:
                        straddle_manager.exit_straddle("EOD Square-off")
                    print("✅ Trading day complete")
                    break

                print()
                self._wait_for_next_candle()

        except KeyboardInterrupt:
            print("\n\n⚠️  Ctrl+C detected - Exiting...")
            self.shutdown()

    def process_candle(self):
        """Process one candle"""
        print(f"\n{'=' * 80}")
        print(f"CANDLE #{self.candle_count} - {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'=' * 80}")

        # Fetch spot
        spot_price = market_data.get_spot_price()
        if not spot_price:
            print("❌ Could not fetch spot price")
            return

        print(f"📍 NIFTY Spot: {spot_price:.2f}")

        # Generate strikes (with hedge strikes if active)
        strikes = self._generate_strikes(spot_price)
        option_chain = market_data.get_option_chain(strikes)

        if not option_chain:
            print("❌ Could not fetch option chain (probably WS not ready or rate-limited)")
            return

        # Entry logic
        if not straddle_manager.straddle_active:
            if config.is_entry_window_open():
                straddle_manager.enter_straddle(spot_price, option_chain)
            else:
                print("⏸️  Outside entry window")
            return

        # Monitoring
        exit_reason = straddle_manager.update_positions(option_chain)

        if exit_reason:
            print(f"\n🚨 EXIT TRIGGERED: {exit_reason}")
            straddle_manager.exit_straddle(exit_reason)
            return

        # Hedging - BOTH LEGS
        if straddle_manager.ce_leg and straddle_manager.pe_leg:
            # Process hedging for CE leg (if losing)
            hedge_manager.process_leg_hedging(
                straddle_manager.ce_leg,
                straddle_manager.pe_leg,
                option_chain
            )

            # Process hedging for PE leg (if losing)
            hedge_manager.process_leg_hedging(
                straddle_manager.pe_leg,
                straddle_manager.ce_leg,
                option_chain
            )

        # Display status
        self._display_status()

    def _generate_strikes(self, spot_price):
        """Generate strikes - INCLUDES ACTIVE HEDGE STRIKES"""
        atm = round(spot_price / config.STRIKE_INTERVAL) * config.STRIKE_INTERVAL
        strikes = []

        # Base range: ±8 strikes around ATM
        for i in range(-8, 9):
            strikes.append(int(atm + i * config.STRIKE_INTERVAL))

        # ✅ ADD ACTIVE HEDGE STRIKES if they exist but are outside base range
        if straddle_manager.straddle_active:
            if straddle_manager.ce_leg and straddle_manager.ce_leg.hedge_active:
                ce_hedge_strike = straddle_manager.ce_leg.hedge_strike
                if ce_hedge_strike not in strikes:
                    strikes.append(ce_hedge_strike)
                    print(f"   📌 Including CE hedge strike {ce_hedge_strike} (outside ATM range)")

            if straddle_manager.pe_leg and straddle_manager.pe_leg.hedge_active:
                pe_hedge_strike = straddle_manager.pe_leg.hedge_strike
                if pe_hedge_strike not in strikes:
                    strikes.append(pe_hedge_strike)
                    print(f"   📌 Including PE hedge strike {pe_hedge_strike} (outside ATM range)")

        # Sort strikes for clean option chain
        strikes.sort()

        return strikes

    def _display_status(self):
        """LIVE-style display for paper trading."""
        if not straddle_manager.straddle_active:
            return

        ce = straddle_manager.ce_leg
        pe = straddle_manager.pe_leg

        print("────────────────────────────────────────────────────────────────────────────────")
        # CE block
        if ce:
            ce_icon = "🛡️" if ce.hedge_active else "✅"
            print(
                f"   CE: {ce.entry_premium:.2f} {ce_icon} "
                f"₹{ce.current_premium:.2f} | Loss: {ce.current_loss_pct:+.1f}%"
            )

            # NEXT line when no hedge yet (L1 target)
            if not ce.hedge_active:
                if len(config.PROGRESSIVE_HEDGING_LEVELS) > 0:
                    l1_pct = config.PROGRESSIVE_HEDGING_LEVELS[0]
                    l1_price = ce.entry_premium * (1 + l1_pct / 100)
                    dist = l1_pct - ce.current_loss_pct
                    print(f"        ⏫ NEXT: L1 at {l1_pct:.0f}% (₹{l1_price:.2f})")
                    print(f"        📏 Distance: {dist:+.1f}%")
            else:
                # Hedge active: show hedge, next level, exit zone
                print(f"        ✅ L{ce.hedge_level} HEDGE ACTIVE: CE {ce.hedge_strike}")

                if ce.hedge_level == 1 and len(config.PROGRESSIVE_HEDGING_LEVELS) > 1:
                    next_pct = config.PROGRESSIVE_HEDGING_LEVELS[1]  # L2
                else:
                    next_pct = config.LEVEL_3_HARD_STOP              # L3

                next_price = ce.entry_premium * (1 + next_pct / 100)
                dist_up = next_pct - ce.current_loss_pct
                print(f"        ⏫ NEXT: L{ce.hedge_level + 1} at {next_pct:.0f}% (₹{next_price:.2f})")
                print(f"        📏 Distance UP: {dist_up:+.1f}%")

                if ce.hedge_level == 1:
                    exit_pct = 0.0
                elif ce.hedge_level == 2 and len(config.PROGRESSIVE_HEDGING_LEVELS) > 0:
                    exit_pct = config.PROGRESSIVE_HEDGING_LEVELS[0]
                else:
                    exit_pct = 0.0

                exit_price = ce.entry_premium * (1 + exit_pct / 100)
                dist_down = exit_pct - ce.current_loss_pct
                print(f"        📉 Exit zone: {exit_pct:.0f}% (₹{exit_price:.2f})")
                print(f"        📏 Distance DOWN: {dist_down:+.1f}%")
                # Optional: hedge P&L if you track it on the leg
                # print(f"        ❌ Hedge P&L: ₹{ce.hedge_pnl:+,.2f}")

        print("────────────────────────────────────────────────────────────────────────────────")
        # PE block
        if pe:
            pe_icon = "🛡️" if pe.hedge_active else "✅"
            print(
                f"   PE: {pe.entry_premium:.2f} {pe_icon} "
                f"₹{pe.current_premium:.2f} | Loss: {pe.current_loss_pct:+.1f}%"
            )

            if not pe.hedge_active:
                if len(config.PROGRESSIVE_HEDGING_LEVELS) > 0:
                    l1_pct = config.PROGRESSIVE_HEDGING_LEVELS[0]
                    l1_price = pe.entry_premium * (1 + l1_pct / 100)
                    dist = l1_pct - pe.current_loss_pct
                    print(f"        ⏫ NEXT: L1 at {l1_pct:.0f}% (₹{l1_price:.2f})")
                    print(f"        📏 Distance: {dist:+.1f}%")
            else:
                print(f"        ✅ L{pe.hedge_level} HEDGE ACTIVE: PE {pe.hedge_strike}")

                if pe.hedge_level == 1 and len(config.PROGRESSIVE_HEDGING_LEVELS) > 1:
                    next_pct = config.PROGRESSIVE_HEDGING_LEVELS[1]
                else:
                    next_pct = config.LEVEL_3_HARD_STOP

                next_price = pe.entry_premium * (1 + next_pct / 100)
                dist_up = next_pct - pe.current_loss_pct
                print(f"        ⏫ NEXT: L{pe.hedge_level + 1} at {next_pct:.0f}% (₹{next_price:.2f})")
                print(f"        📏 Distance UP: {dist_up:+.1f}%")

                if pe.hedge_level == 1:
                    exit_pct = 0.0
                elif pe.hedge_level == 2 and len(config.PROGRESSIVE_HEDGING_LEVELS) > 0:
                    exit_pct = config.PROGRESSIVE_HEDGING_LEVELS[0]
                else:
                    exit_pct = 0.0

                exit_price = pe.entry_premium * (1 + exit_pct / 100)
                dist_down = exit_pct - pe.current_loss_pct
                print(f"        📉 Exit zone: {exit_pct:.0f}% (₹{exit_price:.2f})")
                print(f"        📏 Distance DOWN: {dist_down:+.1f}%")
                # Optional hedge P&L line here too

        print("────────────────────────────────────────────────────────────────────────────────")
        ce_pnl = ce.get_pnl() if ce else 0.0
        pe_pnl = pe.get_pnl() if pe else 0.0
        total_pnl = ce_pnl + pe_pnl
        print(f"   💸 P&L: CE ₹{ce_pnl:+,.2f} | PE ₹{pe_pnl:+,.2f} | TOTAL ₹{total_pnl:+,.2f}")
        print("────────────────────────────────────────────────────────────────────────────────")

    def shutdown(self):
        """Shutdown"""
        if straddle_manager.straddle_active:
            print("\n⚠️  Closing open positions...")
            straddle_manager.exit_straddle("Manual Stop")

        market_data.close()
        print("\n✅ Paper trading session ended")
        print(f"Total candles: {self.candle_count}")

        # Show paper positions summary
        positions = paper_positions.get_positions()
        if positions:
            print(f"\n📋 PAPER POSITIONS SUMMARY:")
            for sec_id, pos in positions.items():
                print(f"   {pos['symbol']}: {pos['qty']} @ ₹{pos['entry_price']:.2f}")


if __name__ == "__main__":
    trader = PaperTrader()
    trader.run()