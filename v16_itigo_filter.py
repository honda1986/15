# -*- coding: utf-8 -*-
"""
v16 1=5形（イチゴ）スジ追撃フィルタ
================================
1着=1号艇／2-3着=5号艇 の3連単・2連単を狙い撃つ独立モジュール。

使い方（最小例）:
    from v16_itigo_filter import Racer, Weather, Race, evaluate_race

    race = Race(
        venue="徳山",
        r_no=12,
        b1=Racer(name="選手A", cls="A1", win_rate=7.20, avg_st=0.14,
                 settle_st=0.13, settle_2rate=0.60, motor_2rate=0.48, f_count=0),
        b2=Racer(name="選手B", cls="B1", win_rate=5.30, avg_st=0.19,
                 settle_st=0.20, settle_2rate=0.20, motor_2rate=0.30, f_count=0),
        b3=Racer(name="選手C", cls="B1", win_rate=5.50, avg_st=0.18,
                 settle_st=0.19, settle_2rate=0.25, motor_2rate=0.35, f_count=0),
        b4=Racer(name="選手D", cls="A2", win_rate=6.30, avg_st=0.15,
                 settle_st=0.15, settle_2rate=0.40, motor_2rate=0.42, f_count=0,
                 makuri_rate=0.38),
        b5=Racer(name="選手E", cls="A2", win_rate=5.80, avg_st=0.16,
                 settle_st=0.16, settle_2rate=0.55, motor_2rate=0.46, f_count=0,
                 course5_avg_st=0.16, weight=51.0),
        b6=Racer(name="選手F", cls="B1", win_rate=5.00, avg_st=0.20,
                 settle_st=0.20, settle_2rate=0.15, motor_2rate=0.25, f_count=0),
        weather=Weather(wind_dir="tail", wind_mps=2, wave_cm=2, stabilizer=False),
        course1_win_rate_venue=0.62,
        v14_hit=False,
    )
    result = evaluate_race(race)
    print(result["report"])
"""

from dataclasses import dataclass, field
from typing import Optional, Literal, Dict, Any, List, Tuple


# ============================================================
# 定数: 場別補正
# ============================================================
VENUE_BONUS_P1 = {
    "徳山": 1.5, "大村": 1.5, "芦屋": 1.5, "下関": 1.5,
    "住之江": 0.5, "尼崎": 0.5, "若松": 0.5, "丸亀": 0.5,
    "戸田": -2.5, "平和島": -2.5, "江戸川": -2.5,
}

VENUE_BONUS_P5 = {
    "戸田": 0.5, "江戸川": 0.5, "平和島": 0.5,
}


# ============================================================
# データ構造
# ============================================================
@dataclass
class Racer:
    """選手データ。未取得値は None のままで可（段階劣化する）"""
    name: str = ""
    cls: Literal["A1", "A2", "B1", "B2", ""] = ""  # 階級
    win_rate: Optional[float] = None      # 全国勝率
    avg_st: Optional[float] = None        # 全国平均ST
    settle_st: Optional[float] = None     # 節間平均ST
    settle_2rate: Optional[float] = None  # 節間2連率 (0.0-1.0)
    motor_2rate: Optional[float] = None   # モーター2連率 (0.0-1.0)
    f_count: int = 0                      # F持ち数 (0,1,2)

    # 任意項目（uchisankaku参照時に埋める）
    exhibit_rank: Optional[int] = None    # 展示タイム順位 1-6
    course5_avg_st: Optional[float] = None  # 5コース半年平均ST（5号艇専用）
    weight: Optional[float] = None        # 体重kg
    makuri_rate: Optional[float] = None   # まくり決まり手率 (0.0-1.0) 4号艇専用


@dataclass
class Weather:
    wind_dir: Literal["tail", "head", "side", "calm"] = "calm"  # 追/向/横/無
    wind_mps: float = 0.0
    wave_cm: float = 0.0
    stabilizer: bool = False  # 安定板使用
    is_night: bool = False    # ナイター


@dataclass
class Race:
    venue: str
    r_no: int
    b1: Racer
    b2: Racer
    b3: Racer
    b4: Racer
    b5: Racer
    b6: Racer
    weather: Weather = field(default_factory=Weather)
    course1_win_rate_venue: Optional[float] = None  # 場別1コース1着率
    v14_hit: bool = False  # v14ハイエナ側でヒットしているか


