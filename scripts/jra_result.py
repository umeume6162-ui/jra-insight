"""Retrieve and normalize one official JRA result for private intake."""
import re
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

from lxml import html


HOST = "www.jra.go.jp"
AGENT = "JRA-INSIGHT-Research/1.0"
TRACKS = dict(zip(
    ("札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"),
    ("SAPPORO", "HAKODATE", "FUKUSHIMA", "NIIGATA", "TOKYO", "NAKAYAMA",
     "CHUKYO", "KYOTO", "HANSHIN", "KOKURA"),
))
NONSTARTERS = ("取消", "除外", "出走取消", "競走除外")


def result_url(url):
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    if (parts.scheme != "https" or parts.netloc != HOST or parts.fragment
            or parts.path != "/JRADB/accessS.html" or set(query) != {"CNAME"}
            or len(query["CNAME"]) != 1
            or not re.fullmatch(r"pw01sde[0-9]{22}/[A-Fa-f0-9]{2}", query["CNAME"][0])):
        raise ValueError("Expected a single official JRA result URL")
    return query["CNAME"][0]


def retrieve_result(url):
    import requests

    result_url(url)

    def read(session, target, limit):
        with session.get(target, allow_redirects=False, timeout=(10, 20), stream=True) as response:
            if response.status_code != 200:
                raise ValueError(f"JRA response HTTP {response.status_code}")
            data = bytearray()
            for chunk in response.iter_content(8192):
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError("JRA response exceeds byte limit")
            return bytes(data)

    with requests.Session() as session:
        session.headers["User-Agent"] = AGENT
        robots = RobotFileParser()
        policy = read(session, f"https://{HOST}/robots.txt", 128_000).decode("utf-8-sig")
        if not re.search(r"(?im)^user-agent\s*:", policy):
            raise ValueError("JRA robots rules could not be verified")
        robots.parse(policy.splitlines())
        if not robots.can_fetch(AGENT, url):
            raise ValueError("JRA robots rules disallow result retrieval")
        raw = read(session, url, 2_000_000)
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp932")


def nodes(element, tag, cls):
    return element.xpath(
        f'.//{tag}[contains(concat(" ", normalize-space(@class), " "), $name)]',
        name=f" {cls} ",
    )


def one(matches):
    if len(matches) != 1:
        raise ValueError("Unexpected JRA result structure")
    return matches[0]


def value(element):
    return " ".join(element.text_content().split())


def parse_horse_weight(weight_text):
    """Keep a published weight and distinguish a missing previous weight from zero change."""
    weight_text = re.sub(r"\s+", "", weight_text)
    if weight_text in ("", "計不"):
        return weight_text, "", False
    match = re.fullmatch(r"(\d+)(?:\(([+-]?\d+|初出走)\))?", weight_text)
    if not match:
        raise ValueError("JRA horse weight format is unsupported")
    first_start = match[2] == "初出走"
    return match[1], "" if first_start else match[2] or "", first_start


