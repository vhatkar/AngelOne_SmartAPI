# options_data_downloader_fixed.py
# Fixed version with proper timezone handling + folder organization + concurrent downloading

import os
import pyotp
import pandas as pd
import json
from SmartApi import SmartConnect
from dotenv import load_dotenv
import time
import concurrent.futures
from threading import Lock

# ---------------------------
# Load Credentials
# ---------------------------
load_dotenv()
API_KEY = os.getenv("API_KEY")
CLIENT_ID = os.getenv("CLIENT_ID")
PASSWORD = os.getenv("PASSWORD")
TOTP_SECRET = os.getenv("TOTP_SECRET")

otp = pyotp.TOTP(TOTP_SECRET).now()
obj = SmartConnect(api_key=API_KEY)
obj.generateSession(CLIENT_ID, PASSWORD, otp)
print("Login Successful")

# 🔥 Create options_data folder if it doesn't exist
output_folder = "options_data"
if not os.path.exists(output_folder):
    os.makedirs(output_folder)
    print(f"✅ Created folder: {output_folder}")
else:
    print(f"📁 Using existing folder: {output_folder}")

# ---------------------------
# Load Instrument Master
# ---------------------------
with open("OpenAPIScripMaster.json", "r") as f:
    instruments = pd.DataFrame(json.load(f))

# ---------------------------
# Filter NIFTY Options by Expiry + Strikes
# ---------------------------
expiry_date = "13JAN26"  # change expiry here (DDMMMYY)
strike_list = list(range(25500, 26500 + 1, 50))
option_type = ["CE", "PE"]  # which side you want

tradingsymbols = []
for strike in strike_list:
    for opt in option_type:
        ts = f"NIFTY{expiry_date}{strike}{opt}"
        if ts in instruments["symbol"].values:
            tradingsymbols.append(ts)
        else:
            print(f"Warning: {ts} not found in master!")

print("Final tradingsymbols:", tradingsymbols[:10], "...")  # Show only first 10
print(f"Total symbols to download: {len(tradingsymbols)}")

# ---------------------------
# Global counters with thread safety
# ---------------------------
successful_downloads = 0
failed_downloads = 0
counter_lock = Lock()

# ---------------------------
# Function to download a single symbol
# ---------------------------
def download_symbol(ts, index, total):
    global successful_downloads, failed_downloads
    
    try:
        # Find symbol in instrument master
        row = instruments[instruments["symbol"] == ts].iloc[0]
        symbol_token = str(row["token"])
        exchange = row["exch_seg"]

        print(f"[{index}/{total}] Downloading {ts} ({exchange}, token={symbol_token})...")

        params = {
            "exchange": exchange,
            "symboltoken": symbol_token,
            "interval": "ONE_MINUTE",
            "fromdate": "2025-12-01 09:15",
            "todate": "2025-12-29 15:30",
        }

        historical = obj.getCandleData(params)

        if "data" not in historical or not historical["data"]:
            print(f"  No data available for {ts}")
            with counter_lock:
                failed_downloads += 1
            return (ts, False, "No data available")

        # Create DataFrame
        df = pd.DataFrame(historical["data"], columns=["datetime", "open", "high", "low", "close", "volume"])

        # Handle timezone properly
        df["datetime"] = pd.to_datetime(df["datetime"])

        # If datetimes are timezone-aware, convert to IST and make timezone-naive
        if df["datetime"].dt.tz is not None:
            df["datetime"] = df["datetime"].dt.tz_convert('Asia/Kolkata').dt.tz_localize(None)

        # Ensure numeric columns
        numeric_cols = ["open", "high", "low", "close", "volume"]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        # Remove any rows with NaN values
        original_len = len(df)
        df = df.dropna()
        if len(df) < original_len:
            print(f"  Removed {original_len - len(df)} rows with missing data")

        # Sort by datetime
        df = df.sort_values("datetime")

        # Keep consistent column naming and format
        df = df[["datetime", "open", "high", "low", "close", "volume"]]

        # Save to options_data folder
        file_name = f"{ts}_ONE_MINUTE.csv"
        file_path = os.path.join(output_folder, file_name)
        df.to_csv(file_path, index=False)

        print(f"  Saved {len(df)} records -> {file_path}")
        print(f"  Date range: {df['datetime'].min()} to {df['datetime'].max()}")
        
        with counter_lock:
            successful_downloads += 1
        
        return (ts, True, f"Saved {len(df)} records")

    except Exception as e:
        print(f"  Error fetching {ts}: {e}")
        with counter_lock:
            failed_downloads += 1
        return (ts, False, str(e))

# ---------------------------
# Concurrent Downloading with ThreadPoolExecutor
# ---------------------------
print(f"\n{'='*60}")
print(f"STARTING CONCURRENT DOWNLOAD")
print(f"Using 3 concurrent workers (max API limit)")
print(f"{'='*60}")

# Limit to 3 concurrent requests (matches API rate limit of 3/sec)
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
    futures = []
    
    # Submit all tasks with staggered starts to respect rate limits
    for i, ts in enumerate(tradingsymbols, 1):
        future = executor.submit(download_symbol, ts, i, len(tradingsymbols))
        futures.append(future)
        
        # Stagger task submissions to respect rate limits
        if i < len(tradingsymbols):
            time.sleep(0.35)  # ~2.85 requests/sec when combined with 3 workers
    
    # Collect results as they complete
    completed = 0
    for future in concurrent.futures.as_completed(futures):
        completed += 1
        symbol, success, message = future.result()
        status = "✅" if success else "❌"
        print(f"[Progress: {completed}/{len(tradingsymbols)}] {status} {symbol}: {message}")

# ---------------------------
# Download Summary
# ---------------------------
print(f"\n{'='*60}")
print(f"DOWNLOAD SUMMARY")
print(f"{'='*60}")
print(f"Successful: {successful_downloads}")
print(f"Failed: {failed_downloads}")
print(f"Total: {len(tradingsymbols)}")
print(f"📁 Files saved in: {os.path.abspath(output_folder)}")
print(f"{'='*60}")

if successful_downloads > 0:
    print(f"\n✅ Files saved with consistent datetime format (timezone-naive IST)")
    print(f"📊 Column format: datetime, open, high, low, close, volume")
    print(f"🎯 Ready for backtesting!")
else:
    print(f"\n❌ No files were successfully downloaded. Please check:")
    print(f"   1. Internet connection")
    print(f"   2. API credentials")
    print(f"   3. Date range validity")
    print(f"   4. Symbol availability in market")