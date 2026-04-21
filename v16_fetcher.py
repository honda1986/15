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
OFFICIAL_BASE = "https://www.boatrace.jp/owpc/pc/race"
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


# ============================================================
# レース結果取得 & 回収率計算
# ============================================================
def fetch_race_result(date_str: str, jcd: str, rno: int) -> Optional[Dict[str, Any]]:
    """
    boatrace.jp公式から結果と払戻金を取得。
    レース未終了（結果未発表）の場合は None。

    Returns:
        {
            "finish_order": [1, 3, 6, 5, 4, 2],   # 着順（1着から6着の艇番）
            "trifecta_combo": "1-3-6",             # 3連単
            "trifecta_payout": 7040,
            "exacta_combo": "1-3",                 # 2連単
            "exacta_payout": 790,
            "kimarite": "逃げ",
            "is_165_hit": False,  # 1着1号艇かつ 5号艇が2-3着
        }
    """
    url = f"{OFFICIAL_BASE}/raceresult?rno={rno}&jcd={jcd}&hd={date_str}"
    html = _get(url, retries=1)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    # 着順抽出: 「１ | 1 | 3353 前田 光昭 | 1'50"4」形式
    # 漢数字の着位→枠番 を取得
    finish_order: List[int] = []
    kanji_to_num = {"１": 1, "２": 2, "３": 3, "４": 4, "５": 5, "６": 6,
                    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}

    # 結果テーブル: 着, 枠, ボートレーサー, レースタイム の4列
    for tr in soup.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if len(cells) >= 3:
            first = cells[0]
            second = cells[1]
            # 1列目が漢数字(着)、2列目が半角数字(枠)
            if first in kanji_to_num and re.fullmatch(r"[1-6]", second):
                finish_order.append(int(second))

    if len(finish_order) < 3:
        return None  # 結果未発表

    # 払戻金抽出
    trifecta_combo = None
    trifecta_payout = None
    exacta_combo = None
    exacta_payout = None

    for tr in soup.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if len(cells) >= 3:
            if cells[0] == "3連単":
                trifecta_combo = cells[1]
                trifecta_payout = _parse_payout(cells[2])
            elif cells[0] == "2連単":
                exacta_combo = cells[1]
                exacta_payout = _parse_payout(cells[2])

    # 決まり手抽出
    kimarite = ""
    m_kim = re.search(r"決まり手\s*(\S+)", text)
    if m_kim:
        candidate = m_kim.group(1)
        if candidate in ("逃げ", "差し", "まくり", "まくり差し", "抜き", "恵まれ"):
            kimarite = candidate

    return {
        "finish_order": finish_order,
        "trifecta_combo": trifecta_combo,
        "trifecta_payout": trifecta_payout,
        "exacta_combo": exacta_combo,
        "exacta_payout": exacta_payout,
        "kimarite": kimarite,
        "is_165_hit": _is_165_hit(finish_order),
    }


def _parse_payout(s: str) -> Optional[int]:
    """「¥7,040」→ 7040"""
    if not s:
        return None
    m = re.search(r"([\d,]+)", s)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _is_165_hit(finish_order: List[int]) -> bool:
    """1着1号艇 & 5号艇が2-3着"""
    if len(finish_order) < 3:
        return False
    return finish_order[0] == 1 and 5 in finish_order[1:3]


def calculate_recovery(bets: Dict[str, List[str]],
                       result: Dict[str, Any],
                       bet_amount: int = 100) -> Dict[str, Any]:
    """
    買い目と結果から回収率を計算。各点100円×投資で計算。

    Returns:
        {
            "total_spent": 1100,
            "total_return": 790,
            "recovery_rate": 71.8,
            "hits": [("1-3", 790)],  # 的中した買い目と払戻金
            "is_profitable": False,
        }
    """
    total_spent = 0
    total_return = 0
    hits: List[Tuple[str, int]] = []

    trifecta_combo = result.get("trifecta_combo")
    trifecta_payout = result.get("trifecta_payout") or 0
    exacta_combo = result.get("exacta_combo")
    exacta_payout = result.get("exacta_payout") or 0

    # 3連単（本線＋押さえ）
    for category in ("3連単_本線", "3連単_押さえ"):
        for bet in bets.get(category, []):
            total_spent += bet_amount
            if bet == trifecta_combo:
                win = trifecta_payout * (bet_amount / 100)
                total_return += int(win)
                hits.append((bet, int(win)))

    # 2連単
    for bet in bets.get("2連単", []):
        total_spent += bet_amount
        if bet == exacta_combo:
            win = exacta_payout * (bet_amount / 100)
            total_return += int(win)
            hits.append((bet, int(win)))

    recovery_rate = (total_return / total_spent * 100) if total_spent > 0 else 0.0
    return {
        "total_spent": total_spent,
        "total_return": total_return,
        "recovery_rate": round(recovery_rate, 1),
        "hits": hits,
        "is_profitable": total_return >= total_spent,
    }


def enrich_candidates_with_results(candidates: List[Dict[str, Any]],
                                    progress_cb=None) -> List[Dict[str, Any]]:
    """
    候補リスト各レースについて結果を取得して enrich。
    未終了レースは result=None のまま残す。
    """
    total = len(candidates)
    done = [0]

    def work(cand):
        try:
            result = fetch_race_result(cand["date"], cand["jcd"], cand["r_no"])
        except Exception:
            result = None
        if result is not None:
            cand["result"] = result
            if cand.get("bets"):
                cand["recovery"] = calculate_recovery(cand["bets"], result)
        else:
            cand["result"] = None
            cand["recovery"] = None
        return cand

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(work, c): c for c in candidates}
        enriched = []
        for fut in concurrent.futures.as_completed(futures):
            done[0] += 1
            if progress_cb:
                progress_cb(done[0], total, "結果取得中")
            try:
                enriched.append(fut.result())
            except Exception:
                enriched.append(futures[fut])

    # 元の順序(TOTAL降順)を維持
    enriched.sort(key=lambda x: (x["scores"]["TOTAL"] if x.get("scores") else -999), reverse=True)
    return enriched


def aggregate_recovery(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    終了済みレース全体の集計。
    """
    finished = [c for c in candidates if c.get("result") is not None]
    if not finished:
        return {"n_finished": 0, "n_total": len(candidates)}

    total_spent = sum(c["recovery"]["total_spent"] for c in finished if c.get("recovery"))
    total_return = sum(c["recovery"]["total_return"] for c in finished if c.get("recovery"))
    n_165_hit = sum(1 for c in finished if c["result"]["is_165_hit"])
    n_any_hit = sum(1 for c in finished if c.get("recovery") and c["recovery"]["total_return"] > 0)

    return {
        "n_total": len(candidates),
        "n_finished": len(finished),
        "n_pending": len(candidates) - len(finished),
        "n_165_hit": n_165_hit,
        "n_any_hit": n_any_hit,
        "hit_165_rate": round(n_165_hit / len(finished) * 100, 1) if finished else 0,
        "hit_any_rate": round(n_any_hit / len(finished) * 100, 1) if finished else 0,
        "total_spent": total_spent,
        "total_return": total_return,
        "recovery_rate": round(total_return / total_spent * 100, 1) if total_spent > 0 else 0,
    }


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
