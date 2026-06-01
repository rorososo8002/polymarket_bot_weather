---
title: Use Python UTF-8 mode when validating Korean Markdown on Windows
date: 2026-06-01
category: workflow-issues
module: documentation validation
problem_type: workflow_issue
component: development_workflow
severity: low
applies_when:
  - "A Python documentation tool reads Korean UTF-8 Markdown on Windows."
  - "Validation fails with UnicodeDecodeError mentioning cp949."
tags: [python, windows, utf8, markdown, validation, documentation]
---

# Use Python UTF-8 mode when validating Korean Markdown on Windows

## 1. 무슨 문제가 있었는지

한국어가 들어 있는 `docs/solutions/` 문서를 검증할 때
`UnicodeDecodeError`가 발생했습니다.

## 2. 왜 문제가 되었는지

문서 파일은 UTF-8로 저장되어 있었지만, Windows의 Python 검증 도구가
기본 문자셋인 `cp949`로 파일을 읽으려고 했습니다. 문서가 깨진 것이
아니라 읽는 방식이 맞지 않았습니다.

## 3. 어떻게 고쳤는지

Python을 실행할 때 UTF-8 모드를 명시합니다.

```powershell
python -X utf8 scripts/validate-frontmatter.py docs/solutions/example.md
```

## 4. 다음에 같은 실수를 막으려면 무엇을 확인해야 하는지

- 한국어 Markdown 검증이 `cp949` 오류로 실패하면 파일 내용을 먼저
  의심하지 않습니다.
- 같은 명령을 `python -X utf8`로 다시 실행합니다.
- UTF-8 모드에서도 실패하면 그때 실제 문서 형식을 확인합니다.

## 5. 이 프로젝트에서 특히 조심해야 할 점

이 프로젝트의 인수인계 문서와 재발 방지 문서에는 한국어 설명이
들어갑니다. Windows에서 Python 기반 문서 도구를 실행할 때는 UTF-8
모드를 명시해야 결과를 믿을 수 있습니다.