# ============================================================
# ハード条件（抽出判定）
# ============================================================
def is_165_candidate(race: Race) -> Tuple[bool, List[str]]:
    """抽出ハード条件。Trueなら候補、reasonsは不合格理由のリスト。"""
    reasons: List[str] = []
    b1, b2, b3, b5 = race.b1, race.b2, race.b3, race.b5
    w = race.weather

    # 1号艇
    if b1.cls not in ("A1", "A2"):
        reasons.append(f"1号艇階級NG({b1.cls})")
    if b1.win_rate is not None and b1.win_rate < 6.20:
        reasons.append(f"1号艇全国勝率NG({b1.win_rate})")
    if race.course1_win_rate_venue is not None and race.course1_win_rate_venue < 0.55:
        reasons.append(f"場別1コース1着率NG({race.course1_win_rate_venue:.2f})")

    # 5号艇
    if b5.cls == "B2" and (b5.settle_2rate is None or b5.settle_2rate < 0.50):
        reasons.append(f"5号艇B2かつ節間2連率不足")
    if b5.win_rate is not None and b5.win_rate < 4.80:
        reasons.append(f"5号艇全国勝率NG({b5.win_rate})")

    # 2-3号艇の弱点判定
    def weak(r: Racer) -> bool:
        if r.cls in ("B1", "B2"):
            return True
        if r.win_rate is not None and r.win_rate <= 5.50:
            return True
        if r.avg_st is not None and r.avg_st >= 0.19:
            return True
        return False

    if not (weak(b2) or weak(b3)):
        reasons.append("2・3号艇に弱点なし")

    # 水面・天候
    if w.stabilizer:
        reasons.append("安定板使用")
    if w.wave_cm >= 10:
        reasons.append(f"波高NG({w.wave_cm}cm)")
    if w.wind_dir == "head" and w.wind_mps >= 7:
        reasons.append(f"向い風強すぎ({w.wind_mps}m)")

    # v14との同時ヒット
    if race.v14_hit:
        reasons.append("v14ハイエナ同時ヒット（両方見送り）")

    return (len(reasons) == 0, reasons)


# ============================================================
# スコア関数（各ブロック）
# ============================================================
def _band(value: Optional[float], bands: List[Tuple[float, float, float]], default: float = 0.0) -> float:
    """value が (lo, hi, pts) の範囲に入ったら pts を返す。lo<=v<hi。"""
    if value is None:
        return default
    for lo, hi, pts in bands:
        if lo <= value < hi:
            return pts
    return default


def score_P1(b1: Racer, venue: str, weather: Weather) -> float:
    """1号艇逃げ期待スコア（最大+10.0）"""
    s = 0.0
    # 階級
    s += {"A1": 2.5, "A2": 1.0, "B1": -0.5, "B2": -2.5}.get(b1.cls, 0.0)
    # 全国勝率
    s += _band(b1.win_rate, [
        (7.50, 99.0, 2.0),
        (7.00, 7.50, 1.5),
        (6.50, 7.00, 1.0),
        (6.20, 6.50, 0.5),
    ])
    # 平均ST
    s += _band(b1.avg_st, [
        (0.00, 0.15, 1.5),
        (0.15, 0.17, 0.8),
        (0.17, 0.19, 0.0),
        (0.19, 9.99, -1.5),
    ])
    # 節間STと全国STの差
    if b1.settle_st is not None and b1.avg_st is not None:
        diff = b1.settle_st - b1.avg_st
        if diff <= -0.02:
            s += 1.0
        elif diff >= 0.03:
            s -= 1.5
    # 節間2連率
    s += _band(b1.settle_2rate, [
        (0.60, 1.01, 1.5),
        (0.40, 0.60, 0.5),
        (0.00, 0.20, -1.5),
    ])
    # モーター2連率
    s += _band(b1.motor_2rate, [
        (0.45, 1.01, 1.0),
        (0.00, 0.25, -1.0),
    ])
    # 場補正
    s += VENUE_BONUS_P1.get(venue, 0.0)
    # F
    if b1.f_count == 1:
        s -= 1.0
    elif b1.f_count >= 2:
        s -= 2.5
    return round(s, 2)


