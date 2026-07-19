"""
산강 매매법 v2-9 신호 엔진 (sangang_signal_engine.py)
=====================================================

첨부하신 "산강식 캐스케이드 확인 + 신뢰도 점수화" 구조를 그대로 코드화한 모듈입니다.

계층 구조 (60분 → 30분 → 15분 → 3분):
    60분봉  → 방향(추세) 확인          (필수, 미충족 시 즉시 관망)
    30분봉  → 주요자리(지지/저항) 꼬리   (필수)
    15분봉  → 전환 확인(양봉/음봉 전환)  (필수)
    3분봉   → 실행 + 가산점 스코어링     (진입 트리거 + 점수)

점수 배점 (100점 만점):
    60분봉 방향 일치        20점  [필수]
    중심가(채널 중심) 위/아래  10점
    30분봉 꼬리 확인         20점  [필수]
    15분봉 전환 확인         20점  [필수]
    3분봉 고가/저가 돌파      10점
    3분봉 20이평 돌파/이탈    10점
    3분봉 거래량 증가        10점

등급:
    90~100  S급
    80~89   A급
    70~79   B급
    70 미만  관망 (필수 4항목 중 하나라도 미충족이면 무조건 관망)

사용 데이터: pandas DataFrame, DatetimeIndex, 컬럼 ['open','high','low','close','volume']
KIS API/Streamlit 대시보드에서 분봉 리샘플링한 데이터를 그대로 넣으면 됩니다.

[v2-9.1 변경사항]
- 꼬리 비율 산출 기준을 "캔들 전체 범위 대비"에서 "몸통(body) 대비"로 변경.
  tail_ratio_threshold=1.0 이 기본값이며, 이는 "꼬리 길이가 몸통 크기 이상"을 의미합니다.
  (예: 몸통 3P, 아래꼬리 4P → 비율 1.33 → 임계값 1.0 이상이므로 충족)
- 주요자리(key_levels) 산출은 아직 자리표시자(placeholder) 상태입니다.
  기존 산강채널/통곡의 벽 계산 코드를 붙여넣어 주시면 compute_key_levels() 자리에
  그대로 이식해 드립니다. 현재는 SangangEngine 생성 시 key_levels 리스트를
  외부에서 직접 주입하는 방식으로 동작합니다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Literal, Optional, List, Dict

Direction = Literal["CALL", "PUT"]


# ----------------------------------------------------------------------
# 결과 데이터 클래스
# ----------------------------------------------------------------------

@dataclass
class SignalResult:
    direction: Optional[Direction]      # 최종 판정 방향 (필수조건 미충족 시 None)
    score: int                          # 0~100
    grade: str                          # S급/A급/B급/관망
    passed_required: bool               # 필수 4조건 모두 충족 여부
    details: Dict[str, bool] = field(default_factory=dict)   # 항목별 충족 여부
    breakdown: Dict[str, int] = field(default_factory=dict)  # 항목별 획득 점수
    touch_count: int = 0                # 해당 주요자리를 몇 번째 터치하는지 (0 = 첫 터치)
    confluence_count: int = 1           # 그 주요자리에 몇 개 지표가 겹쳐있는지 (1 = 단일, 2+ = 중첩)
    reliability_label: str = ""         # "1차 터치 (약 80% 신뢰도)" 등 정성적 설명
    grade_adjusted: bool = False        # 터치/중첩 보정으로 등급이 조정됐는지 여부
    extreme_emphasis: Optional[str] = None  # "채널상단 매도강조" / "채널하단 매수강조" 등

    def __repr__(self):
        d = self.direction or "NONE"
        return f"<SignalResult {d} score={self.score} grade={self.grade} touch={self.touch_count} confluence={self.confluence_count}>"


@dataclass
class ExitSignal:
    should_exit: bool
    reason: Optional[str] = None   # '반대꼬리' | '다음주요자리' | '20이평이탈' | '손절'


# ----------------------------------------------------------------------
# 유틸리티
# ----------------------------------------------------------------------

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _body_size(row: pd.Series) -> float:
    return max(abs(row["close"] - row["open"]), 1e-9)


def _lower_tail_ratio(row: pd.Series) -> float:
    """
    아래꼬리 비율 = (몸통 하단 - 저가) / 몸통 크기   (몸통 대비 기준, v2-9.1 변경)
    도지처럼 몸통이 거의 0이면 비율이 비정상적으로 커질 수 있어 상한(cap)을 둠.
    """
    body_low = min(row["open"], row["close"])
    ratio = (body_low - row["low"]) / _body_size(row)
    return min(ratio, 5.0)  # 도지 등 몸통 극소 캔들 방어용 상한


def _upper_tail_ratio(row: pd.Series) -> float:
    """윗꼬리 비율 = (고가 - 몸통 상단) / 몸통 크기 (몸통 대비 기준, v2-9.1 변경)"""
    body_high = max(row["open"], row["close"])
    ratio = (row["high"] - body_high) / _body_size(row)
    return min(ratio, 5.0)


def near_key_level(price: float, key_levels: List[float], tolerance_pct: float = 0.15) -> Optional[float]:
    """
    price가 key_levels(주요자리: 지지/저항, 채널 상하단, 통곡의 벽 등) 중
    tolerance_pct(%) 이내로 근접해 있으면 그 레벨을 반환, 아니면 None.
    """
    if not key_levels:
        return None
    for lv in key_levels:
        if lv == 0:
            continue
        if abs(price - lv) / abs(lv) * 100 <= tolerance_pct:
            return lv
    return None


# ----------------------------------------------------------------------
# 주요자리(key_levels) 연동 인터페이스 — TODO: 기존 산강채널 코드로 교체
# ----------------------------------------------------------------------

def compute_key_levels_placeholder(df60: pd.DataFrame, df30: pd.DataFrame) -> List[float]:
    """
    ⚠️ 자리표시자(placeholder) 함수입니다.

    기존에 만들어두신 산강채널 / 통곡의 벽(60·120MA 수렴) 계산 코드를
    이 함수 자리에 그대로 옮겨 넣으시면 SangangEngine.set_key_levels()로
    자동 연결됩니다.

    지금은 임시로 "60분봉 구조적 고정 고점/저점"만 반환하는 단순 로직으로
    대체해 두었습니다 (v2-8 앵커링 원칙: 롤링 고저가 아닌 고정 고점/저점 기준).
    실제 채널 상/하단, 중심값, 통곡의 벽 레벨 등을 여기서 계산해서
    리스트로 반환하도록 교체해 주세요.

    기존 코드를 붙여넣어 주시면 이 함수를 그대로 완성해 드리겠습니다.
    """
    structural_high = df60["high"].max()
    structural_low = df60["low"].min()
    return [round(structural_low, 2), round(structural_high, 2)]


# ----------------------------------------------------------------------
# 산강 엔진 본체
# ----------------------------------------------------------------------

class SangangEngine:
    def __init__(
        self,
        key_levels: Optional[List[float]] = None,
        tail_ratio_threshold: float = 1.0,     # 몸통 대비 꼬리 길이 배수 기준 (1.0 = 꼬리가 몸통만큼 이상)
        level_tolerance_pct: float = 0.15,     # 주요자리 근접 허용 오차(%)
        volume_lookback_15m: int = 5,          # 15분봉 거래량 평균 구간
        volume_lookback_3m: int = 20,          # 3분봉 거래량 평균 구간
        ema_period_3m: int = 20,
    ):
        self.key_levels = key_levels or []
        self.confluence_map: dict = {}
        self.tail_ratio_threshold = tail_ratio_threshold
        self.level_tolerance_pct = level_tolerance_pct
        self.volume_lookback_15m = volume_lookback_15m
        self.volume_lookback_3m = volume_lookback_3m
        self.ema_period_3m = ema_period_3m

    def set_key_levels(self, key_levels: List[float]):
        """산강채널/통곡의 벽 등 상위 로직에서 계산한 주요자리를 갱신할 때 사용."""
        self.key_levels = key_levels

    def set_key_levels_with_confluence(self, key_levels: List[float], confluence_map: dict):
        """
        sangang_channel.compute_key_levels_with_confluence()의 결과를 그대로 넣으면
        각 레벨의 '중첩 개수'까지 함께 반영됩니다 (미호출 시 전부 confluence=1로 취급).
        """
        self.key_levels = key_levels
        self.confluence_map = confluence_map or {}

    # ------------------------------------------------------------------
    # 계층 1: 60분봉 방향
    # ------------------------------------------------------------------
    def get_60min_direction(self, df60: pd.DataFrame) -> Direction:
        """
        최근 60분봉 종가가 EMA20(60분 기준) 위/아래인지로 대세 방향 판정.
        """
        closes = df60["close"]
        ema20 = _ema(closes, 20)
        last_close = closes.iloc[-1]
        last_ema = ema20.iloc[-1]
        return "CALL" if last_close >= last_ema else "PUT"

    def check_above_center(self, price: float, channel_center: Optional[float], direction: Direction) -> bool:
        """중심가(채널 중심값) 대비 위/아래 위치 확인. channel_center 미제공 시 False."""
        if channel_center is None:
            return False
        if direction == "CALL":
            return price >= channel_center
        else:
            return price <= channel_center

    # ------------------------------------------------------------------
    # 계층 2: 30분봉 꼬리 (주요자리 확인)
    # ------------------------------------------------------------------
    def check_30min_tail(self, df30: pd.DataFrame, direction: Direction) -> bool:
        """
        직전(또는 최신 확정) 30분봉이 주요자리 근처에서
        방향에 맞는 꼬리(아래꼬리=CALL / 윗꼬리=PUT)를 tail_ratio_threshold 이상 만들었는지 확인.
        """
        matched, _ = self.get_matched_level(df30, direction)
        if matched is None:
            return False

        row = df30.iloc[-1]
        if direction == "CALL":
            return _lower_tail_ratio(row) >= self.tail_ratio_threshold
        else:
            return _upper_tail_ratio(row) >= self.tail_ratio_threshold

    def get_matched_level(self, df30: pd.DataFrame, direction: Direction):
        """
        현재 30분봉이 근접해 있는 key_level과 그 confluence(중첩) 개수를 반환.
        반환: (matched_level: Optional[float], confluence_count: int)
        """
        row = df30.iloc[-1]
        price = row["low"] if direction == "CALL" else row["high"]
        level = near_key_level(price, self.key_levels, self.level_tolerance_pct)
        if level is None:
            return None, 1
        confluence = self.confluence_map.get(level, 1)
        return level, confluence

    # ------------------------------------------------------------------
    # 계층 3: 15분봉 전환
    # ------------------------------------------------------------------
    def check_15min_reversal(self, df15: pd.DataFrame, direction: Direction) -> bool:
        """
        CALL 전환 4조건:
          1) 직전봉 음봉
          2) 현재봉 양봉
          3) 현재봉 종가 > 직전봉 고가
          4) 현재봉 거래량 >= 최근 N봉 평균 거래량
        PUT은 대칭(양봉→음봉, 종가<직전 저가).
        """
        if len(df15) < self.volume_lookback_15m + 1:
            return False

        prev = df15.iloc[-2]
        curr = df15.iloc[-1]
        avg_vol = df15["volume"].iloc[-(self.volume_lookback_15m + 1):-1].mean()

        if direction == "CALL":
            prev_bearish = prev["close"] < prev["open"]
            curr_bullish = curr["close"] > curr["open"]
            break_high = curr["close"] > prev["high"]
            vol_ok = curr["volume"] >= avg_vol
            return prev_bearish and curr_bullish and break_high and vol_ok
        else:
            prev_bullish = prev["close"] > prev["open"]
            curr_bearish = curr["close"] < curr["open"]
            break_low = curr["close"] < prev["low"]
            vol_ok = curr["volume"] >= avg_vol
            return prev_bullish and curr_bearish and break_low and vol_ok

    # ------------------------------------------------------------------
    # 계층 4: 3분봉 실행 + 가산점
    # ------------------------------------------------------------------
    def score_3min(self, df3: pd.DataFrame, direction: Direction) -> Dict[str, bool]:
        """
        3분봉 가산 조건 3가지를 각각 True/False로 반환.
          - 고가/저가 돌파 (직전봉 대비)
          - 20이평 돌파/이탈
          - 거래량 증가 (최근 N봉 평균 대비)
        """
        if len(df3) < max(2, self.volume_lookback_3m + 1):
            return {"break_prior": False, "ema_cross": False, "volume_up": False}

        prev = df3.iloc[-2]
        curr = df3.iloc[-1]
        ema20 = _ema(df3["close"], self.ema_period_3m).iloc[-1]
        avg_vol = df3["volume"].iloc[-(self.volume_lookback_3m + 1):-1].mean()

        if direction == "CALL":
            break_prior = curr["close"] > prev["high"]
            ema_cross = curr["close"] > ema20
        else:
            break_prior = curr["close"] < prev["low"]
            ema_cross = curr["close"] < ema20

        volume_up = curr["volume"] > avg_vol

        return {"break_prior": break_prior, "ema_cross": ema_cross, "volume_up": volume_up}

    # ------------------------------------------------------------------
    # 종합 판정
    # ------------------------------------------------------------------
    def evaluate(
        self,
        df60: pd.DataFrame,
        df30: pd.DataFrame,
        df15: pd.DataFrame,
        df3: pd.DataFrame,
        channel_center: Optional[float] = None,
        direction_override: Optional[Direction] = None,
    ) -> SignalResult:
        """
        4단계 전체를 캐스케이드로 확인하고 100점 만점 점수를 산출합니다.
        direction_override를 지정하면 60분봉 자동판정을 무시하고 해당 방향만 검사합니다
        (예: 이미 60분봉 방향이 확정된 상태에서 재평가할 때).
        """
        from sangang_channel import compute_structural_channel, check_channel_extreme_emphasis

        direction = direction_override or self.get_60min_direction(df60)
        last_price = df3["close"].iloc[-1]

        try:
            channel_info = compute_structural_channel(df3, anchor="session")
            effective_center = channel_center if channel_center is not None else channel_info.center
        except Exception:
            channel_info = None
            effective_center = channel_center

        d60_ok = True  # get_60min_direction 자체가 이미 해당 방향을 반환하므로 항상 충족
        center_ok = self.check_above_center(last_price, effective_center, direction)
        tail_ok = self.check_30min_tail(df30, direction)
        rev_ok = self.check_15min_reversal(df15, direction)
        s3 = self.score_3min(df3, direction)

        details = {
            "60분봉_방향": d60_ok,
            "중심가_위치": center_ok,
            "30분봉_꼬리": tail_ok,
            "15분봉_전환": rev_ok,
            "3분봉_고저돌파": s3["break_prior"],
            "3분봉_20이평": s3["ema_cross"],
            "3분봉_거래량": s3["volume_up"],
        }

        breakdown = {
            "60분봉_방향": 20 if d60_ok else 0,
            "중심가_위치": 10 if center_ok else 0,
            "30분봉_꼬리": 20 if tail_ok else 0,
            "15분봉_전환": 20 if rev_ok else 0,
            "3분봉_고저돌파": 10 if s3["break_prior"] else 0,
            "3분봉_20이평": 10 if s3["ema_cross"] else 0,
            "3분봉_거래량": 10 if s3["volume_up"] else 0,
        }

        score = sum(breakdown.values())

        # 필수조건: 60분봉 방향 / 30분봉 꼬리 / 15분봉 전환 (3개는 원문상 최우선 필수)
        # + 3분봉 계층에서 최소 고가/저가 돌파는 실행 트리거로 필수 취급
        passed_required = d60_ok and tail_ok and rev_ok and s3["break_prior"]

        # ------------------------------------------------------------
        # 터치 횟수 / 중첩 보정 ("첫 번째가 80%" + "중첩되면 신뢰도 상승" 원칙)
        # ------------------------------------------------------------
        from sangang_channel import count_prior_touches, touch_confidence_label

        matched_level, confluence_count = self.get_matched_level(df30, direction)
        touch_count = 0
        reliability_label = ""
        grade_adjusted = False

        if matched_level is not None:
            touch_count = count_prior_touches(
                df30, matched_level, tolerance_pct=self.level_tolerance_pct
            )
            reliability_label = touch_confidence_label(touch_count)

        extreme_emphasis = None
        if channel_info is not None:
            extreme_emphasis = check_channel_extreme_emphasis(df3, channel_info, direction)

        if not passed_required:
            grade = "관망 (필수조건 미충족)"
            final_direction = None
        else:
            if score >= 90:
                grade = "S급"
            elif score >= 80:
                grade = "A급"
            elif score >= 70:
                grade = "B급"
            else:
                grade = "관망"

            # 등급 보정 규칙:
            #   - 3차 터치 이상(반복 시도)인데 중첩(2개 이상 지표 겹침)이 아니면 → 한 단계 강등
            #     ("반복 시도는 저항 약화 가능성" — 단, 중첩이면 그 자체로 신뢰도가 높으므로 봐줌)
            #   - 1차 터치(최초 도달) + 중첩(2개 이상) → 한 단계 승격 (최대 S급)
            tiers = ["관망", "B급", "A급", "S급"]
            if grade in tiers:
                idx = tiers.index(grade)
                if touch_count >= 2 and confluence_count < 2:
                    idx = max(0, idx - 1)
                    grade_adjusted = True
                elif touch_count == 0 and confluence_count >= 2:
                    idx = min(len(tiers) - 1, idx + 1)
                    grade_adjusted = True
                if extreme_emphasis is not None:
                    idx = min(len(tiers) - 1, idx + 1)
                    grade_adjusted = True
                grade = tiers[idx]

            final_direction = direction if grade != "관망" else None

        return SignalResult(
            direction=final_direction,
            score=score,
            grade=grade,
            passed_required=passed_required,
            touch_count=touch_count,
            confluence_count=confluence_count,
            reliability_label=reliability_label,
            grade_adjusted=grade_adjusted,
            extreme_emphasis=extreme_emphasis,
            details=details,
            breakdown=breakdown,
        )

    # ------------------------------------------------------------------
    # 청산 규칙
    # ------------------------------------------------------------------
    def check_exit(
        self,
        position_direction: Direction,
        df3: pd.DataFrame,
        entry_price: float,
        stop_loss_price: Optional[float] = None,
        next_key_level: Optional[float] = None,
    ) -> ExitSignal:
        """
        4가지 청산 규칙을 우선순위대로 확인합니다.
          1) 손절 스탑 도달
          2) 반대 꼬리 발생 (포지션 방향과 반대되는 확정 꼬리)
          3) 다음 주요자리 도달
          4) 3분봉 20이평 이탈 (포지션 방향과 반대로)
        """
        curr = df3.iloc[-1]
        last_price = curr["close"]

        # 1) 손절
        if stop_loss_price is not None:
            if position_direction == "CALL" and last_price <= stop_loss_price:
                return ExitSignal(True, "손절")
            if position_direction == "PUT" and last_price >= stop_loss_price:
                return ExitSignal(True, "손절")

        # 2) 반대 꼬리 (진입 방향이 CALL이면 윗꼬리 과다 발생 시 청산 신호)
        if position_direction == "CALL" and _upper_tail_ratio(curr) >= self.tail_ratio_threshold:
            return ExitSignal(True, "반대꼬리")
        if position_direction == "PUT" and _lower_tail_ratio(curr) >= self.tail_ratio_threshold:
            return ExitSignal(True, "반대꼬리")

        # 3) 다음 주요자리 도달
        if next_key_level is not None:
            if position_direction == "CALL" and last_price >= next_key_level:
                return ExitSignal(True, "다음주요자리")
            if position_direction == "PUT" and last_price <= next_key_level:
                return ExitSignal(True, "다음주요자리")

        # 4) 3분봉 20이평 이탈
        ema20 = _ema(df3["close"], self.ema_period_3m).iloc[-1]
        if position_direction == "CALL" and last_price < ema20:
            return ExitSignal(True, "20이평이탈")
        if position_direction == "PUT" and last_price > ema20:
            return ExitSignal(True, "20이평이탈")

        return ExitSignal(False, None)


# ----------------------------------------------------------------------
# 데모 (합성 데이터로 동작 확인) — 실전에서는 KIS API로 받은 분봉 DataFrame을 그대로 넣으면 됩니다.
# ----------------------------------------------------------------------

def _make_dummy_df(n: int, start_price: float, trend: float, freq_min: int) -> pd.DataFrame:
    """테스트용 합성 OHLCV 데이터 생성."""
    rng = np.random.default_rng(42)
    idx = pd.date_range("2026-07-19 09:00", periods=n, freq=f"{freq_min}min")
    closes = start_price + np.cumsum(rng.normal(trend, 1.0, n))
    opens = closes - rng.normal(0, 0.5, n)
    highs = np.maximum(opens, closes) + np.abs(rng.normal(0.5, 0.5, n))
    lows = np.minimum(opens, closes) - np.abs(rng.normal(0.5, 0.5, n))
    volumes = rng.integers(100, 1000, n).astype(float)
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


if __name__ == "__main__":
    # 예시: 60분/30분/15분/3분 합성 데이터로 엔진 동작 확인
    df60 = _make_dummy_df(30, 1300, trend=0.3, freq_min=60)
    df30 = _make_dummy_df(40, 1300, trend=0.3, freq_min=30)
    df15 = _make_dummy_df(60, 1300, trend=0.3, freq_min=15)
    df3 = _make_dummy_df(200, 1300, trend=0.3, freq_min=3)

    # 30분봉 마지막 저가 근처를 주요자리로 강제 지정 (실전에서는 산강채널/통곡의벽 계산값 사용)
    key_levels = [round(df30["low"].iloc[-1], 2), round(df30["high"].iloc[-1], 2)]

    engine = SangangEngine(key_levels=key_levels, tail_ratio_threshold=0.5)
    result = engine.evaluate(df60, df30, df15, df3, channel_center=df3["close"].mean())

    print(result)
    print("항목별 충족여부:", result.details)
    print("항목별 점수:", result.breakdown)

    if result.direction:
        exit_check = engine.check_exit(
            position_direction=result.direction,
            df3=df3,
            entry_price=df3["close"].iloc[-1],
            stop_loss_price=df3["close"].iloc[-1] * (0.995 if result.direction == "CALL" else 1.005),
            next_key_level=None,
        )
        print("청산 신호:", exit_check)
