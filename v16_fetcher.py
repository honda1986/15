# -*- coding: utf-8 -*-
"""
v16 fetcher: ボートレース公式 (boatrace.jp) スクレイパー
=========================================================
日付と場コードから出走表を取得し、v16_itigo_filter.Race オブジェクトを構築する。

Requirements:
    requests
    beautifulsoup4

使い方:
    from v16_fetcher import fetch_day_candidates
    candidates = fetch_day_candidates("20260420")  # その日の全場・全R走査
    for c in candidates:
        print(c["venue"], c["r_no"], c["scores"]["TOTAL"])
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
    evaluate_race, score_165, is_165_candidate, judge_rank, generate_bets,
)


# ============================================================
# 場コード・場名マッピング
# ============================================================
JCD_TO_NAME = {
    "01": "桐生", "02": "戸田", "03": "江戸川", "04": "平和島",
    "05": "多摩川", "06": "浜名湖", "07": "蒲郡", "08": "常滑",
    "09": "津",   "10": "三国",   "11": "びわこ", "12": "住之江",
    "13": "尼崎", "14": "鳴門",   "15": "丸亀",   "16": "児島",
    "17": "宮島", "18": "徳山",   "19": "下関",   "20": "若松",
    "21": "芦屋", "22": "福岡",   "23": "唐津",   "24": "大村",
}
NAME_TO_JCD = {v: k for k, v in JCD_TO_NAME.items()}


# ============================================================
# 場別1コース1着率（直近1年概算・半年ごと手動更新推奨）
# ============================================================
COURSE1_WIN_RATE = {
    "桐生": 0.54, "戸田": 0.44, "江戸川": 0.46, "平和島": 0.48,
    "多摩川": 0.55, "浜名湖": 0.52, "蒲郡": 0.54, "常滑": 0.54,
    "津": 0.52, "三国": 0.54, "びわこ": 0.50, "住之江": 0.58,
    "尼崎": 0.57, "鳴門": 0.52, "丸亀": 0.56, "児島": 0.55,
    "宮島": 0.56, "徳山": 0.62, "下関": 0.60, "若松": 0.58,
    "芦屋": 0.62, "福岡": 0.54, "唐津": 0.54, "大村": 0.66,
}


# ============================================================
# HTTP取得
# ============================================================
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9",
}
BASE = "https://www.boatrace.jp/owpc/pc/race"
TIMEOUT = 10


def _get(url: str, retries: int = 2) -> Optional[str]:
    """URLをGETしてHTMLを返す。失敗時はNone。"""
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


# ============================================================
# 開催場判定
# ============================================================
def fetch_open_venues(date_str: str) -> List[str]:
    """
    指定日に開催している場のjcdリストを返す。
    全24場を試行して raceindex が取得できた場を開催中とみなす。
    """
    open_jcds: List[str] = []
    def check(jcd):
        url = f"{BASE}/raceindex?jcd={jcd}&hd={date_str}"
        html = _get(url, retries=1)
        if html and "締切予定時刻" in html and "レース一覧" in html:
            return jcd
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(check, JCD_TO_NAME.keys()))
    open_jcds = [j for j in results if j]
    return open_jcds


# ============================================================
# 出走表パーサ
# ============================================================
_RE_TOBAN = re.compile(r"(\d{4})\s*/\s*([AB][12])")
_RE_AGE_WEIGHT = re.compile(r"(\d+)歳\s*/\s*(\d+\.\d+)kg")
_RE_F_L_ST = re.compile(r"F(\d+)\s+L(\d+)\s+(\d+\.\d+)")
_RE_THREE_NUM = re.compile(r"(-|\d+(?:\.\d+)?)\s+(-|\d+(?:\.\d+)?)\s+(-|\d+(?:\.\d+)?)")

# 節間成績: STは「.32」のような小数点始まり、「F.02」「L」などもある
_RE_SETTLE_ST = re.compile(r"^[FL]?\.?(\d{2,3})$")  # .32, F.02, L, 等
# 着順: 単一数字、または 1〜6（全角漢字もあり）
_KANJI_NUM = {"１": 1, "２": 2, "３": 3, "４": 4, "５": 5, "６": 6,
              "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}


def _parse_settle_row(cells_text: list, anchor_kanji: list = None) -> dict:
    """
    選手ブロックの末尾付近にある今節成績（コース・ST・着順の3行）から
    節間ST平均・節間2連率を抽出する。

    cells_text: tbody内のtdテキストを順番に並べたリスト
    anchor_kanji: aタグ内に含まれる漢数字テキストのリスト（着順専用）
    戻り値: {"settle_st": float or None, "settle_2rate": float or None}
    """
    # ST値候補（.NN 形式）を抽出
    st_values = []
    for t in cells_text:
        t = t.strip()
        if t.startswith("F") or t.startswith("L"):
            continue
        m = re.fullmatch(r"\.(\d{2,3})", t)
        if m:
            try:
                v = float("0." + m.group(1))
                if 0.0 <= v <= 0.40:
                    st_values.append(v)
            except ValueError:
                pass

    # 着順候補（aタグ内の漢数字 or 全角数字のみ）
    finish_positions = []
    if anchor_kanji:
        for t in anchor_kanji:
            t = t.strip()
            if t in _KANJI_NUM:
                finish_positions.append(_KANJI_NUM[t])

    settle_st = None
    if st_values:
        settle_st = round(sum(st_values) / len(st_values), 3)

    settle_2rate = None
    if finish_positions:
        in_2 = sum(1 for p in finish_positions if p <= 2)
        settle_2rate = round(in_2 / len(finish_positions), 3)

    return {"settle_st": settle_st, "settle_2rate": settle_2rate}


def _num(s: str) -> Optional[float]:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def parse_racelist(html: str) -> Optional[List[Racer]]:
    """
    出走表HTMLから6艇分のRacerリストを返す。失敗時はNone。
    """
    soup = BeautifulSoup(html, "html.parser")

    # 出走表テーブルは tbody.is-fs12 が6つ並ぶ（各艇1つ）
    tbodies = soup.select("tbody.is-fs12")
    if len(tbodies) < 6:
        return None

    racers: List[Racer] = []
    for tbody in tbodies[:6]:
        text = tbody.get_text(" ", strip=True)

        # 登録番号・級別
        m_toban = _RE_TOBAN.search(text)
        if not m_toban:
            return None
        toban, cls_ = m_toban.group(1), m_toban.group(2)

        # 選手名（aタグ内）
        name_tag = tbody.select_one("a")
        name = ""
        if name_tag:
            name = re.sub(r"\s+", "", name_tag.get_text(strip=True))

        # 年齢/体重
        m_age = _RE_AGE_WEIGHT.search(text)
        weight = _num(m_age.group(2)) if m_age else None

        # F/L/平均ST
        m_fl = _RE_F_L_ST.search(text)
        f_count = int(m_fl.group(1)) if m_fl else 0
        avg_st = _num(m_fl.group(3)) if m_fl else None

        # 勝率行: 全国と当地の順に2つある
        #   「3.46  12.28  29.82」のような3数値
        # F/L行以降から探す
        remaining = text
        if m_fl:
            remaining = text[m_fl.end():]
        triples = _RE_THREE_NUM.findall(remaining)

        # 最初の triple = 全国(勝率, 2連率, 3連率)、次が当地、次がモーター、次がボート
        win_rate = None
        motor_2rate = None
        if len(triples) >= 1:
            win_rate = _num(triples[0][0])
        if len(triples) >= 3:
            # モーター2連率は %表記なので /100
            m2 = _num(triples[2][1])
            motor_2rate = m2 / 100.0 if m2 is not None else None

        # 節間成績抽出: tbody内の全td/aタグをテキスト化して解析
        cells_text = []
        for el in tbody.find_all(["td"]):
            tx = el.get_text(strip=True)
            if tx:
                cells_text.append(tx)
        # aタグ内の漢数字（着順リンク）のみを別途収集
        anchor_kanji = []
        for a in tbody.find_all("a"):
            tx = a.get_text(strip=True)
            # raceresult?rno=... へのリンクが着順リンク
            href = a.get("href", "")
            if "raceresult" in href and tx in _KANJI_NUM:
                anchor_kanji.append(tx)
            elif tx in _KANJI_NUM:
                # href属性がない/他形式でも漢数字単独なら着順扱い
                anchor_kanji.append(tx)
        settle = _parse_settle_row(cells_text, anchor_kanji=anchor_kanji)

        racers.append(Racer(
            name=name,
            cls=cls_,
            win_rate=win_rate,
            avg_st=avg_st,
            motor_2rate=motor_2rate,
            f_count=f_count,
            weight=weight,
            settle_st=settle["settle_st"],
            settle_2rate=settle["settle_2rate"],
        ))

    return racers if len(racers) == 6 else None


def fetch_race(date_str: str, jcd: str, rno: int) -> Optional[Race]:
    """指定レースの Race オブジェクトを返す。失敗時 None。"""
    url = f"{BASE}/racelist?rno={rno}&jcd={jcd}&hd={date_str}"
    html = _get(url)
    if not html:
        return None
    racers = parse_racelist(html)
    if not racers:
        return None

    venue = JCD_TO_NAME[jcd]
    return Race(
        venue=venue,
        r_no=rno,
        b1=racers[0], b2=racers[1], b3=racers[2],
        b4=racers[3], b5=racers[4], b6=racers[5],
        weather=Weather(),  # 直前情報は別取得（未実装）
        course1_win_rate_venue=COURSE1_WIN_RATE.get(venue, 0.52),
        v14_hit=False,
    )


# ============================================================
# 直前情報（任意・展示タイム/天候を補強）
# ============================================================
def fetch_beforeinfo(date_str: str, jcd: str, rno: int) -> Dict[str, Any]:
    """
    直前情報から天候・波・風・展示順位を取得する。
    失敗時は空dict。取得できた項目だけ埋める。
    """
    url = f"{BASE}/beforeinfo?rno={rno}&jcd={jcd}&hd={date_str}"
    html = _get(url, retries=1)
    if not html:
        return {}
    soup = BeautifulSoup(html, "html.parser")
    info: Dict[str, Any] = {}

    # 天候・風・波 (テキストから抽出)
    text = soup.get_text(" ", strip=True)
    m_wind = re.search(r"風速\s*(\d+)\s*m", text)
    if m_wind:
        info["wind_mps"] = float(m_wind.group(1))
    m_wave = re.search(r"波\s*(?:高)?\s*(\d+)\s*cm", text)
    if m_wave:
        info["wave_cm"] = float(m_wave.group(1))

    # 風向判定は画像クラスだが、テキストから簡易判定
    if "追風" in text or "追い風" in text:
        info["wind_dir"] = "tail"
    elif "向風" in text or "向い風" in text:
        info["wind_dir"] = "head"
    elif "横風" in text:
        info["wind_dir"] = "side"

    # 安定板
    if "安定板" in text and "使用" in text:
        info["stabilizer"] = True

    # 展示タイム → 各艇のタイムを抽出して順位付け
    try:
        # 「展示タイム」ラベル付近のテーブルを探す
        times = []
        for td in soup.select("td"):
            tx = td.get_text(strip=True)
            if re.fullmatch(r"\d+\.\d{2}", tx):
                times.append(float(tx))
        if len(times) >= 6:
            top6 = times[:6]
            # 小さい順位1位
            order = sorted(range(6), key=lambda i: top6[i])
            ranks = [0] * 6
            for rank, idx in enumerate(order, start=1):
                ranks[idx] = rank
            info["exhibit_ranks"] = ranks  # インデックス0=1号艇
    except Exception:
        pass

    return info


def enrich_with_beforeinfo(race: Race, date_str: str, jcd: str) -> Race:
    """直前情報を取りに行って Race を更新する。失敗時は無変更。"""
    info = fetch_beforeinfo(date_str, jcd, race.r_no)
    if not info:
        return race
    w = race.weather
    if "wind_mps" in info:
        w.wind_mps = info["wind_mps"]
    if "wave_cm" in info:
        w.wave_cm = info["wave_cm"]
    if "wind_dir" in info:
        w.wind_dir = info["wind_dir"]
    if "stabilizer" in info:
        w.stabilizer = info["stabilizer"]
    # 展示順位
    if "exhibit_ranks" in info:
        boats = [race.b1, race.b2, race.b3, race.b4, race.b5, race.b6]
        for i, r in enumerate(boats):
            r.exhibit_rank = info["exhibit_ranks"][i]
    return race


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
    指定日の全開催場・全12Rを走査し、
    TOTAL >= min_total の候補レースを返す。

    Args:
        date_str: "YYYYMMDD"
        min_total: 最低TOTALスコア（デフォ9.0＝△押さえ以上）
        use_beforeinfo: Trueなら直前情報も取得（遅くなる）
        progress_cb: callable(done, total, label) 進捗コールバック

    Returns:
        [{"venue", "r_no", "jcd", "date", "race", "scores", "judge",
          "bets", "reject_reasons"}, ...]
    """
    # 開催場検出
    if progress_cb:
        progress_cb(0, 1, "開催場を取得中...")
    open_jcds = fetch_open_venues(date_str)
    if not open_jcds:
        return []

    targets: List[Tuple[str, int]] = [
        (jcd, rno) for jcd in open_jcds for rno in range(1, 13)
    ]
    total_count = len(targets)

    results: List[Dict[str, Any]] = []
    done = 0

    def work(item):
        jcd, rno = item
        race = fetch_race(date_str, jcd, rno)
        if race is None:
            return None
        if use_beforeinfo:
            race = enrich_with_beforeinfo(race, date_str, jcd)
        ok, reasons = is_165_candidate(race)
        if not ok:
            return None  # ハード条件NGはドロップ（結果に含めない）
        s = score_165(race)
        if s["TOTAL"] < min_total:
            return None  # スコア不足もドロップ
        return {
            "venue": race.venue, "r_no": rno, "jcd": jcd, "date": date_str,
            "race": race, "scores": s, "judge": judge_rank(s["TOTAL"]),
            "bets": generate_bets(s["TOTAL"]), "reject_reasons": [],
            "candidate": True,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(work, t): t for t in targets}
        for fut in concurrent.futures.as_completed(futures):
            done += 1
            if progress_cb:
                jcd, rno = futures[fut]
                progress_cb(done, total_count, f"{JCD_TO_NAME[jcd]} {rno}R")
            r = fut.result()
            if r is not None:
                results.append(r)

    # TOTAL降順
    results.sort(key=lambda x: (x["scores"]["TOTAL"] if x["scores"] else -999), reverse=True)
    return results


# ============================================================
# 日付変換
# ============================================================
def date_to_str(d) -> str:
    """datetime.date / datetime / 'YYYY-MM-DD' / 'YYYYMMDD' を 'YYYYMMDD' に統一"""
    if isinstance(d, str):
        return d.replace("-", "").replace("/", "")
    if hasattr(d, "strftime"):
        return d.strftime("%Y%m%d")
    raise ValueError(f"Invalid date: {d}")


# ============================================================
# CLI動作確認
# ============================================================
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
