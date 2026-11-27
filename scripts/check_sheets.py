"""Read-only connectivity and schema check; never log credentials or cell values."""
import json
import os
import re
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



def prediction_input_check(expected, observed, observed_at, predicted_at, start_at, phase, *, acquired_at=None):
    """Candidate eligibility only; provenance and official approval remain separate."""
    if phase != "pre_race":
        return "HOLD_NON_PRE_RACE"
    identity = prediction_identity_check(expected, observed)
    if identity != "IDENTITY_MATCH":
        return identity
    timing = prediction_acquisition_check(observed_at, acquired_at, predicted_at, start_at)
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
        actual = prediction_input_check(base, observed, stamp, p, s, phase, acquired_at=stamp)
        if actual != expected:
            raise AssertionError((expected, actual))
    extra = [(None, "HOLD_UNKNOWN_TIME"), ("2026-11-28T12:00:01+09:00", "HOLD_AFTER_PREDICTION")]
    for acquired, expected in extra:
        actual = prediction_input_check(base, base, p, p, s, "pre_race", acquired_at=acquired)
        if actual != expected:
            raise AssertionError((expected, actual))
    return len(cases) + len(extra)



def prediction_acquisition_check(observed_at, acquired_at, predicted_at, start_at):
    """Require both observation and actual acquisition before prediction."""
    observation = prediction_time_check(observed_at, predicted_at, start_at)
    if observation != "TIME_PASS":
        return observation
    acquisition = prediction_time_check(acquired_at, predicted_at, start_at)
    if acquisition != "TIME_PASS":
        return acquisition
    from datetime import datetime
    if datetime.fromisoformat(acquired_at) < datetime.fromisoformat(observed_at):
        return "HOLD_ACQUISITION_ORDER"
    return "TIME_PASS"


def test_prediction_acquisition_check():
    o = "2026-11-28T11:00:00+09:00"
    p = "2026-11-28T12:00:00+09:00"
    s = "2026-11-28T12:30:00+09:00"
    cases = [(o, "TIME_PASS"), (p, "TIME_PASS"),
             (None, "HOLD_UNKNOWN_TIME"),
             ("2026-11-28T10:59:59+09:00", "HOLD_ACQUISITION_ORDER"),
             ("2026-11-28T12:00:01+09:00", "HOLD_AFTER_PREDICTION"),
             (s, "HOLD_POST_START")]
    for acquired, expected in cases:
        actual = prediction_acquisition_check(o, acquired, p, s)
        if actual != expected:
            raise AssertionError((acquired, expected, actual))
    return len(cases)


def prediction_feature_coverage(expected, observations, required, predicted_at, start_at):
    """Read-only candidate coverage; never grants training or publication approval."""
    states = {}
    for name in dict.fromkeys(required):
        item = observations.get(name)
        if not isinstance(item, dict) or item.get("value") is None or item.get("value") == "":
            states[name] = "HOLD_MISSING"
            continue
        states[name] = prediction_input_check(
            expected, item.get("identity"), item.get("observed_at"),
            predicted_at, start_at, item.get("phase"),
            acquired_at=item.get("acquired_at"))
    eligible = sum(v == "CANDIDATE_ONLY" for v in states.values())
    return {"required": len(states), "eligible": eligible, "states": states,
            "complete": bool(states) and eligible == len(states),
            "trainingAllowed": False, "publicationAllowed": False}


def test_prediction_feature_coverage():
    identity = {"race_id": "fixture", "entrant_number": 1, "entrant_id": "000001"}
    p = "2026-11-28T12:00:00+09:00"
    s = "2026-11-28T12:30:00+09:00"
    item = {"identity": identity, "value": 0, "observed_at": p,
            "acquired_at": p, "phase": "pre_race"}
    result = prediction_feature_coverage(identity, {"a": item, "b": dict(item, phase="result")},
                                         ["a", "b", "c"], p, s)
    assert result["eligible"] == 1 and result["required"] == 3
    assert result["states"] == {"a": "CANDIDATE_ONLY", "b": "HOLD_NON_PRE_RACE", "c": "HOLD_MISSING"}
    assert not result["complete"] and not result["trainingAllowed"] and not result["publicationAllowed"]
    assert prediction_feature_coverage(identity, {"a": item}, ["a"], p, s)["complete"]
    assert not prediction_feature_coverage(identity, {}, [], p, s)["complete"]
    return 3


