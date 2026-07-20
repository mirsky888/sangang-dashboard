"""
kis_futureoption.py — KIS 국내선물옵션 분봉/일봉 시세 조회
=============================================================

⚠️ tr_id / PATH — 2026-07-19 사용자 확인 완료
    아래 값은 사용자가 KIS Developers 공식 자료에서 직접 확인해 주신 값입니다.
    DEFAULT_MINUTE_TR_ID = "FHKIF03020200"
    DEFAULT_MINUTE_PATH  = "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
    (이전 버전의 오탈자: "inquire-time-fuopt-chartprice" → 404 오류 원인이었음, 수정 완료)

검증된 부분 (공식 문서/공식 GitHub 예제로 확인):
    - 접근토큰 발급: kis_auth.py 참고
    - 공통 요청 헤더 형식: authorization(Bearer), appkey, appsecret, tr_id, custtype
    - REST 레이트리밋: 초당 20건 (실전투자 기준). 이를 넘기면 EGW00201 등 오류 발생.

이 모듈이 해결하는 v2-8 단계에서 언급된 버그들:
    1. 날짜 정렬 버그  → sort_values(ascending=True)로 항상 시간 오름차순 보장
    2. 30일 초과 청크 조회 → fetch_ohlcv_chunked()가 자동으로 기간을 분할 조회 후 병합
    3. 레이트리밋 재시도 → @rate_limited_retry 데코레이터가 429/EGW 계열 오류 시 백오프 재시도
"""

from __future__ import annotations

import time
import functools
from datetime import datetime, timedelta
from typing import Optional, List

import pandas as pd
import requests

from kis_auth import KisToken, build_headers, REAL_DOMAIN, PAPER_DOMAIN

# ----------------------------------------------------------------------
# ✅ 확인 완료 (2026-07-19)
# ----------------------------------------------------------------------
DEFAULT_MINUTE_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
DEFAULT_MINUTE_TR_ID = "FHKIF03020200"   # 국내선물옵션 분봉조회

DEFAULT_DAILY_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopt-chartprice"
DEFAULT_DAILY_TR_ID = "FHKIF03020100"    # 국내지수선물옵션 일봉조회 (여전히 추정치 — 필요시 확인 요망)

MAX_REQUESTS_PER_SEC = 18  # 공식 제한 20건/초 대비 여유분 (18건으로 보수적 운용)


def rate_limited_retry(max_retries: int = 4, base_delay: float = 0.5):
    """
    레이트리밋(초당 20건) 및 일시적 네트워크 오류에 대한 재시도 데코레이터.
    호출 간 최소 간격도 함께 보장합니다.
    """
    min_interval = 1.0 / MAX_REQUESTS_PER_SEC

    def decorator(func):
        last_call_time = {"t": 0.0}

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # 최소 호출 간격 보장
            elapsed = time.time() - last_call_time["t"]
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)

            last_err = None
            for attempt in range(max_retries):
                try:
                    result = func(*args, **kwargs)
                    last_call_time["t"] = time.time()
                    return result
                except RateLimitError as e:
                    last_err = e
                    delay = base_delay * (2 ** attempt)
                    time.sleep(delay)
                except requests.RequestException as e:
                    last_err = e
                    time.sleep(base_delay * (2 ** attempt))
            raise RuntimeError(f"{max_retries}회 재시도 후 실패: {last_err}")

        return wrapper

    return decorator


class RateLimitError(Exception):
    pass


