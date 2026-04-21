# -*- coding: utf-8 -*-
"""
v16 1=5形フィルタ Streamlit UI (期間指定スクレイピング版・買い目固定)
=======================================================
boatrace.jp から出走表を取得し、指定期間の全開催場・全レースを走査して
v16 1=5形の候補レースを抽出する。

必要ファイル (同じディレクトリに置くこと):
    - v16_itigo_filter.py
    - v16_fetcher.py

起動:
    streamlit run v16_streamlit_app.py
"""

from datetime import datetime, timedelta
import pandas as pd
import streamlit as st

from v16_itigo_filter import (
    Racer, Weather, Race,
    score_165, is_165_candidate, judge_rank, generate_bets,
)
from v16_fetcher import (
    fetch_day_candidates, date_to_str,
    enrich_candidates_with_results, aggregate_recovery,
    JCD_TO_NAME, NAME_TO_JCD, COURSE1_WIN_RATE,
)

st.set_page_config(page_title="v16 1=5形フィルタ", layout="centered")
st.title("🚤 v16 1=5形スジ追撃フィルタ")
st.caption("1着=1号艇 / 2-3着=5号艇 を狙い撃つ")


# ======================================================
# 詳細レンダリング関数（共通）
# ======================================================
def render_detail(sel: dict):
    race = sel["race"]
    # 日付があれば日付もタイトルに表示
    date_label = f"[{sel['date']}] " if "date" in sel else ""
    st.markdown(f"### {date_label}{sel['venue']} {sel['r_no']}R")

    if not sel["candidate"]:
        st.error("❌ 対象外: " + " / ".join(sel["reject_reasons"]))
        return

    j = sel["judge"]
    total = sel["scores"]["TOTAL"]
    if "本命" in j:
        st.success(f"✅ {j}  TOTAL {total:+.2f}")
    elif "推奨" in j:
        st.info(f"🔷 {j}  TOTAL {total:+.2f}")
    elif "押さえ" in j:
        st.warning(f"🔸 {j}  TOTAL {total:+.2f}")
    else:
        st.error(f"✕ {j}  TOTAL {total:+.2f}")

    s = sel["scores"]
    c = st.columns(5)
    c[0].metric("P1 逃げ", f"{s['P1']:+}")
    c[1].metric("P5 5号艇", f"{s['P5']:+}")
    c[2].metric("R45 因果", f"{s['R45']:+}")
    c[3].metric("N23 落下", f"{s['N23']:+}")
    c[4].metric("W 天候", f"{s['W']:+}")

    boats = [race.b1, race.b2, race.b3, race.b4, race.b5, race.b6]
    rows = []
    for i, r in enumerate(boats, start=1):
        rows.append({
            "艇": i,
            "名前": r.name,
            "級": r.cls,
            "勝率": r.win_rate if r.win_rate is not None else "-",
            "ST": r.avg_st if r.avg_st is not None else "-",
            "節ST": r.settle_st if r.settle_st is not None else "-",
            "節2率": f"{r.settle_2rate*100:.0f}" if r.settle_2rate is not None else "-",
            "M2率": f"{r.motor_2rate*100:.0f}" if r.motor_2rate is not None else "-",
            "F": r.f_count,
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # 買い目を 1-45-45, 1-56-56 に固定表示
    if sel["candidate"]:
        st.subheader("🎯 推奨買い目 (固定)")
        st.markdown("**3連単**: 1-4-5 / 1-5-4 / 1-5-6 / 1-6-5")
        st.markdown("*(フォーメーション: 1-45-45, 1-56-56)*")
        st.caption("※オッズ10倍未満の買い目はスキップ推奨")

    # 結果と回収率（終了済みの場合）
    result = sel.get("result")
    recovery = sel.get("recovery")
    if result:
        st.markdown("---")
        st.subheader("🏁 レース結果")
        finish = result["finish_order"]

        c1, c2, c3 = st.columns([1, 1, 1])
        c1.markdown(f"**着順**: {'-'.join(str(n) for n in finish[:3])}")
        if result.get("kimarite"):
            c2.markdown(f"**決まり手**: {result['kimarite']}")
        c3.markdown(f"**1=5形**: {'✅ 的中' if result['is_165_hit'] else '✕ 不発'}")

        # 配当
        d1, d2 = st.columns(2)
        if result.get("trifecta_combo"):
            d1.metric(
                "3連単",
                result["trifecta_combo"],
                f"¥{result.get('trifecta_payout', 0):,}",
            )
        if result.get("exacta_combo"):
            d2.metric(
                "2連単",
                result["exacta_combo"],
                f"¥{result.get('exacta_payout', 0):,}",
            )

        # 回収率
        if recovery:
            st.markdown("### 💰 買い目収支（1点100円換算）")
            rr = recovery["recovery_rate"]
            profit = recovery["total_return"] - recovery["total_spent"]
            r1, r2, r3 = st.columns(3)
            r1.metric("投資額", f"¥{recovery['total_spent']:,}")
            r2.metric("回収額", f"¥{recovery['total_return']:,}")
            r3.metric(
                "回収率",
                f"{rr}%",
                f"{profit:+,}円",
                delta_color=("normal" if rr >= 100 else "inverse"),
            )
            if recovery["hits"]:
                st.markdown("**的中買い目**:")
                for combo, payout in recovery["hits"]:
                    st.markdown(f"- `{combo}` → ¥{payout:,}")
            else:
                st.info("買い目不的中")
    elif sel.get("date"):
        st.info("🕓 このレースはまだ結果が出ていません")

    jcd = sel.get("jcd") or NAME_TO_JCD.get(sel["venue"], "")
    # 公式ページ遷移時のURL用（範囲文字列が入らないように sel['date'] を優先）
    date_str = sel.get("date") or (st.session_state.get("target_date_str", "").split(" ")[0])
    
    if jcd and date_str and len(date_str) == 8:
        st.markdown("**🔗 公式ページ**")
        base = "https://www.boatrace.jp/owpc/pc/race"
        st.markdown(
            f"- [出走表]({base}/racelist?rno={sel['r_no']}&jcd={jcd}&hd={date_str}) "
            f"/ [オッズ]({base}/odds3t?rno={sel['r_no']}&jcd={jcd}&hd={date_str}) "
            f"/ [直前情報]({base}/beforeinfo?rno={sel['r_no']}&jcd={jcd}&hd={date_str})"
        )


# ======================================================
# タブ
# ======================================================
tab1, tab2 = st.tabs(["📅 期間指定で一括抽出", "🔧 手動入力"])

# ------------------------------------------------------
# タブ1: 期間指定で一括抽出
# ------------------------------------------------------
with tab1:
    st.subheader("指定期間の全開催場・全Rを走査")

    col1, col2 = st.columns([2, 1])
    with col1:
        # valueにタプルを渡すことで範囲選択を可能にする
        date_range = st.date_input(
            "対象期間 (開始日と終了日を選択)",
            value=(datetime.now().date(), datetime.now().date()),
            min_value=datetime(2020, 1, 1).date(),
            max_value=datetime.now().date() + timedelta(days=1),
        )
    with col2:
        min_total_opt = st.selectbox(
            "最低スコア",
            options=[("△押さえ以上 (9.0↑)", 9.0),
                     ("○推奨以上 (12.0↑)", 12.0),
                     ("◎本命のみ (15.0↑)", 15.0)],
            format_func=lambda x: x[0],
            index=1,
        )
        min_total = min_total_opt[1]

    use_beforeinfo = st.checkbox(
        "直前情報も取得（天候・展示・安定板）",
        value=False,
        help="取得時間が約2倍になります。直前情報発表前のレースには無効。",
    )
    fetch_results = st.checkbox(
        "レース結果・回収率も取得",
        value=True,
        help="終了済みレースの着順・払戻金・買い目との照合結果を表示します。",
    )

    if st.button("🎯 取得して抽出", use_container_width=True, type="primary"):
        # 範囲選択の処理 (1日だけ選択された場合は要素が1つのタプルになる)
        if isinstance(date_range, tuple) or isinstance(date_range, list):
            start_date = date_range[0]
            end_date = date_range[1] if len(date_range) > 1 else start_date
        else:
            start_date = end_date = date_range

        # 対象日付のリストを作成
        date_list = []
        curr = start_date
        while curr <= end_date:
            date_list.append(curr)
            curr += timedelta(days=1)

        prog = st.progress(0.0, text="処理を開始します...")
        all_cands = []

        with st.spinner(f"データ取得中... (全{len(date_list)}日)"):
            for i, target_d in enumerate(date_list):
                date_str = date_to_str(target_d)

                def pb(done, total, label):
                    # 複数日の進捗を分かりやすく表示
                    overall_text = f"[{i+1}/{len(date_list)}日目: {date_str}] {label}"
                    prog.progress(done / max(total, 1), text=overall_text)

                day_cands = fetch_day_candidates(
                    date_str,
                    min_total=min_total,
                    use_beforeinfo=use_beforeinfo,
                    progress_cb=pb,
                )
                
                # 結果取得（終了済みレースのみ）
                if fetch_results and day_cands:
                    day_cands = enrich_candidates_with_results(day_cands, progress_cb=pb)
                
                # 個別のレースに日付情報を付与
                for c in day_cands:
                    c["date"] = date_str
                    
                all_cands.extend(day_cands)

        prog.empty()
        st.session_state["candidates"] = all_cands
        
        # セッションに保存する日付文字列（表示用）
        if start_date != end_date:
            st.session_state["target_date_str"] = f"{date_to_str(start_date)} ~ {date_to_str(end_date)}"
        else:
            st.session_state["target_date_str"] = date_to_str(start_date)

    # 結果表示
    if "candidates" in st.session_state:
        cands = st.session_state["candidates"]
        display_date_str = st.session_state.get("target_date_str", "")

        if not cands:
            st.warning(
                f"{display_date_str} の候補レースは0件でした。\n\n"
                "考えられる理由:\n"
                "- 指定期間に開催がない\n"
                "- 抽出条件を満たすレースがない\n"
                "- boatrace.jp への接続失敗（時間をおいて再試行）"
            )
        else:
            # 念のため scores が None のものを除外（防御）
            cands = [c for c in cands if c.get("scores") is not None]
            st.success(f"✅ {len(cands)}件の候補を抽出しました ({display_date_str})")

            # 全体集計（終了済みレースがある場合のみ）
            agg = aggregate_recovery(cands)
            if agg.get("n_finished", 0) > 0:
                st.markdown("### 📊 全体成績")
                c1, c2, c3 = st.columns(3)
                c1.metric(
                    "終了レース",
                    f"{agg['n_finished']}/{agg['n_total']}",
                    f"未終了 {agg['n_pending']}" if agg.get('n_pending') else None,
                )
                c2.metric(
                    "買い目的中",
                    f"{agg['n_any_hit']}/{agg['n_finished']}",
                    f"{agg['hit_any_rate']}%",
                )
                rr = agg['recovery_rate']
                c3.metric(
                    "回収率",
                    f"{rr}%",
                    f"{'+' if rr >= 100 else ''}{int(agg['total_return'] - agg['total_spent']):+,}円",
                    delta_color=("normal" if rr >= 100 else "inverse"),
                )
                st.caption(f"投資 ¥{agg['total_spent']:,} / 回収 ¥{agg['total_return']:,}（1点100円換算）")
                st.markdown("---")

            rows = []
            for c in cands:
                s = c["scores"]
                result = c.get("result")
                recovery = c.get("recovery")

                # 結果表示（買い目的中ベース）
                if result:
                    finish = "-".join(str(n) for n in result["finish_order"][:3])
                    # 買い目的中の有無で判定
                    if recovery and recovery.get("hits"):
                        hit_mark = "✅"
                    else:
                        hit_mark = "✕"
                    rr_pct = f"{recovery['recovery_rate']}%" if recovery else "-"
                else:
                    finish = "未終了"
                    hit_mark = "-"
                    rr_pct = "-"

                rows.append({
                    "日付": c.get("date", ""),
                    "場": c["venue"],
                    "R": c["r_no"],
                    "判定": c["judge"],
                    "TOTAL": f"{s['TOTAL']:+.2f}",
                    "結果": finish,
                    "的中": hit_mark,
                    "回収率": rr_pct,
                    "P1": f"{s['P1']:+.1f}",
                    "P5": f"{s['P5']:+.1f}",
                    "R45": f"{s['R45']:+.1f}",
                    "1号艇": c["race"].b1.name,
                    "5号艇": c["race"].b5.name,
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            st.markdown("---")
            st.subheader("🔍 詳細を見る")
            options = [f"{c.get('date', '')} {c['venue']} {c['r_no']}R  {c['judge']} "
                       f"{c['scores']['TOTAL']:+.1f}" for c in cands]
            idx = st.selectbox(
                "レースを選択",
                options=list(range(len(cands))),
                format_func=lambda i: options[i],
            )
            render_detail(cands[idx])


# ------------------------------------------------------
# タブ2: 手動入力
# ------------------------------------------------------
with tab2:
    st.subheader("手動でレースデータを入力")

    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        venues_list = list(JCD_TO_NAME.values())
        m_venue = st.selectbox("場", venues_list, index=17, key="m_venue")
    with col2:
        m_rno = st.number_input("R", 1, 12, 12, key="m_rno")
    with col3:
        m_c1r = st.number_input(
            "場1C1着率", 0.0, 1.0,
            COURSE1_WIN_RATE.get(m_venue, 0.55), step=0.01, key="m_c1r"
        )

    with st.expander("🌊 水面・天候", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            m_wd = st.selectbox("風向", ["calm", "tail", "head", "side"],
                                index=0, key="m_wd")
            m_wv = st.number_input("波(cm)", 0.0, 30.0, 2.0, step=1.0, key="m_wv")
        with c2:
            m_wm = st.number_input("風速(m)", 0.0, 20.0, 2.0, step=1.0, key="m_wm")
            m_st_stab = st.checkbox("安定板使用", key="m_st_stab")
            m_nt = st.checkbox("ナイター", key="m_nt")

    def racer_input(boat_no: int, defaults: dict) -> Racer:
        with st.expander(f"🚤 {boat_no}号艇", expanded=(boat_no in (1, 4, 5))):
            c1, c2 = st.columns(2)
            with c1:
                name = st.text_input("名前", defaults.get("name", ""), key=f"mn{boat_no}")
                cls_ = st.selectbox("階級", ["A1", "A2", "B1", "B2"],
                                     index=["A1", "A2", "B1", "B2"].index(defaults.get("cls", "B1")),
                                     key=f"mc{boat_no}")
                win_rate = st.number_input("全国勝率", 0.0, 10.0,
                                            defaults.get("win_rate", 5.50),
                                            step=0.01, key=f"mw{boat_no}")
                avg_st = st.number_input("全国平均ST", 0.00, 0.30,
                                          defaults.get("avg_st", 0.17),
                                          step=0.01, key=f"mst{boat_no}")
                f_count = st.selectbox("F持ち", [0, 1, 2],
                                        index=defaults.get("f_count", 0),
                                        key=f"mf{boat_no}")
            with c2:
                settle_st = st.number_input("節間ST", 0.00, 0.30,
                                             defaults.get("settle_st", 0.17),
                                             step=0.01, key=f"msst{boat_no}")
                settle_2rate = st.number_input("節間2連率", 0.0, 1.0,
                                                defaults.get("settle_2rate", 0.35),
                                                step=0.01, key=f"ms2r{boat_no}")
                motor_2rate = st.number_input("モーター2連率", 0.0, 1.0,
                                               defaults.get("motor_2rate", 0.35),
                                               step=0.01, key=f"mm{boat_no}")
                exhibit_rank = st.selectbox("展示順位", [None, 1, 2, 3, 4, 5, 6],
                                             key=f"mex{boat_no}")
            makuri = course5 = weight = None
            if boat_no == 4:
                makuri = st.number_input("まくり決まり手率(半年)", 0.0, 1.0, 0.30,
                                          step=0.01, key=f"mmk{boat_no}")
            if boat_no == 5:
                course5 = st.number_input("5コース半年平均ST", 0.00, 0.30, 0.17,
                                           step=0.01, key=f"mc5{boat_no}")
                weight = st.number_input("体重(kg)", 40.0, 80.0, 53.0,
                                          step=0.5, key=f"mwt{boat_no}")
        return Racer(
            name=name, cls=cls_, win_rate=win_rate, avg_st=avg_st,
            settle_st=settle_st, settle_2rate=settle_2rate,
            motor_2rate=motor_2rate, f_count=f_count,
            exhibit_rank=exhibit_rank, course5_avg_st=course5,
            weight=weight, makuri_rate=makuri,
        )

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
    boats = [racer_input(i + 1, d) for i, d in enumerate(defaults)]
    m_v14 = st.checkbox("v14ハイエナ側で既にヒット済み", key="m_v14")

    if st.button("🎯 評価する", use_container_width=True,
                 type="primary", key="m_eval"):
        race = Race(
            venue=m_venue, r_no=int(m_rno),
            b1=boats[0], b2=boats[1], b3=boats[2],
            b4=boats[3], b5=boats[4], b6=boats[5],
            weather=Weather(wind_dir=m_wd, wind_mps=m_wm,
                            wave_cm=m_wv, stabilizer=m_st_stab, is_night=m_nt),
            course1_win_rate_venue=m_c1r,
            v14_hit=m_v14,
        )
        ok, reasons = is_165_candidate(race)
        sel = {
            "venue": m_venue, "r_no": int(m_rno), "race": race,
            "scores": score_165(race) if ok else None,
            "judge": judge_rank(score_165(race)["TOTAL"]) if ok else "対象外",
            "bets": generate_bets(score_165(race)["TOTAL"]) if ok else None,
            "reject_reasons": reasons, "candidate": ok,
        }
        render_detail(sel)
