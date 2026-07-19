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

__version__ = "2.9.3-2026-07-19-first-touch-emphasis"

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


def count_prior_touches(
    df: pd.DataFrame,
    level: float,
    tolerance_pct: float = 0.15,
    lookback_bars: int = 500,
    exclude_last_n: int = 1,
) -> int:
    """
    현재 시점 이전에 이 레벨(지지/저항) 근처에 몇 번이나 '방문'했는지 계산합니다.
    '첫 번째 지지·저항이 80%' 원칙을 위한 터치 횟수 카운터.

    연속된 봉이 계속 레벨 근처에 머무는 것은 '한 번의 방문'으로 묶어서 셉니다
    (그렇지 않으면 박스권에서 터치 횟수가 봉 개수만큼 폭증하므로).

    exclude_last_n: 가장 최근 N개 봉은 '지금 막 도달한 현재의 터치'이므로
    터치 횟수 계산에서 제외합니다 (과거 터치만 셈).
    """
    if df.empty or level == 0:
        return 0

    recent = df.tail(lookback_bars)
    if exclude_last_n > 0 and len(recent) > exclude_last_n:
        recent = recent.iloc[:-exclude_last_n]

    near_mask = (
        (abs(recent["high"] - level) / abs(level) * 100 <= tolerance_pct)
        | (abs(recent["low"] - level) / abs(level) * 100 <= tolerance_pct)
    )

    # 연속된 True 구간을 하나의 '방문'으로 묶어서 카운트
    visits = 0
    prev = False
    for v in near_mask:
        if v and not prev:
            visits += 1
        prev = v

    return visits


def touch_confidence_label(touch_count: int) -> str:
    """
    터치 횟수를 산강의 '첫 번째가 80%' 원칙에 따른 신뢰도 라벨로 변환.
    실측 승률 데이터가 아니라 원칙을 정성적으로 반영한 근사치입니다.
    """
    if touch_count == 0:
        return "1차 터치 (최초 도달, 약 80% 신뢰도)"
    elif touch_count == 1:
        return "2차 터치 (신뢰도 보통)"
    elif touch_count == 2:
        return "3차 터치 (신뢰도 하락)"
    else:
        return f"{touch_count + 1}차 터치 이상 (반복 시도 — 저항 약화 가능성, 돌파 시나리오도 함께 고려)"


def compute_key_levels_with_confluence(
    df60: pd.DataFrame,
    df30: pd.DataFrame,
    df15: Optional[pd.DataFrame] = None,
    ma_periods: List[int] = (15, 30, 60, 90, 120),
    confluence_tolerance_pct: float = 0.05,
) -> tuple:
    """
    compute_key_levels()와 같은 로직이되, 각 최종 레벨이 몇 개의 원본 지표
    (채널 상/하단/중심, 각 MA, 통곡의 벽)에서 비롯됐는지(= 중첩 개수)도 함께 반환합니다.

    반환: (levels: List[float], confluence: Dict[float, int])
        confluence[level] = 그 레벨 근방에 겹친 원본 지표 개수 (2 이상이면 '중첩' 레벨)
    """
    channel = compute_structural_channel(df30, anchor="session")
    raw_levels = [channel.high, channel.low, channel.center, channel.q25, channel.q75]

    ma60_levels = compute_ma_levels(df60, periods=ma_periods)
    raw_levels.extend(ma60_levels.values())

    if df15 is not None and not df15.empty:
        ma15_levels = compute_ma_levels(df15, periods=ma_periods)
        raw_levels.extend(ma15_levels.values())

    tonggok = compute_tonggok_byeok(df60)
    if tonggok is not None:
        raw_levels.append(tonggok)

    raw_levels = sorted(
        round(lv, 2) for lv in raw_levels if lv is not None and not np.isnan(lv)
    )

    # 근접한 원본 레벨들을 하나의 최종 레벨로 병합하면서, 몇 개가 뭉쳤는지(중첩 개수) 기록
    merged: List[float] = []
    confluence: dict = {}
    for lv in raw_levels:
        if merged and abs(lv - merged[-1]) / max(abs(merged[-1]), 1e-9) * 100 <= confluence_tolerance_pct:
            confluence[merged[-1]] += 1
        else:
            merged.append(lv)
            confluence[lv] = 1

    return merged, confluence


