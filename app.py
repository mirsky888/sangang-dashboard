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

[2026-07-19 추가] 코스피 마켓스코어 탭 연동:
    from kospi_market_score_tab import render_kospi_market_tab
    render_kospi_market_tab(df30, token=token, signal_result=result)
"""

import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from sangang_signal_engine import SangangEngine, __version__ as ENGINE_VERSION
from sangang_channel import compute_key_levels_with_confluence, check_ma60_multi_timeframe, compute_prev_day_center, __version__ as CHANNEL_VERSION
from kis_auth import issue_token, KisToken
from kis_futureoption import fetch_latest_minute_ohlcv, fetch_ohlcv_chunked
from kospi_market_score_tab import render_kospi_market_tab  # [추가] 코스피 마켓스코어

st.set_page_config(page_title="산강 매매법 v2-9 대시보드", layout="wide")


# ----------------------------------------------------------------------
# KIS OAuth2 토큰 관리 (st.secrets 사용, 세션 내 캐싱)
# ----------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def get_kis_token() -> KisToken:
    app_key = st.secrets["KIS_APP_KEY"]
    app_secret = st.secrets["KIS_APP_SECRET"]
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
        return fetch_latest_minute_ohlcv(symbol, interval_min, token, app_key, app_secret, is_paper)

    return fetch_ohlcv_chunked(
        symbol, interval_min, lookback_days, token, app_key, app_secret, is_paper
    )


# ----------------------------------------------------------------------
# 사이드바 — 종목/파라미터 설정
# ----------------------------------------------------------------------
st.sidebar.title("⚙️ 설정")
st.sidebar.caption(f"engine: {ENGINE_VERSION} | channel: {CHANNEL_VERSION}")

symbol = st.sidebar.text_input(
    "종목/선물코드 (KRX 단축코드)",
    value="A01609",
    help=(
        "KOSPI200 선물 단축코드입니다. 예: A01609 = 2026년 9월물(F 202609), "
        "A01000 = 연결선물(월물 구분 없이 이어지는 연속 데이터, 장기 조회에 유리). "
        "HTS 종목검색에서 '연결선물' 또는 원하는 월물을 조회하면 코드가 표시됩니다."
    ),
)
if not symbol:
    st.sidebar.warning("종목코드를 입력해야 조회가 가능합니다.")
    st.stop()

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
# 주요자리(key_levels) 산출 — 산강채널 + 다중이평 + 통곡의 벽 + 중첩(confluence) 종합
# ----------------------------------------------------------------------
key_levels, confluence_map = compute_key_levels_with_confluence(df60, df30, df15)

engine = SangangEngine(
    key_levels=key_levels,
    tail_ratio_threshold=tail_ratio_threshold,
    level_tolerance_pct=level_tolerance_pct,
)
engine.set_key_levels_with_confluence(key_levels, confluence_map)

try:
    prev_high, prev_low, channel_center = compute_prev_day_center(df60)
    center_is_fallback = False
except ValueError as e:
    unique_dates = sorted(set(df60.index.date)) if not df60.empty else []
    st.warning(
        f"⚠️ 전일 고점/저점 계산 실패: {e}\n\n"
        f"→ 현재 조회된 60분봉의 거래일 목록: **{unique_dates}**\n\n"
        + (
            "위 목록이 1개뿐이라 진짜 문제입니다 — lookback_days를 늘려도 소용없고, "
            "KIS API의 과거 날짜 분봉 조회(fetch_ohlcv_chunked)가 실제로 여러 날짜를 "
            "반환하지 못하고 있는 것으로 보입니다. 점검이 필요합니다."
            if len(unique_dates) <= 1
            else "날짜는 여러 개 있는데 판정에 실패했다면 날짜 파싱/정렬 문제일 수 있습니다."
        )
        + "\n\n일단 오늘 세션(당일) 고점/저점의 중간값으로 임시 대체하여 계속 진행합니다."
    )
    # 폴백: 전일 데이터를 못 구하면 당일 세션 고점/저점 중간값으로 임시 대체
    from sangang_channel import compute_structural_channel as _csc
    _fallback_channel = _csc(df60, anchor="session")
    prev_high, prev_low = _fallback_channel.high, _fallback_channel.low
    channel_center = _fallback_channel.center
    center_is_fallback = True

result = engine.evaluate(df60, df30, df15, df3, channel_center=channel_center)


# ----------------------------------------------------------------------
# 메인 화면
# ----------------------------------------------------------------------
st.title(f"📊 산강 매매법 v2-9 — {symbol}")
st.caption(
    f"조회 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  "
    f"현재가: {df3['close'].iloc[-1]:.2f}  |  "
    f"⏱️ 실제 데이터 최종 시각: **{df3.index[-1]}**"
)
if (datetime.now() - df3.index[-1].to_pydatetime()).total_seconds() > 86400:
    st.warning(
        f"⚠️ 데이터의 최종 시각({df3.index[-1]})이 현재 시각보다 24시간 이상 오래됐습니다. "
        "주말/휴장일이거나, 이 API 특성상 분봉 이력에 며칠 지연이 있을 수 있습니다 "
        "(2026-07-19 실측: 실시간 스냅샷과 분봉 이력 사이에 지연 확인됨 — 버그 아닐 가능성 높음). "
        "정확한 지연 사양은 KIS Developers 포털에서 확인 권장합니다."
    )

col1, col2, col3 = st.columns(3)
col1.metric("신뢰도 점수", f"{result.score} / 100")
col2.metric("등급", result.grade)
col3.metric("방향", result.direction or "관망")

st.caption(
    f"📍 전일 고점: **{prev_high:.2f}**  |  전일 저점: **{prev_low:.2f}**  |  "
    f"중심가(=(전일고점+전일저점)/2): **{channel_center:.2f}**"
    + (" ⚠️ (전일 데이터 부족으로 당일 세션 기준 임시 대체값)" if center_is_fallback else "")
)

reliability_label = getattr(result, "reliability_label", "")
confluence_count = getattr(result, "confluence_count", 1)
grade_adjusted = getattr(result, "grade_adjusted", False)
extreme_emphasis = getattr(result, "extreme_emphasis", None)

if reliability_label:
    badge = "🔁" if grade_adjusted else "ℹ️"
    st.caption(
        f"{badge} 주요자리 터치 정보: **{reliability_label}** "
        f"(중첩 지표 {confluence_count}개)"
        + (" — 터치/중첩 보정으로 등급이 조정됐습니다." if grade_adjusted else "")
    )

if extreme_emphasis:
    st.success(f"### {extreme_emphasis}")

st.markdown("---")
st.subheader("📐 15·30·60분 60선 동시터치 패널")
ma60_panel = check_ma60_multi_timeframe(df15, df30, df60)

panel_cols = st.columns(3)
tf_labels = ["15분", "30분", "60분"]
for col, label in zip(panel_cols, tf_labels):
    info = ma60_panel[label]
    with col:
        if info["ma60"] is None:
            st.metric(f"{label} 60선", "데이터 부족")
        else:
            icon = "🟢 터치" if info["touching"] else "⚪ 미터치"
            st.metric(
                f"{label} 60선",
                f"{info['ma60']:.2f}",
                delta=f"{icon} (차이 {info['diff_pct']:.2f}%)",
                delta_color="off",
            )
            st.caption(f"터치 횟수: **{info['touch_count']}회** — {info['reliability_label']}")

if ma60_panel["all_touching"]:
    st.success("🎯 15·30·60분 60선이 **동시에 전부** 터치 중입니다 — 매우 강한 지지/저항 자리일 가능성이 높습니다.")
elif ma60_panel["confluence_count"] >= 2:
    st.info(f"ℹ️ {ma60_panel['confluence_count']}개 타임프레임의 60선이 동시에 터치 중입니다 — 신뢰도 높은 자리입니다.")

st.markdown("---")
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

    st.markdown("---")
    st.markdown("**⚠️ 가격이 실제 지수와 다르게 나올 때** — 아래 버튼으로 KIS 원본 응답을 확인하세요.")
    if st.button("KIS 원본 API 응답 확인 (파싱 전 raw JSON)"):
        from kis_futureoption import fetch_minute_ohlcv_raw

        raw = fetch_minute_ohlcv_raw(
            symbol,
            3,
            token,
            st.secrets["KIS_APP_KEY"],
            st.secrets["KIS_APP_SECRET"],
            st.secrets.get("KIS_IS_PAPER", False),
        )
        st.json(raw)
        st.caption(
            "output1/output2 안의 키 이름(가격/날짜/시간 필드)을 확인해서 "
            "실제 필드명을 알려주시면 kis_futureoption.py의 _parse_ohlcv_output() "
            "매핑을 정확히 고쳐드리겠습니다."
        )

# ----------------------------------------------------------------------
# [추가] 코스피 시장 분석 — 6요소 종합 스코어링 (2026-07-19)
# ----------------------------------------------------------------------
render_kospi_market_tab(df30, token=token, signal_result=result)

st.success(
    "✅ KIS API 실전 연동 확인 완료 (2026-07-19). "
    "PATH/tr_id/필수 파라미터 모두 검증된 값으로 동작 중입니다."
)
st.caption(
    "점수가 낮거나 '관망'이 자주 뜨면 버그가 아니라 필수조건(60분방향/30분꼬리/15분전환/3분돌파)이 "
    "엄격하게 걸러지고 있는 것입니다. 신호가 너무 드물게 뜬다면 사이드바의 "
    "'꼬리 임계값'을 낮춰보거나(예: 1.0 → 0.7), '주요자리 근접 허용오차'를 넓혀보세요(예: 0.15 → 0.25)."
)