def prediction_race_coverage(race_id, roster, observations, required, predicted_at, start_at):
    """Audit required features for every entrant in the trusted pre-race roster.

    Observations are grouped by integer entrant number. The caller supplies
    the complete roster and the required features for this race's conditions.
    """
    required = tuple(dict.fromkeys(required))
    entrants = {}
    entrant_ids = set()
    race_state = prediction_time_check(predicted_at, predicted_at, start_at)
    if race_state == "TIME_PASS":
        race_state = "CANDIDATE_ONLY" if roster and required else "HOLD_MISSING"
    for identity in roster:
        if prediction_identity_check(identity, identity) != "IDENTITY_MATCH":
            race_state = "HOLD_UNKNOWN_IDENTITY"
            break
        number = identity["entrant_number"]
        entrant_id = identity["entrant_id"]
        if identity["race_id"] != race_id or number in entrants or entrant_id in entrant_ids:
            race_state = "HOLD_IDENTITY_MISMATCH"
            break
        entrant_ids.add(entrant_id)
        entrants[number] = prediction_feature_coverage(
            identity, observations.get(number, {}), required, predicted_at, start_at)
    else:
        if race_state == "CANDIDATE_ONLY" and not all(row["complete"] for row in entrants.values()):
            race_state = "HOLD_INCOMPLETE_FEATURES"
    return {
        "state": race_state,
        "entrants": entrants,
        "trainingAllowed": False,
        "publicationAllowed": False,
    }


def test_prediction_race_coverage():
    predicted = "2026-11-28T12:00:00+09:00"
    start = "2026-11-28T12:30:00+09:00"
    first = {"race_id": "fixture", "entrant_number": 1, "entrant_id": "000001"}
    second = dict(first, entrant_number=2, entrant_id="000002")
    roster = [first, second]
    feature = {"identity": first, "value": 0, "phase": "pre_race",
               "observed_at": predicted, "acquired_at": predicted}
    second_feature = dict(feature, identity=second)
    observations = {1: {"form": feature}, 2: {"form": second_feature}}

    def audit(entries=roster, data=observations, cutoff=start):
        return prediction_race_coverage("fixture", entries, data, ["form"], predicted, cutoff)

    complete = audit()
    assert complete["state"] == "CANDIDATE_ONLY"
    assert not complete["trainingAllowed"] and not complete["publicationAllowed"]
    for replacement, reason in (
        ({}, "HOLD_MISSING"),
        ({"form": feature}, "HOLD_IDENTITY_MISMATCH"),
        ({"form": dict(second_feature, acquired_at=start)}, "HOLD_POST_START"),
    ):
        report = audit(data={1: observations[1], 2: replacement})
        assert report["state"] == "HOLD_INCOMPLETE_FEATURES"
        assert report["entrants"][2]["states"]["form"] == reason
    assert audit(entries=[first, dict(second, entrant_id=first["entrant_id"])])["state"] == "HOLD_IDENTITY_MISMATCH"
    assert audit(entries=[first, first])["state"] == "HOLD_IDENTITY_MISMATCH"
    assert audit(entries=[])["state"] == "HOLD_MISSING"
    assert audit(cutoff=predicted)["state"] == "HOLD_POST_START"
    return 8


def sheet_capture_time(value):
    """Parse an explicit JST timestamp, preserving its recorded precision."""
    from datetime import datetime, timedelta
    if not isinstance(value, str):
        return None
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}(?::[0-9]{2})? JST", value):
        value = value.removesuffix(" JST") + "+09:00"
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    if stamp.utcoffset() != timedelta(hours=9):
        return None
    return stamp.isoformat()