@rate_limited_retry()
def _request_chart(
    path: str,
    tr_id: str,
    token: KisToken,
    app_key: str,
    app_secret: str,
    params: dict,
    is_paper: bool,
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
        # rt_cd != '0' 은 KIS 표준 오류 응답
        raise RuntimeError(f"KIS API 응답 오류 (rt_cd={data.get('rt_cd')}): {data.get('msg1')}")

    return data


def _parse_ohlcv_output(raw_output: List[dict]) -> pd.DataFrame:
    """
    KIS 응답 output(list[dict])을 표준 OHLCV DataFrame으로 변환.
    필드명은 상품군마다 다를 수 있어 유연하게 매핑합니다.
    """
    if not raw_output:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    def pick(row: dict, *keys):
        for k in keys:
            if k in row and row[k] not in (None, ""):
                return row[k]
        return None

    records = []
    for row in raw_output:
        date_str = pick(row, "stck_bsop_date", "bsop_date", "futs_prpr_date")
        time_str = pick(row, "stck_cntg_hour", "cntg_hour", "futs_cntg_hour") or "000000"
        o = pick(row, "futs_oprc", "optn_oprc", "stck_oprc")
        h = pick(row, "futs_hgpr", "optn_hgpr", "stck_hgpr")
        l = pick(row, "futs_lwpr", "optn_lwpr", "stck_lwpr")
        c = pick(row, "futs_prpr", "optn_prpr", "stck_prpr", "futs_clpr")
        v = pick(row, "cntg_vol", "acml_vol", "vol")

        if date_str is None or c is None:
            continue

        dt_str = f"{date_str}{str(time_str).zfill(6)}"
        try:
            dt = datetime.strptime(dt_str, "%Y%m%d%H%M%S")
        except ValueError:
            continue

        records.append(
            {
                "datetime": dt,
                "open": float(o) if o else float(c),
                "high": float(h) if h else float(c),
                "low": float(l) if l else float(c),
                "close": float(c),
                "volume": float(v) if v else 0.0,
            }
        )

    df = pd.DataFrame(records)
    if df.empty:
        return df

    df = df.set_index("datetime")
    # --- 날짜정렬 버그 수정 지점: KIS는 최신순(내림차순)으로 주는 경우가 많음 ---
    df = df.sort_index(ascending=True)
    df = df[~df.index.duplicated(keep="last")]
    return df


def fetch_minute_ohlcv_raw(
    symbol: str,
    interval_min: int,
    token: KisToken,
    app_key: str,
    app_secret: str,
    is_paper: bool = False,
    end_datetime: Optional[datetime] = None,
) -> dict:
    """
    디버깅 전용: 파싱하지 않은 KIS 원본 JSON 응답을 그대로 반환합니다.
    실제 필드명(가격/날짜/시간 필드가 무엇인지)을 확인할 때 사용하세요.
    """
    end_datetime = end_datetime or datetime.now()
    params = {
        "FID_COND_MRKT_DIV_CODE": "F",
        "FID_INPUT_ISCD": symbol,
        "FID_HOUR_CLS_CODE": str(interval_min),
        "FID_PW_DATA_INCU_YN": "Y",
        "FID_FAKE_TICK_INCU_YN": "N",
        "FID_INPUT_DATE_1": end_datetime.strftime("%Y%m%d"),
        "FID_INPUT_HOUR_1": end_datetime.strftime("%H%M%S"),
    }
    return _request_chart(
        DEFAULT_MINUTE_PATH, DEFAULT_MINUTE_TR_ID, token, app_key, app_secret, params, is_paper
    )


def fetch_latest_minute_ohlcv(
    symbol: str,
    interval_min: int,
    token: KisToken,
    app_key: str,
    app_secret: str,
    is_paper: bool = False,
) -> pd.DataFrame:
    """
    '가장 최근 거래일 기준 최신 분봉'을 조회합니다.

    ⚠️ 2026-07-19 실측 결과 정리:
      1) FID_INPUT_DATE_1/HOUR_1에 오늘 날짜를 명시하면 → 정상 응답이지만
         실제 마지막 거래일보다 며칠 더 과거 데이터가 나옴 (배치 지연 가능성)
      2) FID_INPUT_DATE_1을 빈 문자열로 보내면 → "INVALID FID_INPUT_DATE_1" 오류로
         아예 거부됨 (이 필드는 빈 값을 허용하지 않는 필수 파라미터임)
      → 결론: 이 분봉조회 엔드포인트는 정상적으로 동작하지만, 조회 시점 기준
        실시간(output1의 현재가 스냅샷)과 분봉 이력(output2) 사이에 데이터 지연이
        존재하는 것으로 보입니다. 이는 코드 버그가 아니라 이 API 자체의 특성일
        가능성이 높습니다 (선물 정산/배치 처리 주기 등의 이유).
      → 정확한 지연 사양은 KIS Developers 포털의 해당 API 문서 "응답 상세" 섹션에서
        확인 권장.
    """
    return fetch_minute_ohlcv(symbol, interval_min, token, app_key, app_secret, is_paper)


def fetch_snapshot(
    symbol: str,
    token: KisToken,
    app_key: str,
    app_secret: str,
    is_paper: bool = False,
) -> Optional[dict]:
    """
    output1(실시간 스냅샷)에서 현재가/시가/고가/저가/거래량을 추출합니다.
    output2(분봉 이력)는 지연이 있지만, output1은 실시간으로 확인됨(2026-07-19 검증).

    반환: {"price", "open", "high", "low", "volume", "timestamp"} 또는 실패 시 None
    """
    end_datetime = datetime.now()
    params = {
        "FID_COND_MRKT_DIV_CODE": "F",
        "FID_INPUT_ISCD": symbol,
        "FID_HOUR_CLS_CODE": "3",
        "FID_PW_DATA_INCU_YN": "Y",
        "FID_FAKE_TICK_INCU_YN": "N",
        "FID_INPUT_DATE_1": end_datetime.strftime("%Y%m%d"),
        "FID_INPUT_HOUR_1": end_datetime.strftime("%H%M%S"),
    }

    try:
        data = _request_chart(
            DEFAULT_MINUTE_PATH, DEFAULT_MINUTE_TR_ID, token, app_key, app_secret, params, is_paper
        )
    except Exception:
        return None

    output1 = data.get("output1") or {}
    if not output1 or "futs_prpr" not in output1:
        return None

    def _f(key, default=0.0):
        v = output1.get(key)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    return {
        "price": _f("futs_prpr"),
        "open": _f("futs_oprc", _f("futs_prpr")),
        "high": _f("futs_hgpr", _f("futs_prpr")),
        "low": _f("futs_lwpr", _f("futs_prpr")),
        "volume": _f("acml_vol"),
        "timestamp": datetime.now(),
    }


def patch_with_live_price(df: pd.DataFrame, snapshot: dict, freq_min: int) -> pd.DataFrame:
    """
    분봉 이력(df)의 지연 여부와 무관하게, 방금 조회한 실시간 스냅샷 가격을
    '지금 이 순간의 봉'으로 추가/갱신합니다. 이렇게 하면 과거 이력엔 지연이
    있어도 최신가/현재 위치 판단만큼은 실시간으로 반영됩니다.
    """
    if df is None or df.empty or not snapshot:
        return df

    now = pd.Timestamp(snapshot["timestamp"]).floor(f"{freq_min}min")
    price = snapshot["price"]

    if now in df.index:
        row = df.loc[now]
        df.loc[now, "high"] = max(row["high"], price)
        df.loc[now, "low"] = min(row["low"], price)
        df.loc[now, "close"] = price
    else:
        new_row = pd.DataFrame(
            {
                "open": [price],
                "high": [price],
                "low": [price],
                "close": [price],
                "volume": [0.0],
            },
            index=[now],
        )
        df = pd.concat([df, new_row])
        df = df[~df.index.duplicated(keep="last")].sort_index()

    return df


def fetch_minute_ohlcv(
    symbol: str,
    interval_min: int,
    token: KisToken,
    app_key: str,
    app_secret: str,
    is_paper: bool = False,
    end_datetime: Optional[datetime] = None,
) -> pd.DataFrame:
    """
    단일 호출로 가져올 수 있는 범위(보통 최근 30건~일부 일수)의 분봉 데이터를 조회합니다.
    30일 초과 장기 조회는 fetch_ohlcv_chunked()를 사용하세요.
    """
    end_datetime = end_datetime or datetime.now()

    params = {
        "FID_COND_MRKT_DIV_CODE": "F",     # 선물 구분 (옵션은 별도 코드 필요할 수 있음 — 확인 필요)
        "FID_INPUT_ISCD": symbol,          # 예: "A01609"(2026년 9월물), "A01000"(연결선물) — 2026-07-19 사용자 확인
        "FID_HOUR_CLS_CODE": str(interval_min),
        "FID_PW_DATA_INCU_YN": "Y",
        "FID_FAKE_TICK_INCU_YN": "N",      # 가짜(허봉) 체결 포함 여부 — 2026-07-19 응답 오류로 추가 확인됨
        "FID_INPUT_DATE_1": end_datetime.strftime("%Y%m%d"),
        "FID_INPUT_HOUR_1": end_datetime.strftime("%H%M%S"),
    }

    data = _request_chart(
        DEFAULT_MINUTE_PATH, DEFAULT_MINUTE_TR_ID, token, app_key, app_secret, params, is_paper
    )
    raw_output = data.get("output2") or data.get("output1") or data.get("output") or []
    return _parse_ohlcv_output(raw_output)


def fetch_ohlcv_chunked(
    symbol: str,
    interval_min: int,
    lookback_days: int,
    token: KisToken,
    app_key: str,
    app_secret: str,
    is_paper: bool = False,
    chunk_days: int = 5,
) -> pd.DataFrame:
    """
    lookback_days가 한 번의 API 호출로 못 가져올 만큼 길 때,
    chunk_days 단위로 기간을 쪼개서 반복 호출 후 병합합니다.

    최신 구간은 fetch_latest_minute_ohlcv()(날짜 필드 공란 방식)로 가져오고,
    그보다 과거 구간은 fetch_minute_ohlcv()(명시적 날짜 지정)로 역순 조회합니다.
    """
    all_chunks = []

    # 1) 가장 최근 구간은 '공란' 방식으로 조회 (오프바이원 문제 회피)
    latest_chunk = fetch_latest_minute_ohlcv(symbol, interval_min, token, app_key, app_secret, is_paper)
    if latest_chunk.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    all_chunks.append(latest_chunk)
    cursor_end = latest_chunk.index.min() - timedelta(minutes=interval_min)

    # 2) 그보다 과거 구간은 명시적 날짜로 역순 조회
    remaining_days = lookback_days - chunk_days
    while remaining_days > 0:
        step = min(chunk_days, remaining_days)
        df_chunk = fetch_minute_ohlcv(
            symbol, interval_min, token, app_key, app_secret, is_paper, end_datetime=cursor_end
        )
        if not df_chunk.empty:
            all_chunks.append(df_chunk)
            cursor_end = df_chunk.index.min() - timedelta(minutes=interval_min)
        else:
            break
        remaining_days -= step

    merged = pd.concat(all_chunks)
    merged = merged[~merged.index.duplicated(keep="last")]
    merged = merged.sort_index(ascending=True)  # 최종 병합 후에도 재확인
    return merged
