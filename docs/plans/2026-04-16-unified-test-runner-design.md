# Unified Test Runner Design

## Goal

`btwin-runtime`의 테스트 실행 경험을 하나의 공통 진입점으로 정리한다. 기본 테스트와 provider-attached smoke를 같은 관리 체계 아래 두되, provider 연동은 로컬 opt-in으로 분리하고 run별 HTML/artifact를 남겨 회귀 확인과 사용자 확인을 쉽게 만든다.

## Why

현재 테스트는 `pytest`, 개별 스크립트, 수동 smoke가 섞여 있다. 이 상태에서는 어떤 명령을 언제 써야 하는지 기억 비용이 크고, 실행 결과도 한 군데에 모이지 않는다. provider-attached smoke를 늘릴수록 인증 조건, 모델 고정, 격리 env, 결과 보관 규칙을 사람이 매번 수동으로 맞춰야 해서 재현성과 유지보수성이 떨어진다.

## Scope

이 설계는 다음을 포함한다.

- `pytest`를 공통 테스트 엔진으로 유지
- `scripts/run_tests.py`를 공통 실행 진입점으로 추가
- 테스트 결과를 `.test-artifacts/` 아래 run별 디렉터리로 보관
- `pytest-html`로 사람용 `report.html` 생성
- provider-attached smoke를 로컬 opt-in 규칙으로 분리
- provider smoke 기본 프로필을 `app-server` long-term + `gpt-5.4-mini`로 고정

이 설계는 다음을 포함하지 않는다.

- `btwin test ...` 같은 새 public CLI surface 추가
- 별도 YAML/JSON manifest DSL 도입
- 실제 사람의 자유 입력 UX를 그대로 재현하는 full acceptance 자동화

## Design Decisions

### 1. Test Engine vs. Runner

테스트 정의와 assertion은 계속 `pytest`가 맡는다. 실행 정책, 마커 선택, 리포트 경로, artifact 보관, provider smoke 보호 규칙은 `scripts/run_tests.py`가 맡는다.

이렇게 분리하면 표준성은 유지하면서도 운영 정책을 한 곳에서 통제할 수 있다. 사용자는 긴 `pytest` 옵션을 기억할 필요 없이 `unit`, `integration`, `cli-smoke`, `provider-smoke`, `all` 같은 고정 명령만 사용한다.

### 2. One Artifact System for All Test Types

provider smoke만 따로 관리하지 않는다. 모든 테스트 run은 `.test-artifacts/` 아래에 남는다.

예시:

```text
.test-artifacts/
  latest -> 2026-04-16T16-30-05-provider-smoke/
  2026-04-16T16-30-05-provider-smoke/
    report.html
    metadata.json
    pytest.log
    summary.json
    commands.log
    thread-state.json
    workflow-events.jsonl
    provider-session.json
```

모든 run은 공통 메타데이터와 HTML 리포트를 가진다. provider smoke는 여기에 btwin/provider 특화 artifact를 추가로 남긴다.

### 3. HTML Report as the Human-Facing Default

1차 구현에서는 XML을 기본 산출물로 두지 않는다. 사람이 확인하기 쉬운 `pytest-html`의 `report.html`을 각 run 디렉터리에 남긴다.

이 선택의 이유는 현재 목표가 CI 집계보다 로컬 verification과 회귀 관찰에 더 가깝기 때문이다. 나중에 CI 집계 요구가 생기면 XML은 추가 산출물로 쉽게 붙일 수 있다.

### 4. Retention Policy

artifact는 무한 보관하지 않는다. 기본 보관 개수는 최근 `30` runs다.

보관 개수는 다음 우선순위로 결정한다.

1. `scripts/run_tests.py --keep-runs <N>`
2. `BTWIN_TEST_KEEP_RUNS`
3. 기본값 `30`

`latest` 링크는 항상 가장 최근 run을 가리킨다.

### 5. Provider Smoke Is Opt-In and Local Only

provider-attached smoke는 로컬 사용자 인증이 필요한 테스트다. 따라서 배포/기본 CI 경로에서는 제외한다.

provider smoke의 규칙:

- 기본 실행군에서는 제외
- 사용자가 명시적으로 `provider-smoke`를 선택했을 때만 실행
- provider CLI 또는 인증 상태가 준비되지 않았으면 hard fail보다 `skip`을 우선
- runner가 관련 상태를 preflight로 검사하고 결과를 리포트와 metadata에 남김
- isolated btwin data/config는 유지하되, provider 인증은 기본적으로 현재 사용자 홈을 재사용한다
- 필요하면 `BTWIN_PROVIDER_AUTH_HOME`으로 provider 인증 홈만 별도로 override할 수 있다

### 6. Provider Smoke Default Profile

provider smoke의 기본 프로필은 아래와 같다.

- surface: `app-server`
- continuity: long-term
- model: `gpt-5.4-mini`

`exec`, fallback, short-term, recover는 기본 경로가 아니다. 해당 동작 자체를 검증하는 시나리오에서만 별도 marker나 명시적 옵션으로 돌린다.

