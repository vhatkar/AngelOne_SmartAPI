"""
Paper Trading Position Tracker
Simulates broker positions in memory
"""

from datetime import datetime
from config_paper import config


class PaperPositions:
    def __init__(self):
        self.positions = {}  # {security_id: {'qty': -75, 'symbol': '...', 'entry_price': 150.0}}
        self.trade_log = []

    def execute_order(self, transaction_type, symbol, security_id, quantity, price):
        """Simulate order execution"""
        qty_change = -quantity if transaction_type == 'SELL' else quantity

        if security_id in self.positions:
            self.positions[security_id]['qty'] += qty_change
            if self.positions[security_id]['qty'] == 0:
                del self.positions[security_id]
        else:
            self.positions[security_id] = {
                'qty': qty_change,
                'symbol': symbol,
                'entry_price': price
            }

        self.trade_log.append({
            'time': datetime.now(),
            'type': transaction_type,
            'symbol': symbol,
            'qty': quantity,
            'price': price
        })

        print(f"[PAPER] {transaction_type} {quantity} {symbol} @ ₹{price:.2f}")
        return True

    def get_positions(self):
        """Get current positions"""
        return self.positions

    def calculate_pnl(self, current_prices):
        """Calculate unrealized P&L"""
        total_pnl = 0
        for sec_id, pos in self.positions.items():
            current_price = current_prices.get(sec_id, pos['entry_price'])
            pnl = (pos['entry_price'] - current_price) * abs(pos['qty'])
            total_pnl += pnl
        return total_pnl

    def clear_all(self):
        """Close all positions"""
        self.positions = {}


paper_positions = PaperPositions()
