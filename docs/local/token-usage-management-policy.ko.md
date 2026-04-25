# Token Usage Management Policy

작성일: 2026-04-26  
대상 프로젝트: `btwin`

## 목적

`btwin`의 protocol orchestration은 agent, phase, cycle이 늘어날수록 context 전달 비용이 커질 수 있다.
따라서 token usage는 단순 참고값이 아니라, 앞으로 오케스트레이션 품질을 관리하는 핵심 운영 지표로 본다.

이 문서는 다음 기준을 고정한다.

- Codex에서 실제 token usage를 확인할 수 있는 runtime 경로
- 각 token usage 항목의 의미
- report에 우선 기록해야 하는 지표
- token 최적화 판단 기준

## Codex App-Server Token Usage Source

Codex `app-server`는 provider turn 중 다음 notification을 보낸다.

```text
thread/tokenUsage/updated
```

payload 핵심 구조:

```json
{
  "threadId": "provider-thread-id",
  "turnId": "provider-turn-id",
  "tokenUsage": {
    "last": {
      "inputTokens": 0,
      "cachedInputTokens": 0,
      "outputTokens": 0,
      "reasoningOutputTokens": 0,
      "totalTokens": 0
    },
    "total": {
      "inputTokens": 0,
      "cachedInputTokens": 0,
      "outputTokens": 0,
      "reasoningOutputTokens": 0,
      "totalTokens": 0
    },
    "modelContextWindow": 258400
  }
}
```

`btwin`은 이 notification을 actual provider token usage로 취급한다.
문자 수 기반 token 추정은 운영 지표로 사용하지 않는다.

## Field Semantics

`last`는 직전 provider turn 1회분 사용량이다. `btwin` report에서 agent 호출 단위 비용을 볼 때 우선 사용한다.

`total`은 해당 provider thread/session의 누적 사용량이다. 같은 app-server thread가 여러 turn을 이어갈 때 session 누적 비용을 볼 수 있다.

`modelContextWindow`는 현재 모델의 context window 크기다. context 압박이나 long-thread 위험을 표시할 때 사용한다.

`inputTokens`는 모델 입력으로 들어간 전체 token이다. 사용자 지시뿐 아니라 system/developer instructions, protocol context, thread history, tool 결과, runtime context가 포함될 수 있다.

`cachedInputTokens`는 `inputTokens` 중 provider cache에서 재사용된 token이다. 같은 context가 반복되어 cache hit 된 정도를 보여준다.

`outputTokens`는 모델이 생성한 일반 출력 token이다.

`reasoningOutputTokens`는 reasoning 모델의 내부 추론 출력 token이다. 사용자에게 보이는 텍스트와 별개로 resource usage를 증가시킨다.

`totalTokens`는 provider가 보고한 전체 token usage다. breakdown과 함께 보여줘야 하며, reasoning 포함 방식은 provider schema를 따른다.

## Primary Report Metrics

report는 전체 합계보다 attribution을 우선한다. 특히 다음을 기본 지표로 기록한다.

- actual total tokens: `last.totalTokens`
- actual input tokens: `last.inputTokens`
- cached input tokens: `last.cachedInputTokens`
- uncached input tokens: `last.inputTokens - last.cachedInputTokens`
- output tokens: `last.outputTokens`
- reasoning output tokens: `last.reasoningOutputTokens`
- context window: `modelContextWindow`
- tokens by agent
- tokens by phase
- tokens by cycle
- top expensive turns

## Derived Metrics

### Uncached Input Ratio

```text
uncached_input_ratio = (inputTokens - cachedInputTokens) / inputTokens
```

이 값이 높으면 매 turn 새 context를 많이 주입하고 있다는 뜻이다.
context pack 축소, phase-local contract, contribution summary, stable anchor 전략을 우선 검토한다.

### Cache Hit Ratio

```text
cache_hit_ratio = cachedInputTokens / inputTokens
```

이 값이 높으면 반복 context가 provider cache를 잘 타고 있다는 뜻이다.
단, input 자체가 너무 크면 cache hit이 좋아도 전체 비용은 여전히 높을 수 있다.

### Reasoning Ratio

```text
reasoning_ratio = reasoningOutputTokens / totalTokens
```

이 값이 높으면 문제 난이도, instruction ambiguity, 과도한 reasoning effort를 점검한다.

### Cycle Cost

```text
cycle_cost = sum(last.totalTokens for turns in the same protocol cycle)
```

review-revise loop가 늘어날 때 비용이 어디서 증가하는지 확인한다.

## Management Heuristics

초기에는 절대 기준보다 baseline 대비 변화량을 더 중요하게 본다.

- single turn spike: 동일 agent/phase 평균의 2배 이상
- phase spike: phase baseline 대비 30% 이상 증가
- uncached input spike: uncached input ratio 50% 이상
- reasoning spike: reasoning ratio 30% 이상
- cycle penalty: review-revise cycle 1회 증가분이 전체 비용의 큰 비중을 차지

## Optimization Priorities

1. Full protocol/thread dump를 피하고 current phase contract만 전달한다.
2. raw contribution body보다 tldr와 first-content-line summary를 우선 전달한다.
3. review feedback은 active feedback만 별도 유지한다.
4. phase/cycle이 커질수록 context pack을 요약하고, 필요할 때만 raw evidence를 조회한다.
5. high reasoning effort는 plan/review 등 필요한 phase에 제한하는 방향을 검토한다.
6. report에는 raw token event를 appendix에 남기되, 기본 화면은 phase/agent/cycle attribution 중심으로 보여준다.

## Report UX Requirements

HTML report의 token section은 다음을 보여줘야 한다.

- summary: total actual tokens, uncached input, cached input, output, reasoning
- agent table: calls, total, average, max turn, uncached input ratio
- phase table: calls, total, average, uncached input ratio, reasoning ratio
- cycle table: cycle별 total 및 review/revise 비용
- hotspot list: 가장 비싼 turn 3-5개와 agent/phase/source
- appendix: raw `thread/tokenUsage/updated` event excerpt

표시 문구는 `estimated`를 쓰지 않는다. 실제 app-server usage가 없으면 "No provider token usage recorded"로 표시한다.
