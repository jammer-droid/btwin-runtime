# env.sh Test Helper Design

작성일: 2026-04-15
대상 프로젝트: `btwin-runtime`
성격: 로컬 설계 메모

## 목적

격리 attached 테스트 환경을 실제로 써보는 사용자가 너무 많은 절차를 외우지 않게 한다.

현재는:

1. bootstrap 실행
2. `source env.sh`
3. `btwin serve-api --port ...`
4. 다른 셸에서 다시 `source env.sh`
5. `btwin hud`

처럼 여러 단계를 기억해야 한다.

이번 작업의 목표는 `env.sh`를 source한 뒤 테스트 실행을 helper command 수준까지 단순화하는 것이다.

예상 UX:

```bash
source /tmp/btwin-activation-smoke/env.sh
btwin_test_up
btwin_test_hud
```

## 비목표

이번 작업에서 하지 않는 것:

- HUD 스펙 변경
- runtime selection의 자동 감지
- 글로벌 `~/.btwin` 환경 수정
- global `serve-api` / launchd 프로세스 제어
- shell profile(`~/.zshrc` 등) 수정

## 현재 제약

이미 구현된 `env.sh`는 아래까지만 해준다.

- `BTWIN_CONFIG_PATH`
- `BTWIN_DATA_DIR`
- `BTWIN_API_URL`
- repo `.venv/bin` guarded `PATH` prepend

즉 activation은 됐지만, operator는 여전히 API lifecycle과 HUD 진입을 직접 기억해야 한다.

## 고려한 접근

### 1. `env.sh` 안에 helper function을 같이 생성한다

`bootstrap_isolated_attached_env.sh`가 생성하는 `env.sh` 안에 테스트용 helper function을 같이 포함한다.

예:

- `btwin_test_up`
- `btwin_test_hud`
- `btwin_test_status`
- `btwin_test_down`

장점:

- 사용자가 이미 source하는 파일 하나만 기억하면 된다.
- 현재 activation model을 그대로 유지한다.
- 글로벌 환경과 테스트 환경의 경계가 분명하다.
- 구현 범위가 작다.

단점:

- `env.sh`가 조금 길어진다.

### 2. 별도 `enter.sh`를 생성한다

`env.sh`는 순수 activation만 두고, helper는 `enter.sh`에 넣는다.

장점:

- 파일 역할 분리가 깔끔하다.

단점:

- 사용자가 기억해야 하는 파일이 하나 더 생긴다.
- 지금 요청인 “테스트 자체를 간편하게”에는 1번보다 덜 직접적이다.

### 3. wrapper 실행 파일을 생성한다

예:

```bash
.btwin-attached-test/btwin-test up
.btwin-attached-test/btwin-test hud
```

장점:

- source 없이도 일부 흐름을 감쌀 수 있다.

단점:

- shell-local activation model과 덜 자연스럽다.
- 결국 wrapper 이름을 또 기억해야 한다.

## 선택한 접근

1번을 채택한다.

즉 `env.sh`를 계속 activation entrypoint로 유지하되, 그 안에 테스트 helper function을 같이 생성한다.

이 선택의 이유:

- 지금 이미 사용자가 `source env.sh`를 해야 한다.
- 같은 파일 안에 helper를 두면 진입점이 늘어나지 않는다.
- 글로벌과 테스트 환경이 혼동되지 않는다.

## 제안 helper

### `btwin_test_up`

역할:

- 현재 `BTWIN_API_URL`만 대상으로 health check를 한다.
- 그 URL이 이미 살아 있으면 재사용한다.
- 안 살아 있으면 이 격리 env의 `btwin serve-api --port ...`를 백그라운드로 띄운다.
- PID file은 이 격리 root의 `serve-api.pid`만 사용한다.

중요 제약:

- 글로벌 `btwin serve-api`는 건드리지 않는다.
- 다른 포트에서 돌고 있는 글로벌 서버는 무시한다.
- 같은 포트가 다른 프로세스에 점유돼 있으면 kill하지 않고 에러를 보여준다.

### `btwin_test_hud`

역할:

- 현재 셸이 이미 source된 격리 env라는 전제에서 plain `btwin hud "$@"`를 실행한다.
- API가 살아 있지 않으면 먼저 `btwin_test_up`를 안내하거나, 선택적으로 내부에서 `btwin_test_up`를 호출할 수 있다.

이번 버전에서는 단순성과 예측 가능성을 위해 내부에서 `btwin_test_up`를 먼저 호출하는 편이 낫다.

즉:

```bash
btwin_test_hud --thread <thread_id>
```

를 치면, 해당 격리 API가 이미 있으면 재사용하고 없으면 띄운 뒤 HUD로 들어간다.

### `btwin_test_status`

역할:

- 현재 격리 root, API URL, PID file, health 상태를 짧게 보여준다.
- operator가 “지금 이 셸이 어느 환경을 보고 있지?”를 빠르게 확인하는 용도다.

### `btwin_test_down`

역할:

- 이 env가 관리하는 PID file의 프로세스만 종료한다.
- 글로벌 서버나 다른 프로세스는 종료하지 않는다.

## 동작 원칙

### 1. Shell-local activation은 그대로 유지한다

helper는 `source env.sh` 이후에만 보인다.

즉:

- source한 셸: helper 사용 가능
- source하지 않은 셸: 기존 글로벌 기본 동작

이 경계를 절대 흐리지 않는다.

### 2. Helper는 격리 `BTWIN_API_URL`만 본다

helper는 health check와 server control을 오직 현재 `BTWIN_API_URL`에 대해서만 수행한다.

이 원칙 때문에 글로벌 서버가 돌아도 충돌 없이 우회된다.

### 3. Kill보다 detect를 우선한다

helper는 다른 프로세스를 죽여서 문제를 해결하지 않는다.

예상되는 경우:

- 격리 API가 이미 떠 있음 -> 재사용
- 격리 API가 꺼져 있음 -> 새로 시작
- 같은 포트를 다른 프로세스가 점유 -> 명확한 오류

## 구현 경계

주요 수정 범위:

- `scripts/bootstrap_isolated_attached_env.sh`
- `tests/test_bootstrap_isolated_attached_env.py`
- README의 isolated testing 예시

가급적 건드리지 않을 범위:

- `btwin` Python CLI runtime resolution
- HUD 구현
- 기존 `start|status|stop` 스크립트 command surface

## 검증 기준

최소 검증은 아래를 만족해야 한다.

1. generated `env.sh`를 source하면 helper function이 보인다.
2. `btwin_test_up`는 격리 API가 없을 때만 그 env의 API를 띄운다.
3. `btwin_test_up`는 이미 살아 있는 격리 API를 재사용한다.
4. `btwin_test_hud`는 source된 셸에서 plain `btwin hud` 진입을 단순화한다.
5. `btwin_test_down`는 격리 env가 띄운 PID만 종료한다.
6. source하지 않은 셸과 글로벌 `~/.btwin`은 그대로 유지된다.

## 성공 기준

사용자는 테스트할 때 아래 정도만 기억하면 된다.

```bash
source /tmp/btwin-activation-smoke/env.sh
btwin_test_up
btwin_test_hud
```

그리고 이 흐름이 글로벌 `btwin` 운영 환경을 건드리지 않아야 한다.
