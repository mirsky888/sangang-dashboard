# -*- coding: utf-8 -*-
"""
kospi_market_score.py — 코스피 시장 분석 6요소 스코어링
=============================================================

1. 미국시장 확인 (가장 중요)         → ❌ 미구현 (해외지수 API 모듈 없음)
2. 삼전·하닉 방향                    → ❌ 미구현 (국내주식 현재가 API 모듈 없음)
3. 외국인 선물 + 현물                → ❌ 미구현 (투자자매매동향 API 모듈 없음)
4. 연기금·사모펀드 (기관계로 근사)    → ❌ 미구현 (투자자매매동향 API 모듈 없음)
5. 프로그램매매                      → ❌ 미구현 (프로그램매매 동향 API 모듈 없음)
6. 가격(차트)                        → ✅ 구현 완료 — sangang_channel.py 재사용
>>> 최종판단기준점수 >>> 강세 / 약강세 / 약세 / 폭락장

------------------------------------------------------------------
왜 1~5번이 아직 안 되는가
------------------------------------------------------------------
현재 업로드해주신 kis_futureoption.py는 "국내선물옵션 분봉/일봉 시세"만
다룹니다 (DEFAULT_MINUTE_TR_ID = FHKIF03020200 등). 아래 4종류의
API는 이 파일에 없는 별도 엔드포인트라 새 모듈이 필요합니다.

  - 해외지수/해외선물 시세  (1번 미국시장)   → kis_overseas.py (가칭)
  - 국내주식 현재가         (2번 삼전·하닉)  → kis_domestic_stock.py (가칭)
  - 투자자매매동향(현물/선물) (3,4번)        → kis_investor_trend.py (가칭)
  - 프로그램매매 동향        (5번)           → kis_program_trading.py (가칭)

kis_futureoption.py를 만드실 때처럼, 정확한 tr_id/path를 KIS 공식 문서에서
사용자가 직접 확인해주시면 그 값을 그대로 받아 동일한 패턴
(rate_limited_retry, _parse_ohlcv_output 스타일)으로 채워드리겠습니다.
지금은 자리표시자 상태이며, 호출 시 NotImplementedError를 명시적으로
발생시켜 "구현 안 됨"과 "API 응답 0"을 혼동하지 않도록 했습니다.

------------------------------------------------------------------
지금 당장 되는 것: 6번 가격(차트)
------------------------------------------------------------------
sangang_channel.compute_structural_channel() + ma_alignment_state()를
그대로 재사용해서, 채널 내 현재가 위치(0~100%)와 이평 배열 상태로
점수를 산출합니다. df만 넘기면 바로 동작합니다.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List

import pandas as pd

from sangang_channel import compute_structural_channel, ma_alignment_state


# ---------------------------------------------------------------------------
# 유틸: 구간화 점수 변환
# ---------------------------------------------------------------------------
def _bucket_score(value: float, thresholds: list[tuple[float, int]], default: int) -> int:
    """thresholds는 (경계값, 그 경계 이상일 때 점수) 오름차순 리스트."""
    score = -2 if value < thresholds[0][0] else default
    for boundary, s in thresholds:
        if value >= boundary:
            score = s
    return score


# ---------------------------------------------------------------------------
# 1. 미국시장 확인 — 미구현 (해외지수 API 모듈 필요)
# ---------------------------------------------------------------------------
def get_us_market_score(nasdaq_futures_change_pct: Optional[float] = None) -> float:
    """
    nasdaq_futures_change_pct를 직접 넘겨주면 우선 사용 (다른 곳에서 이미
    받아온 값이 있을 경우). 없으면 미구현 예외를 발생시킵니다.
    """
    if nasdaq_futures_change_pct is None:
        raise NotImplementedError(
            "해외지수(나스닥 선물) 시세 API 모듈이 아직 없습니다. "
            "kis_overseas.py 를 만들거나 nasdaq_futures_change_pct를 직접 전달하세요."
        )
    return _bucket_score(
        nasdaq_futures_change_pct,
        thresholds=[(-2.0, -2), (-1.0, -1), (1.0, 1), (2.0, 2)],
        default=0,
    )


# ---------------------------------------------------------------------------
# 2. 삼전·하닉 방향 — 미구현 (국내주식 현재가 API 모듈 필요)
# ---------------------------------------------------------------------------
def get_semis_direction_score(
    samsung_change_pct: Optional[float] = None,
    skhynix_change_pct: Optional[float] = None,
) -> float:
    if samsung_change_pct is None or skhynix_change_pct is None:
        raise NotImplementedError(
            "국내주식(005930, 000660) 현재가 API 모듈이 아직 없습니다. "
            "kis_domestic_stock.py 를 만들거나 두 종목 등락률을 직접 전달하세요."
        )
    avg_change = (samsung_change_pct + skhynix_change_pct) / 2
    return _bucket_score(
        avg_change, thresholds=[(-2.0, -2), (-1.0, -1), (1.0, 1), (2.0, 2)], default=0
    )


# ---------------------------------------------------------------------------
# 3. 외국인 선물 + 현물 — 미구현 (투자자매매동향 API 모듈 필요)
# ---------------------------------------------------------------------------
def get_foreign_flow_score(
    foreign_futures_net: Optional[float] = None,
    foreign_spot_net: Optional[float] = None,
) -> float:
    if foreign_futures_net is None or foreign_spot_net is None:
        raise NotImplementedError(
            "투자자매매동향(외국인 선물/현물 순매수) API 모듈이 아직 없습니다. "
            "kis_investor_trend.py 를 만들거나 두 값을 직접 전달하세요."
        )
    futures_score = _bucket_score(
        foreign_futures_net, thresholds=[(-3000, -2), (-1000, -1), (1000, 1), (3000, 2)], default=0
    )
    spot_score = _bucket_score(
        foreign_spot_net, thresholds=[(-3000, -2), (-1000, -1), (1000, 1), (3000, 2)], default=0
    )
    return (futures_score + spot_score) / 2


# ---------------------------------------------------------------------------
# 4. 연기금·사모펀드 (기관계로 근사) — 미구현 (투자자매매동향 API 모듈 필요)
# ---------------------------------------------------------------------------
def get_pension_fund_score(institution_net: Optional[float] = None) -> float:
    """
    실시간 API에서 연기금/사모펀드 세부 구분이 어려워 '기관계 순매수' 전체로 근사.
    """
    if institution_net is None:
        raise NotImplementedError(
            "투자자매매동향(기관계 순매수) API 모듈이 아직 없습니다. "
            "kis_investor_trend.py 를 만들거나 institution_net을 직접 전달하세요."
        )
    return _bucket_score(
        institution_net, thresholds=[(-2000, -2), (-500, -1), (500, 1), (2000, 2)], default=0
    )


# ---------------------------------------------------------------------------
# 5. 프로그램매매 — 미구현 (프로그램매매 동향 API 모듈 필요)
# ---------------------------------------------------------------------------
def get_program_trading_score(program_net: Optional[float] = None) -> float:
    if program_net is None:
        raise NotImplementedError(
            "프로그램매매 동향 API 모듈이 아직 없습니다. "
            "kis_program_trading.py 를 만들거나 program_net을 직접 전달하세요."
        )
    return _bucket_score(
        program_net, thresholds=[(-2000, -2), (-500, -1), (500, 1), (2000, 2)], default=0
    )


# ---------------------------------------------------------------------------
# 6. 가격(차트) — ✅ 구현 완료 (sangang_channel.py 재사용)
# ---------------------------------------------------------------------------
def get_price_chart_score(df: pd.DataFrame) -> float:
    """
    df: 채널 판단에 쓸 분봉 DataFrame (예: 30분봉 또는 15분봉).
    sangang_channel의 구조적 고정 채널 + 이평 배열 상태를 그대로 재사용.
    """
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
    scores: dict = field(default_factory=dict)          # 계산된 항목만 담김
    missing_items: List[str] = field(default_factory=list)  # 미구현이라 빠진 항목
    final_score: Optional[float] = None
    verdict: str = "데이터 부족"


def _classify(score: float) -> str:
    """최종 점수(-2~+2 범위) -> 강세/약강세/약세/폭락장.
    임계값은 초기값이며, 실제 데이터(예: 7/13 폭락장) 축적 후 보정 권장."""
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
    nasdaq_futures_change_pct: Optional[float] = None,
    samsung_change_pct: Optional[float] = None,
    skhynix_change_pct: Optional[float] = None,
    foreign_futures_net: Optional[float] = None,
    foreign_spot_net: Optional[float] = None,
    institution_net: Optional[float] = None,
    program_net: Optional[float] = None,
) -> MarketAnalysisResult:
    """
    각 항목에 값이 주어지면 계산하고, 없으면 missing_items에 기록한 뒤
    나머지 항목들의 가중치만으로 재정규화해서 '부분 점수'를 냅니다.
    (즉 지금 당장은 price_chart 하나만 넣어도 동작합니다.)
    """
    scores = {}
    missing = []

    def _try(name, fn, *args):
        try:
            scores[name] = fn(*args)
        except NotImplementedError:
            missing.append(name)

    _try("us_market", get_us_market_score, nasdaq_futures_change_pct)
    _try("semis", get_semis_direction_score, samsung_change_pct, skhynix_change_pct)
    _try("foreign_flow", get_foreign_flow_score, foreign_futures_net, foreign_spot_net)
    _try("pension_fund", get_pension_fund_score, institution_net)
    _try("program_trading", get_program_trading_score, program_net)
    _try("price_chart", get_price_chart_score, price_df)

    if not scores:
        return MarketAnalysisResult(scores={}, missing_items=missing, final_score=None, verdict="데이터 부족")

    active_weight_sum = sum(WEIGHTS[k] for k in scores)
    final_score = sum(scores[k] * WEIGHTS[k] for k in scores) / active_weight_sum
    final_score = round(final_score, 2)

    return MarketAnalysisResult(
        scores=scores,
        missing_items=missing,
        final_score=final_score,
        verdict=_classify(final_score),
    )


# ---------------------------------------------------------------------------
# SangangEngine(진입 신호)과의 연동 헬퍼
# ---------------------------------------------------------------------------
def filter_signal_by_market(signal_direction: Optional[str], market_result: MarketAnalysisResult) -> dict:
    """
    sangang_signal_engine.SangangEngine.evaluate()의 결과(direction: 'CALL'|'PUT'|None)를
    이 모듈의 거시 판단(강세/약강세/약세/폭락장)과 대조해서 '충돌 여부'를 알려주는
    보조 함수입니다. 진입/보류를 자동으로 강제하지는 않고, 참고용 경고만 반환합니다.

    - CALL 신호인데 시장이 '약세'/'폭락장' → 경고
    - PUT  신호인데 시장이 '강세'/'약강세' → 경고
    - missing_items가 있으면(거시 데이터 일부 없음) 신뢰도를 낮춰서 표시
    """
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