def score_P5(b5: Racer, venue: str) -> float:
    """5号艇の2-3着期待スコア（最大+8.0）"""
    s = 0.0
    s += {"A1": 2.5, "A2": 1.5, "B1": 0.0, "B2": -1.5}.get(b5.cls, 0.0)
    s += _band(b5.win_rate, [
        (6.50, 99.0, 1.5),
        (5.50, 6.50, 1.0),
        (5.00, 5.50, 0.5),
    ])
    # 5コース半年ST（なければ全国平均STで代替＋0.5倍）
    target_st = b5.course5_avg_st if b5.course5_avg_st is not None else b5.avg_st
    attenuate = 1.0 if b5.course5_avg_st is not None else 0.5
    s += attenuate * _band(target_st, [
        (0.00, 0.16, 1.5),
        (0.16, 0.18, 0.8),
        (0.20, 9.99, -1.0),
    ])
    # 節間2連率
    s += _band(b5.settle_2rate, [
        (0.50, 1.01, 1.5),
        (0.30, 0.50, 0.5),
        (0.00, 0.20, -1.0),
    ])
    # 節間STが全国平均より0.02早い
    if b5.settle_st is not None and b5.avg_st is not None and b5.settle_st - b5.avg_st <= -0.02:
        s += 1.0
    # モーター
    s += _band(b5.motor_2rate, [
        (0.45, 1.01, 1.5),
        (0.30, 0.45, 0.5),
        (0.00, 0.25, -1.0),
    ])
    # 展示タイム
    if b5.exhibit_rank == 1:
        s += 1.5
    elif b5.exhibit_rank == 2:
        s += 0.8
    elif b5.exhibit_rank == 6:
        s -= 1.0
    # 体重
    if b5.weight is not None:
        if b5.weight <= 52.0:
            s += 0.5
        elif b5.weight >= 57.0:
            s -= 0.5
    # 場補正
    s += VENUE_BONUS_P5.get(venue, 0.0)
    return round(s, 2)


def score_R45(b4: Racer, b5: Racer) -> Tuple[float, float]:
    """4-5号艇因果スコア。(R45, P1への追加ペナルティ) を返す。

    4号艇が突き抜け候補（A1かつ勝率7.0↑かつ節間ST0.13以下）の場合、
    R45=0にキャップしP1から-1.0を返す。
    """
    # 突き抜け判定
    breakout = (
        b4.cls == "A1"
        and b4.win_rate is not None and b4.win_rate >= 7.0
        and b4.settle_st is not None and b4.settle_st <= 0.13
    )
    if breakout:
        return (0.0, -1.0)

    s = 0.0
    # 4号艇階級
    if b4.cls in ("A1", "A2"):
        s += 1.5
    elif b4.cls == "B2":
        s -= 1.0
    # 4号艇平均ST
    s += _band(b4.avg_st, [
        (0.00, 0.15, 1.5),
        (0.15, 0.18, 0.5),
        (0.20, 9.99, -1.0),
    ])
    # 4号艇展示
    if b4.exhibit_rank in (1, 2):
        s += 1.0
    # 4号艇まくり決まり手率
    if b4.makuri_rate is not None and b4.makuri_rate >= 0.35:
        s += 1.0
    # 4-5号艇勝率差
    if b4.win_rate is not None and b5.win_rate is not None:
        diff = b4.win_rate - b5.win_rate
        if abs(diff) <= 0.8:
            s += 0.5
        if diff > 1.5:
            s -= 1.0
    # F2
    if b4.f_count >= 2:
        s -= 1.5
    return (round(s, 2), 0.0)


def score_N23(b2: Racer, b3: Racer) -> float:
    """2-3号艇落下スコア（最大+3.0）"""
    s = 0.0
    # 2号艇
    s += {"B1": 0.5, "B2": 1.0}.get(b2.cls, 0.0)
    if b2.win_rate is not None and b2.win_rate <= 5.50:
        s += 0.5
    if b2.avg_st is not None and b2.avg_st >= 0.19:
        s += 0.5
    if b2.settle_2rate is not None and b2.settle_2rate < 0.30:
        s += 0.5
    # 3号艇
    s += {"B1": 0.3, "B2": 0.7}.get(b3.cls, 0.0)
    if b3.win_rate is not None and b3.win_rate <= 5.50:
        s += 0.3
    if b3.avg_st is not None and b3.avg_st >= 0.19:
        s += 0.3
    return round(s, 2)


