import requests
import json
import time

BASE_URL = "http://localhost:8000"

def test_email_auth():
    print("Testing Email Auth Process...")
    email = "test@example.com"

    # 1. Send OTP Request
    try:
        resp = requests.post(f"{BASE_URL}/auth/send-code", json={"email": email})

        # We expect this to fail if Google SMTP rejects test@example.com
        # but 200 means parsing and SMTP connection was started
        if resp.status_code == 200:
            print("send-code API returned 200 OK.")
        else:
            print(f"send-code API failed with {resp.status_code}: {resp.text}")

    except Exception as e:
        print(f"Test script error: {e}")

if __name__ == "__main__":
    time.sleep(2) # wait for server config
    test_email_auth()
