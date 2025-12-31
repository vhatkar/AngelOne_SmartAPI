# nifty_data_collector_fixed.py
# Fixed version to handle timezone properly

import pandas as pd
import datetime
import pyotp
import os
import time
from dotenv import load_dotenv
from SmartApi import SmartConnect

# ============ USER CONFIG ============
load_dotenv()
API_KEY = os.getenv("API_KEY")
CLIENT_ID = os.getenv("CLIENT_ID")
PASSWORD = os.getenv("PASSWORD")
TOTP_SECRET = os.getenv("TOTP_SECRET")

SYMBOL_TOKEN = "99926000"  # Nifty index (check AngelOne scrip master)
EXCHANGE = "NSE"
START_DATE = "2025-11-24 09:15"
END_DATE = "2025-12-29 15:30"
INTERVAL = "ONE_MINUTE"  # ONE_MINUTE, FIVE_MINUTE, ONE_HOUR, ONE_DAY
CSV_FILE = "nifty_1_min.csv"


# =====================================

def daterange(start_date, end_date, step_days=60):
    """Yield date ranges in chunks (max 60 days per request)."""
    curr = start_date
    while curr < end_date:
        next_date = min(curr + datetime.timedelta(days=step_days), end_date)
        yield curr, next_date
        curr = next_date


def main():
    # Generate TOTP
    totp = pyotp.TOTP(TOTP_SECRET)
    otp = totp.now()

    # Create object
    obj = SmartConnect(api_key=API_KEY)

    # Generate session
    data = obj.generateSession(CLIENT_ID, PASSWORD, otp)
    if "data" not in data or "refreshToken" not in data["data"]:
        print("Login failed:", data)
        return
    print("Login successful!")

    all_data = []

    start_dt = datetime.datetime.strptime(START_DATE, "%Y-%m-%d %H:%M")
    end_dt = datetime.datetime.strptime(END_DATE, "%Y-%m-%d %H:%M")

    for s, e in daterange(start_dt, end_dt, step_days=60):
        historicParam = {
            "exchange": EXCHANGE,
            "symboltoken": SYMBOL_TOKEN,
            "interval": INTERVAL,
            "fromdate": s.strftime("%Y-%m-%d %H:%M"),
            "todate": e.strftime("%Y-%m-%d %H:%M"),
        }
        print(f"Fetching {historicParam['fromdate']} -> {historicParam['todate']}")
        data = obj.getCandleData(historicParam)

        if "data" in data and data["data"]:
            all_data.extend(data["data"])
        else:
            print("No data for this chunk:", historicParam)

        time.sleep(1)  # pause to avoid rate limit

    # Convert to DataFrame
    if not all_data:
        print("No data downloaded")
        return

    df = pd.DataFrame(all_data, columns=["datetime", "open", "high", "low", "close", "volume"])

    # FIXED: Handle timezone properly
    print("Processing datetimes...")

    # Convert to datetime and handle timezone
    df["datetime"] = pd.to_datetime(df["datetime"])

    # If datetimes are already in IST (which AngelOne usually provides),
    # we need to make them timezone-naive for consistency
    if df["datetime"].dt.tz is not None:
        print("Converting timezone-aware datetimes to IST...")
        df["datetime"] = df["datetime"].dt.tz_convert('Asia/Kolkata').dt.tz_localize(None)

    # Sort by datetime
    df.sort_values("datetime", inplace=True)

    # Ensure numeric columns
    numeric_cols = ["open", "high", "low", "close", "volume"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    # Remove any rows with NaN values
    df = df.dropna()

    # Keep consistent column naming (lowercase)
    df = df[["datetime", "open", "high", "low", "close", "volume"]]

    # Save to CSV with datetime as first column (not as index)
    df.to_csv(CSV_FILE, index=False)
    print(f"Data saved to {CSV_FILE} | {len(df)} rows")
    print(f"Date range: {df['datetime'].min()} to {df['datetime'].max()}")
    print(f"Sample datetimes:")
    print(df['datetime'].head())


if __name__ == "__main__":
    main()