def ma_alignment_state(
    df: pd.DataFrame,
    periods: List[int] = (5, 10, 20, 60, 120),
    slope_lookback: int = 5,
) -> str:
    """
    이평선 배열 상태를 판정합니다 (예시 화면처럼 5/10/20/60/120 이평 기준).
    - '역배열': 짧은 이평이 긴 이평보다 아래(하락 정렬)이고, 각 이평이 하락 중
      → 위로 반등해도 각 이평선에서 차례로 저항 받는 하락장 패턴
    - '정배열': 짧은 이평이 긴 이평보다 위(상승 정렬)이고, 각 이평이 상승 중
      → 아래로 눌려도 각 이평선에서 차례로 지지 받는 상승장 패턴
    - '혼조': 위 두 조건에 해당하지 않음
    """
    if len(df) < max(periods) + slope_lookback:
        return "혼조"

    mas = {}
    slopes = {}
    for p in periods:
        series = df["close"].rolling(p).mean()
        mas[p] = series.iloc[-1]
        slopes[p] = series.iloc[-1] - series.iloc[-1 - slope_lookback]

    sorted_periods = sorted(periods)
    values = [mas[p] for p in sorted_periods]

    is_descending_stack = all(values[i] >= values[i + 1] for i in range(len(values) - 1))
    is_ascending_stack = all(values[i] <= values[i + 1] for i in range(len(values) - 1))
    all_falling = all(slopes[p] < 0 for p in periods)
    all_rising = all(slopes[p] > 0 for p in periods)

    if is_descending_stack and all_falling:
        return "역배열"
    if is_ascending_stack and all_rising:
        return "정배열"
    return "혼조"


def check_channel_extreme_emphasis(
    df: pd.DataFrame,
    channel: "ChannelInfo",
    direction: str,
    ma_periods: List[int] = (5, 10, 20, 60, 120),
    touch_count: Optional[int] = None,
) -> Optional[str]:
    """
    채널 극단(상단/하단) + 이평 배열/60·120선 지지저항이 맞아떨어지는 '강조' 자리를 판정합니다.

    touch_count: 60/120선(또는 매칭된 주요자리) 터치 횟수. 0(첫 터치)일 때만
    60·120 지지/저항 강조를 발동합니다 — "처음 터치할 때 지지면 매수강조,
    저항이면 매도강조. 시간이 지나 반복 터치되면 그 신뢰도는 떨어진다"는 원칙 반영.
    touch_count가 None이면(터치 정보 없이 호출되면) 이 조건 없이 판정합니다.

    PUT + 채널상단 근접 + 역배열(하락 저항 정렬) → "채널상단 매도강조"
    CALL + 채널하단 근접 + 정배열 또는 지지 확인 → "채널하단 매수강조"
    해당 없으면 None.
    """
    if df.empty:
        return None

    last_close = float(df["close"].iloc[-1])
    alignment = ma_alignment_state(df, periods=ma_periods)

    near_top = last_close >= channel.q75
    near_bottom = last_close <= channel.q25

    if direction == "PUT" and near_top and alignment == "역배열":
        return "🔻 채널상단 매도강조 — 이평 역배열(하락 저항 정렬) 확인됨"
    if direction == "CALL" and near_bottom and alignment == "정배열":
        return "🔺 채널하단 매수강조 — 이평 정배열(상승 지지 정렬) 확인됨"

    # 60·120 장기이평 지지/저항 — '처음 터치'일 때만 강조 (touch_count == 0)
    is_first_touch = touch_count is None or touch_count == 0
    if len(df) >= max(ma_periods) and is_first_touch:
        ma60 = df["close"].rolling(60).mean().iloc[-1] if len(df) >= 60 else None
        ma120 = df["close"].rolling(120).mean().iloc[-1] if len(df) >= 120 else None

        if direction == "CALL" and near_bottom and ma60 is not None:
            if abs(last_close - ma60) / ma60 * 100 <= 0.15 or (ma120 and abs(last_close - ma120) / ma120 * 100 <= 0.15):
                return "🔺 채널하단 매수강조 — 60·120 장기이평 첫 터치 지지 확인됨"

        if direction == "PUT" and near_top and ma60 is not None:
            if abs(last_close - ma60) / ma60 * 100 <= 0.15 or (ma120 and abs(last_close - ma120) / ma120 * 100 <= 0.15):
                return "🔻 채널상단 매도강조 — 60·120 장기이평 첫 터치 저항 확인됨"

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
