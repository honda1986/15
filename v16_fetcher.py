# -*- coding: utf-8 -*-
"""
v16 fetcher (uchisankaku版): uchisankaku.sakura.ne.jp スクレイパー
================================================================
1場1リクエストで12レース分全データを取得できる。
節間成績・コース別6カ月ST・決まり手率まで揃うため、
v16 1=5形フィルタに必要な全指標が埋まる。
"""

import re
import time
import concurrent.futures
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

import requests
from bs4 import BeautifulSoup

from v16_itigo_filter import (
    Racer, Weather, Race,
    score_165, is_165_candidate, judge_rank, generate_bets,
)


JCD_TO_NAME = {
    "01": "桐生", "02": "戸田", "03": "江戸川", "04": "平和島",
    "05": "多摩川", "06": "浜名湖", "07": "蒲郡", "08": "常滑",
    "09": "津",   "10": "三国",   "11": "びわこ", "12": "住之江",
    "13": "尼崎", "14": "鳴門",   "15": "丸亀",   "16": "児島",
    "17": "宮島", "18": "徳山",   "19": "下関",   "20": "若松",
    "21": "芦屋", "22": "福岡",   "23": "唐津",   "24": "大村",
}
NAME_TO_JCD = {v: k for k, v in JCD_TO_NAME.items()}


def _jcode(jcd: str) -> str:
    """uchisankaku用: 0埋めなしに変換 ('05'→'5')"""
    return str(int(jcd))


COURSE1_WIN_RATE = {
    "桐生": 0.54, "戸田": 0.44, "江戸川": 0.46, "平和島": 0.48,
    "多摩川": 0.55, "浜名湖": 0.52, "蒲郡": 0.54, "常滑": 0.54,
    "津": 0.52, "三国": 0.54, "びわこ": 0.50, "住之江": 0.58,
    "尼崎": 0.57, "鳴門": 0.52, "丸亀": 0.56, "児島": 0.55,
    "宮島": 0.56, "徳山": 0.62, "下関": 0.60, "若松": 0.58,
    "芦屋": 0.62, "福岡": 0.54, "唐津": 0.54, "大村": 0.66,
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9",
}
UCHI_BASE = "https://uchisankaku.sakura.ne.jp"
TIMEOUT = 15


def _get(url: str, retries: int = 2) -> Optional[str]:
    for i in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.encoding = r.apparent_encoding or "utf-8"
            if r.status_code == 200 and len(r.text) > 500:
                return r.text
        except Exception:
            pass
        if i < retries:
            time.sleep(0.8 * (i + 1))
    return None


def _num(s: str) -> Optional[float]:
    if s is None:
        return None
    s = s.strip().replace("%", "").replace("　", "")
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _row_values(tr) -> List[str]:
    return [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]


# ============================================================
# 1場分の全12Rパース
# ============================================================
def parse_venue_page(html: str, venue: str) -> Dict[int, Race]:
    """uchisankakuの1場ページHTMLから {rno: Race} を返す"""
    soup = BeautifulSoup(html, "html.parser")
    result: Dict[int, Race] = {}

    tables = soup.find_all("table")

    for table in tables:
        # このテーブルの直前にある見出し/テキストから「NR」を抽出
        rno = None
        prev = table.find_previous(["h1", "h2", "h3", "h4", "p"])
        hops = 0
        while prev is not None and hops < 5:
            text = prev.get_text(" ", strip=True)
            m = re.search(r"(\d{1,2})R", text)
            if m and int(m.group(1)) <= 12:
                rno = int(m.group(1))
                break
            prev = prev.find_previous(["h1", "h2", "h3", "h4", "p"])
            hops += 1
        if rno is None:
            continue

        rows = table.find_all("tr")
        if len(rows) < 10:
            continue

        boat_data: List[Dict[str, Any]] = [dict() for _ in range(6)]
        current_section = ""  # "成績/全国", "今節成績", "コース別", "決り手", "モーター" など

        for tr in rows:
            cells = _row_values(tr)
            if len(cells) < 6:
                continue

            labels = cells[:-6]
            values = cells[-6:]
            label_parts = [c for c in labels if c]

            # ラベル部の最初の要素はセクション名の可能性が高い
            # rowspanの影響で空セルが飛んだり、単独ラベルになったりするので
            # 「明示的にセクション名が含まれる」場合だけセクション更新
            combined = "".join(label_parts).replace("　", "").replace(" ", "")

            # セクション検出（優先順位順）
            section_keywords = [
                ("今節成績", "今節成績"),
                ("コース別", "コース別"),
                ("決り手", "決り手"),
                ("モーター", "モーター"),
                ("成績", "成績"),  # 汎用ラベル、弱め
            ]
            for key, sec_name in section_keywords:
                if key in combined:
                    current_section = sec_name
                    break

            # 「全国」「当地」のサブセクション判別
            if "全国" in combined and current_section == "成績":
                current_section = "成績/全国"
            elif "当地" in combined and current_section.startswith("成績"):
                current_section = "成績/当地"

            # 最終ラベル = セクション + 末尾のサブラベル
            # 末尾ラベルは label_parts の最後、または combined から section を引いた残り
            subkey = combined
            for key, _ in section_keywords:
                subkey = subkey.replace(key, "")
            subkey = subkey.replace("全国", "").replace("当地", "").replace("／直近6カ月", "").replace("／直近６カ月", "")

            full_label = f"{current_section}/{subkey}" if subkey else current_section

            _assign_row(boat_data, full_label, combined, current_section, subkey, values)

        if any(d.get("cls") for d in boat_data) and rno not in result:
            racers = [_to_racer(boat_data[i]) for i in range(6)]
            result[rno] = Race(
                venue=venue, r_no=rno,
                b1=racers[0], b2=racers[1], b3=racers[2],
                b4=racers[3], b5=racers[4], b6=racers[5],
                weather=Weather(),
                course1_win_rate_venue=COURSE1_WIN_RATE.get(venue, 0.52),
                v14_hit=False,
            )

    return result


