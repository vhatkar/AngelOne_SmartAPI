"""

Individual Leg Class for CE/PE tracking - BUY HEDGE ON LOSING SIDE

NO BUFFER/NO TRAILING - Hold hedges until Level 3

100% CORE LOGIC COMPLIANT + BUY HEDGE STRATEGY

🔥 MODIFIED: Changed from SELL to BUY hedge strategy
🔥 MODIFIED: Hedge placed on LOSING side (same option type)
🔥 MODIFIED: P&L calculation for bought hedges

✅ CRITICAL FIX: Added loading/unloading mechanism for L1 and L2 triggers

"""

from typing import Optional, Dict
from datetime import datetime
from config import config

class Leg:
    """Represents a single leg (CE or PE) of the straddle"""

    def __init__(self, name: str, strike: int, option_type: str,
                 entry_premium: float, symbol: str, security_id: str):
        """Initialize leg"""
        self.name = name
        self.strike = strike
        self.option_type = option_type
        self.entry_premium = entry_premium
        self.symbol = symbol
        self.security_id = security_id
        self.lot_size = config.LOT_SIZE

        # Current state
        self.current_premium = entry_premium
        self.current_loss_pct = 0.0

        # Position state
        self.is_active = True
        self.entry_time: Optional[datetime] = None
        self.exit_time: Optional[datetime] = None

        # ✅ HEDGE STATE - BUY HEDGE ON LOSING SIDE
        self.hedge_active = False
        self.hedge_level = 0
        self.hedge_symbol = None
        self.hedge_security_id = None
        self.hedge_strike = None
        self.hedge_entry_premium = None
        self.hedge_current_premium = None

        # ✅ LEVEL PROGRESSION - CORE LOGIC
        self.next_stop_loss_pct = config.PROGRESSIVE_HEDGING_LEVELS[0]
        self.completed_levels = []

        # ✅ P&L tracking
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0
        self.realized_hedge_pnl = 0.0  # Accumulates hedge P&L across exits

        # 🔥 NEW: Track manual interventions
        self.manual_exit_timestamp = None

        # ✅ CRITICAL FIX: Loading state for triggers
        self.l1_loaded = True  # L1 ready to trigger initially
        self.l2_loaded = True  # L2 ready to trigger initially

    def update_premium(self, current_premium: float):
        """Update current premium and calculate loss percentage"""
        self.current_premium = current_premium
        self.current_loss_pct = ((current_premium - self.entry_premium) / self.entry_premium) * 100
        self.unrealized_pnl = (self.entry_premium - current_premium) * self.lot_size

    def update_hedge_premium(self, hedge_premium: float):
        """Update hedge premium if hedge is active"""
        if self.hedge_active:
            self.hedge_current_premium = hedge_premium

    def should_buy_hedge(self) -> bool:
        """
        ✅ MODIFIED: Check if hedge should be BOUGHT
        🔥 FIXED: Prevents immediate re-buy after manual exit
        🔥 CRITICAL FIX: Added loading/unloading mechanism
        """
        if self.hedge_active:
            return False

        # 🔥 NEW: Prevent immediate re-buy after manual exit (1 minute cooldown)
        if self.manual_exit_timestamp:
            elapsed = (config.get_current_ist_time() - self.manual_exit_timestamp).total_seconds()
            if elapsed < 60:  # Wait at least 1 minute after manual exit
                return False
            else:
                # Cooldown expired, clear flag
                self.manual_exit_timestamp = None

        # Check if we should trigger a hedge
        if self.current_loss_pct >= self.next_stop_loss_pct:
            # Determine which level we're about to trigger
            if self.next_stop_loss_pct == config.PROGRESSIVE_HEDGING_LEVELS[0]:  # L1
                if not self.l1_loaded:
                    return False  # L1 not loaded, cannot trigger
            elif self.next_stop_loss_pct == config.PROGRESSIVE_HEDGING_LEVELS[1]:  # L2
                if not self.l2_loaded:
                    return False  # L2 not loaded, cannot trigger
            return True

        return False

    def buy_hedge(self, hedge_symbol: str, hedge_security_id: str,
                  hedge_strike: int, hedge_premium: float, level: int) -> Dict:
        """✅ MODIFIED: BUY HEDGE on losing side - Hold until Level 3"""
        self.hedge_active = True
        self.hedge_level = level
        self.hedge_symbol = hedge_symbol
        self.hedge_security_id = hedge_security_id
        self.hedge_strike = hedge_strike
        self.hedge_entry_premium = hedge_premium
        self.hedge_current_premium = hedge_premium

        # ✅ CRITICAL FIX: Unload the level that just triggered
        if level == 1:
            self.l1_loaded = False
        elif level == 2:
            self.l2_loaded = False

        # Next target = Level 3 (NO buffer/trail)
        if self.hedge_level < len(config.PROGRESSIVE_HEDGING_LEVELS):
            self.next_stop_loss_pct = config.PROGRESSIVE_HEDGING_LEVELS[self.hedge_level]
        else:
            self.next_stop_loss_pct = config.LEVEL_3_HARD_STOP

        print(f"\n{'='*60}")
        print(f"✅ {self.name} L{level} BUY HEDGE")
        print(f"{'='*60}")
        print(f"  Strike: {hedge_strike}")
        print(f"  Premium: ₹{hedge_premium:.2f}")
        print(f"  Entry Loss: {self.current_loss_pct:.1f}%")
        print(f"  Loading State: L1={'LOADED' if self.l1_loaded else 'UNLOADED'}, L2={'LOADED' if self.l2_loaded else 'UNLOADED'}")
        print(f"  ➡️  Next Stop: {self.next_stop_loss_pct:.1f}%")
        print(f"{'='*60}")

        return {
            'action': 'HEDGE_BUY',
            'leg': self.name,
            'level': level,
            'strike': hedge_strike,
            'premium': hedge_premium,
            'entry_loss_pct': self.current_loss_pct
        }

    def close_hedge(self, hedge_exit_premium: float) -> Dict:
        """Close hedge (for force exits or reversals)
        ✅ MODIFIED: BUY hedge P&L calculation
        """

        # ✅ MODIFIED: BUY hedge P&L calculation (exit - entry)
        hedge_pnl = (hedge_exit_premium - self.hedge_entry_premium) * self.lot_size
        self.realized_hedge_pnl += hedge_pnl  # ACCUMULATE hedge P&L

        print(f"\n{'='*60}")
        print(f"🚪 HEDGE LEVEL {self.hedge_level} CLOSED - {self.name}")
        print(f"{'='*60}")
        print(f"  Entry Premium: ₹{self.hedge_entry_premium:.2f}")
        print(f"  Exit Premium: ₹{hedge_exit_premium:.2f}")
        print(f"  Hedge P&L: ₹{hedge_pnl:.2f}")
        print(f"  Cumulative Hedge P&L: ₹{self.realized_hedge_pnl:.2f}")
        print(f"{'='*60}")

        event = {
            'action': 'HEDGE_CLOSE',
            'leg': self.name,
            'level': self.hedge_level,
            'entry_premium': self.hedge_entry_premium,
            'exit_premium': hedge_exit_premium,
            'pnl': hedge_pnl
        }

        # ✅ CRITICAL FIX: Reset to correct trigger level based on loading logic
        exited_level = self.hedge_level

        # ✅ CORRECT LOADING LOGIC:
        # L1 exits at 0% → L1 loads at 0% → next trigger is L1 at 20%
        # L2 exits at 20% → L2 loads at 20% → next trigger is L2 at 40%
        if exited_level == 1:
            # L1 exited at 0% → L1 is now LOADED → next trigger is L1 at 20%
            self.l1_loaded = True  # L1 loaded
            self.next_stop_loss_pct = config.PROGRESSIVE_HEDGING_LEVELS[0]  # 20%
            print(f"  ✅ L1 loaded - will re-trigger at 20%")
        elif exited_level == 2:
            # L2 exited at 20% → L2 is now LOADED → next trigger is L2 at 40%
            self.l2_loaded = True  # L2 loaded
            self.next_stop_loss_pct = config.PROGRESSIVE_HEDGING_LEVELS[1]  # 40%
            print(f"  ✅ L2 loaded - will re-trigger at 40%")
        else:
            # Fallback
            self.next_stop_loss_pct = config.PROGRESSIVE_HEDGING_LEVELS[0]

        print(f"  ➡️  Next Stop Loss: {self.next_stop_loss_pct:.1f}%")
        print(f"  📋 Completed Levels: {self.completed_levels}")
        print(f"  Loading State: L1={'LOADED' if self.l1_loaded else 'UNLOADED'}, L2={'LOADED' if self.l2_loaded else 'UNLOADED'}")

        # Reset hedge state
        self.hedge_active = False
        self.hedge_level = 0
        self.hedge_symbol = None
        self.hedge_security_id = None
        self.hedge_strike = None
        self.hedge_entry_premium = None
        self.hedge_current_premium = None

        return event

    def is_level_3_triggered(self) -> bool:
        """
        ✅ TRIGGER FIX: Check if Level 3 hard stop is triggered
        """
        return (self.current_loss_pct >= config.LEVEL_3_HARD_STOP and
                not self.hedge_active and
                self.next_stop_loss_pct == config.LEVEL_3_HARD_STOP)

    def get_pnl(self) -> float:
        """
        Get total P&L including all hedges
        ✅ MODIFIED: BUY hedge P&L calculation
        """
        # Leg P&L (sell premium collected - current premium)
        leg_pnl = (self.entry_premium - self.current_premium) * self.lot_size

        # Current hedge P&L (if hedge is active) - ✅ MODIFIED for BUY
        current_hedge_pnl = 0.0
        if self.hedge_active and self.hedge_current_premium:
            current_hedge_pnl = (self.hedge_current_premium - self.hedge_entry_premium) * self.lot_size

        # Total P&L = Leg P&L + Realized Hedge P&L + Current Hedge P&L
        total_pnl = leg_pnl + self.realized_hedge_pnl + current_hedge_pnl

        return total_pnl

    def get_pnl_breakdown(self) -> Dict:
        """
        Get detailed P&L breakdown for logging
        ✅ MODIFIED: BUY hedge P&L calculation
        """
        leg_pnl = (self.entry_premium - self.current_premium) * self.lot_size

        # ✅ MODIFIED: BUY hedge P&L calculation
        current_hedge_pnl = 0.0
        if self.hedge_active and self.hedge_current_premium:
            current_hedge_pnl = (self.hedge_current_premium - self.hedge_entry_premium) * self.lot_size

        return {
            'leg_pnl': leg_pnl,
            'realized_hedge_pnl': self.realized_hedge_pnl,
            'current_hedge_pnl': current_hedge_pnl,
            'total_pnl': leg_pnl + self.realized_hedge_pnl + current_hedge_pnl
        }

    def get_pnl_percentage(self) -> float:
        """Get current loss percentage"""
        return self.current_loss_pct

    def get_total_side_premium(self, include_hedge: bool = True) -> float:
        """
        ✅ ADDED: Total premium on this side (leg + hedge if active)
        KEY for price neutrality calculation
        """
        total = self.current_premium
        if include_hedge and self.hedge_active and self.hedge_current_premium:
            total += self.hedge_current_premium
        return total

    def sync_manual_hedge_addition(self, hedge_symbol: str, hedge_security_id: str,
                                   hedge_level: int, entry_loss_pct: float):
        """✅ ADDED: Sync manually added hedge from broker"""
        self.hedge_active = True
        self.hedge_level = hedge_level
        self.hedge_symbol = hedge_symbol
        self.hedge_security_id = hedge_security_id
        self.hedge_entry_premium = 0  # Unknown, will update on next candle
        self.hedge_current_premium = 0

        # Update next stop loss
        if hedge_level < len(config.PROGRESSIVE_HEDGING_LEVELS):
            self.next_stop_loss_pct = config.PROGRESSIVE_HEDGING_LEVELS[hedge_level]
        else:
            self.next_stop_loss_pct = config.LEVEL_3_HARD_STOP

        print(f"  ✅ Synced manual hedge: L{hedge_level} at {entry_loss_pct:.1f}% loss")

    def sync_manual_hedge_exit(self, exited_level: int):
        """✅ ADDED: Sync manually exited hedge from broker"""
        if self.hedge_active:
            # Calculate approximate P&L (won't be exact since we don't know exit price)
            if self.hedge_entry_premium and self.hedge_current_premium:
                hedge_pnl = (self.hedge_current_premium - self.hedge_entry_premium) * self.lot_size
                self.realized_hedge_pnl += hedge_pnl

            # Mark level as completed
            self.completed_levels.append(exited_level)

            # Reset hedge state
            self.hedge_active = False
            self.hedge_level = 0
            self.hedge_symbol = None
            self.hedge_security_id = None
            self.hedge_entry_premium = None
            self.hedge_current_premium = None

            # Update next stop loss
            if exited_level < len(config.PROGRESSIVE_HEDGING_LEVELS):
                self.next_stop_loss_pct = config.PROGRESSIVE_HEDGING_LEVELS[exited_level]
            else:
                self.next_stop_loss_pct = config.LEVEL_3_HARD_STOP

            print(f"  ✅ Synced manual exit: L{exited_level}")

    def reset_for_new_session(self):
        """Reset state for new session"""
        self.hedge_active = False
        self.hedge_level = 0
        self.completed_levels = []
        self.next_stop_loss_pct = config.PROGRESSIVE_HEDGING_LEVELS[0]
        self.realized_hedge_pnl = 0.0  # Reset accumulated hedge P&L for new session
        self.manual_exit_timestamp = None

        # ✅ CRITICAL FIX: Reset loading states for new session
        self.l1_loaded = True
        self.l2_loaded = True

    def skip_level(self, level: int):
        """
        Skip a level (mark as completed without trading it)
        """
        if level in self.completed_levels:
            print(f"  ⚠️  Level {level} already completed")
            return

        # Add to completed levels
        self.completed_levels.append(level)
        self.completed_levels.sort()

        # Update next stop loss
        available_levels = [i+1 for i in range(len(config.PROGRESSIVE_HEDGING_LEVELS))
                          if (i+1) not in self.completed_levels]

        if available_levels:
            next_level_idx = available_levels[0] - 1
            self.next_stop_loss_pct = config.PROGRESSIVE_HEDGING_LEVELS[next_level_idx]
        else:
            self.next_stop_loss_pct = config.LEVEL_3_HARD_STOP

        print(f"\n{'='*60}")
        print(f"⏭️  LEVEL {level} SKIPPED - {self.name}")
        print(f"{'='*60}")
        print(f"  ✅ Marked as COMPLETED (skipped)")
        print(f"  ➡️  Next Stop Loss: {self.next_stop_loss_pct:.1f}%")
        print(f"  📋 Completed: {self.completed_levels}")
        print(f"{'='*60}")

    def get_next_level(self) -> int:
        """
        Get the next level number
        """
        available_levels = [i+1 for i in range(len(config.PROGRESSIVE_HEDGING_LEVELS))
                          if (i+1) not in self.completed_levels]
        return available_levels[0] if available_levels else 3