def score_W(weather: Weather) -> float:
    """天候・水面補正（±3.0）"""
    s = 0.0
    w = weather
    # 風
    if w.wind_dir == "tail":
        if 1 <= w.wind_mps <= 3:
            s += 1.0
        elif w.wind_mps >= 5:
            s -= 0.5
    elif w.wind_dir == "head":
        if 3 <= w.wind_mps <= 4:
            s += 0.3
        elif 5 <= w.wind_mps <= 6:
            s -= 0.8
    # 波
    if w.wave_cm <= 3:
        s += 0.5
    elif 8 <= w.wave_cm <= 9:
        s -= 1.5
    # ナイター
    if w.is_night:
        s += 0.3
    return round(s, 2)


# ============================================================
# 総合評価・買い目・レポート
# ============================================================
def score_165(race: Race) -> Dict[str, float]:
    """TOTALと内訳を返す。抽出ハード条件はチェックしないので呼び出し側で判定のこと。"""
    P1 = score_P1(race.b1, race.venue, race.weather)
    P5 = score_P5(race.b5, race.venue)
    R45, p1_extra_penalty = score_R45(race.b4, race.b5)
    P1 += p1_extra_penalty
    N23 = score_N23(race.b2, race.b3)
    W = score_W(race.weather)
    TOTAL = round(P1 + P5 + R45 + N23 + W, 2)
    return {"P1": round(P1, 2), "P5": P5, "R45": R45, "N23": N23, "W": W, "TOTAL": TOTAL}


def judge_rank(total: float) -> str:
    if total >= 15.0:
        return "◎本命"
    if total >= 12.0:
        return "○推奨"
    if total >= 9.0:
        return "△押さえ"
    return "✕見送り"


def generate_bets(total: float) -> Dict[str, List[str]]:
    """判定ラインに応じた買い目を返す。
    
    買い目は全ランク共通で 1-5-全 + 1-全-5 の8点固定。
    見送り時のみ買い目なし。
    """
    # 固定買い目: 1-5-全(4点) + 1-全-5(4点) = 8点
    fixed_bets = [
        "1-5-2", "1-5-3", "1-5-4", "1-5-6",  # 1-5-全
        "1-2-5", "1-3-5", "1-4-5", "1-6-5",  # 1-全-5
    ]

    if total >= 15.0:
        return {
            "rank": ["◎本命"],
            "3連単_本線": fixed_bets,
            "3連単_押さえ": [],
            "2連単": [],
        }
    if total >= 12.0:
        return {
            "rank": ["○推奨"],
            "3連単_本線": fixed_bets,
            "3連単_押さえ": [],
            "2連単": [],
        }
    if total >= 9.0:
        return {
            "rank": ["△押さえ"],
            "3連単_本線": fixed_bets,
            "3連単_押さえ": [],
            "2連単": [],
        }
    return {"rank": ["✕見送り"], "3連単_本線": [], "3連単_押さえ": [], "2連単": []}


def _racer_line(label: str, r: Racer) -> str:
    return (
        f"{label} {r.name:<6} {r.cls:<2} "
        f"勝率{r.win_rate if r.win_rate is not None else '-':<5} "
        f"ST{r.avg_st if r.avg_st is not None else '-':<5} "
        f"節ST{r.settle_st if r.settle_st is not None else '-':<5} "
        f"節2率{(r.settle_2rate*100) if r.settle_2rate is not None else '-':<5} "
        f"M{(r.motor_2rate*100) if r.motor_2rate is not None else '-':<5}"
    )


