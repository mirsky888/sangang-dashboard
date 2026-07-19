"""
산강 매매법 v2-9 실전 대시보드 (Streamlit)
=========================================

배포: GitHub push → Streamlit Cloud (로컬 Python 불필요)

⚠️ 이 파일은 뼈대(scaffold)입니다. 아래 표시된 TODO 구간에
기존에 만들어두신 KIS Developers API 연동 코드
(OAuth2 토큰 발급/갱신, 분봉/일봉 조회, 청크 단위 30일 초과 조회,
레이트리밋 재시도 로직 등)를 그대로 옮겨 붙이시면 완성됩니다.
지금은 해당 함수들이 더미(dummy) 데이터를 반환하도록 되어 있어
UI와 산강엔진 연결 구조만 먼저 확인할 수 있습니다.
"""

import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from sangang_signal_engine import SangangEngine
from sangang_channel import compute_key_levels
from kis_auth import issue_token, KisToken
from kis_futureoption import fetch_minute_ohlcv, fetch_ohlcv_chunked

st.set_page_config(page_title="산강 매매법 v2-9 대시보드", layout="wide")


# ----------------------------------------------------------------------
# KIS OAuth2 토큰 관리 (st.secrets 사용, 세션 내 캐싱)# =========================================================

# ----------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def get_kis_token() -> KisToken:
    app_key = st.secrets["ET = "zgJPXDmrSO6OltPRNE5kdTgouqDX1w"]
    app_secret = st.secrets["zgJPXDmrSO6OltPRNE5kdTgouqDX1waPfmkn4e98XK6OcSsx/XUQnrjGqjTPy6sqcO58pgdAw3qbOZK+xg9DF0eS4bh0vPBeU1Qu3SgsueBmGUJ/Ulwq3G95cnqgBgz8vvzj9315TFwYjuwxamLfz6W+ikNdmIe3OkOtg2XDvq+RjZZlBK"]
    is_paper = st.secrets.get("KIS_IS_PAPER", False)
    return issue_token(app_key, app_secret, is_paper=is_paper)


# ----------------------------------------------------------------------
# 분봉 OHLCV 조회 (60초 캐시, 청크 조회 + 레이트리밋 재시도 내장)
# ----------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def fetch_ohlcv(symbol: str, interval_min: int, lookback_days: int = 3) -> pd.DataFrame:
    token = get_kis_token()
    app_key = st.secrets["KIS_APP_KEY"]
    app_secret = st.secrets["KIS_APP_SECRET"]
    is_paper = st.secrets.get("KIS_IS_PAPER", False)

    if lookback_days <= 1:
        return fetch_minute_ohlcv(symbol, interval_min, token, app_key, app_secret, is_paper)

    return fetch_ohlcv_chunked(
        symbol, interval_min, lookback_days, token, app_key, app_secret, is_paper
    )


# ----------------------------------------------------------------------
# 사이드바 — 종목/파라미터 설정
# ----------------------------------------------------------------------
st.sidebar.title("⚙️ 설정")

symbol = st.sidebar.text_input("종목/선물코드", value="KOSPI200F202609")

tail_ratio_threshold = st.sidebar.slider(
    "꼬리 임계값 (몸통 대비 배수)", min_value=0.3, max_value=3.0, value=1.0, step=0.1
)
level_tolerance_pct = st.sidebar.slider(
    "주요자리 근접 허용오차 (%)", min_value=0.05, max_value=1.0, value=0.15, step=0.05
)

auto_refresh = st.sidebar.checkbox("자동 새로고침 (60초)", value=False)
if auto_refresh:
    st.sidebar.caption("체크 시 60초마다 자동으로 재조회합니다. (Streamlit Cloud 무료 티어는 리소스 제한에 유의)")


# ----------------------------------------------------------------------
# 데이터 조회
# ----------------------------------------------------------------------
try:
    token = get_kis_token()
    api_error = None
except Exception as e:
    token = None
    api_error = str(e)

if api_error:
    st.error(
        f"KIS API 토큰 발급에 실패했습니다: {api_error}\n\n"
        "→ .streamlit/secrets.toml (또는 Streamlit Cloud Secrets)에 "
        "KIS_APP_KEY / KIS_APP_SECRET / KIS_IS_PAPER 가 올바르게 설정되어 있는지 확인하세요."
    )
    st.stop()

with st.spinner("분봉 데이터 조회 중..."):
    try:
        df60 = fetch_ohlcv(symbol, 60, lookback_days=10)
        df30 = fetch_ohlcv(symbol, 30, lookback_days=5)
        df15 = fetch_ohlcv(symbol, 15, lookback_days=3)
        df3 = fetch_ohlcv(symbol, 3, lookback_days=1)
    except Exception as e:
        st.error(
            f"시세 조회 중 오류가 발생했습니다: {e}\n\n"
            "→ kis_futureoption.py 상단의 DEFAULT_MINUTE_PATH / DEFAULT_MINUTE_TR_ID 값이 "
            "실제 KIS Developers 포털 문서와 일치하는지 확인해 주세요."
        )
        st.stop()

if df3.empty:
    st.warning("조회된 데이터가 없습니다. 종목코드/장운영시간을 확인해 주세요.")
    st.stop()

# ----------------------------------------------------------------------
# 주요자리(key_levels) 산출 — 산강채널 + 다중이평 + 통곡의 벽 종합
# ----------------------------------------------------------------------
key_levels = compute_key_levels(df60, df30, df15)

engine = SangangEngine(
    key_levels=key_levels,
    tail_ratio_threshold=tail_ratio_threshold,
    level_tolerance_pct=level_tolerance_pct,
)

channel_center = float(df3["close"].tail(60).mean())
result = engine.evaluate(df60, df30, df15, df3, channel_center=channel_center)


# ----------------------------------------------------------------------
# 메인 화면
# ----------------------------------------------------------------------
st.title(f"📊 산강 매매법 v2-9 — {symbol}")
st.caption(f"조회 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  현재가: {df3['close'].iloc[-1]:.2f}")

col1, col2, col3 = st.columns(3)
col1.metric("신뢰도 점수", f"{result.score} / 100")
col2.metric("등급", result.grade)
col3.metric("방향", result.direction or "관망")

st.subheader("항목별 체크리스트")
detail_df = pd.DataFrame(
    {
        "항목": list(result.details.keys()),
        "충족여부": ["✅" if v else "❌" for v in result.details.values()],
        "배점": [result.breakdown[k] for k in result.details.keys()],
    }
)
st.table(detail_df)

st.subheader("주요자리 (key_levels)")
st.write(key_levels)

st.subheader("3분봉 최근 흐름")
st.line_chart(df3["close"].tail(120))

with st.expander("원본 분봉 데이터 (디버깅용)"):
    tf = st.selectbox("타임프레임 선택", ["60분", "30분", "15분", "3분"])
    mapping = {"60분": df60, "30분": df30, "15분": df15, "3분": df3}
    st.dataframe(mapping[tf].tail(30))

st.info(
    "⚠️ kis_futureoption.py 상단의 DEFAULT_MINUTE_PATH / DEFAULT_MINUTE_TR_ID 값은 "
    "추정치입니다. 기존에 쓰시던 대시보드에서 실제로 동작했던 값이 있다면 "
    "그 값으로 교체해 주세요. 나머지(OAuth2 토큰 관리, 날짜정렬, 청크조회, "
    "레이트리밋 재시도, 산강채널 계산)는 모두 실제 로직으로 연결되어 있습니다."
)
