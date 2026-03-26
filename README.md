# 조정훈유바외과 네이버 콘텐츠 모니터링 시스템

네이버 블로그/카페에서 병원 관련 게시글을 자동 수집하고, 부적절한 표현을 AI 기반으로 탐지하여 이메일로 알림을 보내는 모니터링 시스템입니다.

## 시스템 아키텍처

```mermaid
graph TB
    subgraph External["외부 서비스"]
        NAVER["네이버 검색 API"]
        AOAI["Azure OpenAI<br/>(GPT-4.1)"]
        CLAUDE["Claude API<br/>(폴백)"]
        GMAIL["Gmail SMTP<br/>(OAuth2)"]
    end

    subgraph Core["모니터링 코어"]
        SCHEDULER["APScheduler<br/>monitor_job.py"]
        CRAWLER["NaverCrawler<br/>naver_crawler.py"]
        ANALYZER["ContentAnalyzer<br/>content_analyzer.py"]
        NOTIFIER["EmailNotifier<br/>email_notifier.py"]
        DB["SQLite DB<br/>monitoring.db"]
    end

    subgraph Interface["사용자 인터페이스"]
        CLI["CLI<br/>main.py"]
        ADMIN["Flask 대시보드<br/>admin/app.py"]
    end

    CLI --> SCHEDULER
    SCHEDULER --> CRAWLER
    CRAWLER --> NAVER
    SCHEDULER --> ANALYZER
    ANALYZER --> AOAI
    ANALYZER -.->|폴백| CLAUDE
    SCHEDULER --> DB
    SCHEDULER --> NOTIFIER
    NOTIFIER --> GMAIL
    ADMIN --> DB
```

## 모니터링 워크플로우

```mermaid
flowchart TD
    START([스케줄러 트리거<br/>30분 간격]) --> CRAWL

    subgraph CRAWL_PHASE["1단계: 크롤링"]
        CRAWL[키워드별 네이버 API 호출]
        CRAWL --> BLOG["블로그 검색<br/>(키워드 × 3페이지)"]
        CRAWL --> CAFE["카페 검색<br/>(키워드 × 3페이지)"]
        BLOG --> MERGE[결과 병합]
        CAFE --> MERGE
        MERGE --> DEDUP["중복 제거<br/>(link 기준)"]
        DEDUP --> DATE_FILTER["날짜 필터<br/>(최근 30일)"]
    end

    DATE_FILTER --> DB_CHECK{"DB에<br/>이미 존재?"}
    DB_CHECK -->|Yes| SKIP[건너뜀]
    DB_CHECK -->|No| SAVE_POST["게시글 DB 저장"]
    SAVE_POST --> ANALYZE

    subgraph ANALYZE_PHASE["2단계: 분석 (하이브리드 스코어링)"]
        ANALYZE[게시글 분석 시작]
        ANALYZE --> KW_FILTER["1차: 키워드 필터링"]
        ANALYZE --> AI_ANALYZE["2차: LLM 문맥 분석"]

        KW_FILTER --> KW_SCORE["키워드 점수 산출<br/>(카테고리 다양성 40%<br/>+ 키워드 수 60%)"]

        AI_ANALYZE --> AOAI_TRY{"AOAI 호출"}
        AOAI_TRY -->|성공| AI_RESULT["AI 판단 결과"]
        AOAI_TRY -->|실패| CLAUDE_TRY{"Claude 폴백"}
        CLAUDE_TRY -->|성공| AI_RESULT
        CLAUDE_TRY -->|실패| NO_AI["AI 분석 불가<br/>(점수 0.0)"]

        AI_RESULT --> AI_SCORE["AI 점수 산출<br/>(부적절 시 confidence 값)"]
        NO_AI --> AI_SCORE

        KW_SCORE --> HYBRID["하이브리드 스코어<br/>= AI 70% + 키워드 30%"]
        AI_SCORE --> HYBRID

        HYBRID --> THRESHOLD{"hybrid_score<br/>≥ 0.5?"}
    end

    THRESHOLD -->|No| PASS["정상 판정"]
    THRESHOLD -->|Yes| INAPPROPRIATE["부적절 판정"]

    INAPPROPRIATE --> SEVERITY{"심각도 결정"}
    SEVERITY -->|"≥ 0.8"| HIGH["HIGH"]
    SEVERITY -->|"≥ 0.6"| MEDIUM["MEDIUM"]
    SEVERITY -->|"< 0.6"| LOW["LOW"]

    HIGH --> SAVE_DET["탐지 결과 DB 저장"]
    MEDIUM --> SAVE_DET
    LOW --> SAVE_DET

    SAVE_DET --> NOTIFY

    subgraph NOTIFY_PHASE["3단계: 알림"]
        NOTIFY{"부적절<br/>게시글 있음?"}
        NOTIFY -->|Yes| BUILD_EMAIL["HTML 리포트 생성"]
        NOTIFY -->|No| DONE_QUIET["사이클 종료<br/>(알림 없음)"]
        BUILD_EMAIL --> OAUTH["OAuth2 토큰 취득/갱신"]
        OAUTH --> SEND_EMAIL["Gmail XOAUTH2<br/>이메일 발송"]
        SEND_EMAIL --> SAVE_NOTI["알림 이력 DB 저장"]
    end

    SAVE_NOTI --> DONE([사이클 완료])
    DONE_QUIET --> DONE
```