테스트 결과에는 `requested_model`과 `effective_model`을 모두 남겨 실제 provider가 어떤 모델을 썼는지 확인 가능해야 한다.

### 6.5. Provider Smoke Scenario Shape

`provider-smoke`는 하나의 거대한 end-to-end 테스트가 아니다. 같은 marker 그룹 안에서 아래처럼 여러 시나리오로 나눈다.

- baseline scenario
  - `live attach -> direct message -> final answer persist`
- gate scenarios
  - 예: `thread close` blocked, `contribution submit` phase/actor gate, `protocol apply-next` hint

이 방식의 목적은 두 가지다.

- real provider attach 비용은 공유하되, 실패 원인을 시나리오 단위로 분리한다
- 평소에는 baseline만 빠르게 돌리고, gate regression이 필요할 때는 특정 시나리오만 선택 실행할 수 있게 한다

즉 “provider smoke는 하나의 실행 그룹”으로 관리하되, 내부 시나리오는 baseline과 targeted gate flow로 분해한다.

### 7. Automation Boundary for Provider Smoke

정기 smoke는 사람이 Codex에 직접 타이핑하는 UX를 그대로 흉내 내지 않는다. 대신 runner가 아래 흐름을 자동화한다.

1. 격리 attached env 준비
2. test thread/protocol/participants 생성
3. Codex `app-server` 세션 attach
4. 고정 initial prompt 주입
5. `btwin` observable state 검증

검증 기준은 자유 텍스트 품질이 아니라 아래와 같은 상태 변화다.

- message 생성 여부
- contribution 생성 여부
- phase 전이 여부
- workflow event 기록 여부
- runtime metadata 기록 여부

실제 사람이 자연어로 지시하는 acceptance UX는 별도 수동 검증 트랙으로 둔다.

### 8. Scenario Shape in Phase 1

1차에서는 새 manifest DSL을 만들지 않는다. 시나리오는 `pytest` 테스트 함수로 정의하고, 공통 fixture와 helper가 env 준비, provider attach, prompt 주입, artifact 캡처를 맡는다.

이 선택은 초기 구현 비용을 줄이면서도 충분한 구조화를 제공한다. 시나리오가 늘고 중복이 실제로 문제 되기 전까지는 manifest 레이어를 추가하지 않는다.
특히 provider smoke는 동일한 fixture를 공유하는 여러 `pytest` 시나리오로 구성하고, `scripts/run_tests.py provider-smoke --pytest-arg <test-selector>`로 특정 baseline/gate 시나리오만 선택 실행할 수 있게 한다.

## Runner Interface

초기 인터페이스 예시는 다음과 같다.

```bash
uv run python scripts/run_tests.py unit
uv run python scripts/run_tests.py integration
uv run python scripts/run_tests.py cli-smoke
uv run python scripts/run_tests.py provider-smoke
uv run python scripts/run_tests.py all
```

예상 옵션:

- `--keep-runs`
- `--artifact-root`
- `--html`
- `--provider-model`
- `--provider-surface`

단, 1차 구현은 YAGNI 원칙에 따라 필요한 최소 옵션만 연다. `provider-smoke`의 기본 모델/transport는 코드에서 고정하고, override는 꼭 필요한 경우에만 연다.

## Files Likely Involved

- Modify: `pyproject.toml`
- Create: `scripts/run_tests.py`
- Create or modify: `tests/conftest.py`
- Create: `tests/test_run_tests_script.py`
- Create: `tests/test_provider_smoke_runner.py`
- Reuse or adapt: `scripts/bootstrap_isolated_attached_env.sh`
- Reuse or adapt: existing runtime/provider smoke helpers under `tests/`

## Validation Strategy

### Fast Path

- `unit`, `integration`, `cli-smoke`는 provider 없이 반복 실행 가능해야 한다.
- runner가 marker 선택, HTML 경로, artifact metadata를 일관되게 남기는지 확인한다.

### Provider Path

- provider smoke는 로컬 인증 환경에서만 실행
- 기본 경로는 isolated attached env + `app-server` long-term + `gpt-5.4-mini`
- `requested_model`과 `effective_model`이 artifact에 기록되는지 확인
- thread/contribution/phase/workflow-event가 기대 상태로 바뀌는지 확인
- baseline 경로와 gate 경로는 같은 `provider_smoke` 그룹 안의 개별 시나리오로 검증

## Open Questions Resolved

- 사람용 리포트는 `pytest-html`을 사용한다.
- 결과는 run별로 누적 보관한다.
- 기본 보관 개수는 `30`이고 설정으로 바꿀 수 있다.
- provider smoke 자동화는 “thread 생성 + fixed initial prompt injection” 경로로 간다.
- provider smoke 기본 모델은 `gpt-5.4-mini`다.

## Recommendation

1차 구현은 얇은 runner와 공통 artifact 체계에 집중한다. public CLI 추가나 별도 manifest DSL은 미루고, 먼저 `pytest + runner + html + provider fixture` 조합으로 안정적인 기본 경로를 만든다.
