"""
Position Reconciler - ENHANCED with Manual Hedge Detection & Sync
✅ Detects manual hedge additions
✅ Detects manual hedge exits
✅ Auto-syncs script state with broker
✅ Smart level detection algorithm
✅ HYBRID: Updated for 2-level price-neutral + Level 3 hard stop
✅ FIXED: Correct hedge identification for price-neutral strategy
✅ CRITICAL FIX: Corrected all LONG/SHORT sign errors (7 bugs fixed)
"""

from typing import Dict, List, Optional, Tuple
from angelone_api import api
from config import config
import time
from datetime import datetime


class PositionReconciler:
    """Reconciles expected vs actual positions with manual hedge support"""

    def __init__(self):
        self.last_reconciliation_time = None
        self.discrepancy_count = 0
        self.max_allowed_discrepancies = 3
        self.reconciliation_interval = 300  # 5 minutes in seconds

        # Event-driven reconciliation
        self.pending_reconciliation = False
        self.last_order_fill_time = None
        self.reconciliation_cooldown = 5  # Wait 5s after order fill

        # 🔥 NEW: Manual intervention tracking
        self.manual_changes_detected = []
        self.last_manual_sync_time = None

    def should_reconcile(self) -> bool:
        """Check if reconciliation needed"""
        # Priority 1: Pending reconciliation after order fill
        if self.pending_reconciliation:
            if self.last_order_fill_time:
                elapsed = time.time() - self.last_order_fill_time
                if elapsed >= self.reconciliation_cooldown:
                    return True
            else:
                return True

        # Priority 2: Fallback periodic check
        if self.last_reconciliation_time is None:
            return True

        elapsed = (config.get_current_ist_time() - self.last_reconciliation_time).total_seconds()
        return elapsed >= self.reconciliation_interval

    def mark_order_filled(self):
        """Mark that an order was filled - triggers reconciliation"""
        self.pending_reconciliation = True
        self.last_order_fill_time = time.time()
        print(f"   📌 Reconciliation scheduled (after order fill)")

    def get_actual_positions(self) -> List[Dict]:
        """Fetch actual positions from broker"""
        try:
            positions = api.get_positions(force_refresh=True)

            if positions:
                print(f"📋 Fetched {len(positions)} actual positions from broker")

            return positions

        except Exception as e:
            print(f"❌ Error fetching positions: {str(e)}")
            return []

    def build_expected_positions(self, straddle_manager, hedge_manager) -> Dict[str, int]:
        """Build expected positions dictionary"""
        expected = {}
        try:
            # Straddle CE leg
            if straddle_manager.ce_leg and straddle_manager.straddle_active:
                expected[str(straddle_manager.ce_leg.security_id)] = -straddle_manager.ce_leg.lot_size

            # Straddle PE leg
            if straddle_manager.pe_leg and straddle_manager.straddle_active:
                expected[str(straddle_manager.pe_leg.security_id)] = -straddle_manager.pe_leg.lot_size

            # CE Hedge - SELL creates SHORT position (FIXED SIGN)
            if straddle_manager.ce_leg and straddle_manager.ce_leg.hedge_active:
                expected[str(straddle_manager.ce_leg.hedge_security_id)] = -straddle_manager.ce_leg.lot_size  # ✅ CORRECT

            # PE Hedge - SELL creates SHORT position (FIXED SIGN)
            if straddle_manager.pe_leg and straddle_manager.pe_leg.hedge_active:
                expected[str(straddle_manager.pe_leg.hedge_security_id)] = -straddle_manager.pe_leg.lot_size  # ✅ CORRECT

            return expected
        except Exception as e:
            print(f"❌ Error building expected positions: {str(e)}")
            return {}

    def detect_manual_hedge_level(self, leg, current_loss_pct: float) -> Optional[int]:
        """
        🔥 NEW: Smart level detection for manually added hedges

        Args:
            leg: The leg object (CE or PE)
            current_loss_pct: Current loss percentage

        Returns:
            Detected level (1-2) or None if all levels used
        """
        completed = leg.completed_levels
        all_levels = config.PROGRESSIVE_HEDGING_LEVELS  # 🔥 HYBRID: [20, 40]

        # Find available levels (not completed)
        available = [i + 1 for i, pct in enumerate(all_levels)
                     if (i + 1) not in completed]

        if not available:
            print(f"   ⚠️ All levels already used for {leg.name}")
            return None

        # Find closest level to current loss
        closest = min(available,
                      key=lambda lvl: abs(all_levels[lvl - 1] - abs(current_loss_pct)))

        print(f"   🎯 Detected manual hedge level: {closest} (Loss: {current_loss_pct:.1f}%)")
        return closest

    def identify_hedge_positions(self, actual_positions: List[Dict],
                                 straddle_manager) -> Tuple[List[str], List[str]]:
        """
        🔥 CORRECTED: Identify which actual positions are hedges vs straddle legs
        
        PRICE-NEUTRAL LOGIC:
        - CE leg losing (market UP) → SELL PE hedge on profit side
        - PE leg losing (market DOWN) → SELL CE hedge on profit side
        
        Returns:
            (ce_hedge_ids, pe_hedge_ids) where:
            - ce_hedge_ids: PE positions (SHORT) = hedges for CE leg
            - pe_hedge_ids: CE positions (SHORT) = hedges for PE leg
        """
        ce_hedge_ids = []  # PE positions (SHORT) = hedges for CE leg
        pe_hedge_ids = []  # CE positions (SHORT) = hedges for PE leg

        if not straddle_manager.straddle_active:
            return ce_hedge_ids, pe_hedge_ids

        straddle_strike = straddle_manager.strike

        for pos in actual_positions:
            sec_id = str(pos.get('securityId', ''))
            qty = pos.get('netQty', 0)
            symbol = pos.get('tradingsymbol', '')

            # BUG #1 FIXED: Skip if quantity is zero (no position)
            if qty == 0:
                continue

            # BUG #2 FIXED: SHORT positions are potential hedges (we SELL hedges)
            if qty < 0:
                try:
                    # Parse strike from symbol (format: NIFTY25NOV2525900CE)
                    if 'CE' in symbol:
                        strike_str = symbol.split('CE')[0][-5:]  # Last 5 digits before CE
                        hedge_strike = int(strike_str)
                        
                        # CE position (SHORT) = Hedge for PE leg (when PE losing, market DOWN)
                        # Only identify as hedge if it's NOT the straddle strike
                        if hedge_strike != straddle_strike:
                            pe_hedge_ids.append(sec_id)
                            # BUG #4 FIXED: Updated print statement
                            print(f"   🛡️ Identified PE leg hedge (CE SHORT): {symbol} (Strike: {hedge_strike})")

                    elif 'PE' in symbol:
                        strike_str = symbol.split('PE')[0][-5:]  # Last 5 digits before PE
                        hedge_strike = int(strike_str)
                        
                        # PE position (SHORT) = Hedge for CE leg (when CE losing, market UP)
                        # Only identify as hedge if it's NOT the straddle strike
                        if hedge_strike != straddle_strike:
                            ce_hedge_ids.append(sec_id)
                            # BUG #5 FIXED: Updated print statement
                            print(f"   🛡️ Identified CE leg hedge (PE SHORT): {symbol} (Strike: {hedge_strike})")
                            
                except Exception as e:
                    print(f"   ⚠️ Could not parse strike from {symbol}: {e}")
                    pass

        return ce_hedge_ids, pe_hedge_ids

    def reconcile(self, straddle_manager, hedge_manager) -> Dict:
        """
        🔥 ENHANCED: Reconcile with manual hedge detection and auto-sync
        """
        try:
            print("\n" + "=" * 80)
            print("🔍 POSITION RECONCILIATION (WITH MANUAL HEDGE DETECTION)")
            print("=" * 80)

            # Build expected positions
            expected = self.build_expected_positions(straddle_manager, hedge_manager)
            if not expected:
                print("⚠️ No expected positions to reconcile")
                return {
                    'matched': True,
                    'critical': False,
                    'manual_changes': [],
                    'all_positions_closed': False
                }

            print(f"\n📊 Expected Positions: {len(expected)}")
            for sec_id, qty in expected.items():
                print(f"   {sec_id}: {'+' if qty > 0 else ''}{qty}")

            # Fetch actual positions
            time.sleep(1)
            actual_positions = self.get_actual_positions()

            # Build actual positions dict
            actual = {}
            for pos in actual_positions:
                sec_id = str(pos.get('securityId', ''))
                net_qty = pos.get('netQty', 0)
                if sec_id and net_qty != 0:
                    actual[sec_id] = net_qty

            print(f"\n📋 Actual Positions: {len(actual)}")
            for sec_id, qty in actual.items():
                print(f"   {sec_id}: {'+' if qty > 0 else ''}{qty}")

            # 🔥 NEW: Identify hedge positions with CORRECTED logic
            ce_hedge_ids, pe_hedge_ids = self.identify_hedge_positions(
                actual_positions, straddle_manager
            )

            # Compare and detect manual changes
            missing = []
            extra = []
            manual_changes = []
            matched = True
            critical = False

            # Check for missing positions
            for sec_id, expected_qty in expected.items():
                actual_qty = actual.get(sec_id, 0)
                if actual_qty != expected_qty:
                    matched = False
                    missing.append({
                        'security_id': sec_id,
                        'expected': expected_qty,
                        'actual': actual_qty,
                        'difference': expected_qty - actual_qty
                    })

                    # BUG #6 FIXED: Detect manual hedge exit (expected SHORT, got nothing)
                    if expected_qty < 0 and actual_qty == 0:
                        # Expected SHORT hedge but it's gone
                        change = self._detect_manual_hedge_exit(
                            sec_id, straddle_manager
                        )
                        if change:
                            manual_changes.append(change)
                        critical = True

            # Check for extra positions (manual additions)
            for sec_id, actual_qty in actual.items():
                if sec_id not in expected:
                    matched = False
                    extra.append({
                        'security_id': sec_id,
                        'quantity': actual_qty
                    })

                    # BUG #7 FIXED: Detect manual hedge addition (SHORT position)
                    if actual_qty < 0:  # SHORT position
                        if sec_id in ce_hedge_ids:
                            change = self._detect_manual_hedge_addition(
                                sec_id, 'CE', actual_positions, straddle_manager
                            )
                            if change:
                                manual_changes.append(change)
                        elif sec_id in pe_hedge_ids:
                            change = self._detect_manual_hedge_addition(
                                sec_id, 'PE', actual_positions, straddle_manager
                            )
                            if change:
                                manual_changes.append(change)

            # Check if all positions manually closed
            all_positions_closed = False
            if expected and not actual:
                print("🚨 MANUAL INTERVENTION: All positions closed manually!")
                all_positions_closed = True
                critical = True

            # 🔥 FIX #2: Auto-sync manual hedge changes
            manual_hedge_changes = []
            
            # Check for manually exited CE hedge
            if (straddle_manager.ce_leg and 
                straddle_manager.ce_leg.hedge_active and
                not all_positions_closed):
                
                ce_hedge_id = str(straddle_manager.ce_leg.hedge_security_id)
                if ce_hedge_id not in actual:  # Hedge missing from broker
                    print(f"\n🔧 AUTO-SYNC: CE hedge L{straddle_manager.ce_leg.hedge_level} manually exited from broker")
                    exited_level = straddle_manager.ce_leg.hedge_level
                    straddle_manager.ce_leg.sync_manual_hedge_exit(exited_level)
                    
                    manual_hedge_changes.append({
                        'leg': 'CE',
                        'action': 'EXIT',
                        'level': exited_level
                    })
            
            # Check for manually exited PE hedge
            if (straddle_manager.pe_leg and 
                straddle_manager.pe_leg.hedge_active and
                not all_positions_closed):
                
                pe_hedge_id = str(straddle_manager.pe_leg.hedge_security_id)
                if pe_hedge_id not in actual:  # Hedge missing from broker
                    print(f"\n🔧 AUTO-SYNC: PE hedge L{straddle_manager.pe_leg.hedge_level} manually exited from broker")
                    exited_level = straddle_manager.pe_leg.hedge_level
                    straddle_manager.pe_leg.sync_manual_hedge_exit(exited_level)
                    
                    manual_hedge_changes.append({
                        'leg': 'PE',
                        'action': 'EXIT',
                        'level': exited_level
                    })

            # 🔥 NEW: Apply manual changes to script state
            if manual_changes:
                print(f"\n{'=' * 80}")
                print(f"🔄 APPLYING {len(manual_changes)} MANUAL CHANGE(S)")
                print(f"{'=' * 80}")

                for change in manual_changes:
                    self._apply_manual_change(change, straddle_manager)

                self.manual_changes_detected.extend(manual_changes)
                self.last_manual_sync_time = config.get_current_ist_time()

            # Summary
            print("\n" + "=" * 80)
            if matched and not all_positions_closed:
                print("✅ POSITIONS MATCHED - All good!")
            else:
                print("⚠️ POSITION MISMATCH DETECTED")
                if missing:
                    print(f"\n🔴 Missing/Incorrect ({len(missing)}):")
                    for m in missing:
                        print(f"   {m['security_id']}: Expected {m['expected']}, Got {m['actual']}")
                if extra:
                    print(f"\n🟡 Extra Positions ({len(extra)}):")
                    for e in extra:
                        print(f"   {e['security_id']}: {e['quantity']}")
                if manual_changes:
                    print(f"\n🔄 Manual Changes Applied: {len(manual_changes)}")
                if all_positions_closed:
                    print("\n🚨 ALL POSITIONS MANUALLY CLOSED!")
                elif critical:
                    print("\n🚨 CRITICAL MISMATCH - ACTION REQUIRED!")
            print("=" * 80)

            # Update stats
            self.last_reconciliation_time = config.get_current_ist_time()
            if not matched and not manual_changes:  # Only count as discrepancy if not manual
                self.discrepancy_count += 1
            else:
                self.discrepancy_count = 0

            self.pending_reconciliation = False

            return {
                'matched': matched,
                'critical': critical,
                'expected_positions': expected,
                'actual_positions': actual,
                'missing_positions': missing,
                'extra_positions': extra,
                'all_positions_closed': all_positions_closed,
                'manual_hedge_changes': manual_hedge_changes  # 🔥 FIX #2: Added
            }

        except Exception as e:
            print(f"\n❌ Error during reconciliation: {str(e)}")
            import traceback
            traceback.print_exc()
            return {
                'matched': False,
                'critical': True,
                'manual_changes': [],
                'all_positions_closed': False
            }

    def _detect_manual_hedge_exit(self, security_id: str,
                                  straddle_manager) -> Optional[Dict]:
        """🔥 NEW: Detect manual hedge exit"""
        # Check CE leg
        if (straddle_manager.ce_leg and
                straddle_manager.ce_leg.hedge_active and
                str(straddle_manager.ce_leg.hedge_security_id) == security_id):
            return {
                'action': 'MANUAL_HEDGE_EXIT',
                'leg_type': 'CE',
                'security_id': security_id,
                'level': straddle_manager.ce_leg.hedge_level,
                'symbol': straddle_manager.ce_leg.hedge_symbol
            }

        # Check PE leg
        if (straddle_manager.pe_leg and
                straddle_manager.pe_leg.hedge_active and
                str(straddle_manager.pe_leg.hedge_security_id) == security_id):
            return {
                'action': 'MANUAL_HEDGE_EXIT',
                'leg_type': 'PE',
                'security_id': security_id,
                'level': straddle_manager.pe_leg.hedge_level,
                'symbol': straddle_manager.pe_leg.hedge_symbol
            }

        return None

    def _detect_manual_hedge_addition(self, security_id: str, leg_type: str,
                                      actual_positions: List[Dict],
                                      straddle_manager) -> Optional[Dict]:
        """🔥 NEW: Detect manual hedge addition"""
        # Find the position details
        pos_details = None
        for pos in actual_positions:
            if str(pos.get('securityId')) == security_id:
                pos_details = pos
                break

        if not pos_details:
            return None

        symbol = pos_details.get('tradingsymbol', '')

        # Get the corresponding leg
        leg = straddle_manager.ce_leg if leg_type == 'CE' else straddle_manager.pe_leg

        if not leg:
            return None

        # Detect level
        detected_level = self.detect_manual_hedge_level(leg, leg.current_loss_pct)

        if not detected_level:
            return None

        return {
            'action': 'MANUAL_HEDGE_ADDITION',
            'leg_type': leg_type,
            'security_id': security_id,
            'symbol': symbol,
            'level': detected_level,
            'current_loss_pct': leg.current_loss_pct
        }

    def _apply_manual_change(self, change: Dict, straddle_manager):
        """🔥 NEW: Apply manual change to script state"""
        action = change['action']
        leg_type = change['leg_type']

        leg = straddle_manager.ce_leg if leg_type == 'CE' else straddle_manager.pe_leg

        if not leg:
            return

        if action == 'MANUAL_HEDGE_ADDITION':
            print(f"\n   🔄 Syncing manual {leg_type} hedge addition...")
            leg.sync_manual_hedge_addition(
                hedge_symbol=change['symbol'],
                hedge_security_id=change['security_id'],
                hedge_level=change['level'],
                entry_loss_pct=change['current_loss_pct']
            )
            print(f"   ✅ {leg_type} hedge L{change['level']} synced to script state")

        elif action == 'MANUAL_HEDGE_EXIT':
            print(f"\n   🔄 Syncing manual {leg_type} hedge exit...")
            leg.sync_manual_hedge_exit(
                exited_level=change['level']
            )
            print(f"   ✅ {leg_type} hedge L{change['level']} exit synced to script state")

    def get_manual_changes_summary(self) -> str:
        """🔥 NEW: Get summary of manual changes"""
        if not self.manual_changes_detected:
            return "No manual interventions detected"

        summary = f"{len(self.manual_changes_detected)} manual change(s):\n"
        for change in self.manual_changes_detected:
            action = change['action'].replace('_', ' ').title()
            summary += f"  - {action}: {change['leg_type']} L{change['level']}\n"

        return summary