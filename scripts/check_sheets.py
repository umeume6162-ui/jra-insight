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
    hi, bi, fi = index(runners, ("レースID", "馬番", "着順"))
    race_counts, horse_counts, keys = Counter(), Counter(), Counter()
    expected = {}
    nonstarters = Counter()
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
        if cell(row, fi) in ("取消", "除外", "出走取消", "競走除外"):
            nonstarters[key] += 1
    return {
        "races": len(race_counts),
        "runner_rows": sum(horse_counts.values()),
        "duplicate_race_rows": sum(n-1 for n in race_counts.values()),
        "duplicate_runner_rows": sum(n-1 for n in keys.values()),
        "orphan_runner_rows": sum(n for k,n in horse_counts.items() if k not in expected),
        "headcount_mismatch_races": sum(horse_counts[k] - nonstarters[k] != n for k,n in expected.items()),
        "invalid_rows": invalid,
    }

def plan_intake(races, runners, candidate_race, candidate_runners):
    """Read-only plan for a complete normalized race; never writes."""
    if not races or not runners or len(candidate_race) != len(races[0]):
        raise ValueError("Candidate race schema mismatch")
    if not candidate_runners or any(len(r) != len(runners[0]) for r in candidate_runners):
        raise ValueError("Candidate runner schema mismatch")
    ri = races[0].index("レースID")
    hi = runners[0].index("レースID")
    key = str(candidate_race[ri]).strip()
    if not key or any(str(r[hi]).strip() != key for r in candidate_runners):
        raise ValueError("Mixed race keys")
    if any(len(r) > ri and str(r[ri]).strip() == key for r in races[1:]) or any(len(r) > hi and str(r[hi]).strip() == key for r in runners[1:]):
        raise ValueError("Existing race or partial race; manual reconciliation required")
    fi = runners[0].index("着順")
    for row in candidate_runners:
        finish = str(row[fi]).strip()
        if not ((finish.isdecimal() and int(finish) > 0) or finish in ("取消", "除外", "出走取消", "競走除外", "中止", "競走中止", "失格")):
            raise ValueError("Missing or unsupported candidate finish status")
    # Full production schemas require provenance and horse identity.
    for header, records in ((races[0], [candidate_race]), (runners[0], candidate_runners)):
        for field in ("出典URL", "取得日", "馬名"):
            if field not in header:
                continue
            i = header.index(field)
            for row in records:
                value = str(row[i]).strip()
                if not value or (field == "出典URL" and not value.startswith("https://")):
                    raise ValueError("Missing candidate provenance or identity")
    report = audit_ledgers([races[0], candidate_race], [runners[0], *candidate_runners])
    if any(v for k, v in report.items() if k not in ("races", "runner_rows")):
        raise ValueError("Candidate structural anomalies")
    return {"race_rows": 1, "runner_rows": report["runner_rows"], "writes": 0}

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
    runners = [["レースID", "馬番", "着順"], ["a", "1", "1"], ["a", "2", "中止"], ["b", "1", "1"]]
    clean = audit_ledgers(races, runners)
    assert clean["races"] == 2 and clean["runner_rows"] == 3
    assert not any(v for k,v in clean.items() if k not in ("races", "runner_rows"))
    assert audit_ledgers(races, runners + [["a", "3", "取消"], ["b", "2", "除外"]])["headcount_mismatch_races"] == 0
    bad = audit_ledgers(races, runners + [["a", "01"], ["c", "1"], ["b", ""]])
    assert bad["duplicate_runner_rows"] == 1 and bad["orphan_runner_rows"] == 1
    assert bad["headcount_mismatch_races"] == 1 and bad["invalid_rows"] == 1
    assert plan_intake(races, runners, ["c", "1"], [["c", "1", "1"], ["c", "2", "取消"]]) == {"race_rows": 1, "runner_rows": 2, "writes": 0}
    for race, horses in ((["a", "1"], [["a", "1", "1"]]), (["c", "2"], [["c", "1", "1"]]), (["c", "1"], [["d", "1", "1"]]), (["c", "1"], [["c", "1", ""]])):
        try:
            plan_intake(races, runners, race, horses)
        except ValueError:
            continue
        raise AssertionError("Invalid candidate accepted")
    assert plan_intake(races + [[]], runners + [[]], ["c", "1"], [["c", "1", "1"]])["writes"] == 0
    full_races = [["レースID", "頭数", "出典URL", "取得日"]]
    full_runners = [["レースID", "馬番", "着順", "馬名", "出典URL", "取得日"]]
    good_race = ["c", "1", "https://example.org/race", "2026-01-01"]
    good_runner = ["c", "1", "1", "fixture", "https://example.org/race", "2026-01-01"]
    assert plan_intake(full_races, full_runners, good_race, [good_runner])["runner_rows"] == 1
    for i in (3, 4, 5):
        broken = good_runner.copy()
        broken[i] = ""
        try:
            plan_intake(full_races, full_runners, good_race, [broken])
        except ValueError:
            continue
        raise AssertionError("Missing provenance or identity accepted")
    from jra_result import parse_horse_weight
    assert parse_horse_weight("454 (初出走)") == ("454", "", True)
    assert parse_horse_weight("454(0)") == ("454", "0", False)
    assert parse_horse_weight("454(+4)") == ("454", "+4", False)
    assert parse_horse_weight("計不") == ("計不", "", False)
    try:
        parse_horse_weight("454(不明)")
    except ValueError:
        pass
    else:
        raise AssertionError("Unsupported horse weight accepted")
    print("Offline schema tests passed")

