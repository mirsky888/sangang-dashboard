# -*- coding: utf-8 -*-
"""
app.py에 탭으로 추가하는 예시.

    from kospi_market_score_tab import render_kospi_market_tab
    with tab_market:
        render_kospi_market_tab(df30)   # 이미 fetch_ohlcv_chunked 등으로 받아둔 30분봉 df

signal_result(선택)로 SangangEngine.evaluate() 결과를 같이 넘기면,
거시 판단과 3분봉 진입신호가 서로 충돌하는지도 함께 표시합니다.
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


def render_kospi_market_tab(price_df, signal_result=None):
    st.subheader("코스피 시장 분석 — 6요소 종합 스코어링")

    result = run_kospi_market_analysis(price_df=price_df)

    icon = VERDICT_COLOR.get(result.verdict, "⚪")
    if result.final_score is not None:
        st.markdown(f"### {icon} 현재 판단: **{result.verdict}** (부분 점수: {result.final_score})")
    else:
        st.markdown(f"### {icon} 아직 계산 가능한 항목이 없습니다.")

    if result.missing_items:
        missing_labels = ", ".join(LABEL_MAP[m] for m in result.missing_items)
        st.warning(
            f"미구현 항목: {missing_labels}\n\n"
            f"위 항목들은 해외지수·국내주식·투자자매매동향·프로그램매매 API 모듈이 "
            f"추가되면 자동으로 포함됩니다. 현재는 남은 항목만으로 가중치를 재조정한 "
            f"'부분 점수'입니다."
        )

    if result.scores:
        st.markdown("---")
        st.markdown("#### 계산된 항목별 점수 (-2 ~ +2)")
        for name, score in result.scores.items():
            label = LABEL_MAP[name]
            weight = WEIGHTS[name]
            st.progress((score + 2) / 4, text=f"{label}: {score:+.1f}점 (가중치 {int(weight*100)}%)")

    if signal_result is not None:
        st.markdown("---")
        st.markdown("#### 3분봉 진입신호와의 정합성")
        check = filter_signal_by_market(signal_result.direction, result)
        if check["warning"]:
            st.error(check["warning"])
        elif check["aligned"] is True:
            st.success(f"진입신호({signal_result.direction})와 거시 판단이 일치합니다.")
        else:
            st.info("현재 진입신호가 없어(관망) 비교할 대상이 없습니다.")