def jra_past_form_audit(race_id, race_day, roster, expected_count, form_rows, predicted_at, start_at):
    """Join the official pre-race card to saved historical snapshots by horse ID.

    The roster must come from the official card, with identity already verified.
    Form rows use the existing 出走馬データ header and contain no horse numbers.
    """
    from collections import Counter, defaultdict
    from datetime import date

    if type(expected_count) is not int or not 1 <= expected_count <= 18 or len(roster) != expected_count:
        return {"state": "HOLD_INCOMPLETE_ROSTER", "entrants": {},
                "trainingAllowed": False, "publicationAllowed": False}
    numbers = [row.get("entrant_number") for row in roster]
    if (any(type(number) is not int or not 1 <= number <= 18 for number in numbers)
            or len(set(numbers)) != expected_count
            or any(row.get("race_id") != race_id for row in roster)):
        return {"state": "HOLD_IDENTITY_MISMATCH", "entrants": {},
                "trainingAllowed": False, "publicationAllowed": False}
    unknown_ids = [row["entrant_number"] for row in roster
                   if not isinstance(row.get("entrant_id"), str)
                   or not re.fullmatch(r"[0-9]{10}", row["entrant_id"])]
    fields = ("馬ID", "対象レースID", "近走日", "着順", "出典URL", "取得日時")
    if not form_rows or any(form_rows[0].count(field) != 1 for field in fields):
        raise ValueError("Past-form ledger schema missing or ambiguous")
    from urllib.parse import urlsplit

    by_id = defaultdict(list)
    matched_rows = Counter()
    rejected_rows = defaultdict(Counter)
    target_day = date.fromisoformat(race_day)
    prediction, start = map(sheet_capture_time, (predicted_at, start_at))
    for values in form_rows[1:]:
        row = dict(zip(form_rows[0], values))
        if row.get("対象レースID") != race_id:
            continue
        horse_id = row.get("馬ID")
        matched_rows[horse_id] += 1
        if not str(row.get("着順") or "").strip():
            rejected_rows[horse_id]["HOLD_MISSING_FINISH"] += 1
            continue
        try:
            prior_day = date.fromisoformat(row.get("近走日", ""))
            sources = [urlsplit(url) for url in (row.get("出典URL") or "").split()]
        except (TypeError, ValueError):
            rejected_rows[horse_id]["HOLD_INVALID_DATE_OR_SOURCE"] += 1
            continue
        if prior_day >= target_day:
            rejected_rows[horse_id]["HOLD_NON_PRIOR_RACE"] += 1
            continue
        if not sources or any(
            url.scheme != "https" or url.netloc not in ("www.jra.go.jp", "jra.go.jp") for url in sources
        ):
            rejected_rows[horse_id]["HOLD_UNVERIFIED_SOURCE"] += 1
            continue
        captured = sheet_capture_time(row.get("取得日時"))
        if not captured or captured[:10] < prior_day.isoformat():
            rejected_rows[horse_id]["HOLD_UNKNOWN_OR_INVALID_CAPTURE"] += 1
            continue
        by_id[horse_id].append((prior_day, captured, str(row["着順"]).strip()))

    observations = {}
    form_counts = {}
    conflicting_days = {}
    form_evidence = {}
    for identity in roster:
        if identity["entrant_number"] in unknown_ids:
            continue
        horse_id = identity["entrant_id"]
        snapshots = by_id[horse_id]
        number = identity["entrant_number"]
        finishes_by_day = defaultdict(set)
        holds = rejected_rows[horse_id].copy()
        for day, stamp, finish in snapshots:
            timing = prediction_acquisition_check(stamp, stamp, prediction, start)
            if timing == "TIME_PASS":
                finishes_by_day[day].add(finish)
            else:
                holds[timing] += 1
        conflicting_days[number] = sorted(day.isoformat() for day, finishes in finishes_by_day.items()
                                          if len(finishes) > 1)
        eligible_days = {day for day, finishes in finishes_by_day.items() if len(finishes) == 1}
        form_counts[number] = len(eligible_days)
        form_evidence[number] = {"ledgerRows": matched_rows[horse_id],
                                 "eligibleDays": len(eligible_days), "heldRows": dict(holds)}
        eligible = [(day, stamp) for day, stamp, _ in snapshots if day in eligible_days
                    and prediction_acquisition_check(stamp, stamp, prediction, start) == "TIME_PASS"]
        held = [(day, stamp) for day, stamp, _ in snapshots if day not in finishes_by_day]
        selected = max(eligible or held, default=None)
        if selected is None:
            continue
        prior_day, captured = selected
        number = identity["entrant_number"]
        observations[number] = {"past_form": {
            "identity": identity, "value": prior_day.isoformat(), "phase": "pre_race",
            "observed_at": captured, "acquired_at": captured}}
    known = [row for row in roster if row["entrant_number"] not in unknown_ids]
    report = prediction_race_coverage(race_id, known, observations, ["past_form"], prediction, start)
    if unknown_ids:
        report["state"] = "HOLD_UNKNOWN_IDENTITY"
    review = {
        "verifyHorseId": sorted(unknown_ids),
        "missingLedgerRows": sorted(number for number, evidence in form_evidence.items()
                                    if not evidence["ledgerRows"]),
        "reviewHeldForm": sorted(number for number, evidence in form_evidence.items()
                                 if evidence["ledgerRows"] and not evidence["eligibleDays"]),
    }
    return dict(report, rosterCount=expected_count, missingIds=unknown_ids,
                formCounts=form_counts, conflictingFormDays=conflicting_days,
                formEvidence=form_evidence, review=review)


