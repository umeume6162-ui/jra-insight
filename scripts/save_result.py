"""Append one official result to the existing Sheets ledgers and verify it."""
from decimal import Decimal
import re

from check_sheets import audit_ledgers, plan_intake


RACE_TAB = "全レース台帳"
RUNNERS_TAB = "全出走馬成績"
NUMERIC_FIELDS = {
    "開催回", "日次", "R", "距離m", "頭数", "前半3F", "後半3F",
    "馬番", "枠番", "年齢", "斤量", "着順", "1角", "2角", "3角", "4角",
    "上がり3F", "人気", "馬体重", "増減",
}
MATCH_EXCEPTIONS = {"取得日", "検証状態"}


def cell(row, index):
    return str(row[index]).strip() if index < len(row) and row[index] is not None else ""


def normalized(field, value):
    if field in NUMERIC_FIELDS and re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value):
        return Decimal(value)
    return value


def records_for(ledger, race_key):
    index = ledger[0].index("レースID")
    return [row for row in ledger[1:] if cell(row, index) == race_key]


def matches(header, actual, expected):
    def row_values(row):
        return tuple(normalized(field, cell(row, index)) for index, field in enumerate(header)
                     if field not in MATCH_EXCEPTIONS)

    if "馬番" in header:
        by_number = header.index("馬番")
        def number_key(row):
            value = normalized("馬番", cell(row, by_number))
            if not isinstance(value, Decimal) or value != value.to_integral_value():
                raise ValueError("Saved runner number is invalid")
            return value
        actual = sorted(actual, key=number_key)
        expected = sorted(expected, key=number_key)
    return [row_values(row) for row in actual] == [row_values(row) for row in expected]


def append_request(sheet_id, header, records):
    rows = []
    for record in records:
        values = []
        for index, field in enumerate(header):
            value = normalized(field, cell(record, index))
            if isinstance(value, Decimal):
                number = int(value) if value == value.to_integral_value() else float(value)
                values.append({"userEnteredValue": {"numberValue": number}})
            elif value:
                values.append({"userEnteredValue": {"stringValue": value}})
            else:
                values.append({})
        rows.append({"values": values})
    return {"appendCells": {"sheetId": sheet_id, "rows": rows, "fields": "userEnteredValue"}}


def save_result(candidate, read_ledgers, sheet_ids, append):
    """Recheck existing keys immediately before a single write for both tabs."""
    races, runners = read_ledgers()
    for ledger, fields in ((races, {"レースID", "頭数", "取得日", "出典URL"}),
                           (runners, {"レースID", "馬ID", "馬番", "馬名", "着順", "取得日", "出典URL"})):
        if not ledger or not fields.issubset(ledger[0]) or len(set(ledger[0])) != len(ledger[0]):
            raise ValueError("Required ledger headers are missing or duplicated")
    if set(sheet_ids) != {RACE_TAB, RUNNERS_TAB} or len(set(sheet_ids.values())) != 2:
        raise ValueError("Unexpected ledger sheet mapping")

    race, horses = candidate["race"], candidate["runners"]
    plan = plan_intake([races[0]], [runners[0]], race, horses)
    horse_id = runners[0].index("馬ID")
    ids = [cell(row, horse_id) for row in horses]
    if len(set(ids)) != len(horses) or any(not re.fullmatch(r"[0-9]{10}", value) for value in ids):
        raise ValueError("A writable candidate requires distinct official horse IDs")
    before = audit_ledgers(races, runners)
    if any(value for name, value in before.items() if name not in {"races", "runner_rows"}):
        raise ValueError("Existing ledger health check failed")
    key = cell(race, races[0].index("レースID"))
    old_race, old_horses = records_for(races, key), records_for(runners, key)
    if old_race or old_horses:
        if not matches(races[0], old_race, [race]) or not matches(runners[0], old_horses, horses):
            raise ValueError("Existing race differs from the official result or is incomplete")
        return {**plan, "verified_race_rows": 1, "verified_runner_rows": len(horses), "already_saved": True}

    append([
        append_request(sheet_ids[RACE_TAB], races[0], [race]),
        append_request(sheet_ids[RUNNERS_TAB], runners[0], horses),
    ])
    saved_races, saved_runners = read_ledgers()
    if saved_races[0] != races[0] or saved_runners[0] != runners[0]:
        raise ValueError("Ledger headers changed during post-write verification")
    if not matches(races[0], records_for(saved_races, key), [race]):
        raise ValueError("Post-write race verification failed")
    if not matches(runners[0], records_for(saved_runners, key), horses):
        raise ValueError("Post-write runner verification failed")
    return {**plan, "writes": 1 + len(horses), "verified_race_rows": 1,
            "verified_runner_rows": len(horses), "already_saved": False}
