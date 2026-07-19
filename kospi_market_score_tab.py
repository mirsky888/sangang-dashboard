# -*- coding: utf-8 -*-
"""
app.py 맨 아래(원본 분봉 데이터 expander 다음, 마지막 st.success 이전)에
아래 두 줄만 추가하면 됩니다.

    from kospi_market_score_tab import render_kospi_market_tab
    render_kospi_market_tab(df30, token=token, signal_result=result)

token은 app.py에 이미 있는 get_kis_token()의 반환값을 그대로 재사용합니다.
"""

import streamlit as st
from kospi_market_score import run_kospi_market_analysis, filter_signal_by_market, WEIGHTS

LABEL_MAP = {
    "us_market": "1. 미국시장",
    "semis": "2. 삼전·하닉",
    "foreign_flow": "3. 외국인 선물+현물",
    "pension_fund": "4. 연기금·사모펀드(근사)",
    "program_trading": "5. 프로그램매매",
    "price_chart": "6. 가격(차트)",
}

VERDICT_COLOR = {
    "강세": "🟢",
    "약강세": "🟡",
    "약세": "🟠",
    "폭락장": "🔴",
    "데이터 부족": "⚪",
}


def render_kospi_market_tab(price_df, token=None, signal_result=None):
    st.markdown("---")
    st.subheader("📊 코스피 시장 분석 — 6요소 종합 스코어링")

    app_key = st.secrets["KIS_APP_KEY"] if token is not None else None
    app_secret = st.secrets["KIS_APP_SECRET"] if token is not None else None
    is_paper = st.secrets.get("KIS_IS_PAPER", False) if token is not None else False

    result = run_kospi_market_analysis(
        price_df=price_df,
        token=token,
        app_key=app_key,
        app_secret=app_secret,
        is_paper=is_paper,
    )

    icon = VERDICT_COLOR.get(result.verdict, "⚪")
    if result.final_score is not None:
        st.markdown(f"### {icon} 현재 판단: **{result.verdict}** (부분 점수: {result.final_score})")
    else:
        st.markdown(f"### {icon} 아직 계산 가능한 항목이 없습니다.")

    if result.missing_items:
        missing_labels = ", ".join(LABEL_MAP[m] for m in result.missing_items)
        st.warning(
            f"미구현/실패 항목: {missing_labels}\n\n"
            f"현재는 계산 가능한 나머지 항목만으로 가중치를 재조정한 '부분 점수'입니다."
        )

    if result.errors:
        with st.expander("⚠️ 실제 API 호출 실패 상세 (디버깅용)"):
            for name, msg in result.errors.items():
                st.code(f"[{LABEL_MAP.get(name, name)}]\n{msg}")
            st.caption(
                "위 에러가 tr_id 오류인지, 계좌 권한(모의/실전 불일치) 문제인지, "
                "네트워크/레이트리밋 문제인지에 따라 대응이 다릅니다. "
                "메시지를 붙여주시면 정확한 원인을 같이 확인하겠습니다."
            )

    if result.scores:
        st.markdown("#### 계산된 항목별 점수 (-2 ~ +2)")
        for name, score in result.scores.items():
            label = LABEL_MAP[name]
            weight = WEIGHTS[name]
            st.progress((score + 2) / 4, text=f"{label}: {score:+.1f}점 (가중치 {int(weight*100)}%)")

    if signal_result is not None:
        st.markdown("#### 3분봉 진입신호와의 정합성")
        check = filter_signal_by_market(signal_result.direction, result)
        if check["warning"]:
            st.error(check["warning"])
        elif check["aligned"] is True:
            st.success(f"진입신호({signal_result.direction})와 거시 판단이 일치합니다.")
        else:
            st.info("현재 진입신호가 없어(관망) 비교할 대상이 없습니다.")