def test_jra_past_form_audit():
    roster = [{"race_id": "2026-11-29-TOKYO-12", "entrant_number": 1, "entrant_id": "2022105102"}]
    race_id = roster[0]["race_id"]
    row = ["2022105102", race_id, "2026-11-01", "5", "https://www.jra.go.jp/JRADB/example", "2026-11-27 20:29 JST"]
    headers = ["馬ID", "対象レースID", "近走日", "着順", "出典URL", "取得日時"]

    def audit(rows, cutoff="2026-11-29 15:00 JST", count=1):
        return jra_past_form_audit(race_id, "2026-11-29", roster, count, [headers, *rows],
                                   cutoff, "2026-11-29 15:40 JST")

    assert audit([row])["state"] == "CANDIDATE_ONLY"
    assert audit([row], count=2)["state"] == "HOLD_INCOMPLETE_ROSTER"
    assert audit([row], cutoff="2026-11-27 20:00 JST")["entrants"][1]["states"]["past_form"] == "HOLD_AFTER_PREDICTION"
    assert audit([row[:2] + ["2026-11-29"] + row[3:]])["entrants"][1]["states"]["past_form"] == "HOLD_MISSING"
    partial = jra_past_form_audit(race_id, "2026-11-29",
                                  [{"race_id": race_id, "entrant_number": 2, "entrant_id": None}, *roster],
                                  2, [headers, row], "2026-11-29 15:00 JST", "2026-11-29 15:40 JST")
    assert partial["state"] == "HOLD_UNKNOWN_IDENTITY" and partial["missingIds"] == [2]
    assert partial["entrants"][1]["states"]["past_form"] == "CANDIDATE_ONLY"
    assert audit([row, row])["formCounts"] == {1: 1}
    conflict = row[:3] + ["2"] + row[4:]
    assert audit([row, conflict])["conflictingFormDays"] == {1: ["2026-11-01"]}
    assert audit([row, conflict])["state"] == "HOLD_INCOMPLETE_FEATURES"
    assert audit([row, conflict[:5] + ["2026-11-29 15:01 JST"]])["formCounts"] == {1: 1}
    assert audit([])["formEvidence"][1] == {"ledgerRows": 0, "eligibleDays": 0, "heldRows": {}}
    unknown_time = row[:5] + ["2026-11-27"]
    assert audit([unknown_time])["formEvidence"][1]["heldRows"] == {"HOLD_UNKNOWN_OR_INVALID_CAPTURE": 1}
    assert audit([row], cutoff="2026-11-27 20:00 JST")["formEvidence"][1]["heldRows"] == {"HOLD_AFTER_PREDICTION": 1}
    assert audit([])["review"]["missingLedgerRows"] == [1]
    assert audit([unknown_time])["review"]["reviewHeldForm"] == [1]
    assert partial["review"]["verifyHorseId"] == [2]
    duplicate = [roster[0], dict(roster[0], entrant_id=None)]
    assert jra_past_form_audit(race_id, "2026-11-29", duplicate, 2, [headers, row],
                               "2026-11-29 15:00 JST", "2026-11-29 15:40 JST")["state"] == "HOLD_IDENTITY_MISMATCH"
    return 16


