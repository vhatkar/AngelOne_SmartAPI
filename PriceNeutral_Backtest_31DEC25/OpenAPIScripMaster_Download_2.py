import requests
import os
import json
from datetime import datetime

def download_scrip_master():
    """
    Downloads the OpenAPIScripMaster.json file from AngelOne and overwrites the existing file.

    Returns:
        bool: True if download successful, False otherwise
    """
    url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    file_name = "OpenAPIScripMaster.json"

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting download...")
    print(f"URL: {url}")

    try:
        # Send GET request with timeout
        response = requests.get(url, timeout=30)

        # Check if request was successful
        if response.status_code == 200:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Download successful (Status: {response.status_code})")

            # Validate JSON content
            try:
                data = response.json()
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] JSON validation successful")
                print(f"Total instruments: {len(data)}")
            except json.JSONDecodeError as e:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERROR: Invalid JSON content")
                return False

            # Get file size
            file_size_mb = len(response.content) / (1024 * 1024)
            print(f"File size: {file_size_mb:.2f} MB")

            # Write to file (this will overwrite if file exists)
            with open(file_name, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)

            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] File saved successfully: {file_name}")
            print(f"File location: {os.path.abspath(file_name)}")

            return True

        else:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERROR: Failed to download")
            print(f"Status code: {response.status_code}")
            print(f"Response: {response.text[:200]}")
            return False

    except requests.exceptions.Timeout:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERROR: Request timed out")
        return False
    except requests.exceptions.ConnectionError:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERROR: Connection error - Check internet connection")
        return False
    except requests.exceptions.RequestException as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERROR: {str(e)}")
        return False
    except IOError as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERROR: Failed to write file - {str(e)}")
        return False
    except Exception as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERROR: Unexpected error - {str(e)}")
        return False

if __name__ == "__main__":
    success = download_scrip_master()
    if success:
        print("\n✓ Download completed successfully")
    else:
        print("\n✗ Download failed")