def parse_result(document, url, expected_key, retrieved_date):
    token = result_url(url)[9:-3]
    root = html.fromstring(document)
    result = one(nodes(root, "div", "race_result_unit"))
    header = one(nodes(result, "div", "race_header"))

    def header_value(cls):
        return value(one(nodes(header, "*", cls)))

    day = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日（[^）]+） (\d+)回(\D+)(\d+)日",
                       header_value("date"))
    number = re.fullmatch(r"(\d+)レース", one(nodes(header, "div", "race_number")).xpath("string(./img/@alt)"))
    if not day or not number or day[5] not in TRACKS:
        raise ValueError("JRA page is missing the expected race identity")
    event = date(int(day[1]), int(day[2]), int(day[3]))
    race_number = int(number[1])
    track_code = tuple(TRACKS).index(day[5]) + 1
    key = f"{event.isoformat()}-{TRACKS[day[5]]}-{race_number:02d}"
    official_key = f"{track_code:02d}{event.year:04d}{int(day[4]):02d}{int(day[6]):02d}{race_number:02d}{event:%Y%m%d}"
    if key != expected_key or token != official_key or not 1 <= race_number <= 12:
        raise ValueError("JRA URL, page and requested race do not match")

    course = re.fullmatch(r"コース：([\d,]+)メートル（(.+)）", header_value("course"))
    if not course:
        raise ValueError("JRA course format is unsupported")
    surface_labels = one(nodes(header, "div", "baba")).xpath(".//li[not(contains(@class, 'weather'))]")
    surface_row = one(surface_labels)
    surface = value(one(nodes(surface_row, "span", "cap")))
    weather = value(one(nodes(one(nodes(header, "li", "weather")), "span", "txt")))
    condition = value(one(nodes(surface_row, "span", "txt")))
    distance = int(course[1].replace(",", ""))

    runners = []
    seen_numbers = set()
    seen_ids = set()
    for row in result.xpath("./table/tbody/tr"):
        def cell(cls):
            return one(nodes(row, "td", cls))

        horse_number = int(value(cell("num")))
        name_link = one(cell("horse").xpath("./a"))
        horse_url = urlsplit(urljoin(url, name_link.get("href", "")))
        if horse_url.scheme != "https" or horse_url.netloc != HOST or horse_url.path != "/JRADB/accessU.html":
            raise ValueError("JRA horse profile link is missing or unsupported")
        horse_query = parse_qs(horse_url.query).get("CNAME", [])
        id_match = re.fullmatch(r"pw01dud10(\d{10})/[A-Fa-f0-9]{2}", horse_query[0]) if len(horse_query) == 1 else None
        age = re.fullmatch(r"(牡|牝|せん|セ)(\d+)", value(cell("age")))
        frame = re.match(r"枠([1-8])", cell("waku").xpath("string(./img/@alt)"))
        if not age or not frame or not id_match or horse_number in seen_numbers or id_match[1] in seen_ids:
            raise ValueError("JRA result has a missing or duplicate runner identity")
        seen_numbers.add(horse_number)
        seen_ids.add(id_match[1])
        finish = value(cell("place"))
        if finish == "同着":
            raise ValueError("JRA dead heat needs an explicit numeric placing")
        corner = {}
        for position in cell("corner").xpath(".//li"):
            label = re.fullmatch(r"([1-4])コーナー通過順位", position.get("title", ""))
            if not label or label[1] in corner:
                raise ValueError("JRA corner position could not be read")
            corner[label[1]] = value(position)
        body_weight, weight_change, first_start = parse_horse_weight(value(cell("h_weight")))
        runners.append({
            "レースID": key, "馬ID": id_match[1], "馬番": str(horse_number), "枠番": frame[1],
            "馬名": value(name_link), "年齢": age[2], "性別": "せん" if age[1] == "セ" else age[1],
            "斤量": value(cell("weight")), "騎手": value(cell("jockey")), "調教師": value(cell("trainer")),
            "着順": finish, "走破時計": value(cell("time")), "着差": value(cell("margin")),
            **{f"{i}角": corner.get(str(i), "") for i in range(1, 5)},
            "上がり3F": value(cell("f_time")), "人気": value(cell("pop")),
            "馬体重": body_weight, "増減": weight_change,
            "取得日": retrieved_date, "出典URL": url,
            "検証状態": "JRA公式結果・掲載馬ID" + ("・馬体重増減は初出走" if first_start else ""),
        })

    if not 1 <= len(runners) <= 18 or seen_numbers != set(range(1, len(runners) + 1)):
        raise ValueError("JRA result runner numbers are incomplete")
    placed = {}
    for runner in runners:
        rank = runner["着順"]
        if rank.isascii() and rank.isdecimal():
            placed.setdefault(rank, []).append(runner)
    winners = placed.get("1", [])
    if not winners or not all(re.fullmatch(r"\d+:\d{2}\.\d", row["走破時計"]) for row in winners):
        raise ValueError("JRA result has no completed winning time")

    laps = root.xpath('.//th[normalize-space(.)="ハロンタイム"]/following-sibling::td[1]')
    lap_text = value(one(laps)) if laps else ""
    lap_times = [Decimal(part) for part in re.findall(r"\d+\.\d", lap_text)]
    rule = header_value("rule")
    race = {
        "レースID": key, "開催日": event.isoformat(), "競馬場": day[5], "開催回": day[4],
        "日次": day[6], "R": str(race_number), "レース名": header_value("race_name"),
        "競走区分": "牝" if rule in ("牝［指定］", "牝[指定]") else rule, "年齢条件": header_value("category"),
        "クラス": header_value("class"), "芝ダート障害": surface, "距離m": str(distance),
        "コース": course[2].split("・", 1)[-1], "馬場状態": condition, "天候": weather,
        "頭数": str(sum(row["着順"] not in NONSTARTERS for row in runners)),
        "前半3F": str(sum(lap_times[:3])) if len(lap_times) >= 3 and distance % 200 == 0 else "",
        "後半3F": str(sum(lap_times[-3:])) if len(lap_times) >= 3 else "",
        "勝ち時計": winners[0]["走破時計"], "取得日": retrieved_date, "出典URL": url,
        "検証状態": "JRA公式結果・掲載全頭候補" + (f"・ラップ {lap_text}" if lap_text else ""),
    }
    for rank in ("1", "2", "3"):
        placed_at_rank = placed.get(rank, [])
        if len(placed_at_rank) == 1:
            race[f"{rank}着馬ID"] = placed_at_rank[0]["馬ID"]
    return race, runners


def candidate_from_url(url, key, race_header, runner_header):
    acquired = datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()
    race, runners = parse_result(retrieve_result(url), url, key, acquired)
    return {
        "race": [race.get(field, "") for field in race_header],
        "runners": [[row.get(field, "") for field in runner_header] for row in runners],
    }
