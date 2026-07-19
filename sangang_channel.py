"""
sangang_channel.py — 산강채널 계산 모듈
=========================================

산강_매매법_집대성.md / 통합매매법_v2-9.md 원칙을 코드화:
    - 채널은 '구조적 고정 고점/저점'에 앵커링 (롤링 인트라데이 고저 아님)
    - 0/25/50/75/100% 4분할 프레임
    - 60/90/120분봉 등 장기 이평을 지지/저항으로 병행 체크
    - 통곡의 벽: 60·120 이평 수렴 구간을 강한 저항대로 취급
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ChannelInfo:
    high: float
    low: float
    center: float
    q25: float
    q75: float
    anchor_start: pd.Timestamp
    anchor_end: pd.Timestamp


def compute_structural_channel(
    df: pd.DataFrame,
    anchor: str = "session",
) -> ChannelInfo:
    """
    구조적 고정 고점/저점 기반 채널 계산.

    anchor='session' : 데이터프레임에 포함된 가장 최근 거래일(당일) 전체 구간의 고정 고저
    anchor='all'      : 전달받은 df 전체 구간의 고정 고저 (예: 특정 기준일 이후 전체)

    ⚠️ 롤링(rolling) 고저가 아니라 '고정된' 구간의 고저를 앵커로 삼는 것이
    v2-8/v2-9의 핵심 원칙입니다 — 장중에 새로 갱신되는 고저에 채널이 매틱마다
    흔들리지 않도록, 세션(또는 지정 구간) 시작 시점의 고정값을 그대로 유지합니다.
    """
    if df.empty:
        raise ValueError("빈 DataFrame으로는 채널을 계산할 수 없습니다.")

    if anchor == "session":
        last_date = df.index[-1].date()
        session_df = df[df.index.date == last_date]
        if session_df.empty:
            session_df = df
    else:
        session_df = df

    high = float(session_df["high"].max())
    low = float(session_df["low"].min())
    center = (high + low) / 2
    q25 = low + (high - low) * 0.25
    q75 = low + (high - low) * 0.75

    return ChannelInfo(
        high=high,
        low=low,
        center=center,
        q25=q25,
        q75=q75,
        anchor_start=session_df.index[0],
        anchor_end=session_df.index[-1],
    )


def compute_ma_levels(df: pd.DataFrame, periods: List[int] = (15, 30, 60, 90, 120)) -> dict:
    """
    분봉 DataFrame에 대해 지정된 기간의 단순이동평균(SMA) 최신값을 지지/저항 후보로 반환.
    periods는 '해당 분봉 기준 봉 개수'입니다 (예: 3분봉 df에 60을 주면 3분×60=180분 이평).
    """
    levels = {}
    for p in periods:
        if len(df) >= p:
            levels[f"MA{p}"] = float(df["close"].rolling(p).mean().iloc[-1])
    return levels


def compute_tonggok_byeok(
    df60: pd.DataFrame,
    ma_short: int = 60,
    ma_long: int = 120,
    convergence_threshold_pct: float = 0.3,
) -> Optional[float]:
    """
    '통곡의 벽' — 60·120 이평 수렴 구간을 강한 저항/지지대로 판정.
    두 이평의 괴리율이 convergence_threshold_pct(%) 이내로 수렴하면
    그 근방 가격대를 강력한 구조적 레벨로 반환합니다 (수렴 아니면 None).
    """
    if len(df60) < ma_long:
        return None

    ma_s = df60["close"].rolling(ma_short).mean().iloc[-1]
    ma_l = df60["close"].rolling(ma_long).mean().iloc[-1]

    if ma_s is None or ma_l is None or np.isnan(ma_s) or np.isnan(ma_l):
        return None

    gap_pct = abs(ma_s - ma_l) / ma_l * 100
    if gap_pct <= convergence_threshold_pct:
        return float((ma_s + ma_l) / 2)
    return None


def compute_key_levels(
    df60: pd.DataFrame,
    df30: pd.DataFrame,
    df15: Optional[pd.DataFrame] = None,
    ma_periods: List[int] = (15, 30, 60, 90, 120),
) -> List[float]:
    """
    산강채널 + 다중이평 + 통곡의 벽을 종합해 SangangEngine에 넘길
    key_levels(주요자리) 리스트를 만드는 최종 함수.

    app.py의 compute_key_levels_placeholder()를 이 함수로 교체해서 사용하세요.
    """
    channel = compute_structural_channel(df30, anchor="session")
    levels = [channel.high, channel.low, channel.center, channel.q25, channel.q75]

    ma60_levels = compute_ma_levels(df60, periods=ma_periods)
    levels.extend(ma60_levels.values())

    if df15 is not None and not df15.empty:
        ma15_levels = compute_ma_levels(df15, periods=ma_periods)
        levels.extend(ma15_levels.values())

    tonggok = compute_tonggok_byeok(df60)
    if tonggok is not None:
        levels.append(tonggok)

    # 중복 제거 (소수점 둘째자리 기준 근접값 병합)
    levels = sorted(set(round(lv, 2) for lv in levels if lv is not None and not np.isnan(lv)))

    merged = []
    for lv in levels:
        if not merged or abs(lv - merged[-1]) / max(abs(merged[-1]), 1e-9) * 100 > 0.05:
            merged.append(lv)

    return merged