def jra_profile_id(profile_url, card_name, profile_name):
    """Bind an independently read JRA profile heading to the racecard name."""
    from unicodedata import normalize
    from urllib.parse import parse_qs, urlsplit

    if not all(isinstance(value, str) and value.strip()
               for value in (profile_url, card_name, profile_name)):
        return None
    if normalize("NFKC", card_name).strip() != normalize("NFKC", profile_name).strip():
        return None
    try:
        source = urlsplit(profile_url)
        query = parse_qs(source.query, keep_blank_values=True)
    except ValueError:
        return None
    if (source.scheme != "https" or source.netloc not in ("www.jra.go.jp", "jra.go.jp")
            or source.path != "/JRADB/accessU.html" or source.fragment
            or set(query) != {"CNAME"} or len(query["CNAME"]) != 1):
        return None
    match = re.fullmatch(r"pw01dud[01]0([0-9]{10})/[A-F0-9]{2}", query["CNAME"][0])
    return match[1] if match else None


def test_jra_profile_id():
    name = "クロワデュノール"
    url = "https://www.jra.go.jp/JRADB/accessU.html?CNAME=pw01dud002022105102%2F98"
    assert jra_profile_id(url, name, name) == "2022105102"
    assert jra_profile_id(url.replace("dud00", "dud10"), name, name) == "2022105102"
    assert jra_profile_id(url, name, "別の馬") is None
    assert jra_profile_id(url.replace("www.jra.go.jp", "example.test"), name, name) is None
    assert jra_profile_id(url + "&CNAME=another", name, name) is None
    assert jra_profile_id(url.replace("accessU", "accessD"), name, name) is None
    return 6


def audit_past_form_file(path, *, public_summary=False):
    """Read a local export of one or more verified rosters and saved form ledgers."""
    with open(path, encoding="utf-8") as stream:
        data = json.load(stream)

    def audit(snapshot):
        return jra_past_form_audit(
            snapshot["race_id"], snapshot["race_day"], snapshot["roster"],
            snapshot["expected_count"], snapshot["form_rows"],
            snapshot["predicted_at"], snapshot["start_at"],
        )

    if public_summary and "snapshots" not in data:
        raise ValueError("Public summaries require a snapshots list with explicit race IDs")
    if "snapshots" in data:
        snapshots = data["snapshots"]
        if not isinstance(snapshots, list) or not snapshots:
            raise ValueError("Expected a nonempty snapshots list")
        reports = {}
        for snapshot in snapshots:
            key = snapshot["race_id"]
            if (not re.fullmatch(r"\d{4}-\d{2}-\d{2}-[A-Z]+-(?:0[1-9]|1[0-2])", key)
                    or key[:10] != snapshot["race_day"] or key in reports):
                raise ValueError("Invalid or duplicate audit race ID")
            report = audit(snapshot)
            if public_summary:
                review = report.get("review")
                if review is None:
                    raise ValueError("Cannot export aggregate counts from an incomplete roster audit")
                as_of = sheet_capture_time(snapshot["predicted_at"])
                if as_of is None:
                    raise ValueError("Public aggregate requires a JST prediction cutoff")
                reports[key] = {
                    "state": report["state"],
                    "asOf": as_of,
                    "expectedCount": snapshot["expected_count"],
                    "missingIdCount": len(review.get("verifyHorseId", [])),
                    "missingFormCount": len(review.get("missingLedgerRows", [])),
                    "heldFormCount": len(review.get("reviewHeldForm", [])),
                }
            else:
                reports[key] = report
        schema = "jra-public-prediction-coverage-v1" if public_summary else "jra-prediction-audit-list-v1"
        output = {"schema": schema, "reports": reports}
    else:
        output = audit(data)
    print(json.dumps(output, ensure_ascii=False, indent=2))


