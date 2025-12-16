"""
Angel One API Wrapper - SIMPLIFIED REACTIVE AUTHENTICATION
✅ ONE LOGIN PER DAY (JWT valid 24-28 hours)
✅ REACTIVE RE-LOGIN ONLY ON AB1007/auth errors
✅ NO PROACTIVE REFRESHES
✅ NO JWT REFRESH THREADS
✅ SIMPLIFIED & STABLE
✅ CRITICAL OPERATION LOCKS
✅ WEB SOCKET AUTO-RECONNECT
"""

import pyotp
import time
import json
import os
import requests
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2
from SmartApi.smartWebSocketOrderUpdate import SmartWebSocketOrderUpdate
from config import config
import threading


# ============================================================================
# ANGEL ONE ERROR CODES - Simplified reactive error handling
# ============================================================================
ANGEL_ERROR_CODES = {
    # ALL Authentication Errors → Full re-login
    'AB1007': {
        'type': 'AUTH',
        'message': 'Login ID/password invalid or AMX session expired',
        'action': 'FULL_RELOGIN',
        'severity': 'CRITICAL'
    },
    'AB1010': {
        'type': 'AUTH',
        'message': 'AMX session expired',
        'action': 'FULL_RELOGIN',
        'severity': 'CRITICAL'
    },
    'AB1003': {
        'type': 'AUTH',
        'message': 'Concurrent login detected',
        'action': 'FULL_RELOGIN',
        'severity': 'CRITICAL'
    },
    
    # JWT Token Errors → Full re-login (NO JWT refresh)
    'AG8001': {
        'type': 'JWT',
        'message': 'Invalid JWT token',
        'action': 'FULL_RELOGIN',
        'severity': 'CRITICAL'
    },
    'AG8002': {
        'type': 'JWT',
        'message': 'JWT token expired',
        'action': 'FULL_RELOGIN',
        'severity': 'CRITICAL'
    },
    'AG8003': {
        'type': 'JWT',
        'message': 'JWT token validation failed',
        'action': 'FULL_RELOGIN',
        'severity': 'CRITICAL'
    },
    
    # Rate Limiting
    'AB2001': {
        'type': 'RATE_LIMIT',
        'message': 'Rate limit exceeded',
        'action': 'WAIT_AND_RETRY',
        'severity': 'MEDIUM'
    },
    
    # Server/System Errors
    'AB1004': {
        'type': 'SERVER_ERROR',
        'message': 'Something went wrong, please try after sometime',
        'action': 'RETRY',
        'severity': 'MEDIUM'
    },
    
    # Order/Trading Errors
    'AB1008': {
        'type': 'ORDER_ERROR',
        'message': 'Invalid order variety',
        'action': 'CHECK_PARAMS',
        'severity': 'HIGH'
    },
    'AB1035': {
        'type': 'PASSWORD_ERROR',
        'message': 'Login password has expired',
        'action': 'ALERT_USER',
        'severity': 'CRITICAL'
    },
}


