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

def audit_ledgers(races, runners):
    from collections import Counter
    def index(rows, fields):
        if not rows or any(f not in rows[0] for f in fields):
            raise ValueError("Ledger schema missing")
        return [rows[0].index(f) for f in fields]
    def cell(row, i):
        return str(row[i]).strip() if i < len(row) else ""
    ri, ni = index(races, ("レースID", "頭数"))
    hi, bi = index(runners, ("レースID", "馬番"))
    race_counts, horse_counts, keys = Counter(), Counter(), Counter()
    expected = {}
    invalid = 0
    for row in races[1:]:
        if not any(row):
            continue
        key, n = cell(row, ri), cell(row, ni)
        if not key or not n.isdecimal() or int(n) <= 0:
            invalid += 1
            continue
        race_counts[key] += 1
        expected[key] = int(n)
    for row in runners[1:]:
        if not any(row):
            continue
        key, number = cell(row, hi), cell(row, bi)
        if not key or not number.isdecimal() or int(number) <= 0:
            invalid += 1
            continue
        keys[key, int(number)] += 1
        horse_counts[key] += 1
    return {
        "races": len(race_counts),
        "runner_rows": sum(horse_counts.values()),
        "duplicate_race_rows": sum(n-1 for n in race_counts.values()),
        "duplicate_runner_rows": sum(n-1 for n in keys.values()),
        "orphan_runner_rows": sum(n for k,n in horse_counts.items() if k not in expected),
        "headcount_mismatch_races": sum(horse_counts[k] != n for k,n in expected.items()),
        "invalid_rows": invalid,
    }

def self_test():
    rows = [["項目", "現行仕様 test"]] + [[x, "test"] for x in SPEC_ITEMS]
    validate_spec(rows)
    for bad in ([], rows[:-1], [["項目"]] + rows[1:]):
        try:
            validate_spec(bad)
        except ValueError:
            continue
        raise AssertionError("Incomplete specification accepted")
    races = [["レースID", "頭数"], ["a", "2"], ["b", "1"]]
    runners = [["レースID", "馬番"], ["a", "1"], ["a", "2"], ["b", "1"]]
    clean = audit_ledgers(races, runners)
    assert clean["races"] == 2 and clean["runner_rows"] == 3
    assert not any(v for k,v in clean.items() if k not in ("races", "runner_rows"))
    bad = audit_ledgers(races, runners + [["a", "01"], ["c", "1"], ["b", ""]])
    assert bad["duplicate_runner_rows"] == 1 and bad["orphan_runner_rows"] == 1
    assert bad["headcount_mismatch_races"] == 1 and bad["invalid_rows"] == 1
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
    report = audit_ledgers(read("'全レース台帳'!A:P"), read("'全出走馬成績'!A:C"))
    print(json.dumps(report, sort_keys=True))
    if any(v for k,v in report.items() if k not in ("races", "runner_rows")):
        raise SystemExit("Ledger quality anomalies detected; no writes performed")
    print("Specification and ledger structural checks: OK; no writes performed")

if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--self-test"]:
        self_test()
    else:
        main()