def corner_position_study(races, runners, *, track, surface, distance, going, first3_cutoff=None, race_class=None):
    """Describe final-corner positions in complete races with matching conditions."""
    from collections import Counter, defaultdict

    from decimal import Decimal, InvalidOperation

    def seconds(raw):
        try:
            value = Decimal(str(raw))
        except InvalidOperation:
            return None
        return value if value.is_finite() and value > 0 else None

    cutoff = seconds(first3_cutoff)
    if first3_cutoff is not None and cutoff is None:
        raise ValueError("Expected a positive front 3F cutoff")
    race_fields = {"レースID", "競馬場", "芝ダート障害", "距離m", "馬場状態", "頭数"}
    if cutoff is not None:
        race_fields.add("前半3F")
    if race_class is not None:
        race_fields.add("クラス")
    runner_fields = {"レースID", "馬番", "着順", "4角"}
    if not races or not runners or not race_fields.issubset(races[0]) or not runner_fields.issubset(runners[0]):
        raise ValueError("Corner study ledger schema missing")
    by_key = defaultdict(list)
    for values in runners[1:]:
        row = dict(zip(runners[0], values))
        by_key[row.get("レースID")].append(row)

    def empty_counts():
        return {key: Counter(runners=0, wins=0, top_three=0)
                for key in ("1_to_4", "5_to_8", "9_plus")}

    counts = empty_counts()
    pace = {band: {"race_ids": [], "positions": empty_counts()} for band in ("at_most", "above")}
    pace_held = []
    included, held, seen = [], {}, set()
    nonstarters = {"取消", "除外", "出走取消", "競走除外"}
    for values in races[1:]:
        card = dict(zip(races[0], values))
        if (card.get("競馬場"), card.get("芝ダート障害"), str(card.get("距離m")), card.get("馬場状態")) != (
            track, surface, str(distance), going
        ):
            continue
        if race_class is not None and card.get("クラス") != race_class:
            continue
        key = card["レースID"]
        if not key or key in seen:
            raise ValueError("Duplicate or missing race key")
        seen.add(key)
        rows = by_key[key]
        starters = [row for row in rows if str(row.get("着順", "")).strip() not in nonstarters]
        numbers = [str(row.get("馬番", "")).strip() for row in rows]
        if (str(len(starters)) != str(card.get("頭数")) or not starters
                or any(not n.isdecimal() or int(n) <= 0 for n in numbers)
                or len({int(n) for n in numbers}) != len(numbers)):
            held[key] = "incomplete_roster"
            continue
        finishes = [str(row.get("着順", "")).strip() for row in starters]
        corners = [str(row.get("4角", "")).strip() for row in starters]
        if (any(not n.isdecimal() or int(n) <= 0 for n in finishes + corners)
                or sorted(int(n) for n in finishes if int(n) <= 3) != [1, 2, 3]):
            held[key] = "finish_or_corner_unusable"
            continue
        included.append(key)
        targets = [counts]
        if cutoff is not None:
            front = seconds(card.get("前半3F"))
            if front is None:
                pace_held.append(key)
            else:
                bucket = pace["at_most" if front <= cutoff else "above"]
                bucket["race_ids"].append(key)
                targets.append(bucket["positions"])
        for finish, corner in zip(map(int, finishes), map(int, corners)):
            band = "1_to_4" if corner <= 4 else "5_to_8" if corner <= 8 else "9_plus"
            for target in targets:
                target[band]["runners"] += 1
                target[band]["wins"] += finish == 1
                target[band]["top_three"] += finish <= 3
    report = {"race_ids": included, "held": held, "positions": counts}
    if cutoff is not None:
        report["pace"] = {"first3_cutoff": str(cutoff), "groups": pace, "held_race_ids": pace_held}
    return report