## 콘텐츠 분석 로직 상세

```mermaid
flowchart LR
    subgraph INPUT["입력"]
        POST["게시글<br/>(제목 + 본문)"]
    end

    subgraph KEYWORD["키워드 분석 (30%)"]
        direction TB
        CAT1["욕설/비하"]
        CAT2["허위 의료 정보"]
        CAT3["명예훼손"]
        CAT4["허위 리뷰 조작"]
        CAT5["불친절/서비스"]
        CAT6["위생/시설"]
        CAT7["금전/보험"]

        CAT_SCORE["카테고리 점수<br/>min(매칭 카테고리수/3, 1.0)<br/>× 0.4"]
        KW_CNT["키워드 점수<br/>min(매칭 키워드수/5, 1.0)<br/>× 0.6"]
        CAT_SCORE --> KW_TOTAL["keyword_score"]
        KW_CNT --> KW_TOTAL
    end

    subgraph AI["LLM 분석 (70%)"]
        direction TB
        PROMPT["프롬프트 생성<br/>(병원명 + 기준 + 게시글)"]
        LLM_CALL["LLM 호출<br/>(AOAI → Claude)"]
        JSON_PARSE["JSON 응답 파싱<br/>is_inappropriate<br/>confidence<br/>severity<br/>reason"]
        PROMPT --> LLM_CALL --> JSON_PARSE
        JSON_PARSE --> AI_TOTAL["ai_score<br/>(부적절 시 confidence)"]
    end

    subgraph SCORING["하이브리드 스코어"]
        CALC["hybrid = AI×0.7 + KW×0.3"]
        JUDGE{"≥ 0.5?"}
        CALC --> JUDGE
    end

    POST --> KEYWORD
    POST --> AI
    KW_TOTAL --> CALC
    AI_TOTAL --> CALC

    JUDGE -->|Yes| BAD["부적절"]
    JUDGE -->|No| OK["정상"]
```

## LLM 폴백 전략

```mermaid
sequenceDiagram
    participant A as ContentAnalyzer
    participant AOAI as Azure OpenAI (GPT-4.1)
    participant Claude as Claude API (Sonnet)

    A->>AOAI: 분석 요청 (1차)
    alt 성공
        AOAI-->>A: JSON 응답 (is_inappropriate, confidence, ...)
        Note over A: 토큰 사용량 기록
    else 실패 (타임아웃/에러)
        AOAI-->>A: Exception
        A->>Claude: 분석 요청 (2차 폴백)
        alt 성공
            Claude-->>A: JSON 응답
        else 실패
            Claude-->>A: Exception
            Note over A: score = 0.0, reason = "LLM 분석 불가"
        end
    end
```

