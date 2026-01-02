"""
Scrip Master Downloader for Angel One
Downloads and caches NFO scrip master locally
USES EXACT FORMAT FROM LIVE TRADING CODE
"""

import requests
import json
import os
from datetime import datetime, timedelta


class ScripMasterDownloader:
    def __init__(self):
        self.scrip_file = "angelone_scrip_master.json"
        self.url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        self.nfo_scrips = {}

    def download(self, force=False):
        """Download scrip master from Angel One"""
        if os.path.exists(self.scrip_file) and not force:
            file_time = datetime.fromtimestamp(os.path.getmtime(self.scrip_file))
            if datetime.now() - file_time < timedelta(days=1):
                print(f"✅ Using cached scrip master (updated {file_time.strftime('%Y-%m-%d %H:%M')})")
                return self.load_from_file()

        print(f"📥 Downloading scrip master from Angel One...")
        try:
            response = requests.get(self.url, timeout=30)
            response.raise_for_status()

            scrip_data = response.json()
            nfo_scrips = [s for s in scrip_data if s.get('exch_seg') == 'NFO']

            print(f"✅ Downloaded {len(nfo_scrips)} NFO instruments")

            with open(self.scrip_file, 'w') as f:
                json.dump(nfo_scrips, f, indent=2)

            print(f"✅ Saved to {self.scrip_file}")
            self._build_lookup(nfo_scrips)
            return True

        except Exception as e:
            print(f"❌ Download failed: {e}")
            if os.path.exists(self.scrip_file):
                print(f"⚠️  Using old cached file")
                return self.load_from_file()
            return False

    def load_from_file(self):
        """Load scrip master from cached file"""
        try:
            with open(self.scrip_file, 'r') as f:
                nfo_scrips = json.load(f)

            print(f"✅ Loaded {len(nfo_scrips)} NFO instruments from cache")
            self._build_lookup(nfo_scrips)
            return True

        except Exception as e:
            print(f"❌ Failed to load scrip master: {e}")
            return False

    def _build_lookup(self, nfo_scrips):
        """Build fast lookup dictionary - FILTERS FOR NIFTY ONLY"""
        self.nfo_scrips = {}

        count = 0
        for scrip in nfo_scrips:
            symbol = scrip.get('symbol', '')
            token = scrip.get('token', '')

            # ✅ FILTER: Only NIFTY options (exclude BANKNIFTY, FINNIFTY, etc.)
            if symbol and token:
                if 'NIFTY' in symbol and not any(x in symbol for x in ['BANK', 'FINN', 'MIDCP', 'NXT']):
                    # Convert strike from paise to rupees
                    strike_paise = scrip.get('strike', '0')
                    strike = int(float(strike_paise) / 100) if strike_paise else 0

                    self.nfo_scrips[symbol] = {
                        'token': token,
                        'name': scrip.get('name', ''),
                        'expiry': scrip.get('expiry', ''),
                        'strike': strike,
                        'lotsize': scrip.get('lotsize', ''),
                        'instrumenttype': scrip.get('instrumenttype', ''),
                        'tick_size': scrip.get('tick_size', '0.05')
                    }
                    count += 1

        print(f"✅ Built lookup index for {count} NIFTY option contracts")

    def search_symbol(self, symbol):
        """Search for symbol in scrip master"""
        return self.nfo_scrips.get(symbol)

    def find_option(self, underlying, expiry, strike, option_type):
        """
        Find option contract using EXACT format from live trading
        underlying: 'NIFTY'
        expiry: 'DDMMMYY' format (e.g., '26DEC24')
        strike: Integer strike price
        option_type: 'CE' or 'PE'
        """
        # ✅ EXACT FORMAT FROM LIVE CODE
        # Parse expiry components
        day = expiry[:2]  # "26"
        month = expiry[2:5]  # "DEC"
        year = expiry[5:]  # "24"

        # Try all 6 format variations (same as live trading)
        formats_to_try = [
            f"{underlying}{expiry}{strike}{option_type}",  # NIFTY26DEC2426000CE ✅ PRIMARY
            f"{underlying}{day}{month}{year}{strike}{option_type}",  # NIFTY26DEC2426000CE (same)
            f"{underlying}{month}{year}{strike}{option_type}",  # NIFTYDEC2426000CE (monthly)
            f"{underlying}{year}{month[0]}{day}{strike}{option_type}",  # NIFTY24D2626000CE (compressed)
            f"{underlying}{day}{month}{year}C{strike}",  # NIFTY26DEC24C26000
            f"{underlying}{day}{month}{year[2:]}{strike}{option_type}"  # NIFTY26DEC2426000CE
        ]

        for fmt in formats_to_try:
            if fmt in self.nfo_scrips:
                scrip_data = self.nfo_scrips[fmt]
                return {
                    'symbol': fmt,
                    'token': scrip_data['token'],
                    'strike': strike,
                    'expiry': scrip_data['expiry'],
                    'lotsize': scrip_data['lotsize']
                }

        # ✅ DEBUG: Show what we searched for
        print(f"⚠️  Symbol not found. Tried:")
        for fmt in formats_to_try[:2]:  # Show first 2 attempts
            print(f"   - {fmt}")

        return None

    def get_all_strikes_for_expiry(self, underlying, expiry, option_type='CE'):
        """Get all available strikes for given expiry"""
        strikes = []

        # Build search prefix using primary format
        search_prefix = f"{underlying}{expiry}"

        for symbol, data in self.nfo_scrips.items():
            if symbol.startswith(search_prefix) and symbol.endswith(option_type):
                strike = data.get('strike', 0)
                if strike:
                    strikes.append(strike)

        return sorted(set(strikes))


# Global instance
scrip_master = ScripMasterDownloader()
