"""
Leg Class - Paper Trading Version
COMPATIBLE with paper trading ONLY
"""

from typing import Optional, Dict
from datetime import datetime
from config_paper import config  # ✅ FIXED: Use paper config


class Leg:
    """Represents a single leg (CE or PE) of the straddle - PAPER VERSION"""

    def __init__(self, name: str, strike: int, option_type: str,
                 entry_premium: float, symbol: str, security_id: str):
        """Initialize leg for PAPER trading"""
        self.name = name
        self.strike = strike
        self.option_type = option_type
        self.entry_premium = entry_premium
        self.symbol = symbol
        self.security_id = security_id
        self.lot_size = config.LOT_SIZE  # ✅ Use paper config

        # Current state
        self.current_premium = entry_premium
        self.current_loss_pct = 0.0

        # Position state
        self.is_active = True
        self.entry_time = None
        self.exit_time = None

        # ✅ HEDGE STATE - PAPER VERSION
        self.hedge_active = False
        self.hedge_level = 0
        self.hedge_symbol = None
        self.hedge_security_id = None
        self.hedge_strike = None
        self.hedge_entry_premium = None
        self.hedge_current_premium = None

        # ✅ LEVEL PROGRESSION - SIMPLIFIED for paper
        self.next_stop_loss_pct = config.PROGRESSIVE_HEDGING_LEVELS[0]
        self.completed_levels = []

        # ✅ P&L tracking - SIMPLIFIED for paper
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0
        self.realized_hedge_pnl = 0.0

    def update_premium(self, current_premium: float):
        """Update current premium"""
        self.current_premium = current_premium
        if self.entry_premium > 0:
            self.current_loss_pct = ((current_premium - self.entry_premium) / self.entry_premium) * 100
            self.unrealized_pnl = (self.entry_premium - current_premium) * self.lot_size

    def update_hedge_premium(self, hedge_premium: float):
        """Update hedge premium"""
        if self.hedge_active:
            self.hedge_current_premium = hedge_premium

    def should_sell_hedge(self) -> bool:
        """Check if should sell hedge - PAPER VERSION"""
        if self.hedge_active:
            return False

        # Check current loss against next stop loss
        if self.current_loss_pct >= self.next_stop_loss_pct:
            # Don't hedge at Level 3
            if self.next_stop_loss_pct >= config.LEVEL_3_HARD_STOP:
                return False
            return True

        return False

    def sell_hedge(self, hedge_symbol: str, hedge_security_id: str,
                   hedge_strike: int, hedge_premium: float, level: int) -> Dict:
        """Sell hedge - PAPER VERSION"""
        self.hedge_active = True
        self.hedge_level = level
        self.hedge_symbol = hedge_symbol
        self.hedge_security_id = hedge_security_id
        self.hedge_strike = hedge_strike
        self.hedge_entry_premium = hedge_premium
        self.hedge_current_premium = hedge_premium

        # Set next stop loss
        if level < len(config.PROGRESSIVE_HEDGING_LEVELS):
            self.next_stop_loss_pct = config.PROGRESSIVE_HEDGING_LEVELS[level]
        else:
            self.next_stop_loss_pct = config.LEVEL_3_HARD_STOP

        print(f"\n✅ {self.name} L{level} HEDGE SOLD")
        print(f"   Strike: {hedge_strike}, Premium: ₹{hedge_premium:.2f}")

        return {
            'action': 'HEDGE_SELL',
            'leg': self.name,
            'level': level,
            'strike': hedge_strike,
            'premium': hedge_premium
        }

    def close_hedge(self, hedge_exit_premium: float) -> Dict:
        """Close hedge - PAPER VERSION (MATCHES LIVE BEHAVIOR)"""
        if not self.hedge_active:
            return {}

        # Calculate hedge P&L (SELL hedge)
        hedge_pnl = (self.hedge_entry_premium - hedge_exit_premium) * self.lot_size
        self.realized_hedge_pnl += hedge_pnl

        print(f"\n🔄 {self.name} L{self.hedge_level} HEDGE CLOSED (REVERSAL)")
        print(f"   Entry: ₹{self.hedge_entry_premium:.2f}, Exit: ₹{hedge_exit_premium:.2f}")
        print(f"   Hedge P&L: ₹{hedge_pnl:,.2f}")

        # ✅ CRITICAL FIX: DO NOT mark level as completed on reversal exit
        # This allows re-entry of same level if price moves against us again
        # (Matches live trading behavior)

        exited_level = self.hedge_level

        # Reset hedge state
        self.hedge_active = False
        self.hedge_level = 0
        self.hedge_symbol = None
        self.hedge_security_id = None
        self.hedge_strike = None
        self.hedge_entry_premium = None
        self.hedge_current_premium = None

        # ✅ KEEP SAME NEXT STOP LOSS (don't increment level)
        # Next stop loss remains same as before hedge entry
        # E.g., if L1 exited, next_stop_loss_pct stays at 20%

        return {
            'action': 'HEDGE_CLOSE',
            'leg': self.name,
            'level': exited_level,
            'pnl': hedge_pnl
        }

    def is_level3_triggered(self) -> bool:
        """Check if Level 3 hard stop triggered"""
        return (self.current_loss_pct >= config.LEVEL_3_HARD_STOP and
                not self.hedge_active)

    def get_pnl(self) -> float:
        """Get total P&L - PAPER VERSION"""
        # Leg P&L (we SOLD the option)
        leg_pnl = (self.entry_premium - self.current_premium) * self.lot_size

        # Current hedge P&L (if active)
        current_hedge_pnl = 0.0
        if self.hedge_active and self.hedge_current_premium:
            current_hedge_pnl = (self.hedge_entry_premium - self.hedge_current_premium) * self.lot_size

        # Total P&L
        total_pnl = leg_pnl + self.realized_hedge_pnl + current_hedge_pnl

        return total_pnl

    def get_total_side_premium(self, include_hedge: bool = True) -> float:
        """Total premium on this side"""
        total = self.current_premium

        if include_hedge and self.hedge_active and self.hedge_current_premium:
            total += self.hedge_current_premium

        return total

    def get_next_level(self) -> int:
        """Get next hedge level number"""
        for i in range(1, 3):  # Only Level 1 and 2
            if i not in self.completed_levels:
                return i
        return 3  # Level 3 (hard stop)