## 이메일 발송 흐름

```mermaid
sequenceDiagram
    participant N as EmailNotifier
    participant T as token.json
    participant G as Google OAuth2
    participant S as Gmail SMTP

    N->>T: token.json 읽기
    alt 토큰 만료
        N->>G: refresh_token으로 갱신 요청
        G-->>N: 새 access_token
        N->>T: token.json 업데이트
    end
    N->>N: HTML 리포트 생성
    N->>S: SMTP 연결 (587/TLS)
    N->>S: AUTH XOAUTH2 (access_token)
    N->>S: 이메일 발송
    S-->>N: 발송 결과
```

## 프로젝트 구조

```
test/
├── main.py                    # CLI 진입점 (--once, --stats)
├── config.py                  # 전역 설정 (키워드, 카테고리, API 키 등)
├── oauth2_setup.py            # Gmail OAuth2 토큰 발급 스크립트
├── requirements.txt           # Python 의존성
├── .env                       # 환경변수 (API 키, 이메일 설정)
├── monitoring.db              # SQLite DB (자동 생성)
│
├── crawler/
│   └── naver_crawler.py       # 네이버 블로그/카페 검색 API 크롤러
│
├── analyzer/
│   └── content_analyzer.py    # 하이브리드 콘텐츠 분석기 (키워드 + LLM)
│
├── notifier/
│   └── email_notifier.py      # Gmail OAuth2 이메일 알림 발송
│
├── storage/
│   └── database.py            # SQLite 저장소 (게시글/탐지/알림 이력)
│
├── scheduler/
│   └── monitor_job.py         # APScheduler 기반 주기 실행
│
└── admin/
    ├── app.py                 # Flask 관리 대시보드
    └── templates/
        └── index.html         # 대시보드 UI
```

## 실행 방법

```bash
# 의존성 설치
pip install -r requirements.txt

# Gmail OAuth2 토큰 발급 (최초 1회)
python oauth2_setup.py

# 1회 실행
python main.py --once

# 스케줄러 모드 (30분 간격 자동 실행)
python main.py

# 누적 통계 확인
python main.py --stats

# 관리 대시보드 실행 (포트 5000)
python admin/app.py
```

## 환경변수 (.env)

```env
# LLM 설정
LLM_PROVIDER=aoai
AOAI_ENDPOINT=https://your-endpoint.openai.azure.com/
AOAI_API_KEY=your-key
AOAI_DEPLOYMENT=gpt-4.1
AOAI_API_VERSION=2024-12-01-preview
ANTHROPIC_API_KEY=sk-ant-...           # Claude 폴백용

# 네이버 API
NAVER_CLIENT_ID=your-client-id
NAVER_CLIENT_SECRET=your-secret

# 이메일 (OAuth2)
EMAIL_SENDER=your@gmail.com
EMAIL_RECIPIENTS=recipient1@gmail.com,recipient2@gmail.com
```

## 부적절 표현 탐지 카테고리 (7종)

| 카테고리 | 설명 | 키워드 예시 |
|----------|------|------------|
| 욕설/비하 | 욕설, 비하 표현 | 쓰레기, 돌팔이, 사기, ㅅㅂ ... |
| 허위 의료 정보 | 검증되지 않은 의료 주장 | 수술 실패, 의료사고, 과잉진료 ... |
| 명예훼손 | 명예훼손성 주장 | 고소당, 폐원, 면허취소, 소송 ... |
| 허위 리뷰 조작 | 리뷰 조작 의심 | 알바, 가짜 리뷰, 별점 조작 ... |
| 불친절/서비스 | 서비스 불만 | 불친절, 무례, 태도 불량 ... |
| 위생/시설 | 위생/시설 문제 | 비위생, 더러, 감염 ... |
| 금전/보험 | 금전/보험 관련 불만 | 바가지, 부당 청구, 환불 거부 ... |
