# Protocol DSL Guide

작성일: 2026-04-18
성격: 로컬 가이드 문서
대상 프로젝트: `btwin`
선행 문서: `docs/plans/2026-04-18-protocol-policy-design.md`

## 목적

이 문서는 `Protocol Policy Design`에서 고정한 개념을 실제 선언 포맷으로 내리기 위한 DSL 가이드 초안이다.

이번 문서의 목적은 두 가지다.

- 지금 이미 지원되는 protocol YAML 작성 규칙을 정리
- 장기적으로 `phase / gate / guard`를 에이전트가 선언/편집할 수 있도록 확장할 방향을 정리

이 문서는 구현 명세서가 아니라 authoring guide다.
즉 “지금 무엇을 적을 수 있는가”와 “나중에 무엇을 적게 만들 것인가”를 구분해서 설명한다.

## 핵심 원칙

### 1. 현재 canonical schema는 `phase + transition`이다

현재 protocol DSL의 canonical 구조는 다음에 가깝다.

- `phases`
- `transitions`
- `outcomes`
- 선택적으로 `roles`, `interaction`

정책 문서에서 `gate`라고 부르는 것은 현재 DSL에서는 주로 `transition`으로 나타난다.

즉:

- 개념: `gate`
- 현재 DSL 표기: `transition`

추가로 현재 schema는 authoring intent를 보존하기 위한 별도 선언도 받기 시작했다.

- top-level `gates`
- top-level `outcome_policies`
- phase-level `gate`
- phase-level `outcome_policy`

중요:

- 이 필드들은 현재 **authoring-only metadata**다
- runtime evaluator는 아직 계속 `transitions`와 top-level `outcomes`를 canonical 입력으로 사용한다
- 즉 authoring object를 적었다고 해서 runtime behavior가 바로 바뀌지는 않는다

### 2. `guard`는 아직 fully declarative object가 아니다

현재는 protocol author가 YAML에서 모든 guard를 직접 구현하지 않는다.

지금 상태:

- baseline system guard는 btwin runtime이 항상 적용
- protocol schema는 named `guard_sets`와 phase-level `guard_set` reference를 지원한다
- protocol은 phase/actions/procedure/decided_by/outcomes/guard_set을 통해 guard 판단에 필요한 힌트를 제공

장기 목표:

- protocol이 richer guard object나 guard preset/template까지 선언할 수 있게 하기
- baseline system guard는 계속 runtime invariant로 유지하기

### 3. HUD는 protocol-defined structure viewer다

HUD는 현재 canonical phase-cycle state가 있을 때 다음 구조를 가능한 한 그대로 보여줘야 한다.

- procedure lane
- gate lane
- 장기적으로 protocol guard reference

즉 DSL에 `alias`, `key` 같은 시각화 메타데이터를 둘 필요가 있다.

## 현재 지원되는 DSL

### Protocol skeleton

```yaml
name: review-loop
description: Simple review and revise loop
roles:
  - reviewer
  - implementer
outcomes:
  - retry
  - accept

phases:
  - name: review
    description: Review current result
    actions: [review, decide]
    procedure:
      - role: reviewer
        action: review
        alias: Review
        key: review-pass
      - role: implementer
        action: revise
        alias: Revise
        key: revise-pass
    decided_by: user

  - name: decision
    description: Finalize result
    actions: [decide]

transitions:
  - from: review
    to: review
    on: retry
    alias: Retry Gate
    key: gate-retry

  - from: review
    to: decision
    on: accept
    alias: Accept Gate
    key: gate-accept
```

## 객체별 작성 규칙

### 1. `phase`

현재 phase에서 가장 중요한 필드는:

- `name`
- `description`
- `actions`
- `procedure`
- `template`
- `decided_by`
- `cycle`

권장 규칙:

- `name`은 stable identifier여야 한다
- `description`은 사용자 설명용으로 짧게 쓴다
- `actions`는 runtime constraint와 validator가 해석할 수 있는 현재 지원 값만 쓴다
- `procedure`는 strict execution pointer가 아니라 observability/re-anchor용 구조로 본다

### 2. `procedure`

`procedure` step은 현재 다음 필드를 가진다.

- `role`
- `action`
- `guidance`
- `alias`
- `key`

권장 규칙:

- `role`은 phase 안에서 해당 절차를 대표하는 actor 이름
- `action`은 runtime/HUD에서 의미를 유지할 수 있는 stable action name
- `alias`는 HUD 표시용 사람이 읽기 좋은 이름
- `key`는 시각화/identity용 stable key

권장 예:

```yaml
procedure:
  - role: reviewer
    action: review
    alias: Review
    key: review-pass
  - role: implementer
    action: revise
    alias: Revise
    key: revise-pass
```

### 3. `gate` as `transition`

현재는 별도 `gates:` 블록보다 `transitions:`가 canonical form이다.

필드:

- `from`
- `to`
- `on`
- `alias`
- `key`

권장 규칙:

- `on`은 `outcomes`와 일치해야 한다
- same-phase loop도 명시적으로 적는다
- `alias`와 `key`는 HUD/API identity를 위해 가능한 한 적는다

예:

```yaml
transitions:
  - from: review
    to: review
    on: retry
    alias: Retry Gate
    key: gate-retry
```

authoring intent를 richer하게 남기고 싶으면 별도로 `gates:`를 함께 적을 수 있다.

예:

