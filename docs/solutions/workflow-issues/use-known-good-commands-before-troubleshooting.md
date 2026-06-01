---
title: Use known-good commands before troubleshooting routine operations
date: 2026-06-01
category: workflow-issues
module: routine verification and Oracle access
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - "Running routine local pytest verification."
  - "Starting Oracle SSH, SCP, remote pytest, log, or dashboard checks."
  - "A fresh chat is about to reconstruct a command from memory."
tags: [workflow, pytest, ssh, oracle, powershell, token-safety]
---

# Use known-good commands before troubleshooting routine operations

## 1. 무슨 문제가 있었는지

Windows 로컬 pytest와 Oracle SSH 작업에서 이미 해결한 환경 문제를
다음 작업 때 다시 조사하는 일이 있었습니다. 예를 들어 pytest는
Windows 사용자 임시 폴더 권한 때문에 한 번 실패한 뒤에야 저장소 내부
임시 폴더로 바꾸었습니다.

SSH도 같은 종류의 위험이 있습니다. 이미 확정된 Oracle 주소와 키
경로가 있는데도 다른 경로나 복잡한 따옴표 조합을 먼저 시도하면,
실제 서버 문제와 명령어 모양 문제를 구분하느라 시간이 듭니다.

## 2. 왜 문제가 되었는지

재발 방지 문서가 있어도 실패한 뒤에만 찾아보면 첫 실패는 반복됩니다.
또한 여러 문서에 명령이 흩어져 있으면 새 작업자는 어느 명령부터
실행해야 하는지 다시 판단해야 합니다.

초보자 관점에서 비유하면, 매번 길을 잃은 뒤 지도를 찾는 방식입니다.
자주 가는 길이라면 출발 전에 내비게이션에 저장된 경로를 먼저
선택하는 편이 낫습니다.

## 3. 어떻게 고쳤는지

- 로컬 pytest는 루트 `conftest.py`가 자동으로 `.pytest-tmp/` 아래의
  프로세스별 폴더를 사용하게 했습니다.
- routine 명령의 첫 경로를 `docs/codex/known-good-commands.md` 한곳에
  모았습니다.
- `AGENTS.md`가 로컬 pytest와 VPS/SSH 작업 전에 이 문서를 먼저 읽도록
  안내합니다.
- SSH는 키 파일 존재 여부를 내용을 열지 않고 확인한 뒤, harmless한
  `date` 명령으로 접속부터 검증합니다.

## 4. 다음에 같은 실수를 막으려면 무엇을 확인해야 하는지

- routine 작업을 시작할 때 known-good 문서의 첫 명령을 그대로
  사용합니다.
- 기록된 명령이 실패하면 같은 목적의 변형 명령을 여러 개 만들지
  않습니다.
- 먼저 나온 오류가 권한, 파일 경로, 네트워크, 원격 서비스, 따옴표
  문제 중 무엇인지 확인합니다.
- 새로운 변형이 실제로 필요했다면 검증 후 known-good 문서 또는 관련
  상세 문서를 갱신합니다.

## 5. 이 프로젝트에서 특히 조심해야 할 점

- private key는 `ssh -i` 또는 `scp -i`의 identity file로만 사용합니다.
  내용을 열거나 출력해서는 안 됩니다.
- 대용량 runtime 로그는 전체를 열지 말고 bounded tail이나 요약부터
  확인합니다.
- pytest 기본 임시 폴더를 명시적으로 덮어쓸 때는 왜 필요한지
  설명합니다.
- known-good 명령은 문제를 숨기는 우회가 아닙니다. 이미 검증된 환경
  전제를 자동 적용하여 제품 코드 테스트에 바로 도달하게 하는
  실행 경로입니다.

## Related

- `docs/codex/known-good-commands.md`
- `docs/codex/ssh-powershell.md`
- `docs/solutions/workflow-issues/pytest-temp-permission-2026-05-26.md`
