# -*- coding: utf-8 -*-
"""
v16 1=5形フィルタ Streamlit UIサンプル
=====================================
v16_itigo_filter.py を同じディレクトリに置いた状態で
    streamlit run v16_streamlit_app.py
で起動。

スマホ入力を想定して1号艇から順に縦スクロールで入力。
"""

import streamlit as st
from v16_itigo_filter import (
    Racer, Weather, Race,
    evaluate_race, VENUE_BONUS_P1,
)

st.set_page_config(page_title="v16 1=5形フィルタ", layout="centered")
st.title("🚤 v16 1=5形スジ追撃フィルタ")
st.caption("1着=1号艇 / 2-3着=5号艇 を狙い撃つ")


# --------- 場・レース番号 ---------
col1, col2, col3 = st.columns([2, 1, 1])
with col1:
    venues = ["桐生", "戸田", "江戸川", "平和島", "多摩川", "浜名湖",
              "蒲郡", "常滑", "津", "三国", "びわこ", "住之江",
              "尼崎", "鳴門", "丸亀", "児島", "宮島", "徳山",
              "下関", "若松", "芦屋", "福岡", "唐津", "大村"]
    venue = st.selectbox("場", venues, index=17)
with col2:
    r_no = st.number_input("R", 1, 12, 12)
with col3:
    course1_rate = st.number_input("場1C1着率", 0.0, 1.0, 0.60, step=0.01)


# --------- 天候 ---------
with st.expander("🌊 水面・天候", expanded=False):
    c1, c2 = st.columns(2)
    with c1:
        wind_dir = st.selectbox("風向", ["calm", "tail", "head", "side"], index=0)
        wave_cm = st.number_input("波(cm)", 0.0, 30.0, 2.0, step=1.0)
    with c2:
        wind_mps = st.number_input("風速(m)", 0.0, 20.0, 2.0, step=1.0)
        stabilizer = st.checkbox("安定板使用")
        is_night = st.checkbox("ナイター")


# --------- 選手入力用ヘルパー ---------
def racer_input(label: str, boat_no: int, defaults: dict) -> Racer:
    with st.expander(f"🚤 {label}", expanded=(boat_no in (1, 4, 5))):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("名前", defaults.get("name", ""), key=f"n{boat_no}")
            cls_ = st.selectbox("階級", ["A1", "A2", "B1", "B2"],
                                index=["A1", "A2", "B1", "B2"].index(defaults.get("cls", "B1")),
                                key=f"c{boat_no}")
            win_rate = st.number_input("全国勝率", 0.0, 10.0,
                                        defaults.get("win_rate", 5.50), step=0.01, key=f"w{boat_no}")
            avg_st = st.number_input("全国平均ST", 0.00, 0.30,
                                      defaults.get("avg_st", 0.17), step=0.01, key=f"st{boat_no}")
            f_count = st.selectbox("F持ち", [0, 1, 2],
                                    index=defaults.get("f_count", 0), key=f"f{boat_no}")
        with c2:
            settle_st = st.number_input("節間ST", 0.00, 0.30,
                                         defaults.get("settle_st", 0.17), step=0.01, key=f"sst{boat_no}")
            settle_2rate = st.number_input("節間2連率", 0.0, 1.0,
                                            defaults.get("settle_2rate", 0.35), step=0.01, key=f"s2r{boat_no}")
            motor_2rate = st.number_input("モーター2連率", 0.0, 1.0,
                                           defaults.get("motor_2rate", 0.35), step=0.01, key=f"m{boat_no}")
            exhibit_rank = st.selectbox("展示順位", [None, 1, 2, 3, 4, 5, 6], key=f"ex{boat_no}")

        # 4号艇・5号艇だけの追加項目
        makuri = None
        course5 = None
        weight = None
        if boat_no == 4:
            makuri = st.number_input("まくり決まり手率(半年)", 0.0, 1.0, 0.30, step=0.01, key=f"mk{boat_no}")
        if boat_no == 5:
            course5 = st.number_input("5コース半年平均ST", 0.00, 0.30, 0.17, step=0.01, key=f"c5st{boat_no}")
            weight = st.number_input("体重(kg)", 40.0, 80.0, 53.0, step=0.5, key=f"wt{boat_no}")

    return Racer(
        name=name, cls=cls_, win_rate=win_rate, avg_st=avg_st,
        settle_st=settle_st, settle_2rate=settle_2rate,
        motor_2rate=motor_2rate, f_count=f_count,
        exhibit_rank=exhibit_rank, course5_avg_st=course5,
        weight=weight, makuri_rate=makuri,
    )


