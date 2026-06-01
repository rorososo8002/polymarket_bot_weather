---
title: Reduce Codex context without deleting active workspace data
date: 2026-05-31
last_updated: 2026-06-01
category: workflow-issues
module: codex workspace
problem_type: workflow_issue
component: development_workflow
severity: low
applies_when:
  - "Reducing Codex token usage in an active development workspace"
  - "Large ignored folders such as runtime/, .git/, or .antigravitycli/ remain on disk"
tags: [codex, token-usage, workspace, codexignore, runtime-data, git]
---

# Reduce Codex context without deleting active workspace data

## 1. 무슨 문제가 있었는지

The active weather-bot workspace contains local operational data, Git metadata,
helper environments, runtime logs, and handoff documents. These files can be
useful, but reading too much on every task wastes tokens. The entrance guide can
also become expensive if it grows into a full engineering manual.

## 2. 왜 문제가 되었는지

Codex spends tokens on files it reads, not simply on files that exist on disk.
Deleting `.git/`, runtime data, or helper directories to save tokens can destroy
useful state. The opposite extreme is also wasteful: reading generic rules and
old chronological progress on every new chat spends tokens without improving
the next decision.

## 3. 어떻게 고쳤는지

Keep using the active workspace and control what gets read:

- Keep `AGENTS.md` as the always-on entrance guide with project-specific safety,
  handoff, testing, and failure-observability rules.
- Do not keep a separate mandatory checklist full of generic engineering
  reminders when the important rules already fit in `AGENTS.md`.
- Keep situation-specific VPS, SSH, runtime-data, and strategy rules in
  `docs/codex/`.
- Keep `docs/production-progress.md` short and current with `완료`, `진행 중`,
  `다음 작업`, and `이어받는 AI에게`.
- Add `.codexignore` patterns for `.git/`, `.antigravitycli/`, `runtime/`,
  caches, archives, logs, and generated runtime files.
- Read ignored operational files only when the task specifically needs them.
- Use bounded tails, counts, filters, and samples for large runtime data.

## 4. 다음에 같은 실수를 막으려면 무엇을 확인해야 하는지

- 새 규칙을 `AGENTS.md`에 넣기 전에 모든 작업에서 항상 읽어야 하는
  내용인지 확인합니다.
- `docs/production-progress.md`가 과거 작업 일기가 아니라 현재 인수인계
  문서인지 확인합니다.
- 대용량 파일은 전체를 열지 않고 크기, 개수, tail, 필터, 작은 샘플로
  확인합니다.
- 토큰 절약을 이유로 `.git/`, `runtime/`, `.antigravitycli/`를 삭제하지
  않습니다.

## 5. 이 프로젝트에서 특히 조심해야 할 점

- Oracle VPS 로그와 paper-trading runtime 파일은 운영 증거입니다. 크다고
  해서 삭제하면 안 됩니다.
- `paper_decisions.csv`, `paper_trades.csv`, `paper_raw_snapshots.jsonl`은
  필요한 범위만 읽습니다.
- 안전 규칙을 지나치게 줄여서 실거래 금지, SSH 키 보호, fail-closed,
  WebSocket 구독 유지 같은 프로젝트 전용 규칙을 잃으면 안 됩니다.

## Related

- [Dashboard large decision log initial scan](../performance-issues/dashboard-large-decision-log-initial-scan.md)
- [Verify remote dashboard state and entry counters](./verify-remote-dashboard-state-and-entry-counters.md)
