---
name: btwin-import
description: Use when the user wants to import markdown files from an external directory into B-TWIN. Handles analysis, date/tag extraction, and batch import using LLM judgment.
---

# B-TWIN Import

외부 마크다운 디렉토리의 파일들을 분석하여 B-TWIN에 임포트합니다.
LLM이 각 파일의 내용을 읽고, 날짜/slug/tags를 판단하여 `btwin_import_entry` 도구로 저장합니다.

## When to Use

- 사용자가 "임포트해줘", "이 폴더 데이터 가져와" 등을 요청할 때
- `/btwin-import` 실행 시
- 외부 마크다운 프로젝트의 데이터를 B-TWIN으로 마이그레이션할 때

## Workflow

1. **소스 디렉토리 스캔** — Glob 도구로 `.md` 파일 목록 수집
2. **파일별 분석** — 각 파일을 Read로 읽고 내용 분석
3. **임포트 계획 제시** — 사용자에게 어떤 파일을 어떻게 임포트할지 보여주고 확인
4. **도구 호출** — `btwin_import_entry`로 각 Entry 저장
5. **결과 보고** — 임포트된 항목 수와 요약 출력

## Step 1: 파일 스캔

Glob 도구로 소스 디렉토리의 `.md` 파일을 수집한다.

**제외 대상:**
- 도트 디렉토리 하위 파일 (`.git/`, `.claude/`, `.venv/` 등)
- `node_modules/` 하위
- 이미 btwin 포맷인 `entries/` 하위

## Step 2: 파일 분석

각 `.md` 파일을 Read 도구로 읽고, 다음을 판단한다:

### 멀티섹션 감지

파일 안에 날짜 기반 헤딩(`### 260226`, `## 2026-02-26` 등)이 2개 이상이면, 각 섹션을 독립 Entry로 분리한다. 파일 제목 등 헤딩 이전 내용은 각 Entry의 prefix로 포함한다.

### 날짜 결정 (우선순위)

1. 파일 내용의 날짜 헤딩 (멀티섹션의 경우)
2. 파일명의 날짜 패턴 (YYMMDD, YYYY-MM-DD 등)
3. 파일 내용에서 날짜 맥락 추론
4. 확인 불가 시 사용자에게 질문

출력 형식: **반드시 `YYYY-MM-DD`** (예: `2026-02-24`)

### Slug 결정

파일의 핵심 주제를 반영하는 **영문 kebab-case** slug를 생성한다.
- 내용을 읽고 의미 있는 이름을 만든다
- 예: 프로젝트 회고 → `project-retrospective`, API 설계 문서 → `api-design-doc`
- 2~4단어, 영문 소문자, 하이픈 구분

### Tags 결정

파일의 **내용을 기반으로** 의미 있는 태그를 생성한다.
- 폴더 경로는 참고만 하고, 내용에서 주제를 추출한다
- 예: 아키텍처 결정 문서 → `[architecture, design-decision, backend]`
- 태그는 영문 소문자 kebab-case
- 3~5개 정도가 적절

## Step 3: 임포트 계획 제시

분석 결과를 사용자에게 테이블로 보여주고 확인을 받는다:

```
| # | 원본 파일 | date | slug | tags |
|---|-----------|------|------|------|
| 1 | notes/weekly.md (섹션 260226) | 2026-02-26 | api-refactoring-plan | backend, refactoring, api |
| 2 | reports/sprint-review-260224.md | 2026-02-24 | sprint-3-review | sprint, review, retrospective |
| ... | ... | ... | ... | ... |
```

**반드시 사용자 확인 후 진행한다.**

## Step 4: 도구 호출

확인이 되면 각 Entry에 대해 `btwin_import_entry`를 호출한다:

- `content`: 마크다운 본문 (원본 그대로)
- `date`: YYYY-MM-DD
- `slug`: 판단한 slug
- `tags`: 쉼표 구분 태그 문자열 (예: `"architecture,design-decision,backend"`)
- `source_path`: 원본 파일의 절대 경로 (중복 임포트 방지용)

## Step 5: 결과 보고

임포트 완료 후 요약을 출력한다:
- 임포트된 Entry 수
- 스킵된 파일 (비-마크다운 등)
- `btwin_search`로 검색 가능 여부 확인 안내

## Rules

- **내용을 반드시 읽고 판단한다** — 파일명이나 폴더 구조만으로 추측하지 않는다
- **원본 내용은 수정하지 않는다** — content는 원본 마크다운 그대로 저장
- **사용자 확인 필수** — 임포트 계획을 보여주고 승인 후 실행
- **한 번에 하나씩** — 파일이 많으면 배치로 나눠서 진행 (10개 단위)
- **date 형식은 YYYY-MM-DD** — 반드시 이 형식으로 통일
- **언어**: 한국어로 안내