def _assign_row(boat_data: List[Dict[str, Any]], full_label: str,
                combined: str, section: str, subkey: str, values: List[str]):
    """セクション文脈を考慮した属性代入"""

    # 階級
    if "級別" in combined:
        for i, v in enumerate(values):
            if v in ("A1", "A2", "B1", "B2"):
                boat_data[i]["cls"] = v
        return

    # 氏名
    if "氏名" in combined:
        for i, v in enumerate(values):
            boat_data[i]["name"] = v.replace("　", "").replace(" ", "")
        return

    # 体重
    if "体重" in combined:
        for i, v in enumerate(values):
            m = re.search(r"(\d+\.\d+)", v)
            if m:
                boat_data[i]["weight"] = float(m.group(1))
        return

    # F数
    if "F数" in combined:
        for i, v in enumerate(values):
            m = re.search(r"F(\d+)", v)
            if m:
                boat_data[i]["f_count"] = int(m.group(1))
        return

    # 全国勝率 (セクション=成績/全国 かつ ラベル=勝率)
    if section == "成績/全国" and "勝率" in subkey:
        for i, v in enumerate(values):
            n = _num(v)
            if n is not None:
                boat_data[i]["win_rate"] = n
        return

    # 今節成績 ST
    if section == "今節成績" and "ST" in subkey:
        for i, v in enumerate(values):
            n = _num(v)
            if n is not None:
                boat_data[i]["settle_st"] = n
        return

    # 今節成績 2連率
    if section == "今節成績" and "2連率" in subkey:
        for i, v in enumerate(values):
            n = _num(v)
            if n is not None:
                boat_data[i]["settle_2rate"] = n / 100.0
        return

    # コース別 ST（追い風/向い風 除外）
    if section == "コース別" and subkey == "ST":
        for i, v in enumerate(values):
            n = _num(v)
            if n is not None:
                boat_data[i]["avg_st"] = n
        return

    # モーター 2連率
    if section == "モーター" and "2連率" in subkey:
        for i, v in enumerate(values):
            n = _num(v)
            if n is not None:
                boat_data[i]["motor_2rate"] = n / 100.0
        return

    # 決り手 捲り (捲り差し, 捲られ は除く)
    if section == "決り手" and subkey == "捲り":
        for i, v in enumerate(values):
            v_clean = v.replace("(", "").replace(")", "")
            n = _num(v_clean)
            if n is not None:
                boat_data[i]["makuri_pct"] = n
        return


def _to_racer(d: Dict[str, Any]) -> Racer:
    makuri_pct = d.get("makuri_pct")
    return Racer(
        name=d.get("name", ""),
        cls=d.get("cls", ""),
        win_rate=d.get("win_rate"),
        avg_st=d.get("avg_st"),
        settle_st=d.get("settle_st"),
        settle_2rate=d.get("settle_2rate"),
        motor_2rate=d.get("motor_2rate"),
        f_count=d.get("f_count", 0),
        weight=d.get("weight"),
        course5_avg_st=d.get("avg_st"),
        makuri_rate=(makuri_pct / 100.0) if makuri_pct is not None else None,
    )


