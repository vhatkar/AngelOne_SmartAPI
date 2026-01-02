"""
Simple CSV Logger for Paper Trading
Minimal logging - just trade entries and exits
"""

import csv
import os
from datetime import datetime


class SimpleLogger:
    def __init__(self):
        self.filename = "paper_trades.csv"
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        """Create CSV with headers if doesn't exist"""
        if not os.path.exists(self.filename):
            with open(self.filename, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'Date', 'Time', 'Event', 'Strike',
                    'CE_Premium', 'PE_Premium', 'Total_Premium',
                    'Exit_Reason', 'CE_PnL', 'PE_PnL', 'Total_PnL',
                    'Duration_Minutes'
                ])

    def log_entry(self, strike, ce_premium, pe_premium):
        """Log straddle entry"""
        with open(self.filename, 'a', newline='') as f:
            writer = csv.writer(f)
            now = datetime.now()
            writer.writerow([
                now.strftime('%Y-%m-%d'),
                now.strftime('%H:%M:%S'),
                'ENTRY',
                strike,
                f"{ce_premium:.2f}",
                f"{pe_premium:.2f}",
                f"{ce_premium + pe_premium:.2f}",
                '', '', '', '', ''
            ])
        print(f"✅ Logged entry to {self.filename}")

    def log_exit(self, strike, exit_reason, ce_pnl, pe_pnl, total_pnl, duration_minutes):
        """Log straddle exit"""
        with open(self.filename, 'a', newline='') as f:
            writer = csv.writer(f)
            now = datetime.now()
            writer.writerow([
                now.strftime('%Y-%m-%d'),
                now.strftime('%H:%M:%S'),
                'EXIT',
                strike,
                '', '', '',
                exit_reason,
                f"{ce_pnl:.2f}",
                f"{pe_pnl:.2f}",
                f"{total_pnl:.2f}",
                f"{duration_minutes:.1f}"
            ])
        print(f"✅ Logged exit to {self.filename}")

    def log_hedge(self, leg_name, level, strike, premium, action='ENTRY'):
        """Log hedge events"""
        with open(self.filename, 'a', newline='') as f:
            writer = csv.writer(f)
            now = datetime.now()
            writer.writerow([
                now.strftime('%Y-%m-%d'),
                now.strftime('%H:%M:%S'),
                f'HEDGE_{action}',
                strike,
                f"{leg_name}_L{level}",
                f"{premium:.2f}",
                '', '', '', '', '', ''
            ])


logger = SimpleLogger()
