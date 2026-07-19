# -*- coding: utf-8 -*-
"""
kospi_market_score.py — 코스피 시장 분석 6요소 스코어링
=============================================================

1. 미국시장 확인 (가장 중요)         → ✅ 구현 완료 (나스닥100 지수, HHDFS00000300 — 선물은 아직 미구현)
2. 삼전·하닉 방향                    → ✅ 구현 완료 (inquire_price, FHKST01010100)
3. 외국인 선물 + 현물                → ⚠️ 현물만 구현 (선물 쪽은 국내선물옵션 카테고리 별도 모듈 필요)
4. 연기금·사모펀드 (기관계로 근사)    → ✅ 구현 완료 (시장별 투자자매매동향, FHPTJ04030000)
5. 프로그램매매                      → ✅ 구현 완료 (comp_program_trade_today, FHPPG04600101)
6. 가격(차트)                        → ✅ 구현 완료 — sangang_channel.py 재사용
>>> 최종판단기준점수 >>> 강세 / 약강세 / 약세 / 폭락장

------------------------------------------------------------------
이번에 새로 연결한 부분 (출처: 공식 domestic_stock_functions.py)
------------------------------------------------------------------
아래 tr_id/path는 한국투자증권 공식 GitHub 샘플(domestic_stock_functions.py)에서
그대로 추출한 값입니다. 다만 그 파일은 `ka._url_fetch()`라는 별도 인증 스타일을
쓰고 있어서, 기존 kis_futureoption.py의 `build_headers()` 스타일로 다시 감쌌습니다.

⚠️ 응답 필드명(등락률/순매수량 등 정확한 키 이름)은 docstring에 명시되어 있지
않아 일반적으로 쓰이는 필드명을 넣어뒀습니다. 실제 응답 JSON을 한 번 찍어보고
`_TODO_FIELD_*` 표시된 부분만 확인/수정하시면 됩니다.

------------------------------------------------------------------
여전히 미구현인 부분
------------------------------------------------------------------
  - 1번(미국시장/해외지수): domestic_stock_functions.py는 국내주식 전용이라
    여기 없습니다. 해외주식(overseas_stock) 카테고리의 별도 함수 파일이 필요합니다.
  - 3번의 '선물' 쪽(외국인 선물 순매수): 국내선물옵션(domestic_futureoption)
    카테고리의 투자자매매동향 함수가 필요합니다 (kis_futureoption.py에는
    시세 조회만 있고 투자자매매동향은 없음).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List

import pandas as pd
import requests

from sangang_channel import compute_structural_channel, ma_alignment_state
from kis_auth import KisToken, build_headers, REAL_DOMAIN, PAPER_DOMAIN
from kis_futureoption import rate_limited_retry, RateLimitError


# ---------------------------------------------------------------------------
# 공통 GET 호출 헬퍼 (kis_futureoption.py의 _request_chart와 동일 패턴)
# ---------------------------------------------------------------------------
@rate_limited_retry()
def _request_domestic(
    path: str,
    tr_id: str,
    token: KisToken,
    app_key: str,
    app_secret: str,
    params: dict,
    is_paper: bool = False,
) -> dict:
    domain = PAPER_DOMAIN if is_paper else REAL_DOMAIN
    url = f"{domain}{path}"
    headers = build_headers(token, app_key, app_secret, tr_id)

    res = requests.get(url, headers=headers, params=params, timeout=10)

    if res.status_code == 429 or "EGW" in res.text[:200]:
        raise RateLimitError(f"레이트리밋 감지: {res.status_code} {res.text[:200]}")
    if res.status_code != 200:
        raise RuntimeError(f"KIS API 오류 {res.status_code}: {res.text[:300]}")

    data = res.json()
    if data.get("rt_cd") not in (None, "0"):
        raise RuntimeError(f"KIS API 응답 오류 (rt_cd={data.get('rt_cd')}): {data.get('msg1')}")

    return data


# ---------------------------------------------------------------------------
# 유틸: 구간화 점수 변환
# ---------------------------------------------------------------------------
def _bucket_score(value: float, thresholds: list[tuple[float, int]], default: int) -> int:
    score = -2 if value < thresholds[0][0] else default
    for boundary, s in thresholds:
        if value >= boundary:
            score = s
    return score


# ---------------------------------------------------------------------------
# 1. 미국시장 확인 — ✅ 구현 완료 (나스닥100/다우/S&P500 지수로 대체)
#    ✅ tr_id/path 확정: 사용자의 overseas_stock_functions.py에서 직접 확인됨
#    (함수명: price(), v1_해외주식-009 해외주식 현재체결가)
#    tr_id = "HHDFS00000300"
#    path  = "/uapi/overseas-price/v1/quotations/price"
#    params: AUTH="", EXCD="NAS", SYMB="NDX"(나스닥100) / ".DJI"(다우) / "SPX"(S&P500)
#
#    참고: 더 공식적인 '지수 전용' API도 확인됨 —
#    inquire_daily_chartprice() (tr_id FHKST03030100, fid_cond_mrkt_div_code="N")
#    다우30/나스닥100/S&P500 전용이며 공식 예시가 fid_input_iscd=".DJI"로 명시됨.
#    지금 쓰는 price() 방식이 부정확하면 이 함수로 교체 권장.
#
#    ⚠️ 이건 '나스닥 선물'이 아니라 '나스닥 지수 현재가'입니다. 해외선물(NQ)은
#    /uapi/overseas-futureoption/ 카테고리로 완전히 별도이며 아직 미구현입니다.
# ---------------------------------------------------------------------------
def _fetch_index_change_pct(
    symbol: str,
    token: KisToken, app_key: str, app_secret: str,
    excd: str = "NAS",
    is_paper: bool = False,
) -> float:
    """
    해외지수(또는 해외주식) 현재가 조회 -> 등락률(%) 반환.
    path: /uapi/overseas-price/v1/quotations/price
    tr_id: HHDFS00000300 (사용자 overseas_stock_functions.py의 price() 함수와 동일하게 확인됨)
    """
    params = {
        "AUTH": "",
        "EXCD": excd,
        "SYMB": symbol,
    }
    data = _request_domestic(
        "/uapi/overseas-price/v1/quotations/price",
        "HHDFS00000300",
        token, app_key, app_secret, params, is_paper,
    )
    output = data.get("output", {})
    # _TODO_FIELD_5: 등락률 필드명 확인 필요. 여러 코드 예제에서 'rate' 사용을 확인했으나
    # overseas_stock_functions.py의 price() docstring에는 응답 필드 상세가 없어 100% 확정은 아님.
    return float(output.get("rate", 0.0))


def get_us_market_score(
    nasdaq_futures_change_pct: Optional[float] = None,
    token: Optional[KisToken] = None,
    app_key: Optional[str] = None,
    app_secret: Optional[str] = None,
    is_paper: bool = False,
    index_symbol: str = "NDX",  # NDX(나스닥100) / .DJI(다우) / SPX(S&P500)
) -> float:
    if nasdaq_futures_change_pct is None:
        if token is None:
            raise NotImplementedError(
                "미국시장 등락률이 없습니다. nasdaq_futures_change_pct를 직접 전달하거나 "
                "token/app_key/app_secret을 넘겨 API를 호출하게 하세요."
            )
        nasdaq_futures_change_pct = _fetch_index_change_pct(
            index_symbol, token, app_key, app_secret, excd="NAS", is_paper=is_paper
        )
    return _bucket_score(
        nasdaq_futures_change_pct,
        thresholds=[(-2.0, -2), (-1.0, -1), (1.0, 1), (2.0, 2)],
        default=0,
    )


# ---------------------------------------------------------------------------
# 2. 삼전·하닉 방향 — ✅ 구현 완료
#    출처: inquire_price() / tr_id FHKST01010100
# ---------------------------------------------------------------------------
def _fetch_stock_change_pct(
    symbol: str, token: KisToken, app_key: str, app_secret: str, is_paper: bool = False
) -> float:
    """
    주식현재가 시세 조회 -> 전일대비 등락률(%) 반환.
    path: /uapi/domestic-stock/v1/quotations/inquire-price
    tr_id: FHKST01010100
    """
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": symbol,
    }
    data = _request_domestic(
        "/uapi/domestic-stock/v1/quotations/inquire-price",
        "FHKST01010100",
        token, app_key, app_secret, params, is_paper,
    )
    output = data.get("output", {})
    # _TODO_FIELD_1: 등락률 필드명 확인 필요. 통상 'prdy_ctrt'(전일대비율) 사용.
    return float(output.get("prdy_ctrt", 0.0))


def get_semis_direction_score(
    samsung_change_pct: Optional[float] = None,
    skhynix_change_pct: Optional[float] = None,
    token: Optional[KisToken] = None,
    app_key: Optional[str] = None,
    app_secret: Optional[str] = None,
    is_paper: bool = False,
) -> float:
    """
    직접 등락률(samsung_change_pct/skhynix_change_pct)을 넘기면 그 값을 우선 사용.
    안 넘기고 token/app_key/app_secret을 넘기면 API를 직접 호출해서 가져옴.
    """
    if samsung_change_pct is None or skhynix_change_pct is None:
        if token is None:
            raise NotImplementedError(
                "삼전·하닉 등락률이 없습니다. samsung_change_pct/skhynix_change_pct를 직접 "
                "전달하거나, token/app_key/app_secret을 넘겨 API를 호출하게 하세요."
            )
        samsung_change_pct = _fetch_stock_change_pct("005930", token, app_key, app_secret, is_paper)
        skhynix_change_pct = _fetch_stock_change_pct("000660", token, app_key, app_secret, is_paper)

    avg_change = (samsung_change_pct + skhynix_change_pct) / 2
    return _bucket_score(
        avg_change, thresholds=[(-2.0, -2), (-1.0, -1), (1.0, 1), (2.0, 2)], default=0
    )


# ---------------------------------------------------------------------------
# 3. 외국인 선물 + 현물 — ⚠️ 현물만 구현
#    출처: inquire_investor_time_by_market() / tr_id FHPTJ04030000
# ---------------------------------------------------------------------------
def _fetch_market_investor_trend(
    market_code: str,  # 예: "999"(전체) — 정확한 코드는 API 문서에서 재확인 필요
    sector_code: str,  # 예: "S001" — 업종구분, 정확한 코드는 API 문서에서 재확인 필요
    token: KisToken, app_key: str, app_secret: str, is_paper: bool = False,
) -> dict:
    """
    시장별 투자자매매동향(시세) 조회.
    path: /uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market
    tr_id: FHPTJ04030000
    output에 외국인/기관/개인 순매수 관련 필드가 시간대별로 담겨있을 것으로 추정됨
    (정확한 필드명은 실제 응답 확인 필요).
    """
    params = {
        "FID_INPUT_ISCD": market_code,
        "FID_INPUT_ISCD_2": sector_code,
    }
    data = _request_domestic(
        "/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market",
        "FHPTJ04030000",
        token, app_key, app_secret, params, is_paper,
    )
    output = data.get("output", [])
    return output[0] if isinstance(output, list) and output else (output or {})


def get_foreign_flow_score(
    foreign_futures_net: Optional[float] = None,  # 여전히 미구현 — 국내선물옵션 투자자매매동향 필요
    foreign_spot_net: Optional[float] = None,
    token: Optional[KisToken] = None,
    app_key: Optional[str] = None,
    app_secret: Optional[str] = None,
    is_paper: bool = False,
) -> float:
    if foreign_spot_net is None:
        if token is None:
            raise NotImplementedError(
                "외국인 현물 순매수 데이터가 없습니다. foreign_spot_net을 직접 전달하거나 "
                "token/app_key/app_secret을 넘겨 API를 호출하게 하세요."
            )
        row = _fetch_market_investor_trend("999", "S001", token, app_key, app_secret, is_paper)
        # _TODO_FIELD_2: 외국인 순매수 필드명 확인 필요. 통상 'frgn_ntby_qty' 계열 사용.
        foreign_spot_net = float(row.get("frgn_ntby_qty", 0.0))

    spot_score = _bucket_score(
        foreign_spot_net, thresholds=[(-3000, -2), (-1000, -1), (1000, 1), (3000, 2)], default=0
    )

    if foreign_futures_net is None:
        # 선물 쪽 데이터가 없으면 현물만으로 판단 (신뢰도 낮음, 별도 표시 권장)
        return spot_score

    futures_score = _bucket_score(
        foreign_futures_net, thresholds=[(-3000, -2), (-1000, -1), (1000, 1), (3000, 2)], default=0
    )
    return (futures_score + spot_score) / 2


# ---------------------------------------------------------------------------
# 4. 연기금·사모펀드 (기관계로 근사) — ✅ 구현 완료
#    출처: inquire_investor_time_by_market() / tr_id FHPTJ04030000 (3번과 동일 API, 기관 필드 사용)
# ---------------------------------------------------------------------------
def get_pension_fund_score(
    institution_net: Optional[float] = None,
    token: Optional[KisToken] = None,
    app_key: Optional[str] = None,
    app_secret: Optional[str] = None,
    is_paper: bool = False,
) -> float:
    if institution_net is None:
        if token is None:
            raise NotImplementedError(
                "기관계 순매수 데이터가 없습니다. institution_net을 직접 전달하거나 "
                "token/app_key/app_secret을 넘겨 API를 호출하게 하세요."
            )
        row = _fetch_market_investor_trend("999", "S001", token, app_key, app_secret, is_paper)
        # _TODO_FIELD_3: 기관계 순매수 필드명 확인 필요. 통상 'orgn_ntby_qty' 계열 사용.
        institution_net = float(row.get("orgn_ntby_qty", 0.0))

    return _bucket_score(
        institution_net, thresholds=[(-2000, -2), (-500, -1), (500, 1), (2000, 2)], default=0
    )


# ---------------------------------------------------------------------------
# 5. 프로그램매매 — ✅ 구현 완료
#    출처: comp_program_trade_today() / tr_id FHPPG04600101
# ---------------------------------------------------------------------------
def get_program_trading_score(
    program_net: Optional[float] = None,
    token: Optional[KisToken] = None,
    app_key: Optional[str] = None,
    app_secret: Optional[str] = None,
    is_paper: bool = False,
) -> float:
    if program_net is None:
        if token is None:
            raise NotImplementedError(
                "프로그램매매 순매수 데이터가 없습니다. program_net을 직접 전달하거나 "
                "token/app_key/app_secret을 넘겨 API를 호출하게 하세요."
            )
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_MRKT_CLS_CODE": "K",  # K:코스피, Q:코스닥
            "FID_SCTN_CLS_CODE": "",   # 구간 구분 코드 (빈 값이라도 필드 자체는 필수)
            "FID_INPUT_ISCD": "",      # 입력종목코드 (전체 시장 조회 시 공란)
            "FID_COND_MRKT_DIV_CODE1": "",  # 시장분류코드
            "FID_INPUT_HOUR_1": "",    # 입력시간 (공란 = 최신)
        }
        data = _request_domestic(
            "/uapi/domestic-stock/v1/quotations/comp-program-trade-today",
            "FHPPG04600101",
            token, app_key, app_secret, params, is_paper,
        )
        output = data.get("output", [])
        latest = output[0] if isinstance(output, list) and output else {}
        # _TODO_FIELD_4: 프로그램매매 순매수 필드명 확인 필요. 통상 'whol_ntby_qty' 계열 사용.
        program_net = float(latest.get("whol_ntby_qty", 0.0))

    return _bucket_score(
        program_net, thresholds=[(-2000, -2), (-500, -1), (500, 1), (2000, 2)], default=0
    )


# ---------------------------------------------------------------------------
# 6. 가격(차트) — ✅ 구현 완료 (sangang_channel.py 재사용)
# ---------------------------------------------------------------------------
def get_price_chart_score(df: pd.DataFrame) -> float:
    channel = compute_structural_channel(df, anchor="session")
    last_price = float(df["close"].iloc[-1])
    span = channel.high - channel.low
    position_pct = 50.0 if span == 0 else (last_price - channel.low) / span * 100

    channel_score = _bucket_score(
        position_pct, thresholds=[(20, -2), (40, -1), (60, 1), (80, 2)], default=0
    )
    alignment = ma_alignment_state(df)
    ma_score = {"정배열": 1, "역배열": -1, "혼조": 0}.get(alignment, 0)

    return (channel_score + ma_score) / 2


# ---------------------------------------------------------------------------
# 최종 종합 스코어링 — 미구현 항목은 자동으로 빼고 '부분 점수'로 계산
# ---------------------------------------------------------------------------
WEIGHTS = {
    "us_market": 0.30,
    "semis": 0.15,
    "foreign_flow": 0.20,
    "pension_fund": 0.10,
    "program_trading": 0.10,
    "price_chart": 0.15,
}


@dataclass
class MarketAnalysisResult:
    scores: dict = field(default_factory=dict)
    missing_items: List[str] = field(default_factory=list)
    errors: dict = field(default_factory=dict)  # {항목명: "에러 메시지"} — 미구현이 아니라 호출 실패한 경우
    final_score: Optional[float] = None
    verdict: str = "데이터 부족"


def _classify(score: float) -> str:
    if score >= 1.2:
        return "강세"
    elif score >= 0.3:
        return "약강세"
    elif score >= -1.0:
        return "약세"
    else:
        return "폭락장"


def run_kospi_market_analysis(
    price_df: pd.DataFrame,
    token: Optional[KisToken] = None,
    app_key: Optional[str] = None,
    app_secret: Optional[str] = None,
    is_paper: bool = False,
    quote_token: Optional[KisToken] = None,
    quote_app_key: Optional[str] = None,
    quote_app_secret: Optional[str] = None,
    nasdaq_futures_change_pct: Optional[float] = None,
    samsung_change_pct: Optional[float] = None,
    skhynix_change_pct: Optional[float] = None,
    foreign_futures_net: Optional[float] = None,
    foreign_spot_net: Optional[float] = None,
    institution_net: Optional[float] = None,
    program_net: Optional[float] = None,
) -> MarketAnalysisResult:
    """
    token/app_key/app_secret을 넘기면 3(현물),4,5번 항목은 자동으로 API를 호출해서 채웁니다.

    1번(미국시장)/2번(삼전·하닉)은 모의투자 앱키로는 호출이 막히는 TR이라 별도로
    quote_token/quote_app_key/quote_app_secret(실전투자 전용 앱키)을 받습니다.
    이 값이 없으면 기존 token/app_key/app_secret으로 폴백하되, 모의투자 앱키라면
    여전히 EGW02004(도메인 불일치) 에러가 날 수 있습니다.
    이 실전 전용 키 호출은 항상 실전 도메인(is_paper=False)으로 고정됩니다.

    개별 값(samsung_change_pct 등)을 직접 넘기면 그 값이 우선합니다.
    """
    scores = {}
    missing = []
    errors = {}

    # 1,2번(시세 전용)에 쓸 자격증명: 실전 전용 키가 있으면 그걸 쓰고, 없으면 기존 키로 폴백
    q_token = quote_token if quote_token is not None else token
    q_app_key = quote_app_key if quote_app_key is not None else app_key
    q_app_secret = quote_app_secret if quote_app_secret is not None else app_secret

    def _try(name, fn, *args, **kwargs):
        try:
            scores[name] = fn(*args, **kwargs)
        except NotImplementedError:
            missing.append(name)
        except Exception as e:
            # API 호출 자체가 실패한 경우 (tr_id 오류, 계좌 권한, 네트워크 등).
            # 여기서 앱이 죽지 않도록 반드시 잡아서 '에러' 항목으로 기록한다.
            missing.append(name)
            errors[name] = f"{type(e).__name__}: {e}"

    _try(
        "us_market", get_us_market_score,
        nasdaq_futures_change_pct, q_token, q_app_key, q_app_secret, False,  # 시세는 항상 실전 도메인
    )
    _try(
        "semis", get_semis_direction_score,
        samsung_change_pct, skhynix_change_pct, q_token, q_app_key, q_app_secret, False,
    )
    _try(
        "foreign_flow", get_foreign_flow_score,
        foreign_futures_net, foreign_spot_net, token, app_key, app_secret, is_paper,
    )
    _try(
        "pension_fund", get_pension_fund_score,
        institution_net, token, app_key, app_secret, is_paper,
    )
    _try(
        "program_trading", get_program_trading_score,
        program_net, token, app_key, app_secret, is_paper,
    )
    _try("price_chart", get_price_chart_score, price_df)

    if not scores:
        return MarketAnalysisResult(
            scores={}, missing_items=missing, errors=errors, final_score=None, verdict="데이터 부족"
        )

    active_weight_sum = sum(WEIGHTS[k] for k in scores)
    final_score = sum(scores[k] * WEIGHTS[k] for k in scores) / active_weight_sum
    final_score = round(final_score, 2)

    return MarketAnalysisResult(
        scores=scores,
        missing_items=missing,
        errors=errors,
        final_score=final_score,
        verdict=_classify(final_score),
    )


# ---------------------------------------------------------------------------
# SangangEngine(진입 신호)과의 연동 헬퍼
# ---------------------------------------------------------------------------
def filter_signal_by_market(signal_direction: Optional[str], market_result: MarketAnalysisResult) -> dict:
    if signal_direction is None:
        return {"aligned": None, "warning": None}

    bullish_verdicts = {"강세", "약강세"}
    bearish_verdicts = {"약세", "폭락장"}

    conflict = (
        (signal_direction == "CALL" and market_result.verdict in bearish_verdicts)
        or (signal_direction == "PUT" and market_result.verdict in bullish_verdicts)
    )

    warning = None
    if conflict:
        base = (
            f"⚠️ 3분봉 진입신호는 {signal_direction}이지만, "
            f"거시 시장판단은 '{market_result.verdict}'로 방향이 엇갈립니다."
        )
        if market_result.missing_items:
            base += f" 미구현 항목({', '.join(market_result.missing_items)})이 있어 거시 판단 신뢰도가 낮을 수 있으니 참고만 하세요."
        warning = base

    return {"aligned": not conflict, "warning": warning}
