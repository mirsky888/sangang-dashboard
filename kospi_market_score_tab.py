# -*- coding: utf-8 -*-
"""
app.py 맨 아래(원본 분봉 데이터 expander 다음, 마지막 st.success 이전)에
아래 두 줄만 추가하면 됩니다.

    from kospi_market_score_tab import render_kospi_market_tab
    render_kospi_market_tab(df30, token=token, signal_result=result)

token은 app.py에 이미 있는 get_kis_token()의 반환값을 그대로 재사용합니다.

[2026-07-20 추가] 시세 전용 실전 앱키 지원
    1번(미국시장)/2번(삼전·하닉)은 모의투자 앱키로는 호출이 막히는 TR입니다
    (EGW02004: 실전투자 도메인은 모의투자 앱키로 호출 불가).
    Streamlit Secrets에 아래 두 값을 추가하면, 이 두 항목만 자동으로
    실전투자 전용 앱키로 우회 호출합니다 (나머지 3,4,5,6번은 기존 모의투자 키 그대로 사용):

        KIS_REAL_APP_KEY = "..."
        KIS_REAL_APP_SECRET = "..."

    추가하지 않으면 기존과 동일하게 동작합니다 (1,2번은 계속 실패할 수 있음).
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


@st.cache_resource(show_spinner=False)
def _get_quote_token(app_key: str, app_secret: str):
    """시세 전용 실전투자 앱키로 별도 토큰 발급 (모의투자 토큰과 별개로 캐싱)."""
    from kis_auth import issue_token
    return issue_token(app_key, app_secret, is_paper=False)


def render_kospi_market_tab(price_df, token=None, signal_result=None):
    st.markdown("---")
    st.subheader("📊 코스피 시장 분석 — 6요소 종합 스코어링")

    app_key = st.secrets["KIS_APP_KEY"] if token is not None else None
    app_secret = st.secrets["KIS_APP_SECRET"] if token is not None else None
    is_paper = st.secrets.get("KIS_IS_PAPER", False) if token is not None else False

    # 시세 전용(1,2번) 실전 앱키 — secrets에 있으면 별도 토큰 발급해서 사용
    quote_token = quote_app_key = quote_app_secret = None
    if "KIS_REAL_APP_KEY" in st.secrets and "KIS_REAL_APP_SECRET" in st.secrets:
        quote_app_key = st.secrets["KIS_REAL_APP_KEY"]
        quote_app_secret = st.secrets["KIS_REAL_APP_SECRET"]
        try:
            quote_token = _get_quote_token(quote_app_key, quote_app_secret)
        except Exception as e:
            st.warning(
                f"⚠️ 시세 전용 실전 앱키(KIS_REAL_APP_KEY) 토큰 발급 실패: {e}\n\n"
                "1, 2번 항목은 기존 모의투자 키로 폴백해서 시도하며, 계속 실패할 수 있습니다."
            )

    result = run_kospi_market_analysis(
        price_df=price_df,
        token=token,
        app_key=app_key,
        app_secret=app_secret,
        is_paper=is_paper,
        quote_token=quote_token,
        quote_app_key=quote_app_key,
        quote_app_secret=quote_app_secret,
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
