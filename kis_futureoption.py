"""
kis_futureoption.py — KIS 국내선물옵션 분봉/일봉 시세 조회
=============================================================

⚠️ tr_id 확인 필요 안내
    국내지수선물옵션 분봉조회의 정확한 tr_id/PATH는 KIS Developers 포털 로그인 후에만
    문서 원문을 볼 수 있어 이번 세션에서 100% 확정 검증은 못했습니다.
    아래 DEFAULT_MINUTE_TR_ID 값("FHKIF03020200")은 KIS의 국내지수선물옵션 계열
    TR ID 명명 규칙(FHKIF = 국내지수선물옵션)에 따른 가장 유력한 값입니다.
    기존에 이미 동작하던 대시보드 코드가 있으시다면, 그 코드의
    tr_id / PATH 문자열만 아래 상수 두 개에 그대로 옮겨 넣으시면 됩니다.

검증된 부분 (공식 문서/공식 GitHub 예제로 확인):
    - 접근토큰 발급: kis_auth.py 참고
    - 공통 요청 헤더 형식: authorization(Bearer), appkey, appsecret, tr_id, custtype
    - REST 레이트리밋: 초당 20건 (실전투자 기준). 이를 넘기면 EGW00201 등 오류 발생.
    - 해외선물옵션 분봉조회 API(inquire-time-futurechartprice)의 정확한 구조를 참고하여
      국내선물옵션 버전의 파라미터 네이밍을 유추해 구성했습니다. 실제 파라미터명이 다르면
      MINUTE_PATH 아래 params 딕셔너리의 키 이름만 교체하면 됩니다.

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
# ⚠️ 확인 필요: 아래 두 값을 기존에 쓰시던 값으로 교체 가능
# ----------------------------------------------------------------------
DEFAULT_MINUTE_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopt-chartprice"
DEFAULT_MINUTE_TR_ID = "FHKIF03020200"   # 국내지수선물옵션 분봉조회 (추정치 — 확인 필요)

DEFAULT_DAILY_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopt-chartprice"
DEFAULT_DAILY_TR_ID = "FHKIF03020100"    # 국내지수선물옵션 일봉조회 (추정치 — 확인 필요)

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
        "FID_INPUT_ISCD": symbol,
        "FID_HOUR_CLS_CODE": str(interval_min),
        "FID_PW_DATA_INCU_YN": "Y",
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
    (KIS 분봉 API는 1회 호출당 반환 건수 제한이 있어, 과거로 갈수록
     end_datetime을 앞으로 당겨가며 순차 조회하는 방식입니다.)
    """
    all_chunks = []
    now = datetime.now()
    cursor_end = now

    remaining_days = lookback_days
    while remaining_days > 0:
        step = min(chunk_days, remaining_days)
        df_chunk = fetch_minute_ohlcv(
            symbol, interval_min, token, app_key, app_secret, is_paper, end_datetime=cursor_end
        )
        if not df_chunk.empty:
            all_chunks.append(df_chunk)
            cursor_end = df_chunk.index.min() - timedelta(minutes=interval_min)
        else:
            # 더 이상 데이터가 없으면 중단
            break

        remaining_days -= step

    if not all_chunks:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    merged = pd.concat(all_chunks)
    merged = merged[~merged.index.duplicated(keep="last")]
    merged = merged.sort_index(ascending=True)  # 최종 병합 후에도 재확인
    return merged
