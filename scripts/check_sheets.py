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

def verify_saved_result(races, runners, fetch_candidate, verify_replay=False):
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
    if verify_replay:
        from save_result import RACE_TAB, RUNNERS_TAB, save_result

        def reject_write(_requests):
            raise ValueError("Replay verification must not issue a write")

        replay = save_result(candidate, lambda: (races, runners),
                             {RACE_TAB: 1, RUNNERS_TAB: 2}, reject_write)
        if not replay["already_saved"] or replay["writes"]:
            raise ValueError("Replay did not recognize the complete stored race")
        return {"verified_saved_races": 1, **replay}
    return {"verified_saved_races": 1, "runner_rows": plan["runner_rows"], "writes": 0}


def main(candidate_path=None, source_url=None, expected_key=None, verify_saved=False, write=False, verify_replay=False):
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    from requests import RequestException
    if not all(os.environ.get(x) for x in ("GOOGLE_SERVICE_ACCOUNT_JSON", "JRA_SHEET_ID")):
        raise SystemExit("Required repository secrets missing")
    if write and not (source_url and expected_key):
        raise ValueError("Writing requires an official result URL and the expected race ID")
    scope = "https://www.googleapis.com/auth/spreadsheets"
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
        scopes=[scope if write else scope + ".readonly"])
    session = AuthorizedSession(credentials)
    base = "https://sheets.googleapis.com/v4/spreadsheets/" + os.environ["JRA_SHEET_ID"]
    def read(a1):
        response = session.get(base + "/values/" + quote(a1, safe=""),
                               params={"valueRenderOption": "UNFORMATTED_VALUE"} if write or verify_replay else None,
                               timeout=30)
        if response.status_code != 200:
            raise SystemExit(f"Sheets read failed: HTTP {response.status_code}")
        return response.json().get("values", [])
    validate_spec(read("'現行仕様'!A1:B13"))
    for tab in ("全レース台帳", "全出走馬成績"):
        if read(f"'{tab}'!A1:A1") != [["レースID"]]:
            raise SystemExit("Ledger header check failed")
    def read_ledgers():
        return read("'全レース台帳'!A:Z"), read("'全出走馬成績'!A:AA")

    races, runners = read_ledgers()
    if write:
        from jra_result import candidate_from_url
        from save_result import RACE_TAB, RUNNERS_TAB, save_result

        candidate = candidate_from_url(source_url, expected_key, races[0], runners[0])
        race = dict(zip(races[0], candidate["race"]))
        if race["開催日"] >= race["取得日"]:
            raise ValueError("Writes are limited to dates before the current JRA day")
        race["検証状態"] = race["検証状態"].replace("掲載全頭候補", "掲載全頭確認・公式馬ID")
        candidate["race"] = [race[field] for field in races[0]]
        response = session.get(base, params={"fields": "sheets.properties(sheetId,title)"}, timeout=30)
        if response.status_code != 200:
            raise SystemExit(f"Sheets metadata read failed: HTTP {response.status_code}")
        sheet_ids = {p["title"]: p["sheetId"] for item in response.json()["sheets"]
                     if (p := item["properties"])["title"] in {RACE_TAB, RUNNERS_TAB}}

        def append(requests):
            try:
                response = session.post(base + ":batchUpdate", json={"requests": requests}, timeout=30)
            except RequestException:
                raise SystemExit("Sheets write response missing. Re-read both ledgers before retrying.") from None
            if response.status_code != 200:
                raise SystemExit(f"Sheets write returned HTTP {response.status_code}. Re-read both ledgers before retrying.")

        print(json.dumps(save_result(candidate, read_ledgers, sheet_ids, append), sort_keys=True))
        return
    if verify_saved:
        from jra_result import candidate_from_url
        print(json.dumps(verify_saved_result(races, runners, candidate_from_url, verify_replay), sort_keys=True))
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


def prediction_time_check(observed_at, predicted_at, start_at):
    """Check temporal eligibility only; never grant publication or training approval."""
    from datetime import datetime, timedelta
    try:
        times = [datetime.fromisoformat(v) for v in (observed_at, predicted_at, start_at)]
        if any(t.utcoffset() != timedelta(hours=9) for t in times):
            return "HOLD_UNKNOWN_TIME"
    except (ValueError, TypeError):
        return "HOLD_UNKNOWN_TIME"
    observed, predicted, start = times
    if predicted >= start or observed >= start:
        return "HOLD_POST_START"
    if observed > predicted:
        return "HOLD_AFTER_PREDICTION"
    return "TIME_PASS"