class AngelOneAPI:
    """Production-ready Angel One API wrapper - Simplified reactive authentication"""

    def __init__(self):
        """Initialize API connection"""
        self.smart_api = None
        self.feed_token = None
        self.is_connected = False
        
        # ✅ Store auth token with Bearer prefix
        self.auth_token_string = None

        # ✅ WebSocket attributes
        self.ws_enabled = False
        self.market_ws = None  # SmartWebSocketV2 for market data
        self.order_ws = None  # SmartWebSocketOrderUpdate for order updates

        # Order tracking for WebSocket notifications
        self.order_fill_callbacks = {}  # {order_id: callback_function}
        self.order_fill_events = {}  # {order_id: threading.Event()}
        self.order_statuses = {}  # {order_id: status}

        # Scrip master
        self.scrip_master = {}
        self.scrip_master_file = "OpenAPIScripMaster.json"
        self.scrip_master_url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

        # ✅ WebSocket token cache with timestamps
        self.token_cache = {}  # token -> {'ltp': float, 'timestamp': float}
        self.token_cache_ttl = 60  # Cache validity: 60 seconds

        # ✅ Advanced rate limiting per operation
        self.last_api_call_time = {}  # operation_type -> timestamp

        # NIFTY spot fallback
        self.nifty_spot_failures = 0
        self.use_futures_for_spot = False
        self.nifty_futures_token = None

        # Rate limiting
        self.connection_failures = 0
        self.max_connection_failures = 3

        # 🔥 Position cache for optimization
        self.position_cache = []
        self.position_cache_time = 0
        self.position_cache_ttl = 30  # 30 seconds TTL

        # 🔥 NEW: Critical operation lock
        self.critical_operation_lock = threading.Lock()
        self.critical_operation_in_progress = False

        # Load scrip master
        self._load_scrip_master()

    def acquire_critical_lock(self, operation_name: str):
        """Acquire lock before critical operations"""
        print(f"🔒 Acquiring lock for: {operation_name}")
        self.critical_operation_lock.acquire()
        self.critical_operation_in_progress = True
        print(f"✅ Lock acquired: {operation_name}")

    def release_critical_lock(self, operation_name: str):
        """Release lock after critical operations"""
        self.critical_operation_in_progress = False
        self.critical_operation_lock.release()
        print(f"🔓 Lock released: {operation_name}")

    def _rate_limit(self, delay: float = 0.5):
        """Enforce rate limiting between API calls"""
        elapsed = time.time() - self.last_api_call_time.get('default', 0)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self.last_api_call_time['default'] = time.time()

    def _advanced_rate_limit(self, operation_type: str = "default"):
        """
        🔥 Advanced rate limiting with per-operation tracking
        """
        rate_limits = {
            "order": 0.12,          # 10 orders/sec
            "position": 1.0,        # Position API - conservative
            "ltp": 0.35,            # Individual LTP fetch
            "batch_ltp": 2.5,       # Batch LTP - very conservative
            "fill_price": 1.0,      # Fill price fetch
            "default": 0.5          # Default conservative
        }
        
        delay = rate_limits.get(operation_type, rate_limits["default"])
        
        # Get last call time for this operation type
        last_call = self.last_api_call_time.get(operation_type, 0)
        elapsed = time.time() - last_call
        
        if elapsed < delay:
            sleep_time = delay - elapsed
            print(f"   ⏱️ Rate limiting [{operation_type}]: waiting {sleep_time:.2f}s")
            time.sleep(sleep_time)
        
        self.last_api_call_time[operation_type] = time.time()

    def _handle_api_error(self, error_msg: str, error_code: str = None) -> tuple:
        """
        🔥 SIMPLIFIED REACTIVE ERROR HANDLING
        Full re-login on ANY auth error, NO JWT refresh
        
        Returns:
            tuple: (recovery_successful: bool, should_retry: bool)
        """
        # Extract error code if not provided
        if not error_code:
            import re
            match = re.search(r"'errorcode':\s*'([^']+)'", error_msg)
            if match:
                error_code = match.group(1)
        
        print(f"\n{'='*70}")
        print(f"⚠️ API ERROR")
        print(f"{'='*70}")
        
        # 🔥 ALL authentication errors → Full re-login
        if error_code in ['AB1007', 'AB1010', 'AB1003', 'AG8001', 'AG8002', 'AG8003']:
            print(f"   Authentication error ({error_code}) detected")
            print(f"   🔄 Performing full re-login...")
            success = self.login()
            
            if success:
                # 🔥 FIX 5: Invalidate caches after re-login
                print("🧹 Clearing stale caches...")
                self.invalidate_position_cache()
                self.token_cache.clear()
                
                # Let WebSocket stabilize
                time.sleep(3)
                
                print("✅ Re-login complete, caches cleared")
            
            return (success, True)  # Always retry after re-login
        
        # Rate limit → Wait and retry
        elif error_code == 'AB2001':
            print(f"   Rate limit hit, waiting 60 seconds...")
            time.sleep(60)
            return (True, True)
        
        # Server error → Wait and retry
        elif error_code == 'AB1004':
            print(f"   Server error, waiting 5 seconds...")
            time.sleep(5)
            return (True, True)
        
        # Other errors → Don't retry automatically
        else:
            print(f"   Non-critical error: {error_msg[:100]}")
            return (False, False)

    def _load_scrip_master(self):
        """Load or download scrip master with 24hr cache"""
        try:
            # Check if file exists and is recent
            should_download = True
            if os.path.exists(self.scrip_master_file):
                file_age = time.time() - os.path.getmtime(self.scrip_master_file)
                if file_age < 86400:  # 24 hours
                    should_download = False
                    print(f"📂 Using cached scrip master (age: {file_age / 3600:.1f} hours)")

            # Download if needed
            if should_download:
                print(f"⬇️ Downloading scrip master from Angel One...")
                response = requests.get(self.scrip_master_url, timeout=30)
                response.raise_for_status()

                with open(self.scrip_master_file, 'w') as f:
                    f.write(response.text)
                print(f"✅ Downloaded fresh scrip master")

            # Load from file
            print(f"📖 Loading scrip master...")
            with open(self.scrip_master_file, 'r') as f:
                data = json.load(f)

            # Filter for NFO (options) only and NIFTY
            count = 0
            for scrip in data:
                if scrip.get('exch_seg') == 'NFO':
                    symbol = scrip.get('symbol', '')
                    if 'NIFTY' in symbol and not any(x in symbol for x in ['BANK', 'FINN', 'MIDCP', 'NXT']):
                        token = scrip.get('token')
                        if symbol and token:
                            # Convert strike from paise to rupees
                            strike_paise = scrip.get('strike', '0')
                            strike = float(strike_paise) / 100 if strike_paise else 0

                            self.scrip_master[symbol] = {
                                'token': token,
                                'name': scrip.get('name', ''),
                                'expiry': scrip.get('expiry', ''),
                                'strike': int(strike),
                                'lotsize': scrip.get('lotsize', '')
                            }
                            count += 1

            print(f"✅ Loaded {count} NIFTY option contracts")

        except Exception as e:
            print(f"⚠️ Error loading scrip master: {str(e)}")
            print("   Will use searchScrip API fallback")

    def _setup_market_websocket_callbacks(self):
        """Setup callbacks for market data WebSocket"""

        def on_data(wsapp, message):
            """Handle market data updates"""
            try:
                # Message format from SmartWebSocketV2
                if isinstance(message, dict):
                    token = message.get('token')
                    ltp = message.get('last_traded_price')

                    if token and ltp:
                        # LTP is in paise, convert to rupees
                        ltp_rupees = ltp / 100.0

                        # ✅ Update token cache (for get_ltp fallback)
                        self.token_cache[token] = {
                            'ltp': ltp_rupees,
                            'timestamp': time.time()
                        }
            except Exception as e:
                print(f"⚠️ Market WS data error: {e}")

        def on_open(wsapp):
            """Market WebSocket connected"""
            print("✅ Market WebSocket V2 connected")

        def on_error(wsapp, error):
            """Market WebSocket error"""
            print(f"⚠️ Market WebSocket error: {error}")

        def on_close(wsapp):
            """Market WebSocket closed"""
            print("⚠️ Market WebSocket closed")

        # Assign callbacks
        self.market_ws.on_data = on_data
        self.market_ws.on_open = on_open
        self.market_ws.on_error = on_error
        self.market_ws.on_close = on_close

    def _setup_order_websocket_callbacks(self):
        """✅ Setup callbacks for order update WebSocket"""

        def on_message(wsapp, message):
            """Handle order update messages"""
            try:
                # Messages come as STRING
                if isinstance(message, str):
                    data = json.loads(message)
                elif isinstance(message, dict):
                    data = message
                else:
                    return

                # Order data is nested in 'orderData' key
                order_data = data.get('orderData', {})
                
                # Check if this is initial connection message
                if not order_data or not order_data.get('orderid'):
                    if data.get('status-code') == '200':
                        print(f"✅ Order WebSocket authenticated (User: {data.get('user-id')})")
                    return

                # Extract order info
                order_id = str(order_data.get('orderid', ''))
                order_status = order_data.get('orderstatus', '').lower()

                if not order_id:
                    return

                # Normalize status
                status_map = {
                    'complete': 'TRADED',
                    'rejected': 'REJECTED',
                    'cancelled': 'CANCELLED',
                    'open': 'PENDING',
                    'trigger pending': 'PENDING',
                    'open pending': 'PENDING'
                }
                normalized_status = status_map.get(order_status, order_status.upper())

                # Store status
                self.order_statuses[order_id] = normalized_status
                print(f"📢 Order Update: {order_id} - {normalized_status}")

                # Trigger event if waiting
                if order_id in self.order_fill_events:
                    if normalized_status in ['TRADED', 'REJECTED', 'CANCELLED']:
                        self.order_fill_events[order_id].set()
                        print(f"   🔔 Event triggered for final status: {normalized_status}")

                # Call callback if registered
                if order_id in self.order_fill_callbacks:
                    callback = self.order_fill_callbacks[order_id]
                    callback(order_id, normalized_status)

            except json.JSONDecodeError as e:
                print(f"⚠️ Order WS JSON parse error: {e}")
            except Exception as e:
                print(f"⚠️ Order WS message error: {e}")

        def on_open(wsapp):
            """Order WebSocket connected"""
            print("✅ Order WebSocket connected")

        def on_error(wsapp, error):
            """Order WebSocket error"""
            print(f"⚠️ Order WebSocket error: {error}")

        def on_close(wsapp, close_status_code=None, close_msg=None):
            """Order WebSocket closed"""
            print(f"⚠️ Order WebSocket closed (code: {close_status_code})")

        # Assign callbacks
        self.order_ws.on_message = on_message
        self.order_ws.on_open = on_open
        self.order_ws.on_error = on_error
        self.order_ws.on_close = on_close

    def login(self) -> bool:
        """
        🎯 ELEGANT RE-LOGIN: Update tokens in existing WebSockets
        Credit: Pravin's brilliant insight - work WITH the library, not against it! 🏆
        """
        if self.critical_operation_in_progress:
            print("⚠️ Critical operation in progress, deferring re-login...")
            return False
        
        if not self.critical_operation_lock.acquire(blocking=False):
            print("⚠️ Another login in progress, skipping...")
            return False
        
        try:
            print("\n" + "=" * 80)
            print("[LOGIN] Logging into Angel One...")
            
            # Generate new session
            self.smart_api = SmartConnect(api_key=config.API_KEY)
            totp = pyotp.TOTP(config.TOTP_SECRET).now()
            
            data = self.smart_api.generateSession(
                clientCode=config.CLIENT_ID,
                password=config.PASSWORD,
                totp=totp
            )
            
            if data['status']:
                # Get new tokens
                new_feed_token = data['data']['feedToken']
                jwt_token = data['data']['jwtToken']
                
                if not jwt_token.startswith('Bearer '):
                    new_auth_token = f"Bearer {jwt_token}"
                else:
                    new_auth_token = jwt_token
                
                # 🔥 PROVEN SOLUTION: Update tokens in existing WebSockets
                # WebSocket will use NEW tokens in all future requests!
                if self.market_ws:
                    print("🔄 Updating market WebSocket tokens...")
                    self.market_ws.auth_token = new_auth_token
                    self.market_ws.feed_token = new_feed_token
                    print("   ✅ Market WS now using NEW token")
                
                if self.order_ws:
                    print("🔄 Updating order WebSocket tokens...")
                    self.order_ws.auth_token = new_auth_token
                    self.order_ws.feed_token = new_feed_token
                    print("   ✅ Order WS now using NEW token")
                
                # Update our stored tokens
                self.auth_token_string = new_auth_token
                self.feed_token = new_feed_token
                self.is_connected = True
                
                print(f"✅ Login successful - Session valid until ~{config.get_current_ist_time() + timedelta(hours=24)}")
                print(f"   🧵 ZERO NEW THREADS - Existing threads updated with new tokens!")
                print(f"   No refresh needed for your 6.5-hour trading session")
                
                # Only initialize WebSockets if they don't exist yet (first login)
                if not self.market_ws or not self.order_ws:
                    print("📡 Creating WebSockets for first time...")
                    self._initialize_websockets()
                
                return True
            else:
                print(f"❌ Login failed: {data.get('message', 'Unknown error')}")
                return False
        
        except Exception as e:
            print(f"❌ Login error: {str(e)}")
            return False
        
        finally:
            self.critical_operation_lock.release()

    def _initialize_websockets(self):
        """Initialize WebSockets after login"""
        try:
            print("📡 Initializing WebSockets...")
            
            # Market WebSocket
            self.market_ws = SmartWebSocketV2(
                auth_token=self.auth_token_string,
                api_key=config.API_KEY,
                client_code=config.CLIENT_ID,
                feed_token=self.feed_token,
                max_retry_attempt=3,
                retry_delay=5
            )
            self._setup_market_websocket_callbacks()
            market_thread = threading.Thread(target=self.market_ws.connect, daemon=True)
            market_thread.start()
            
            # Subscribe to NIFTY spot
            time.sleep(2)
            self._subscribe_nifty_spot()
            
            # Order WebSocket (optional)
            try:
                self.order_ws = SmartWebSocketOrderUpdate(
                    self.auth_token_string,
                    config.API_KEY,
                    config.CLIENT_ID,
                    self.feed_token
                )
                self._setup_order_websocket_callbacks()
                order_thread = threading.Thread(target=self.order_ws.connect, daemon=True)
                order_thread.start()
                print("✅ Order WebSocket initialized")
            except Exception as e:
                print(f"⚠️ Order WebSocket failed: {e}")
                self.order_ws = None
            
            self.ws_enabled = True
            print("✅ WebSockets ready")
            
        except Exception as e:
            print(f"⚠️ WebSocket initialization failed: {e}")
            self.ws_enabled = False

    def _subscribe_nifty_spot(self):
        """Subscribe to NIFTY 50 spot using Official WebSocket V2"""
        try:
            if not self.market_ws:
                return

            # NIFTY 50 spot token
            token_list = [
                {
                    "exchangeType": 1,  # NSE_CM for indices
                    "tokens": ["99926000"]  # NIFTY 50
                }
            ]

            self.market_ws.subscribe(
                correlation_id="nifty_spot",
                mode=1,  # LTP mode
                token_list=token_list
            )

            print("✅ Subscribed to NIFTY 50 spot (WebSocket V2)")

        except Exception as e:
            print(f"⚠️ NIFTY subscription failed: {e}")

    def check_websocket_health(self) -> bool:
        """
        ✅ FIX 3: WebSocket auto-reconnect with health check
        Check WebSocket health and auto-reconnect if stale
        """
        if not self.ws_enabled or not self.market_ws:
            return False
        
        # Check if we've received recent data
        if self.token_cache:
            try:
                most_recent = max(
                    (data['timestamp'] for data in self.token_cache.values()),
                    default=0
                )
                
                # Data within last 60 seconds = healthy
                if time.time() - most_recent < 60:
                    return True
                
                # Stale data - reconnect
                print("⚠️ WebSocket data stale (>60s), reconnecting...")
                
            except Exception as e:
                print(f"⚠️ Error checking WebSocket health: {e}")
        
        # No recent data or error - reconnect
        try:
            self._reconnect_websockets()
            return True
        except Exception as e:
            print(f"❌ WebSocket reconnect failed: {e}")
            return False

    def _reconnect_websockets(self):
        """✅ FIX 3: Reconnect WebSockets after health check failure"""
        print("🔄 Reconnecting WebSockets...")
        
        # Close existing connections
        try:
            if self.market_ws:
                self.market_ws.close_connection()
            if self.order_ws:
                self.order_ws.close_connection()
        except:
            pass
        
        time.sleep(2)
        
        # Reinitialize
        self._initialize_websockets()
        
        print("✅ WebSocket reconnected")

    def get_token_from_master(self, trading_symbol: str) -> Optional[str]:
        """Get token from scrip master file"""
        if trading_symbol in self.scrip_master:
            return self.scrip_master[trading_symbol]['token']
        return None

    def _fetch_token_from_api(self, trading_symbol: str, exchange: str = "NFO") -> Optional[str]:
        """Fallback: Fetch token using searchScrip API"""
        try:
            self._advanced_rate_limit('ltp')
            result = self.smart_api.searchScrip(exchange, trading_symbol)

            if result and result.get('status') and result.get('data'):
                data = result['data']
                if len(data) > 0:
                    return data[0]['symboltoken']
            return None
        except Exception as e:
            print(f"   ⚠️ searchScrip failed: {e}")
            return None

    def search_scrip(self, symbol: str, strike: int, option_type: str) -> Optional[Dict]:
        """
        Create option symbol in Angel One format
        Tries 6+ format variations to find the correct one
        """
        try:
            expiry = config.EXPIRY_DATE  # e.g., "04NOV25"

            # Parse expiry components
            day = expiry[:2]
            month = expiry[2:5]
            year = expiry[5:]

            # Format variations to try
            formats_to_try = [
                f"NIFTY{expiry}{strike}{option_type}",  # NIFTY04NOV2426000CE
                f"NIFTY{day}{month}{year}{strike}{option_type}",  # NIFTY04NOV2426000CE
                f"NIFTY{month}{year}{strike}{option_type}",  # NIFTYNOV2426000CE (monthly)
                f"NIFTY{year}{month[0]}{day}{strike}{option_type}",  # NIFTY24N0426000CE (compressed)
                f"NIFTY{day}{month}{year}C{strike}",  # NIFTY04NOV24C26000
                f"NIFTY{day}{month}{year[:2]}{strike}{option_type}",  # NIFTY04NOV2426000CE
            ]

            # Try each format and check if it exists in scrip master
            for fmt in formats_to_try:
                if fmt in self.scrip_master:
                    scrip_data = self.scrip_master[fmt]
                    return {
                        'symbol': fmt,
                        'security_id': scrip_data['token'],
                        'trading_symbol': fmt,
                        'strike': strike,
                        'option_type': option_type,
                        'lot_size': int(scrip_data['lotsize'])
                    }

            # If not found in master, try searchScrip API with first format
            print(f"   ⚠️ {strike}{option_type} not in master, trying API...")
            first_format = formats_to_try[0]
            token = self._fetch_token_from_api(first_format)

            if token:
                return {
                    'symbol': first_format,
                    'security_id': token,
                    'trading_symbol': first_format,
                    'strike': strike,
                    'option_type': option_type,
                    'lot_size': config.LOT_SIZE
                }

            return None

        except Exception as e:
            print(f"   ❌ Error creating symbol: {str(e)}")
            return None

    def get_spot_price(self, max_retries: int = 3) -> Optional[float]:
        """Get NIFTY spot price with WebSocket V2-first, REST fallback"""

        # If already using futures, continue with that
        if self.use_futures_for_spot:
            return self._get_spot_from_futures(max_retries)

        # ✅ Try WebSocket V2 first (fastest)
        if self.ws_enabled and self.market_ws:
            # Check token cache (populated by WebSocket callbacks)
            if "99926000" in self.token_cache:
                cache_data = self.token_cache["99926000"]
                # Check if data is fresh (less than 60 seconds old)
                if time.time() - cache_data['timestamp'] < 60:
                    ws_spot = cache_data['ltp']
                    print(f"📈 NIFTY: {ws_spot:.2f} (WebSocket V2)")
                    self.nifty_spot_failures = 0
                    self.connection_failures = 0
                    return ws_spot

        # Fallback to REST API
        for attempt in range(max_retries):
            try:
                if not config.is_market_open():
                    return None

                self._advanced_rate_limit('ltp')

                spot_data = self.smart_api.ltpData("NSE", "NIFTY 50", "99926000")

                if spot_data and spot_data.get('status'):
                    ltp = float(spot_data['data']['ltp'])
                    print(f"📈 NIFTY: {ltp:.2f} (REST API)")
                    self.nifty_spot_failures = 0
                    self.connection_failures = 0
                    return ltp

                if attempt < max_retries - 1:
                    time.sleep(3)
                    continue

            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"   ⚠️ Spot price retry {attempt + 1}/{max_retries}...")
                    time.sleep(5)
                    if attempt == max_retries - 2:
                        self.reconnect_if_needed()

        # Failed to get spot
        self.nifty_spot_failures += 1
        print(f"   ⚠️ Spot failures: {self.nifty_spot_failures}/5")

        # Switch to futures after 5 failures
        if self.nifty_spot_failures >= 5:
            print(f"   🔄 Switching to NIFTY Futures for spot price")
            self.use_futures_for_spot = True
            return self._get_spot_from_futures(max_retries)

        return None

    def _get_spot_from_futures(self, max_retries: int = 3) -> Optional[float]:
        """Get spot price from NIFTY current month futures"""
        # TODO: Implement futures-based spot price
        # For now, retry spot
        print(f"   ℹ️ Futures fallback not yet implemented, retrying spot")
        self.use_futures_for_spot = False
        self.nifty_spot_failures = 0
        return None

    def get_ltp(self, security_id: str) -> Optional[float]:
        """✅ Get LTP with WebSocket V2-first, REST fallback with smart caching"""
        # ✅ Try WebSocket V2 first (fastest)
        if self.ws_enabled and self.market_ws:
            # Check token cache (WebSocket data)
            if security_id in self.token_cache:
                cache_data = self.token_cache[security_id]
                # Check if data is fresh (less than 60 seconds old)
                if time.time() - cache_data['timestamp'] < self.token_cache_ttl:
                    return cache_data['ltp']
        
        # Fallback to REST API with rate limiting
        try:
            if not config.is_market_open():
                return None
            
            self._advanced_rate_limit("ltp")
            
            response = self.smart_api.ltpData("NFO", security_id, security_id)
            
            if response and response.get('status'):
                fetched = response.get('data', {}).get('fetched')
                if fetched and len(fetched) > 0:
                    ltp = float(fetched[0].get('ltp', 0))
                    # Update cache for future use
                    self.token_cache[security_id] = {
                        'ltp': ltp,
                        'timestamp': time.time()
                    }
                    return ltp
        except Exception as e:
            print(f"❌ Error fetching LTP for {security_id}: {e}")
        
        return None

    def get_ltp_with_retry(self, security_id: str, max_retries: int = 5) -> Optional[float]:
        """Get LTP with aggressive retry logic"""
        for attempt in range(max_retries):
            ltp = self.get_ltp(security_id)

            if ltp is not None:
                return ltp

            if attempt < max_retries - 1:
                wait_time = 3 if attempt == 0 else 10
                print(f"   ⚠️ LTP retry {attempt + 1}/{max_retries} in {wait_time}s...")
                time.sleep(wait_time)

                if attempt >= 1:
                    self.reconnect_if_needed()

        print(f"   ❌ Could not fetch LTP after {max_retries} attempts")
        return None

    def subscribe_instruments_to_websocket(self, instruments: List[Dict]):
        """Subscribe instruments to WebSocket V2 for real-time updates"""
        try:
            if not self.ws_enabled or not self.market_ws:
                return

            # Group by exchange type
            nfo_tokens = []

            for inst in instruments:
                security_id = inst.get('SecurityId') or inst.get('security_id')
                if security_id:
                    nfo_tokens.append(security_id)

            if nfo_tokens:
                token_list = [
                    {
                        "exchangeType": 2,  # NSE_FO
                        "tokens": nfo_tokens
                    }
                ]

                self.market_ws.subscribe(
                    correlation_id=f"options_{int(time.time())}",
                    mode=1,  # LTP mode
                    token_list=token_list
                )

                print(f"✅ Subscribed {len(nfo_tokens)} instruments to WebSocket V2")

        except Exception as e:
            print(f"⚠️ WebSocket subscription failed: {e}")

    def place_order(self, transaction_type: str, symbol: str, security_id: str,
                    quantity: int, order_type: str = "MARKET", price: float = 0) -> Dict:
        """
        🔥 Place live order with full response
        Returns immediate order status without extra API call
        """
        try:
            self._advanced_rate_limit('order')

            print(f"   📤 {transaction_type} {symbol}")

            order_params = {
                "variety": "NORMAL",
                "tradingsymbol": symbol,
                "symboltoken": security_id,
                "transactiontype": transaction_type,
                "exchange": "NFO",
                "ordertype": order_type,
                "producttype": "INTRADAY",
                "duration": "DAY",
                "quantity": str(quantity),
                "price": str(price) if order_type == "LIMIT" else "0",
                "squareoff": "0",
                "stoploss": "0"
            }

            # 🔥 USE FULL RESPONSE METHOD for immediate status
            response = self.smart_api.placeOrderFullResponse(order_params)

            if not response:
                return {
                    'success': False,
                    'order_id': None,
                    'message': 'No response from API',
                    'order_status': None,
                    'raw_response': None
                }

            # Handle dictionary response
            if isinstance(response, dict):
                status = response.get('status', False)
                
                if not status:
                    error_msg = response.get('message', 'Unknown error')
                    error_code = response.get('errorcode')
                    print(f"   ❌ Order failed: {error_msg} (Code: {error_code})")
                    return {
                        'success': False,
                        'order_id': None,
                        'message': error_msg,
                        'error_code': error_code,
                        'order_status': None,
                        'raw_response': response
                    }

                data = response.get('data', {})
                order_id = data.get('orderid')
                
                # 🔥 EXTRACT IMMEDIATE ORDER STATUS
                order_status = data.get('orderstatus', data.get('status', 'UNKNOWN'))

                if not order_id:
                    print(f"   ❌ No order ID in response")
                    return {
                        'success': False,
                        'order_id': None,
                        'message': 'No order ID in successful response',
                        'order_status': order_status,
                        'raw_response': response
                    }

                # 🔥 LOG IMMEDIATE STATUS
                print(f"   ✅ Order placed: {order_id} | Immediate status: {order_status}")

                return {
                    'success': True,
                    'order_id': str(order_id),
                    'message': 'Order placed successfully',
                    'order_status': order_status,
                    'raw_response': response
                }

            # 🔥 Handle string response (direct order_id)
            if isinstance(response, str):
                print(f"   ✅ Order placed (direct ID): {response}")
                return {
                    'success': True,
                    'order_id': response,
                    'message': 'Order placed successfully',
                    'order_status': 'PENDING',
                    'raw_response': response
                }
            
            # Unknown response type
            return {
                'success': False,
                'order_id': None,
                'message': f'Invalid response type: {type(response)}',
                'order_status': None,
                'raw_response': response
            }

        except Exception as e:
            print(f"   ❌ Exception: {str(e)}")
            return {
                'success': False,
                'order_id': None,
                'message': f'Exception: {str(e)}',
                'order_status': None,
                'raw_response': None
            }

    def get_order_fill_price(self, order_id: str) -> Optional[float]:
        """
        🔥 Get actual fill price from order book
        
        Args:
            order_id: Order ID to fetch
            
        Returns:
            Actual average fill price, or None if not found
        """
        try:
            self._advanced_rate_limit('fill_price')
            
            # Fetch order book
            response = self.smart_api.orderBook()
            
            if not response or not response.get('status'):
                print(f"⚠️ Could not fetch order book for fill price")
                return None
            
            # Find the specific order
            orders = response.get('data', [])
            
            for order in orders:
                if str(order.get('orderid')) == str(order_id):
                    # Extract average price (actual fill price)
                    avg_price = order.get('averageprice')
                    
                    if avg_price:
                        fill_price = float(avg_price)
                        print(f"   💰 Actual fill price: ₹{fill_price:.2f}")
                        return fill_price
                    else:
                        # Try tradedprice as fallback
                        traded_price = order.get('price')
                        if traded_price:
                            fill_price = float(traded_price)
                            print(f"   💰 Actual fill price: ₹{fill_price:.2f}")
                            return fill_price
            
            print(f"⚠️ Order {order_id} not found in order book")
            return None
            
        except Exception as e:
            print(f"⚠️ Error fetching fill price for {order_id}: {e}")
            return None

    def verify_order_fill_websocket(self, order_id: str, order_status: str = None, max_wait: int = 30) -> bool:
        """
        🔥 Verify order fill - WebSocket ONLY
        
        Args:
            order_id: Order ID to verify
            order_status: Immediate status from placeOrderFullResponse (if available)
            max_wait: Maximum wait time in seconds
            
        Returns:
            bool: True if order filled, False otherwise
        """
        try:
            # 🔥 PRIORITY 1: Check immediate status from placeOrderFullResponse
            if order_status:
                status_lower = order_status.lower()
                
                if status_lower in ['complete', 'traded', 'filled']:
                    print(f"   ✅ Order FILLED (immediate status from place response)")
                    return True
                    
                elif status_lower in ['rejected', 'cancelled']:
                    print(f"   ❌ Order {status_lower.upper()} (immediate status)")
                    return False
                
                # If status is 'PENDING' or 'OPEN', continue to WebSocket verification
                print(f"   ⏳ Order status: {order_status} - waiting for fill...")
            
            # 🔥 PRIORITY 2: Check if WebSocket ALREADY received notification
            if order_id in self.order_statuses:
                status = self.order_statuses[order_id]
                print(f"   ⚡ Found existing status from WebSocket: {status}")
                
                if status == 'TRADED':
                    print(f"   ✅ Order FILLED (WebSocket already notified before we started waiting!)")
                    # Cleanup
                    del self.order_statuses[order_id]
                    return True
                elif status in ['REJECTED', 'CANCELLED']:
                    print(f"   ❌ Order {status}")
                    # Cleanup
                    del self.order_statuses[order_id]
                    return False
            
            # 🔥 PRIORITY 3: WebSocket hasn't notified yet, wait for it
            if self.ws_enabled and self.order_ws:
                print(f"   ⚡ Waiting for WebSocket fill notification...")
                
                # Create event for this order
                self.order_fill_events[order_id] = threading.Event()
                
                # Wait for event (with timeout)
                filled = self.order_fill_events[order_id].wait(timeout=max_wait)
                
                # Check final status
                if filled and order_id in self.order_statuses:
                    status = self.order_statuses[order_id]
                    
                    if status == 'TRADED':
                        print(f"   ✅ Order FILLED (WebSocket notification)")
                        return True
                    elif status in ['REJECTED', 'CANCELLED']:
                        print(f"   ❌ Order {status}")
                        return False
                    else:
                        print(f"   ⚠️ Event set but status is '{status}', continuing to wait...")
                
                if not filled:
                    print(f"   ⚠️ WebSocket timeout after {max_wait}s")
                    
                    # 🔥 FINAL CHECK: Maybe notification came right after timeout
                    if order_id in self.order_statuses:
                        status = self.order_statuses[order_id]
                        if status == 'TRADED':
                            print(f"   ✅ Order FILLED (found in final check)")
                            return True
                    
                    print(f"   ❌ No TRADED status received - order may still be pending")
                    return False
            
            # No WebSocket - can't verify
            print(f"   ⚠️ WebSocket not available - cannot verify order")
            return False

        except Exception as e:
            print(f"   ❌ Verification error: {e}")
            return False
            
        finally:
            # Cleanup
            if order_id in self.order_fill_events:
                del self.order_fill_events[order_id]
            if order_id in self.order_statuses:
                del self.order_statuses[order_id]

    def verify_order_fill(self, order_id: str, max_retries: int = 15, retry_delay: int = 3) -> bool:
        """
        🔥 Verify if order gets filled using Individual Order API
        """
        for attempt in range(max_retries):
            self._advanced_rate_limit('order')
            
            try:
                order_status = self.get_order_status(order_id)
                
                if not order_status:
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
                    return False
                
                # Log status periodically
                if attempt == 0 or attempt % 3 == 0:
                    print(f"   📋 Order {order_id}: {order_status}")
                
                # Check final status
                if order_status == 'TRADED':
                    print(f"   ✅ Order FILLED!")
                    return True
                elif order_status in ['REJECTED', 'CANCELLED']:
                    print(f"   ❌ Order {order_status}")
                    return False
                elif order_status == 'PENDING':
                    time.sleep(retry_delay)
                    continue
                else:
                    print(f"   ⚠️ Unknown status: {order_status}")
                    time.sleep(retry_delay)
                    
            except Exception as e:
                print(f"   ⚠️ Error checking order: {e}")
                time.sleep(retry_delay)
        
        print(f"   ⏰ Timeout: Order not filled after {max_retries} attempts")
        return False

    def place_order_with_verification(self, transaction_type: str, symbol: str,
                                      security_id: str, quantity: int,
                                      order_type: str = "MARKET", price: float = 0,
                                      is_critical: bool = False,
                                      max_wait: int = None) -> Dict:
        """
        🔥 FIX 2: Simple order placement with reactive error handling
        Uses _handle_api_error() for all error recovery
        """
        # Set max_wait based on critical flag if not provided
        if max_wait is None:
            max_wait = 60 if is_critical else 30
        
        max_attempts = 3 if is_critical else 1

        for attempt in range(max_attempts):
            try:
                # 🔥 Place order with full response
                place_result = self.place_order(
                    transaction_type, symbol, security_id, quantity, order_type, price
                )

                # 🔥 Check for errors using _handle_api_error()
                if not place_result['success']:
                    error_msg = place_result.get('message', '')
                    error_code = place_result.get('error_code')
                    
                    # Use the centralized error handler
                    recovery_success, should_retry = self._handle_api_error(error_msg, error_code)
                    
                    if not should_retry or attempt >= max_attempts - 1:
                        return {
                            'success': False,
                            'order_id': None,
                            'message': error_msg,
                            'filled': False,
                            'raw_response': place_result['raw_response']
                        }
                    
                    # Retry after recovery
                    print(f"  🔄 Retrying order after recovery (attempt {attempt + 2}/{max_attempts})...")
                    time.sleep(10 if is_critical else 5)
                    continue

                # Order placed successfully
                order_id = place_result['order_id']
                order_status = place_result.get('order_status')

                # 🔥 Verify with immediate status passed
                filled = self.verify_order_fill_websocket(
                    order_id, 
                    order_status=order_status,
                    max_wait=max_wait
                )

                if filled or not is_critical:
                    # 🔥 Invalidate position cache after order fill
                    self.invalidate_position_cache()
                    
                    return {
                        'success': True,
                        'order_id': order_id,
                        'message': 'Order filled' if filled else 'Order not filled',
                        'filled': filled,
                        'raw_response': place_result['raw_response']
                    }

                # Not filled but is critical - retry
                if is_critical and attempt < max_attempts - 1:
                    wait_time = 5
                    print(f"   ⚠️ Critical order not filled, retry {attempt + 2}/{max_attempts} in {wait_time}s...")
                    time.sleep(wait_time)
                    continue

            except Exception as e:
                error_msg = str(e)
                print(f"   ❌ Exception during order placement: {error_msg}")
                
                # Try to handle auth errors in exception too
                recovery_success, should_retry = self._handle_api_error(error_msg)
                
                if should_retry and attempt < max_attempts - 1:
                    time.sleep(5)
                    continue
                
                return {
                    'success': False,
                    'order_id': None,
                    'message': f'Exception: {error_msg}',
                    'filled': False,
                    'raw_response': None
                }

        # All attempts exhausted
        return {
            'success': False,
            'order_id': None,
            'message': f'Order failed after {max_attempts} attempts',
            'filled': False,
            'raw_response': None
        }

    def get_batch_ltp(self, security_ids: List[str], exchange: str = "NFO") -> Dict[str, float]:
        """
        ✅ Batch LTP fetch with empty response detection
        """
        try:
            if not security_ids:
                return {}
            
            # Remove duplicates
            unique_ids = list(set(security_ids))
            
            # Apply rate limiting BEFORE making the call
            self._advanced_rate_limit("batch_ltp")
            
            print(f"   🚀 Batch LTP request: {len(unique_ids)} instruments from {exchange}")
            
            try:
                response = self.smart_api.getMarketData("LTP", unique_ids)
                
                # 🔥 Detect empty response (token expiry)
                if not response:
                    raise Exception("Empty response - likely token expiry")
                
                if isinstance(response, bytes) and response == b'':
                    raise Exception("Empty JSON response - token expired")
                
                if isinstance(response, str) and response == '':
                    raise Exception("Empty string response - token expired")
                
                if not response.get('status'):
                    err_msg = response.get('message', 'Unknown error')
                    
                    if 'parse' in err_msg.lower() or 'json' in err_msg.lower():
                        raise Exception(f"JSON parse error - likely token expiry: {err_msg}")
                    
                    print(f"   ⚠️ Batch LTP failed: {err_msg}")
                    return {}
                
                # Extract LTPs
                ltp_dict = {}
                data = response.get('data', {})
                fetched = data.get('fetched', [])
                
                for item in fetched:
                    if isinstance(item, dict):
                        token = item.get('symboltoken') or item.get('token')
                        ltp = item.get('ltp') or item.get('lastPrice')
                        if token and ltp:
                            ltp_dict[str(token)] = float(ltp)
                            # Update cache
                            self.token_cache[str(token)] = {
                                'ltp': float(ltp),
                                'timestamp': time.time()
                            }
                
                success_count = len(ltp_dict)
                total_count = len(unique_ids)
                print(f"   ✅ Batch LTP: Fetched {success_count}/{total_count} instruments in 1 call")
                
                return ltp_dict
                
            except Exception as batch_error:
                error_str = str(batch_error).lower()
                
                # 🔥 Handle empty response / token expiry
                if any(keyword in error_str for keyword in ['empty', 'json', 'parse', 'token', 'expired']):
                    print("🔄 Empty response detected - re-login...")
                    
                    # Re-login
                    if self.login():
                        print("✅ Re-login successful - Retrying batch LTP...")
                        time.sleep(2)
                        
                        # Single retry after re-login
                        try:
                            response = self.smart_api.getMarketData("LTP", unique_ids)
                            
                            if response and response.get('status'):
                                # Process retry response
                                ltp_dict = {}
                                data = response.get('data', {})
                                fetched = data.get('fetched', [])
                                
                                for item in fetched:
                                    if isinstance(item, dict):
                                        token = item.get('symboltoken') or item.get('token')
                                        ltp = item.get('ltp') or item.get('lastPrice')
                                        if token and ltp:
                                            ltp_dict[str(token)] = float(ltp)
                                
                                print(f"✅ Retry successful: {len(ltp_dict)} LTPs fetched")
                                return ltp_dict
                        except:
                            pass
                            
                print(f"❌ Batch LTP error: {batch_error}")
                return {}
                
        except Exception as e:
            print(f"❌ Batch LTP with fallback error: {e}")
            return {}

    def get_batch_ltp_with_fallback(self, security_ids: List[str], exchange: str = "NFO") -> Dict[str, float]:
        """
        ✅ Batch LTP with WebSocket-first, automatic fallback to API calls
        """
        try:
            ltp_dict = {}
            missing_ids = []
            
            # STEP 1: Check WebSocket cache first
            for security_id in security_ids:
                if security_id in self.token_cache:
                    cache_data = self.token_cache[security_id]
                    if time.time() - cache_data.get('timestamp', 0) < self.token_cache_ttl:
                        ltp_dict[security_id] = cache_data['ltp']
                    else:
                        missing_ids.append(security_id)
                else:
                    missing_ids.append(security_id)
            
            # If all data from cache, return immediately
            if not missing_ids:
                print(f"   ✅ All {len(ltp_dict)} LTPs from WebSocket cache (0 API calls)")
                return ltp_dict
            
            print(f"   📊 WebSocket cache: {len(ltp_dict)}/{len(security_ids)} | Fetching {len(missing_ids)} from API")
            
            # STEP 2: Try batch fetch for missing data
            if len(missing_ids) > 0:
                batch_ltps = self.get_batch_ltp(missing_ids, exchange)
                
                if batch_ltps:
                    ltp_dict.update(batch_ltps)
                    still_missing = [sid for sid in missing_ids if sid not in batch_ltps]
                else:
                    still_missing = missing_ids
                
                # STEP 3 - Individual fallback for remaining strikes
                if still_missing:
                    print(f"   🔄 Batch failed for {len(still_missing)} strikes, trying individual calls...")
                    
                    for i, sid in enumerate(still_missing):
                        try:
                            ltp = self.get_ltp(sid)
                            if ltp:
                                ltp_dict[sid] = ltp
                                print(f"     ✅ Individual fetch {i+1}/{len(still_missing)}: {sid}")
                            time.sleep(0.3)  # Rate limit between individual calls
                        except Exception as e:
                            print(f"     ❌ Failed to fetch {sid}: {e}")
                            continue
            
            return ltp_dict
            
        except Exception as e:
            print(f"❌ Batch LTP with fallback error: {e}")
            return {}

    def get_order_status(self, order_id: str) -> Optional[str]:
        """
        🔥 Get single order status using orderBook API
        """
        try:
            self._advanced_rate_limit('order')
            
            # ✅ Use orderBook() to get all orders
            response = self.smart_api.orderBook()
            
            if not response:
                return None
            
            if not response.get('status'):
                return None
            
            # Parse response data
            orders = response.get('data', [])
            
            if not isinstance(orders, list):
                return None
            
            # Find the specific order
            for order in orders:
                if str(order.get('orderid')) == str(order_id):
                    # Extract order status
                    order_status = order.get('orderstatus', '').lower()
                    
                    if not order_status:
                        order_status = order.get('status', '').lower()
                    
                    if not order_status:
                        return None
                    
                    # Normalize status to DHAN format
                    status_map = {
                        'complete': 'TRADED',
                        'rejected': 'REJECTED',
                        'cancelled': 'CANCELLED',
                        'open': 'PENDING',
                        'trigger pending': 'PENDING',
                        'open pending': 'PENDING',
                        'after market order req received': 'PENDING',
                        'modify after market order req received': 'PENDING',
                        'cancelled after market order': 'CANCELLED',
                        'modify pending': 'PENDING',
                        'trigger_pending': 'PENDING'
                    }
                    
                    normalized = status_map.get(order_status, order_status.upper())
                    return normalized
            
            # Order not found in order book
            return None
            
        except Exception as e:
            print(f"⚠️ Get order status error ({order_id}): {e}")
            return None

    def get_option_chain(self, strikes: List[int], max_retries: int = 3) -> Dict[int, Dict[str, float]]:
        """
        🔥 Get option chain using WebSocket-first Batch LTP API
        """
        for attempt in range(max_retries):
            try:
                print(f"📊 Fetching {len(strikes)} strikes... (Range: {min(strikes)}-{max(strikes)})")

                option_chain = {}
                
                # 🔥 STEP 1: Collect all security IDs first
                all_security_ids = []
                strike_mapping = {}  # Maps strike -> {CE_id, PE_id, symbols}
                
                for strike in strikes:
                    ce_search = self.search_scrip('NIFTY', strike, 'CE')
                    pe_search = self.search_scrip('NIFTY', strike, 'PE')

                    if ce_search and pe_search:
                        ce_id = ce_search['security_id']
                        pe_id = pe_search['security_id']
                        
                        all_security_ids.extend([ce_id, pe_id])
                        
                        strike_mapping[strike] = {
                            'CE_id': ce_id,
                            'PE_id': pe_id,
                            'CE_symbol': ce_search['symbol'],
                            'PE_symbol': pe_search['symbol']
                        }

                if not all_security_ids:
                    print(f"   ❌ No valid strikes found in scrip master")
                    if attempt < max_retries - 1:
                        time.sleep(5)
                        continue
                    return {}

                # 🔥 STEP 2: Subscribe to WebSocket V2 if enabled
                if self.ws_enabled and self.market_ws:
                    try:
                        ws_instruments = [{'SecurityId': sid} for sid in all_security_ids]
                        self.subscribe_instruments_to_websocket(ws_instruments)
                        time.sleep(2)  # Wait for initial WebSocket data
                    except Exception as e:
                        print(f"   ⚠️ WebSocket subscription failed: {e}")

                # 🔥 STEP 3: WebSocket-first batch fetch all LTPs
                print(f"   🚀 WebSocket-first fetching {len(all_security_ids)} LTPs...")
                batch_ltps = self.get_batch_ltp_with_fallback(all_security_ids, "NFO")
                
                if not batch_ltps:
                    print(f"   ❌ Batch LTP fetch failed")
                    if attempt < max_retries - 1:
                        time.sleep(5)
                        continue
                    return {}

                # 🔥 STEP 4: Build option chain from batch results
                missing_strikes = []
                
                for strike, ids in strike_mapping.items():
                    ce_price = batch_ltps.get(ids['CE_id'])
                    pe_price = batch_ltps.get(ids['PE_id'])

                    if ce_price and pe_price:
                        option_chain[strike] = {
                            'CE': ce_price,
                            'PE': pe_price,
                            'CE_symbol': ids['CE_symbol'],
                            'PE_symbol': ids['PE_symbol'],
                            'CE_security_id': ids['CE_id'],
                            'PE_security_id': ids['PE_id']
                        }
                    else:
                        missing_strikes.append(strike)

                if missing_strikes:
                    print(f"   ⚠️ Missing strikes: {missing_strikes}")

                if option_chain:
                    print(f"   ✅ Chain built: {len(option_chain)} strikes - {len(batch_ltps)} LTPs fetched")
                    self.connection_failures = 0
                    return option_chain
                else:
                    if attempt < max_retries - 1:
                        wait_time = 5 if attempt == 0 else 20
                        print(f"   ⚠️ Empty chain, retry {attempt + 1}/{max_retries} in {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                    else:
                        print(f"   ❌ Option chain empty after {max_retries} attempts!")
                        return {}

            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = 5 if attempt == 0 else 20
                    print(f"   ⚠️ Error, retry {attempt + 1}/{max_retries} in {wait_time}s: {str(e)}")
                    time.sleep(wait_time)
                else:
                    print(f"   ❌ Error fetching option chain after {max_retries} attempts: {str(e)}")
                    return {}

        return {}

    def update_straddle_premiums_batch(self, ce_security_id: str, pe_security_id: str,
                                       ce_hedge_security_id: Optional[str] = None,
                                       pe_hedge_security_id: Optional[str] = None) -> Dict[str, float]:
        """
        🔥 Update all straddle premiums in ONE batch call
        """
        try:
            # Collect all IDs that need updates
            security_ids = [ce_security_id, pe_security_id]
            
            if ce_hedge_security_id:
                security_ids.append(ce_hedge_security_id)
            if pe_hedge_security_id:
                security_ids.append(pe_hedge_security_id)
            
            # WebSocket-first batch fetch
            batch_ltps = self.get_batch_ltp_with_fallback(security_ids, "NFO")
            
            # Build result dict
            result = {
                'ce': batch_ltps.get(ce_security_id),
                'pe': batch_ltps.get(pe_security_id),
                'ce_hedge': batch_ltps.get(ce_hedge_security_id) if ce_hedge_security_id else None,
                'pe_hedge': batch_ltps.get(pe_hedge_security_id) if pe_hedge_security_id else None
            }
            
            return result
            
        except Exception as e:
            print(f"❌ Batch premium update error: {e}")
            return {}

    def get_positions(self, force_refresh: bool = False) -> List[Dict]:
        """
        🔥 Get positions with smart caching
        """
        try:
            # 🔥 Check cache first (unless force refresh requested)
            if not force_refresh:
                cache_age = time.time() - self.position_cache_time
                if cache_age < self.position_cache_ttl and self.position_cache:
                    print(f"📋 Using cached positions (age: {cache_age:.1f}s)")
                    return self.position_cache
            
            # Fetch fresh positions from broker
            self._advanced_rate_limit('position')
            response = self.smart_api.position()

            if response and response.get('status'):
                positions = response.get('data', [])

                # Map to DHAN-compatible format
                mapped_positions = []
                for pos in positions:
                    # Only include NFO positions with non-zero quantity
                    if pos.get('exchange') == 'NFO' and int(pos.get('netqty', 0)) != 0:
                        mapped_positions.append({
                            'securityId': pos.get('symboltoken'),
                            'netQty': int(pos.get('netqty', 0)),
                            'tradingsymbol': pos.get('tradingsymbol')
                        })

                # 🔥 Update cache
                self.position_cache = mapped_positions
                self.position_cache_time = time.time()
                
                print(f"\n📋 Fetched {len(mapped_positions)} actual positions from broker (cached for {self.position_cache_ttl}s)")
                return mapped_positions
            else:
                print(f"⚠️ Failed to fetch positions: {response}")
                return []

        except Exception as e:
            print(f"❌ Error fetching positions: {str(e)}")
            return []
    
    def invalidate_position_cache(self):
        """
        🔥 Invalidate position cache after order execution
        Call this after successful order fills
        """
        self.position_cache = []
        self.position_cache_time = 0
        print("🔄 Position cache invalidated")

    def reconnect_if_needed(self) -> bool:
        """Reconnect if connection is dead"""
        try:
            self.connection_failures += 1

            if self.connection_failures <= self.max_connection_failures:
                print(f"⚠️ Connection unhealthy (attempt {self.connection_failures}/{self.max_connection_failures}), reconnecting...")

                time.sleep(3)

                if self.login():
                    print("✅ Reconnection successful!")
                    self.connection_failures = 0
                    return True
                else:
                    print("❌ Reconnection failed")
                    return False
            else:
                print(f"❌ Max reconnection attempts ({self.max_connection_failures}) reached")
                return False
        except Exception as e:
            print(f"❌ Reconnection error: {str(e)}")
            return False

    def logout(self):
        """Logout from Angel One and stop WebSocket V2"""
        try:
            # Stop WebSocket V2
            if self.ws_enabled:
                if self.market_ws:
                    self.market_ws.close_connection()
                    print("✅ Market WebSocket V2 stopped")
                if self.order_ws:
                    self.order_ws.close_connection()
                    print("✅ Order WebSocket stopped")

            # Logout from API
            if self.smart_api:
                self.smart_api.terminateSession(config.CLIENT_ID)
                print("✅ Logged out successfully")
                self.is_connected = False
        except Exception as e:
            print(f"⚠️ Logout error: {str(e)}")

    def get_system_health(self) -> Dict:
        """
        🔥 Get comprehensive system health status
        """
        return {
            'connection': {
                'is_connected': self.is_connected,
                'connection_failures': self.connection_failures,
            },
            'websocket': {
                'enabled': self.ws_enabled,
                'market_ws_active': bool(self.market_ws),
                'order_ws_active': bool(self.order_ws),
                'token_cache_size': len(self.token_cache),
            },
            'session': {
                'has_auth_token': bool(self.auth_token_string),
                'has_feed_token': bool(self.feed_token),
            },
            'cache': {
                'position_cache_size': len(self.position_cache),
                'position_cache_age': time.time() - self.position_cache_time if self.position_cache_time else None,
                'scrip_master_size': len(self.scrip_master),
            }
        }


# Create global API instance
api = AngelOneAPI()