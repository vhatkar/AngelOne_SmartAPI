"""
bot_controller.py - Trading Bot Control Menu
Handles Ctrl+C signal and provides interactive menu
Simplified: ONLY Ctrl+C, no other keyboard shortcuts
"""

import signal
import sys
import time
from datetime import datetime
from config import config
from angelone_api import api


class BotController:
    """
    Handles Ctrl+C signal and provides interactive control menu
    Separates UI/control logic from trading logic
    """
    
    def __init__(self, trader_instance):
        """
        Initialize controller with reference to trader
        
        Args:
            trader_instance: LiveTrader instance to control
        """
        self.trader = trader_instance
        self.menu_active = False
        
        # Register Ctrl+C handler
        signal.signal(signal.SIGINT, self._signal_handler)
        print("✅ Bot Controller initialized - Press Ctrl+C anytime for menu")
    
    def _signal_handler(self, sig, frame):
        """
        Handle Ctrl+C signal - show interactive menu
        This pauses the main trading loop
        """
        # Set menu active flags
        self.menu_active = True
        self.trader.menu_active = True
        
        try:
            self._show_menu()
        finally:
            # Resume trading loop
            self.menu_active = False
            self.trader.menu_active = False
    
    def _show_menu(self):
        """Display and handle interactive control menu"""
        while True:
            print("\n" + "=" * 80)
            print("🎮 TRADING CONTROL MENU (Ctrl+C)")
            print("=" * 80)
            print()
            print("📊 QUICK ACTIONS")
            print("   P - Check P&L (Display positions & profit/loss)")
            print("   S - Sync Positions (Force reconciliation with broker)")
            print("   R - Re-login (Manual authentication refresh)")
            print("   H - Help (Show this menu)")
            print("   C - Continue Trading (Resume operation)")
            print()
            print("🛡️ HEDGE CONTROL")
            print("   1 - Skip CE level (Prevent hedge at next CE level)")
            print("   2 - Skip PE level (Prevent hedge at next PE level)")
            print("   3 - Force SELL CE hedge (Manual CE hedge entry)")
            print("   4 - Force SELL PE hedge (Manual PE hedge entry)")
            print("   5 - Force EXIT CE hedge (Close active CE hedge)")
            print("   6 - Force EXIT PE hedge (Close active PE hedge)")
            print("   7 - Display hedge status (Detailed hedge view)")
            print()
            print("🚪 EXIT")
            print("   0 - EXIT OPTIONS (Stop trading & handle positions)")
            print()
            print("=" * 80)
            
            try:
                choice = input("➤ Choice: ").strip().upper()
                
                if choice == 'P':
                    self._handle_check_pl()
                elif choice == 'S':
                    self._handle_sync_positions()
                elif choice == 'R':
                    self._handle_relogin()
                elif choice == 'H':
                    continue  # Redisplay menu
                elif choice == 'C' or choice == '':
                    # 🔥 FIX 1: Set flags BEFORE returning to prevent 2-minute delay
                    self.menu_active = False
                    self.trader.menu_active = False
                    print("▶️ Resuming trading...")
                    return
                elif choice == '1':
                    self._handle_skip_ce_level()
                elif choice == '2':
                    self._handle_skip_pe_level()
                elif choice == '3':
                    self._handle_force_sell_ce()
                elif choice == '4':
                    self._handle_force_sell_pe()
                elif choice == '5':
                    self._handle_force_exit_ce()
                elif choice == '6':
                    self._handle_force_exit_pe()
                elif choice == '7':
                    self._handle_display_hedge_status()
                elif choice == '0':
                    self._handle_exit_options()
                    return
                else:
                    print(f"❌ Invalid choice: {choice}")
                    # No input wait - just show menu again
                    
            except KeyboardInterrupt:
                print("\n🚨 FORCE EXIT - Ctrl+C pressed again!")
                print("⚠️ CHECK BROKER TERMINAL FOR OPEN POSITIONS!")
                sys.exit(0)
            except Exception as e:
                print(f"❌ Error: {e}")
                print("▶️ Resuming trading...")
                return
    
    # ========================================
    # Menu Action Handlers
    # ========================================
    
    def _handle_check_pl(self):
        """Display P&L and positions"""
        print("\n" + "=" * 80)
        print("💰 CURRENT P&L")
        print("=" * 80)
        if self.trader.straddle_manager.straddle_active:
            self.trader._display_status()
        else:
            print("📭 No active positions yet")
        # 🔥 FIX 2: No input wait - returns to menu immediately
    
    def _handle_sync_positions(self):
        """Force position reconciliation"""
        print("\n" + "=" * 80)
        print("🔄 MANUAL SYNC REQUESTED")
        print("=" * 80)
        if self.trader:
            self.trader._safe_reconcile("Manual Sync from Ctrl+C Menu")
            print("✅ Sync complete!")
        else:
            print("❌ Trading system not initialized yet")
        # 🔥 FIX 2: No input wait - returns to menu immediately
    
    def _handle_relogin(self):
        """Manual re-login to refresh session"""
        print("\n" + "=" * 80)
        print("🔐 MANUAL RE-LOGIN")
        print("=" * 80)
        print("⚠️ This will:")
        print("   • Disconnect current session")
        print("   • Refresh authentication tokens")
        print("   • Reconnect WebSockets")
        print()
        confirm = input("Type YES to re-login: ").strip().upper()
        
        if confirm == 'YES':
            print("\n🔄 Re-logging in...")
            if api.login():
                print("✅ Re-login successful!")
                print("✅ Fresh session established")
            else:
                print("❌ Re-login failed!")
                print("⚠️ Check credentials and try again")
        else:
            print("❌ Cancelled")
        # 🔥 FIX 2: No input wait - returns to menu immediately
    
    def _handle_skip_ce_level(self):
        """Skip CE's next level"""
        if self.trader.straddle_manager.straddle_active:
            self.trader._action_skip_ce_level()
        else:
            print("❌ No active straddle")
        # 🔥 FIX 2: No input wait - returns to menu immediately
    
    def _handle_skip_pe_level(self):
        """Skip PE's next level"""
        if self.trader.straddle_manager.straddle_active:
            self.trader._action_skip_pe_level()
        else:
            print("❌ No active straddle")
        # 🔥 FIX 2: No input wait - returns to menu immediately
    
    def _handle_force_sell_ce(self):
        """Force sell CE hedge"""
        if self.trader.straddle_manager.straddle_active:
            self.trader._action_force_sell_ce()
        else:
            print("❌ No active straddle")
        # 🔥 FIX 2: No input wait - returns to menu immediately
    
    def _handle_force_sell_pe(self):
        """Force sell PE hedge"""
        if self.trader.straddle_manager.straddle_active:
            self.trader._action_force_sell_pe()
        else:
            print("❌ No active straddle")
        # 🔥 FIX 2: No input wait - returns to menu immediately
    
    def _handle_force_exit_ce(self):
        """Force exit CE hedge"""
        if self.trader.straddle_manager.straddle_active:
            self.trader._action_force_exit_ce()
        else:
            print("❌ No active straddle")
        # 🔥 FIX 2: No input wait - returns to menu immediately
    
    def _handle_force_exit_pe(self):
        """Force exit PE hedge"""
        if self.trader.straddle_manager.straddle_active:
            self.trader._action_force_exit_pe()
        else:
            print("❌ No active straddle")
        # 🔥 FIX 2: No input wait - returns to menu immediately
    
    def _handle_display_hedge_status(self):
        """Display detailed hedge status"""
        if self.trader.straddle_manager.straddle_active:
            self.trader._display_hedge_status()
        else:
            print("❌ No active straddle")
        # 🔥 FIX 2: No input wait - returns to menu immediately
    
    def _handle_exit_options(self):
        """Handle exit menu"""
        print("\n" + "=" * 80)
        print("🚪 EXIT OPTIONS")
        print("=" * 80)
        print()
        print("1 - AUTO SQUARE-OFF (Let script close all positions)")
        print("    • Immediate exit with retry logic")
        print("    • Full Excel logging")
        print("    • Best for fast exit")
        print()
        print("2 - SAFE EXIT (I will close manually from broker)")
        print("    • Full control over exit timing & prices")
        print("    • Can leg out strategically")
        print("    • Must manually close ALL positions")
        print()
        print("C - Cancel (Return to trading)")
        print()
        print("=" * 80)
        
        exit_choice = input("➤ Choice (1/2/C): ").strip().upper()
        
        if exit_choice == '1':
            # Auto square-off
            if self.trader.straddle_manager.straddle_active:
                print("\n" + "=" * 80)
                print("🔄 AUTO SQUARE-OFF INITIATED")
                print("=" * 80)
                self.trader.process_force_exit_and_reentry("Manual Stop - Auto Square-Off")
                print("✅ All positions closed successfully")
            else:
                print("✅ No active positions")
            
            print("👋 Exiting script...")
            sys.exit(0)
            
        elif exit_choice == '2':
            # Safe exit
            if self.trader.straddle_manager.straddle_active:
                self.trader._display_exit_summary()
                
                ce = self.trader.straddle_manager.ce_leg
                pe = self.trader.straddle_manager.pe_leg
                
                print("\n" + "=" * 80)
                print("🛑 SAFE EXIT - Positions remain OPEN")
                print("=" * 80)
                print("\n📋 CHECKLIST - Close from Angel One terminal:")
                print(f"   ✓ Close CE: {ce.symbol}")
                print(f"   ✓ Close PE: {pe.symbol}")
                if ce.hedge_active:
                    print(f"   ✓ Close CE Hedge (L{ce.hedge_level}): {ce.hedge_symbol}")
                if pe.hedge_active:
                    print(f"   ✓ Close PE Hedge (L{pe.hedge_level}): {pe.hedge_symbol}")
                print("=" * 80)
            else:
                print("✅ No active positions")
            
            print("👋 Exiting script...")
            sys.exit(0)
            
        elif exit_choice == 'C':
            print("❌ Cancelled - Resuming trading...")
            return
        else:
            print("❌ Invalid choice - Resuming trading...")
            return