def verify_saved_result(races, runners, fetch_candidate):
    """Compare an already saved result with a fresh read of its official source."""
    from jra_result import result_url

    race_header, runner_header = races[0], runners[0]
    race_key = race_header.index("レースID")
    race_url = race_header.index("出典URL")
    runner_key = runner_header.index("レースID")
    horse_id = runner_header.index("馬ID")

    def cell(row, column):
        return str(row[column] or "").strip() if column < len(row) else ""

    saved_by_race = {}
    for row in runners[1:]:
        if cell(row, runner_key):
            saved_by_race.setdefault(cell(row, runner_key), []).append(row)

    for row in reversed(races[1:]):
        key = cell(row, race_key)
        url = cell(row, race_url)
        saved = saved_by_race.get(key, [])
        if not saved or not all(cell(runner, horse_id) for runner in saved):
            continue
        try:
            result_url(url)
        except ValueError:
            continue
        break
    else:
        raise ValueError("No complete saved JRA result with official horse IDs and a supported URL")

    candidate = fetch_candidate(url, key, race_header, runner_header)
    plan = plan_intake([race_header], [runner_header], candidate["race"], candidate["runners"])
    race_fields = [race_header.index(field) for field in ("レースID", "頭数", "勝ち時計")]
    if any(cell(row, index) != cell(candidate["race"], index) for index in race_fields):
        raise ValueError("Official result disagrees with the saved race")
    runner_fields = [runner_header.index(field) for field in ("馬番", "馬ID", "馬名", "着順")]

    def runner_rows(records):
        return sorted(tuple("".join(cell(record, index).split()) for index in runner_fields)
                      for record in records)

    if runner_rows(saved) != runner_rows(candidate["runners"]):
        raise ValueError("Official result disagrees with the saved runner identities or finishing order")
    return {"verified_saved_races": 1, "runner_rows": plan["runner_rows"], "writes": 0}


def main(candidate_path=None, source_url=None, expected_key=None, verify_saved=False):
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
    races = read("'全レース台帳'!A:Z")
    runners = read("'全出走馬成績'!A:AA")
    if verify_saved:
        from jra_result import candidate_from_url
        print(json.dumps(verify_saved_result(races, runners, candidate_from_url), sort_keys=True))
        return
    if source_url:
        from jra_result import candidate_from_url
        candidate = candidate_from_url(source_url, expected_key, races[0], runners[0])
    elif candidate_path:
        with open(candidate_path, encoding="utf-8") as handle:
            candidate = json.load(handle)
    if source_url or candidate_path:
        if not isinstance(candidate, dict) or set(candidate) != {"race", "runners"}:
            raise ValueError("Unexpected candidate file schema")
        print(json.dumps(plan_intake(races, runners, candidate["race"], candidate["runners"]), sort_keys=True))
        return
    report = audit_ledgers(races, runners)
    print(json.dumps(report, sort_keys=True))
    if any(v for k,v in report.items() if k not in ("races", "runner_rows")):
        raise SystemExit("Ledger quality anomalies detected; no writes performed")
    print("Specification and ledger structural checks: OK; no writes performed")

if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--self-test"]:
        self_test()
    elif len(sys.argv) == 3 and sys.argv[1] == "--candidate":
        main(sys.argv[2])
    elif len(sys.argv) == 4 and sys.argv[1] == "--official-result":
        main(source_url=sys.argv[2], expected_key=sys.argv[3])
    elif sys.argv[1:] == ["--verify-saved-result"]:
        main(verify_saved=True)
    elif len(sys.argv) == 1:
        main()
    else:
        raise SystemExit("Usage: check_sheets.py [--self-test | --candidate FILE | --official-result URL RACE_ID | --verify-saved-result]")

