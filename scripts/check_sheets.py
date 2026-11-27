"""Read-only connectivity check. Never logs credentials or cell contents."""
import json
import os
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

def main():
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    session = AuthorizedSession(credentials)
    sheet_id = os.environ["JRA_SHEET_ID"]
    base = "https://sheets.googleapis.com/v4/spreadsheets/" + sheet_id
    response = session.get(base + "/values/%27%E7%8F%BE%E8%A1%8C%E4%BB%95%E6%A7%98%27%21A1%3AB13", timeout=30)
    if response.status_code != 200:
        raise SystemExit(f"Sheets read failed: HTTP {response.status_code}")
    rows = response.json().get("values", [])
    if len(rows) < 13 or rows[0][0] != "項目" or not rows[0][1].startswith("現行仕様"):
        raise SystemExit("Specification missing or unexpected")
    print("Specification read: OK")
    for tab, expected in [("全レース台帳", "レースID"), ("全出走馬成績", "レースID")]:
        response = session.get(base + "/values/" + __import__("urllib.parse", fromlist=["quote"]).quote(f"'{tab}'!A1:A1", safe=""), timeout=30)
        if response.status_code != 200 or response.json().get("values") != [[expected]]:
            raise SystemExit("Ledger header check failed")
    print("Both ledger headers: OK; no writes performed")

if __name__ == "__main__":
    main()
