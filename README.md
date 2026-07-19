# 산강 매매법 v2-9 대시보드

60분→30분→15분→3분 캐스케이드 확인 + 신뢰도 점수화 엔진을 얹은 Streamlit 대시보드.

## 파일 구성

```
sangang-dashboard/
├── app.py                          # Streamlit 메인 앱 (실전 연동 완료)
├── kis_auth.py                     # KIS OAuth2 토큰 발급/캐싱 (만료 10분 전 자동 재발급)
├── kis_futureoption.py             # 분봉/일봉 조회: 날짜정렬 수정, 30일 초과 청크 조회, 레이트리밋 재시도
├── sangang_channel.py              # 산강채널(구조적 고정 고저 앵커링) + 다중이평 + 통곡의 벽 계산
├── sangang_signal_engine.py        # 산강 매매법 신호 엔진 (60/30/15/3분 캐스케이드 + 점수화)
├── requirements.txt                # 의존 패키지
├── .gitignore                      # secrets.toml 등 민감정보 제외
├── .streamlit/
│   └── secrets.toml.example        # KIS API 키 입력 템플릿 (실제 파일은 커밋 금지)
└── README.md
```

## ⚠️ 배포 전 반드시 확인할 것 — tr_id / PATH

`kis_futureoption.py` 상단의 다음 두 상수는 **추정치**입니다:

```python
DEFAULT_MINUTE_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopt-chartprice"
DEFAULT_MINUTE_TR_ID = "FHKIF03020200"
```

KIS Developers 포털(로그인 필요)의 "국내선물옵션 > 기간별시세(분)" 문서에서 정확한 PATH와 tr_id를 확인해 위 두 줄만 교체해 주세요. 이전에 이미 동작하던 코드가 있다면 그 값을 그대로 붙여넣으면 됩니다. 나머지 로직(토큰 캐싱, 헤더 구성, 날짜정렬, 청크조회, 레이트리밋 재시도, 산강채널 계산)은 공식 문서/공식 예제로 검증된 구조입니다.

## 로컬 없이 GitHub → Streamlit Cloud 배포 순서

1. **GitHub 저장소 생성**
   - github.com에서 새 저장소(예: `sangang-dashboard`) 생성 (Private 권장 — API 키 관련 코드가 포함되므로)
   - "Add file → Upload files"로 이 폴더의 파일들을 그대로 업로드 (드래그 앤 드롭 가능, 로컬 Python/Git 설치 불필요)
   - `.streamlit/secrets.toml.example`은 올리되, 실제 `secrets.toml`은 **절대 올리지 마세요** (`.gitignore`에 이미 등록되어 있어 방지됨)

2. **Streamlit Cloud 연결**
   - share.streamlit.io 접속 → "New app"
   - 방금 만든 GitHub 저장소 선택, `app.py`를 메인 파일로 지정
   - "Advanced settings" → **Secrets**란에 `secrets.toml.example` 내용을 실제 값으로 채워서 붙여넣기
     ```
     KIS_APP_KEY = "실제_앱키"
     KIS_APP_SECRET = "실제_앱시크릿"
     KIS_ACCOUNT_NO = "실제_계좌번호"
     KIS_IS_PAPER = false
     ```
   - Deploy 클릭

3. **완료 후 배포 URL**로 바로 접속해서 대시보드 확인 가능. 이후 GitHub에 새로 push할 때마다 Streamlit Cloud가 자동으로 재배포합니다.

## 남은 확인사항

| 항목 | 상태 |
|---|---|
| KIS OAuth2 토큰 관리 | ✅ 완료 (`kis_auth.py`, 만료 10분전 자동 재발급, 지수 백오프 재시도) |
| 분봉 조회 (날짜정렬/청크/레이트리밋) | ✅ 완료 (`kis_futureoption.py`) |
| 국내선물옵션 분봉 PATH/tr_id | ⚠️ 추정치 — 위 안내대로 실제 값으로 확인/교체 필요 |
| 산강채널·통곡의 벽 계산 | ✅ 완료 (`sangang_channel.py`) |
| 옵션 프리미엄(콜/풋) 조회, 계좌 잔고 조회 등 | 미포함 — 필요하시면 같은 패턴으로 추가 가능 |

## 점수 체계 요약

| 항목 | 배점 | 필수 여부 |
|---|---|---|
| 60분봉 방향 일치 | 20 | 필수 |
| 중심가(채널 중심) 위/아래 위치 | 10 | - |
| 30분봉 꼬리 확인 (몸통 대비) | 20 | 필수 |
| 15분봉 전환 확인 | 20 | 필수 |
| 3분봉 고가/저가 돌파 | 10 | 필수(실행 트리거) |
| 3분봉 20이평 돌파/이탈 | 10 | - |
| 3분봉 거래량 증가 | 10 | - |

등급: 90~100 S급 / 80~89 A급 / 70~79 B급 / 그 외 관망 (필수조건 미충족 시 무조건 관망)

자세한 로직 및 배경 원리는 `sangang_signal_engine.py` 상단 docstring과 `통합매매법_v2-9.md` 참고.
