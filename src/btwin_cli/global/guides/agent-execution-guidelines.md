# Shared Agent Execution Guidelines

These guidelines apply to all coding agents used in this repository, including Codex, Claude Code, and other MCP-capable assistants.

They are inspired by Andrej Karpathy's observations about common LLM coding failures and adapted to B-TWIN's project workflow.

## Purpose

Use these rules to reduce four common failure modes:

- hidden assumptions
- overcomplicated implementations
- unrelated edits outside the request
- weak or missing verification

These rules are behavioral defaults, not a replacement for project-specific instructions in `AGENTS.md`, `CLAUDE.md`, `README.md`, or the documents under `global/`.

## The Four Principles

### 1. Think Before Coding

Do not silently choose an interpretation when the request is ambiguous.

- State assumptions explicitly before implementing when they matter.
- Surface multiple reasonable interpretations instead of picking one quietly.
- Push back when a simpler or safer approach better matches the request.
- Stop and ask when confusion would likely cause rework or incorrect changes.

## 2. Simplicity First

Prefer the minimum change that fully solves the task.

- Do not add features, abstraction layers, or configurability that were not requested.
- Avoid speculative error handling for scenarios the code path cannot reach.
- Prefer existing patterns over introducing a new framework or helper for one use.
- Rewrite a bloated solution if a smaller direct solution is available.

## 3. Surgical Changes

Touch only what is needed for the current request.

- Do not refactor adjacent code unless the task requires it.
- Do not rewrite comments, rename symbols, or restyle files as drive-by cleanup.
- Match existing local style and structure unless the task is explicitly about changing it.
- Remove only the dead code or unused imports created by your own change.

## 4. Goal-Driven Execution

Translate requests into verifiable outcomes.

- Define what will prove the task is done before making broad changes.
- Prefer tests, focused commands, or exact checks over vague claims that something "should work."
- For bug fixes, reproduce the failure first when practical.
- For multi-step work, state the plan in terms of implementation steps and verification steps.

## How To Apply These Rules In B-TWIN

### Codex

- Treat this document as shared execution policy in addition to `AGENTS.md`.
- When a task is not narrowly local or trivial, read this document near the start of the session.
- Use it to guide implementation, code review, refactoring, and handoff summaries.

### Claude Code

- Treat this document as the shared behavioral baseline in addition to `CLAUDE.md`.
- If a local or personal Claude plugin provides similar rules, prefer the stricter rule when they conflict.

### Other agents

- Use this document as the project-local fallback when no agent-specific skill or plugin exists.
- Keep behavior aligned with these principles even if the delivery mechanism differs.

## Relationship To Existing Project Guides

- `README.md` remains the product and setup entrypoint.
- `global/guidelines.md` remains the source of truth for btwin memory and record rules.
- `global/orchestration-guidelines.md` remains the source of truth for workflow state transitions and task rules.
- This document only defines cross-agent execution behavior for coding work.

## Tradeoff

These rules intentionally bias toward correctness, restraint, and explicit verification over raw speed.

For trivial changes, use judgment. The goal is not ceremony for its own sake. The goal is to reduce expensive LLM mistakes on work that can easily drift.
