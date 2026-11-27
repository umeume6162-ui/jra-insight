"""Read-only connectivity and schema check; never log credentials or cell values."""
import json
import os
from urllib.parse import quote

SPEC_ITEMS = ("目的", "取得", "レース", "出走馬", "コース", "人物・血統", "品質", "優先順", "研究", "自動運用", "開発", "報告")

def validate_spec(rows):
    if len(rows) < 13 or len(rows[0]) < 2 or rows[0][0] != "項目" or not rows[0][1].startswith("現行仕様"):
        raise ValueError("Specification missing or unexpected")
    items = {r[0] for r in rows[1:] if len(r) >= 2 and str(r[1]).strip()}
    if not set(SPEC_ITEMS).issubset(items):
        raise ValueError("Specification incomplete")

def self_test():
    rows = [["項目", "現行仕様 test"]] + [[x, "test"] for x in SPEC_ITEMS]
    validate_spec(rows)
    for bad in ([], rows[:-1], [["項目"]] + rows[1:]):
        try:
            validate_spec(bad)
        except ValueError:
            continue
        raise AssertionError("Incomplete specification accepted")
    print("Offline schema tests passed")

def main():
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    if not all(os.environ.get(x) for x in ("GOOGLE_SERVICE_ACCOUNT_JSON", "JRA_SHEET_ID")):
        raise SystemExit("Required repository secrets missing")
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    session = AuthorizedSession(credentials)
    base = "https://sheets.googleapis.com/v4/spreadsheets/" + os.environ["JRA_SHEET_ID"]
    def read(a1):
        response = session.get(base + "/values/" + quote(a1, safe=""), timeout=30)
        if response.status_code != 200:
            raise SystemExit(f"Sheets read failed: HTTP {response.status_code}")
        return response.json().get("values", [])
    validate_spec(read("'現行仕様'!A1:B13"))
    for tab in ("全レース台帳", "全出走馬成績"):
        if read(f"'{tab}'!A1:A1") != [["レースID"]]:
            raise SystemExit("Ledger header check failed")
    print("Specification and both ledger headers: OK; no writes performed")

if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--self-test"]:
        self_test()
    else:
        main()