# 初期値（デモ）
defaults = [
    {"name": "田中", "cls": "A1", "win_rate": 7.20, "avg_st": 0.14,
     "settle_st": 0.13, "settle_2rate": 0.62, "motor_2rate": 0.48},
    {"name": "佐藤", "cls": "B1", "win_rate": 5.30, "avg_st": 0.19,
     "settle_st": 0.20, "settle_2rate": 0.22, "motor_2rate": 0.30},
    {"name": "鈴木", "cls": "B1", "win_rate": 5.40, "avg_st": 0.18,
     "settle_st": 0.19, "settle_2rate": 0.25, "motor_2rate": 0.35},
    {"name": "高橋", "cls": "A2", "win_rate": 6.30, "avg_st": 0.15,
     "settle_st": 0.15, "settle_2rate": 0.42, "motor_2rate": 0.42},
    {"name": "伊藤", "cls": "A2", "win_rate": 5.80, "avg_st": 0.16,
     "settle_st": 0.16, "settle_2rate": 0.55, "motor_2rate": 0.46},
    {"name": "渡辺", "cls": "B1", "win_rate": 5.00, "avg_st": 0.20,
     "settle_st": 0.20, "settle_2rate": 0.15, "motor_2rate": 0.25},
]

boats = []
for i, d in enumerate(defaults, start=1):
    boats.append(racer_input(f"{i}号艇", i, d))

v14_hit = st.checkbox("v14ハイエナ側で既にヒット済み（両方見送り判定用）")

# --------- 評価 ---------
if st.button("🎯 評価する", use_container_width=True, type="primary"):
    race = Race(
        venue=venue, r_no=int(r_no),
        b1=boats[0], b2=boats[1], b3=boats[2],
        b4=boats[3], b5=boats[4], b6=boats[5],
        weather=Weather(wind_dir=wind_dir, wind_mps=wind_mps,
                        wave_cm=wave_cm, stabilizer=stabilizer, is_night=is_night),
        course1_win_rate_venue=course1_rate,
        v14_hit=v14_hit,
    )
    result = evaluate_race(race)

    # 判定バナー
    if not result["candidate"]:
        st.error(f"❌ 対象外 / 不合格理由: " + " ・ ".join(result["reject_reasons"]))
    else:
        j = result["judge"]
        if "本命" in j:
            st.success(f"✅ {j}  TOTAL {result['scores']['TOTAL']}")
        elif "推奨" in j:
            st.info(f"🔷 {j}  TOTAL {result['scores']['TOTAL']}")
        elif "押さえ" in j:
            st.warning(f"🔸 {j}  TOTAL {result['scores']['TOTAL']}")
        else:
            st.error(f"✕ {j}  TOTAL {result['scores']['TOTAL']}")

        # スコア内訳
        s = result["scores"]
        c = st.columns(5)
        c[0].metric("P1 逃げ", f"{s['P1']:+}")
        c[1].metric("P5 5号艇", f"{s['P5']:+}")
        c[2].metric("R45 因果", f"{s['R45']:+}")
        c[3].metric("N23 落下", f"{s['N23']:+}")
        c[4].metric("W 天候", f"{s['W']:+}")

        # 買い目
        bets = result["bets"]
        if bets and (bets["3連単_本線"] or bets["2連単"]):
            st.subheader("🎯 推奨買い目")
            if bets["3連単_本線"]:
                st.markdown("**3連単 本線**: " + " / ".join(bets["3連単_本線"]))
            if bets["3連単_押さえ"]:
                st.markdown("**3連単 押さえ**: " + " / ".join(bets["3連単_押さえ"]))
            if bets["2連単"]:
                st.markdown("**2連単**: " + " / ".join(bets["2連単"]))
            st.caption("※3連単 1-5-X のオッズ10倍未満の買い目はスキップ推奨")

    # 全文レポート
    with st.expander("📋 全文レポート"):
        st.code(result["report"])
