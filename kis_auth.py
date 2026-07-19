"""
kis_auth.py — KIS Developers OAuth2 접근토큰 관리
====================================================

검증된 공식 스펙 (apiportal.koreainvestment.com, 2026-07 기준):
  실전투자 도메인: https://openapi.koreainvestment.com:9443
  모의투자 도메인: https://openapivts.koreainvestment.com:29443
  토큰 발급 PATH : /oauth2/tokenP  (POST)
  요청 body      : {"grant_type": "client_credentials", "appkey": ..., "appsecret": ...}
  응답            : {"access_token": ..., "token_type": "Bearer", "expires_in": 86400, ...}

주의사항 (KIS 공지 기준):
  - 접근토큰은 유효기간 24시간이며, 동일 앱키로 너무 잦은 재발급 요청 시
    "EGW00133"(초당 거래건수 초과) 또는 재발급 제한 오류가 날 수 있습니다.
    → 따라서 토큰은 반드시 캐싱하고, 만료 임박(예: 만료 10분 전) 시에만 재발급합니다.
  - TLS 1.0/1.1은 2025-12-12부로 미지원 (requests 최신 버전 사용 시 문제 없음).
"""

from __future__ import annotations

import time
import json
import os
from dataclasses import dataclass
from typing import Optional

import requests

REAL_DOMAIN = "https://openapi.koreainvestment.com:9443"
PAPER_DOMAIN = "https://openapivts.koreainvestment.com:29443"

TOKEN_CACHE_PATH = os.path.join(os.path.dirname(__file__), ".kis_token_cache.json")


@dataclass
class KisToken:
    access_token: str
    issued_at: float          # time.time() 기준
    expires_in: int           # 초 단위 (보통 86400)
    is_paper: bool

    @property
    def expires_at(self) -> float:
        return self.issued_at + self.expires_in

    @property
    def is_valid(self) -> bool:
        # 만료 10분 전부터는 재발급 대상으로 취급 (여유분)
        return time.time() < (self.expires_at - 600)

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "issued_at": self.issued_at,
            "expires_in": self.expires_in,
            "is_paper": self.is_paper,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "KisToken":
        return cls(**d)


def _domain(is_paper: bool) -> str:
    return PAPER_DOMAIN if is_paper else REAL_DOMAIN


def _load_cached_token(is_paper: bool) -> Optional[KisToken]:
    if not os.path.exists(TOKEN_CACHE_PATH):
        return None
    try:
        with open(TOKEN_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        tok = KisToken.from_dict(data)
        if tok.is_paper == is_paper and tok.is_valid:
            return tok
    except Exception:
        return None
    return None


def _save_token_cache(token: KisToken):
    try:
        with open(TOKEN_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(token.to_dict(), f)
    except Exception:
        # Streamlit Cloud 등 파일쓰기 제한 환경에서는 캐시 저장 실패해도 무방
        # (호출측에서 st.cache_resource로 메모리 캐싱하므로 동작에는 지장 없음)
        pass


def issue_token(app_key: str, app_secret: str, is_paper: bool = False, max_retries: int = 3) -> KisToken:
    """
    캐시된 유효 토큰이 있으면 그대로 반환하고, 없으면 새로 발급합니다.
    재발급 제한(EGW00133 등) 대응을 위해 지수 백오프 재시도를 포함합니다.
    """
    cached = _load_cached_token(is_paper)
    if cached is not None:
        return cached

    url = f"{_domain(is_paper)}/oauth2/tokenP"
    headers = {"content-type": "application/json; charset=UTF-8"}
    body = {"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret}

    last_err = None
    for attempt in range(max_retries):
        try:
            res = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
            if res.status_code == 200:
                data = res.json()
                token = KisToken(
                    access_token=data["access_token"],
                    issued_at=time.time(),
                    expires_in=int(data.get("expires_in", 86400)),
                    is_paper=is_paper,
                )
                _save_token_cache(token)
                return token
            else:
                last_err = f"HTTP {res.status_code}: {res.text}"
        except requests.RequestException as e:
            last_err = str(e)

        # 지수 백오프 (1초, 2초, 4초 ...)
        time.sleep(2 ** attempt)

    raise RuntimeError(f"KIS 토큰 발급 실패 ({max_retries}회 재시도 후): {last_err}")


def build_headers(token: KisToken, app_key: str, app_secret: str, tr_id: str, custtype: str = "P") -> dict:
    """조회/주문 API 공통 헤더 생성."""
    return {
        "content-type": "application/json; charset=UTF-8",
        "authorization": f"Bearer {token.access_token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": tr_id,
        "custtype": custtype,
    }