# ============================================================
# 取得関数
# ============================================================
def fetch_venue_races(date_str: str, jcd: str) -> Dict[int, Race]:
    venue = JCD_TO_NAME[jcd]
    url = f"{UCHI_BASE}/racelist.php?jcode={_jcode(jcd)}&date={date_str}"
    html = _get(url)
    if not html:
        return {}
    return parse_venue_page(html, venue)


def fetch_race(date_str: str, jcd: str, rno: int) -> Optional[Race]:
    races = fetch_venue_races(date_str, jcd)
    return races.get(rno)


def enrich_with_beforeinfo(race: Race, date_str: str, jcd: str) -> Race:
    """互換API維持のためスタブ"""
    return race


# ============================================================
# 開催場判定
# ============================================================
def fetch_open_venues(date_str: str) -> List[str]:
    """24場を並列で軽く叩いて開催中の場を検出"""
    def check(jcd):
        url = f"{UCHI_BASE}/racelist.php?jcode={_jcode(jcd)}&date={date_str}"
        html = _get(url, retries=1)
        if html and ("今節成績" in html or "級別" in html):
            return jcd
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(check, JCD_TO_NAME.keys()))
    return [j for j in results if j]


# ============================================================
# 日次一括抽出
# ============================================================
def fetch_day_candidates(
    date_str: str,
    min_total: float = 9.0,
    use_beforeinfo: bool = False,
    progress_cb=None,
) -> List[Dict[str, Any]]:
    """
    uchisankakuから1場1リクエストで取得し、
    TOTAL >= min_total の候補レースを返す。
    """
    if progress_cb:
        progress_cb(0, 1, "開催場を検索中...")
    open_jcds = fetch_open_venues(date_str)
    if not open_jcds:
        return []

    total_venues = len(open_jcds)
    results: List[Dict[str, Any]] = []
    done_count = [0]

    def work(jcd):
        races = fetch_venue_races(date_str, jcd)
        out = []
        for rno, race in races.items():
            ok, _ = is_165_candidate(race)
            if not ok:
                continue
            s = score_165(race)
            if s["TOTAL"] < min_total:
                continue
            out.append({
                "venue": race.venue, "r_no": rno, "jcd": jcd, "date": date_str,
                "race": race, "scores": s, "judge": judge_rank(s["TOTAL"]),
                "bets": generate_bets(s["TOTAL"]), "reject_reasons": [],
                "candidate": True,
            })
        return jcd, out

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(work, j): j for j in open_jcds}
        for fut in concurrent.futures.as_completed(futures):
            done_count[0] += 1
            jcd = futures[fut]
            if progress_cb:
                progress_cb(done_count[0], total_venues, f"{JCD_TO_NAME[jcd]}")
            try:
                _, venue_results = fut.result()
                results.extend(venue_results)
            except Exception:
                pass

    results.sort(key=lambda x: (x["scores"]["TOTAL"] if x["scores"] else -999), reverse=True)
    return results


def date_to_str(d) -> str:
    if isinstance(d, str):
        return d.replace("-", "").replace("/", "")
    if hasattr(d, "strftime"):
        return d.strftime("%Y%m%d")
    raise ValueError(f"Invalid date: {d}")


if __name__ == "__main__":
    import sys
    date_arg = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y%m%d")
    print(f"日付: {date_arg}")

    def pb(done, total, label):
        print(f"  [{done:3d}/{total:3d}] {label}", end="\r")

    cands = fetch_day_candidates(date_arg, min_total=9.0, progress_cb=pb)
    print()
    print(f"\n候補 {len(cands)}件:")
    print(f"{'場':<6}{'R':>3}  {'判定':<8} {'TOTAL':>6}  P1    P5    R45   N23   W")
    print("-" * 70)
    for c in cands:
        s = c["scores"]
        print(f"{c['venue']:<6}{c['r_no']:>3}  {c['judge']:<8} "
              f"{s['TOTAL']:>+6.2f}  {s['P1']:+.2f} {s['P5']:+.2f} "
              f"{s['R45']:+.2f} {s['N23']:+.2f} {s['W']:+.2f}")