def test_prediction_time_check():
    """Regression cases for temporal eligibility."""
    p = "2026-11-28T12:00:00+09:00"
    s = "2026-11-28T12:30:00+09:00"
    cases = [
        (p, p, s, "TIME_PASS"),
        ("2026-11-28T11:59:59+09:00", p, s, "TIME_PASS"),
        ("2026-11-28T12:00:01+09:00", p, s, "HOLD_AFTER_PREDICTION"),
        (s, p, s, "HOLD_POST_START"),
        (p, s, s, "HOLD_POST_START"),
        ("2026-11-28T12:30:01+09:00", p, s, "HOLD_POST_START"),
        (None, p, s, "HOLD_UNKNOWN_TIME"),
        ("2026-11-28T11:00:00", p, s, "HOLD_UNKNOWN_TIME"),
        ("invalid", p, s, "HOLD_UNKNOWN_TIME"),
        (p, p, None, "HOLD_UNKNOWN_TIME"),
    ]
    for observed, predicted, start, expected in cases:
        actual = prediction_time_check(observed, predicted, start)
        if actual != expected:
            raise AssertionError((observed, predicted, start, expected, actual))
    return len(cases)



def prediction_identity_check(expected, observed):
    """Compare canonical race, entrant number and verified identity keys."""
    if not isinstance(expected, dict) or not isinstance(observed, dict):
        return "HOLD_UNKNOWN_IDENTITY"
    for key in ("race_id", "entrant_number", "entrant_id"):
        left, right = expected.get(key), observed.get(key)
        if key == "entrant_number":
            valid = all(type(v) is int and 1 <= v <= 18 for v in (left, right))
        else:
            valid = all(isinstance(v, str) and v.strip() and v == v.strip() for v in (left, right))
        if not valid:
            return "HOLD_UNKNOWN_IDENTITY"
        if left != right:
            return "HOLD_IDENTITY_MISMATCH"
    return "IDENTITY_MATCH"


def test_prediction_identity_check():
    base = {"race_id": "race-a", "entrant_number": 1, "entrant_id": "000001"}
    cases = [(base, "IDENTITY_MATCH"),
             (dict(base, race_id="race-b"), "HOLD_IDENTITY_MISMATCH"),
             (dict(base, entrant_number=2), "HOLD_IDENTITY_MISMATCH"),
             (dict(base, entrant_id="000002"), "HOLD_IDENTITY_MISMATCH"),
             (dict(base, entrant_id=None), "HOLD_UNKNOWN_IDENTITY"),
             (dict(base, entrant_number=True), "HOLD_UNKNOWN_IDENTITY"),
             (dict(base, entrant_number=19), "HOLD_UNKNOWN_IDENTITY"),
             (dict(base, entrant_number=0), "HOLD_UNKNOWN_IDENTITY")]
    for observed, expected in cases:
        actual = prediction_identity_check(base, observed)
        if actual != expected:
            raise AssertionError((observed, expected, actual))
    return len(cases)



def prediction_input_check(expected, observed, observed_at, predicted_at, start_at, phase):
    """Candidate eligibility only; provenance and official approval remain separate."""
    if phase != "pre_race":
        return "HOLD_NON_PRE_RACE"
    identity = prediction_identity_check(expected, observed)
    if identity != "IDENTITY_MATCH":
        return identity
    timing = prediction_time_check(observed_at, predicted_at, start_at)
    if timing != "TIME_PASS":
        return timing
    return "CANDIDATE_ONLY"


def test_prediction_input_check():
    base = {"race_id": "race-a", "entrant_number": 1, "entrant_id": "000001"}
    p = "2026-11-28T12:00:00+09:00"
    s = "2026-11-28T12:30:00+09:00"
    cases = [
        (base, p, "pre_race", "CANDIDATE_ONLY"),
        (base, p, "result", "HOLD_NON_PRE_RACE"),
        (base, p, None, "HOLD_NON_PRE_RACE"),
        (dict(base, race_id="race-b"), p, "pre_race", "HOLD_IDENTITY_MISMATCH"),
        (base, s, "pre_race", "HOLD_POST_START"),
        (base, None, "pre_race", "HOLD_UNKNOWN_TIME"),
        (base, "2026-11-28T12:01:00+09:00", "pre_race", "HOLD_AFTER_PREDICTION"),
    ]
    for observed, stamp, phase, expected in cases:
        actual = prediction_input_check(base, observed, stamp, p, s, phase)
        if actual != expected:
            raise AssertionError((expected, actual))
    return len(cases)


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--self-test"]:
        self_test()
        print("prediction timestamp cases:", test_prediction_time_check())
        print("prediction identity cases:", test_prediction_identity_check())
        print("prediction input cases:", test_prediction_input_check())
    elif len(sys.argv) == 3 and sys.argv[1] == "--candidate":
        main(sys.argv[2])
    elif len(sys.argv) == 4 and sys.argv[1] == "--official-result":
        main(source_url=sys.argv[2], expected_key=sys.argv[3])
    elif len(sys.argv) == 4 and sys.argv[1] == "--save-official-result":
        main(source_url=sys.argv[2], expected_key=sys.argv[3], write=True)
    elif sys.argv[1:] == ["--verify-saved-result"]:
        main(verify_saved=True)
    elif sys.argv[1:] == ["--verify-intake-replay"]:
        main(verify_saved=True, verify_replay=True)
    elif len(sys.argv) == 1:
        main()
    else:
        raise SystemExit("Usage: check_sheets.py [--self-test | --candidate FILE | --official-result URL RACE_ID | --save-official-result URL RACE_ID | --verify-saved-result | --verify-intake-replay]")