```yaml
gates:
  - name: review-gate
    authoring_only: true
    description: Review phase outcome routing intent
    routes:
      - outcome: retry
        target_phase: review
        alias: Retry Loop
        key: retry-loop
      - outcome: accept
        target_phase: decision
        alias: Accept Gate
        key: accept-gate
```

주의:

- 위 `gates:`는 현재 normalization 전까지 authoring 참고용 선언이다
- 실제 runtime phase advance는 여전히 `transitions:`를 기준으로 판단한다
- phase는 `gate: review-gate`처럼 이 선언을 참조할 수 있지만, 그 참조만으로 runtime transition이 생기지는 않는다

### 4. `outcomes`

`outcomes`는 protocol이 인정하는 결과 어휘다.

현재는 `transition.on`과 함께 쓰인다.

권장 규칙:

- protocol에서 허용되는 outcome vocabulary를 top-level에 모아둔다
- `transition.on`은 이 목록의 부분집합이어야 한다

예:

```yaml
outcomes:
  - retry
  - accept
  - reject
```

추가로 outcome emission intent를 authoring 레벨에서 적고 싶으면
top-level `outcome_policies:`와 phase-level `outcome_policy:`를 함께 쓸 수 있다.

예:

```yaml
outcome_policies:
  - name: review-outcomes
    authoring_only: true
    description: Who is expected to emit review outcomes
    emitters: [reviewer, user]
    actions: [decide]
    outcomes: [retry, accept]

phases:
  - name: review
    outcome_policy: review-outcomes
```

주의:

- 이것도 현재는 authoring-only metadata다
- runtime permission enforcement는 아직 이 object를 직접 소비하지 않는다
- migration 기간에는 top-level `outcomes`와 `transitions`를 계속 적어야 runtime behavior가 명확하다

### 5. `decided_by`

현재 DSL에서 outcome emission 권한을 간접적으로 표현하는 가장 가까운 필드다.

현재 지원 값:

- `user`
- `consensus`
- `vote`

주의:

- 이것은 full outcome emission policy는 아니다
- 누가 어떤 outcome을 제출 가능한지에 대한 정밀 정책은 장기적으로 별도 규칙이 필요하다

## guard 가이드

### 현재

현재 protocol author는 개별 guard object를 직접 모두 선언하지는 않지만,
named `guard_sets`와 phase-level `guard_set` reference는 현재 schema다.

현재 guard 관련 선언은 다음 두 층으로 제공된다.

- top-level `guard_sets`
- phase-level `guard_set`

그리고 phase의 다른 필드도 간접 힌트를 제공한다.

- `actions`
- `template`
- `decided_by`
- `phase_participants`

runtime은 이것과 thread 상태를 바탕으로 baseline system guard를 해석한다.

### 장기 목표

장기적으로는 현재의 `guard_sets` reference를 넘어 richer guard object까지 선언할 수 있어야 한다.

예시 방향:

```yaml
guard_sets:
  - name: review-default
    guards:
      - contribution_required
      - phase_actor_eligibility
      - direct_target_eligibility

phases:
  - name: review
    guard_set: review-default
```

중요:

- 위 예시의 `guard_sets` / `guard_set` 자체는 현재 지원 schema다
- 다만 이것이 full declarative guard object schema를 뜻하지는 않는다
- baseline system guard는 여전히 항상 적용된다

## 작성 정책

### 1. author는 먼저 phase와 gate를 명확히 적는다

guard DSL이 아직 완전하지 않더라도, protocol author는 최소한 다음을 명확히 선언해야 한다.

- 어떤 phase가 있는가
- phase 안에서 어떤 procedure가 관찰 가능한가
- 어떤 outcome이 가능한가
- 어떤 transition이 가능한가

### 2. same-phase loop는 명시적으로 적는다

retry loop는 암묵 규칙이 아니라 transition으로 적는다.

좋은 예:

```yaml
- from: review
  to: review
  on: retry
```

### 3. visual metadata는 optional이지만 강하게 권장한다

HUD/API가 protocol-defined structure viewer 역할을 하려면 다음을 가능한 한 적는 게 좋다.

- step `alias`
- step `key`
- transition `alias`
- transition `key`

### 4. role/action 이름은 재사용 가능하게 짓는다

장기적으로 recomposable building blocks를 염두에 두면:

- phase name
- procedure action
- transition outcome
- key

는 가능한 한 재사용 가능한 어휘를 쓰는 편이 좋다.

## 현재 지원 범위 vs 목표 범위

### 현재 지원 범위

- `phase`
- `procedure`
- `guard_sets`
- phase-level `guard_set`
- authoring-only `gate` / `outcome_policy` reference
- authoring-only `gates`
- authoring-only `outcome_policies`
- `transition`
- `outcomes`
- `decided_by`
- `alias` / `key`

### 목표 범위

- richer declarative guard object / preset schema
- explicit outcome emission policy
- recomposable protocol building blocks

## 후속 구현 작업

이 가이드를 실제 제품 기능으로 만들려면 후속 구현 계획에서 최소한 다음이 필요하다.

- system guard를 named protocol guard로 분리
- baseline system guard와 protocol guard를 evaluator에서 분리
- validator가 guard reference를 읽도록 확장
- HUD/API가 guard reference를 surface에 포함
- `btwin protocol create/edit`가 이 DSL을 편집할 수 있게 확장

## 한 줄 요약

현재 protocol DSL의 중심은 `phase + procedure + transition`이며, `gate`는 현재 `transition`으로 표현된다. `guard_sets`와 phase-level `guard_set`은 이미 current schema이고, richer `gate` / outcome-policy object는 아직 normalization 전 authoring-only metadata다.
