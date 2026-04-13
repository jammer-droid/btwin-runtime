---
name: bt:sync
description: Use when the user asks to sync btwin data, project repos, or set up a new device — handles cross-device git-based sync for btwin-data, emberlit, honglab-backup
---

# Cross-Device Sync

Git-based manual sync for btwin-data and project repos across devices.

## Source of Truth

`~/.btwin/SYNC.md` — 최신 동기화 가이드. 이 스킬과 충돌 시 SYNC.md를 따를 것.

## Repos

| Repo | Local Path | Remote |
|------|-----------|--------|
| btwin-data | `~/.btwin/` | jammer-droid/btwin-data (private) |
| emberlit | `~/playground/graphics/emberlit/` | jammer-droid/emberlit (private) |
| honglab-backup | `~/playground/graphics/honglab-backup/` | jammer-droid/honglab-backup (private) |

## Workflow

### Pull (start of session)

```bash
# 1. Stop btwin processes (prevents ChromaDB file lock)
# macOS: pkill -f btwin
# Windows: taskkill /F /IM btwin.exe

# 2. Pull btwin-data
cd ~/.btwin && git pull

# 3. Rebuild index
btwin indexer reconcile && btwin indexer refresh

# 4. Pull project repos
cd ~/playground/graphics/emberlit && git pull
cd ~/playground/graphics/honglab-backup && git pull

# 5. Restart btwin server
btwin serve
```

### Push (end of session)

```bash
# 1. btwin-data
cd ~/.btwin && git add -A && git commit -m "sync: <summary>" && git push

# 2. Project repos (if changed)
cd ~/playground/graphics/emberlit && git add -A && git commit -m "<msg>" && git push
cd ~/playground/graphics/honglab-backup && git add -A && git commit -m "<msg>" && git push
```

## Agent Execution

사용자가 "sync 해줘" 또는 "동기화" 요청 시:

1. **방향 확인**: pull(다른 기기에서 작업한 내용 가져오기) vs push(현재 작업 올리기)
2. **각 레포 git status 확인** — 변경사항 있는 레포만 처리
3. **pull 시**: btwin 프로세스 중지 필요 여부 확인 → pull → indexer reconcile/refresh
4. **push 시**: 각 레포 diff 확인 → 커밋 메시지 작성 → push
5. **결과 보고**: 어떤 레포가 동기화되었는지 요약

### Pre-flight Checks

```bash
# 각 레포 상태 확인 (병렬 실행)
cd ~/.btwin && git status --short && git log --oneline -1
cd ~/playground/graphics/emberlit && git status --short && git log --oneline -1
cd ~/playground/graphics/honglab-backup && git status --short && git log --oneline -1
```

## Notes

- ChromaDB index는 git에 포함되지만, pull 후 반드시 `reconcile + refresh` 실행
- `config.yaml`에 절대경로 설정 금지 — OS별 경로 차이로 동기화 깨짐
- 양쪽 기기에서 동시 편집 금지 — 항상 pull 먼저
- `agents.json` 충돌 시 `last_seen` 타임스탬프가 최신인 버전 유지