def format_report(race: Race) -> str:
    """人間可読のレポートを文字列で返す。"""
    lines = [f"━━━ {race.venue} {race.r_no}R  v16 1=5形フィルタ ━━━"]

    # 抽出判定
    ok, reasons = is_165_candidate(race)
    if not ok:
        lines.append("【抽出判定】対象外")
        for r in reasons:
            lines.append(f"  - {r}")
        return "\n".join(lines)

    # 出走選手
    lines.append("【出走メンバー】")
    for label, r in zip(["1号艇", "2号艇", "3号艇", "4号艇", "5号艇", "6号艇"],
                        [race.b1, race.b2, race.b3, race.b4, race.b5, race.b6]):
        lines.append(_racer_line(label, r))

    # 天候
    w = race.weather
    lines.append(f"【水面】風{w.wind_dir} {w.wind_mps}m / 波{w.wave_cm}cm / "
                 f"安定板{'有' if w.stabilizer else '無'}{'/ナイター' if w.is_night else ''}")

    # スコア
    s = score_165(race)
    lines.append("【スコア内訳】")
    lines.append(f"  P1(1号艇逃げ)   : {s['P1']:+.2f}  / +10.0")
    lines.append(f"  P5(5号艇2-3着)  : {s['P5']:+.2f}  / +8.0")
    lines.append(f"  R45(4-5因果)    : {s['R45']:+.2f}  / +4.0")
    lines.append(f"  N23(2-3落下)    : {s['N23']:+.2f}  / +3.0")
    lines.append(f"  W(天候水面)     : {s['W']:+.2f}  / ±3.0")
    lines.append(f"  ─ TOTAL         : {s['TOTAL']:+.2f}")
    lines.append(f"  判定            : {judge_rank(s['TOTAL'])}")

    # 展開シナリオ推定
    lines.append("【展開シナリオ】")
    if s["R45"] >= 3.0 and (race.b4.exhibit_rank in (1, 2) or (race.b4.makuri_rate or 0) >= 0.35):
        lines.append("  想定A: 4号艇まくり不発→5号艇まくり差し（最頻）")
    if race.b5.cls in ("A1", "A2") and (race.b5.motor_2rate or 0) >= 0.45:
        lines.append("  想定B: 5号艇ダッシュ戦伸び一閃")
    if s["N23"] >= 2.0:
        lines.append("  想定C: 2・3号艇自滅で5号艇繰り上がり")

    # 買い目
    bets = generate_bets(s["TOTAL"])
    if bets["3連単_本線"] or bets["2連単"]:
        lines.append("【買い目】")
        if bets["3連単_本線"]:
            lines.append("  3連単本線: " + " / ".join(bets["3連単_本線"]))
        if bets["3連単_押さえ"]:
            lines.append("  3連単押さえ: " + " / ".join(bets["3連単_押さえ"]))
        if bets["2連単"]:
            lines.append("  2連単: " + " / ".join(bets["2連単"]))
        lines.append("  ※3連単 1-5-X のオッズ10倍未満の買い目はスキップ推奨")
    else:
        lines.append("【買い目】見送り")

    return "\n".join(lines)


def evaluate_race(race: Race) -> Dict[str, Any]:
    """ワンショット評価。UIから直接叩くためのエントリポイント。"""
    ok, reasons = is_165_candidate(race)
    if not ok:
        return {
            "candidate": False,
            "reject_reasons": reasons,
            "scores": None,
            "judge": "対象外",
            "bets": None,
            "report": format_report(race),
        }
    s = score_165(race)
    return {
        "candidate": True,
        "reject_reasons": [],
        "scores": s,
        "judge": judge_rank(s["TOTAL"]),
        "bets": generate_bets(s["TOTAL"]),
        "report": format_report(race),
    }


# ============================================================
# デモ実行
# ============================================================
if __name__ == "__main__":
    demo = Race(
        venue="徳山",
        r_no=12,
        b1=Racer(name="田中", cls="A1", win_rate=7.20, avg_st=0.14,
                 settle_st=0.13, settle_2rate=0.62, motor_2rate=0.48, f_count=0),
        b2=Racer(name="佐藤", cls="B1", win_rate=5.30, avg_st=0.19,
                 settle_st=0.20, settle_2rate=0.22, motor_2rate=0.30, f_count=0),
        b3=Racer(name="鈴木", cls="B1", win_rate=5.40, avg_st=0.18,
                 settle_st=0.19, settle_2rate=0.25, motor_2rate=0.35, f_count=0),
        b4=Racer(name="高橋", cls="A2", win_rate=6.30, avg_st=0.15,
                 settle_st=0.15, settle_2rate=0.42, motor_2rate=0.42, f_count=0,
                 exhibit_rank=2, makuri_rate=0.38),
        b5=Racer(name="伊藤", cls="A2", win_rate=5.80, avg_st=0.16,
                 settle_st=0.16, settle_2rate=0.55, motor_2rate=0.46, f_count=0,
                 exhibit_rank=1, course5_avg_st=0.16, weight=51.0),
        b6=Racer(name="渡辺", cls="B1", win_rate=5.00, avg_st=0.20,
                 settle_st=0.20, settle_2rate=0.15, motor_2rate=0.25, f_count=0),
        weather=Weather(wind_dir="tail", wind_mps=2, wave_cm=2,
                        stabilizer=False, is_night=False),
        course1_win_rate_venue=0.62,
        v14_hit=False,
    )

    result = evaluate_race(demo)
    print(result["report"])
    print()
    print("=== 返り値（辞書） ===")
    for k, v in result.items():
        if k != "report":
            print(f"{k}: {v}")
