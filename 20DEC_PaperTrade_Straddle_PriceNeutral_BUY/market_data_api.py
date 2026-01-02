"""
Market Data API - WebSocket for real-time prices
WITH SCRIP MASTER INTEGRATION
"""

from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2
import pyotp
from config_paper import config
import time
import threading
from scrip_master_downloader import scrip_master  # ✅ ADD THIS


class MarketDataAPI:
    def __init__(self):
        self.smartapi = None
        self.websocket = None
        self.logged_in = False

        # WebSocket data cache
        self.ltp_cache = {}
        self.cache_lock = threading.Lock()

        # Connection state
        self.ws_connected = False
        self.auth_token = None
        self.feed_token = None
        
        # Rate limiting flag
        self.rate_limit_hit = False

    def login(self):
        """Login for market data"""
        try:
            # ✅ DOWNLOAD SCRIP MASTER FIRST
            if not scrip_master.download():
                print("⚠️  Scrip master download failed, continuing anyway...")

            self.smartapi = SmartConnect(api_key=config.API_KEY)

            totp = pyotp.TOTP(config.TOTP_SECRET).now()
            data = self.smartapi.generateSession(
                config.CLIENT_ID,
                config.PASSWORD,
                totp
            )

            if data['status']:
                self.auth_token = data['data']['jwtToken']
                self.feed_token = self.smartapi.getfeedToken()
                self.logged_in = True

                print("✅ Logged in successfully")
                self._init_websocket()
                return True

            return False

        except Exception as e:
            print(f"❌ Login failed: {e}")
            return False

    def _init_websocket(self):
        """Initialize WebSocket"""
        try:
            def on_open(wsapp):
                print("✅ WebSocket connected")
                self.ws_connected = True

            def on_close(wsapp):
                print("⚠️  WebSocket disconnected")
                self.ws_connected = False

            def on_error(wsapp, error):
                print(f"❌ WebSocket error: {error}")

            def on_data(wsapp, message):
                try:
                    if isinstance(message, dict):
                        token = str(message.get('token', ''))
                        ltp = message.get('last_traded_price', 0)

                        if token and ltp:
                            # ✅ SmartAPI sends LTP in paise for options → convert to rupees
                            ltp_rupees = float(ltp) / 100.0
                            with self.cache_lock:
                                self.ltp_cache[token] = ltp_rupees
                except:
                    pass

            self.websocket = SmartWebSocketV2(
                auth_token=self.auth_token,
                api_key=config.API_KEY,
                client_code=config.CLIENT_ID,
                feed_token=self.feed_token
            )

            self.websocket.on_open = on_open
            self.websocket.on_close = on_close
            self.websocket.on_error = on_error
            self.websocket.on_data = on_data

            ws_thread = threading.Thread(target=self.websocket.connect, daemon=True)
            ws_thread.start()
            time.sleep(2)

            if self.ws_connected:
                print("✅ WebSocket initialized")

        except Exception as e:
            print(f"❌ WebSocket init failed: {e}")

    def is_ws_ready(self) -> bool:
        """Return True if WebSocket is connected and has some recent data."""
        return self.ws_connected

    def subscribe_instruments(self, security_ids):
        """Subscribe to instruments"""
        if not self.websocket or not self.ws_connected:
            print("⚠️  WebSocket not connected")
            return False

        try:
            token_list = {
                "exchangeType": 2,
                "tokens": [str(sid) for sid in security_ids]
            }

            self.websocket.subscribe(
                correlation_id=f"sub_{int(time.time())}",
                mode=1,
                token_list=[token_list]
            )

            print(f"✅ Subscribed to {len(security_ids)} instruments")
            time.sleep(1)
            return True

        except Exception as e:
            print(f"❌ Subscribe failed: {e}")
            return False

    def get_spot_price(self):
        """Get NIFTY spot price"""
        try:
            response = self.smartapi.ltpData("NSE", "NIFTY 50", "99926000")
            if response and response.get('status'):
                # Note: Spot price from ltpData is already in rupees
                return float(response['data']['ltp'])
        except Exception as e:
            print(f"❌ Spot price error: {e}")
        return None

    def get_ltp(self, security_id):
        """Get LTP from WebSocket cache (preferred) or REST (limited)."""
        # Try WebSocket cache first
        with self.cache_lock:
            if str(security_id) in self.ltp_cache:
                return self.ltp_cache[str(security_id)]

        # If rate limit was hit in this candle, skip REST
        if self.rate_limit_hit:
            return None

        # Fallback to REST API - but avoid hammering when rate-limited
        try:
            response = self.smartapi.getMarketData("LTP", [str(security_id)])
            if not response:
                return None

            # Angel sometimes returns raw bytes / error text instead of JSON
            # You saw: "Access denied because of exceeding access rate"
            if isinstance(response, bytes):
                text = response.decode(errors="ignore")
                if "Access denied because of exceeding access rate" in text:
                    print("⚠️  Rate limit hit on getMarketData, skipping further REST LTPs this candle")
                    self.rate_limit_hit = True
                    return None

            if isinstance(response, str):
                if "Access denied because of exceeding access rate" in response:
                    print("⚠️  Rate limit hit on getMarketData, skipping further REST LTPs this candle")
                    self.rate_limit_hit = True
                    return None

            if isinstance(response, dict) and response.get('status'):
                fetched = response['data'].get('fetched', [])
                for item in fetched:
                    if str(item.get('symboltoken')) == str(security_id):
                        # ✅ Convert paise to rupees
                        ltp_paise = float(item.get('ltp', 0))
                        ltp_rupees = ltp_paise / 100.0
                        with self.cache_lock:
                            self.ltp_cache[str(security_id)] = ltp_rupees
                        return ltp_rupees

        except Exception as e:
            error_msg = str(e)
            if "Access denied because of exceeding access rate" in error_msg:
                print("⚠️  Rate limit hit on getMarketData, skipping further REST LTPs this candle")
                self.rate_limit_hit = True
                return None
            print(f"⚠️  LTP fetch failed: {e}")
        return None

    def get_option_chain(self, strikes):
        """Build option chain using scrip master + WebSocket, with safe fallback."""
        try:
            # ✅ 1) If WebSocket is not ready on very first call, DO NOT hammer REST
            if not self.is_ws_ready():
                print("⚠️  WebSocket not connected yet, skipping option chain for this candle")
                return {}

            # Reset rate limit flag for new candle
            self.rate_limit_hit = False
            
            print(f"📊 Fetching option chain for {len(strikes)} strikes...")

            security_ids = []
            strike_mapping = {}

            for strike in strikes:
                # ✅ USE SCRIP MASTER instead of API search
                ce_option = scrip_master.find_option(config.UNDERLYING, config.EXPIRY_DATE, strike, 'CE')
                pe_option = scrip_master.find_option(config.UNDERLYING, config.EXPIRY_DATE, strike, 'PE')

                if ce_option and pe_option:
                    ce_id = ce_option['token']
                    ce_symbol = ce_option['symbol']
                    pe_id = pe_option['token']
                    pe_symbol = pe_option['symbol']

                    security_ids.extend([ce_id, pe_id])
                    strike_mapping[strike] = {
                        'CE_id': ce_id,
                        'PE_id': pe_id,
                        'CE_symbol': ce_symbol,
                        'PE_symbol': pe_symbol
                    }

            # Subscribe to WebSocket
            if security_ids:
                self.subscribe_instruments(security_ids)

            # Build chain
            option_chain = {}
            for strike, ids in strike_mapping.items():
                ce_ltp = self.get_ltp(ids['CE_id'])
                pe_ltp = self.get_ltp(ids['PE_id'])

                if ce_ltp and pe_ltp:
                    option_chain[strike] = {
                        'CE': ce_ltp,
                        'PE': pe_ltp,
                        'CE_symbol': ids['CE_symbol'],
                        'PE_symbol': ids['PE_symbol'],
                        'CE_security_id': ids['CE_id'],
                        'PE_security_id': ids['PE_id']
                    }

            print(f"✅ Option chain: {len(option_chain)} strikes")
            return option_chain

        except Exception as e:
            print(f"❌ Option chain error: {e}")
            return {}

    def search_scrip(self, underlying, strike, option_type):
        """Search for option using scrip master"""
        try:
            option = scrip_master.find_option(underlying, config.EXPIRY_DATE, strike, option_type)
            if option:
                return (option['token'], option['symbol'])
        except Exception as e:
            print(f"⚠️  Scrip search failed: {e}")
        return None

    def close(self):
        """Close WebSocket"""
        if self.websocket:
            try:
                self.websocket.close_connection()
                print("✅ WebSocket closed")
            except:
                pass


market_data = MarketDataAPI()