def test_corner_position_study():
    races = [["レースID", "競馬場", "芝ダート障害", "距離m", "馬場状態", "頭数"],
             ["race", "京都", "芝", "1200", "良", "3"]]
    runners = [["レースID", "馬番", "着順", "4角"],
               ["race", "1", "1", "2"], ["race", "2", "2", "5"],
               ["race", "3", "3", "9"], ["race", "4", "除外", ""]]
    def study(rows):
        return corner_position_study(races, rows, track="京都", surface="芝", distance=1200, going="良")
    report = study(runners)
    assert report["race_ids"] == ["race"]
    assert report["positions"]["1_to_4"]["wins"] == 1
    assert study(runners[:-2])["held"] == {"race": "incomplete_roster"}
    assert study([*runners, runners[-1]])["held"] == {"race": "incomplete_roster"}
    assert study([*runners[:3], ["race", "3", "3", ""], runners[-1]])["held"] == {"race": "finish_or_corner_unusable"}
    races[0].extend(["前半3F", "クラス"])
    races[1].extend(["34.0", "1勝クラス"])
    options = {"track": "京都", "surface": "芝", "distance": 1200, "going": "良",
               "first3_cutoff": 34, "race_class": "1勝クラス"}
    assert corner_position_study(races, runners, **options)["pace"]["groups"]["at_most"]["race_ids"] == ["race"]
    races[1][-2] = ""
    assert corner_position_study(races, runners, **options)["pace"]["held_race_ids"] == ["race"]
    assert not corner_position_study(races, runners, **dict(options, race_class="未勝利"))["race_ids"]
    return 8


def study_corner_file(path):
    with open(path, encoding="utf-8") as stream:
        snapshot = json.load(stream)
    result = corner_position_study(snapshot["races"], snapshot["runners"],
                                   **snapshot["conditions"])
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--self-test"]:
        self_test()
        print("corner study cases:", test_corner_position_study())
        print("prediction timestamp cases:", test_prediction_time_check())
        print("prediction identity cases:", test_prediction_identity_check())
        print("prediction input cases:", test_prediction_input_check())
        print("prediction acquisition cases:", test_prediction_acquisition_check())
        print("prediction coverage cases:", test_prediction_feature_coverage())
        print("prediction race coverage cases:", test_prediction_race_coverage())
        print("source adapter cases:", test_jra_past_form_audit())
        print("official profile identity cases:", test_jra_profile_id())
    elif len(sys.argv) == 3 and sys.argv[1] == "--audit-past-form-public":
        audit_past_form_file(sys.argv[2], public_summary=True)
    elif len(sys.argv) == 3 and sys.argv[1] == "--audit-past-form":
        audit_past_form_file(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "--study-corners":
        study_corner_file(sys.argv[2])
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
        raise SystemExit("Usage: check_sheets.py [--self-test | --study-corners FILE | --audit-past-form FILE | --audit-past-form-public FILE | --candidate FILE | --official-result URL RACE_ID | --save-official-result URL RACE_ID | --verify-saved-result | --verify-intake-replay]")

