# aorch QuantPilot collaboration adapter

사용자 지시와 루트 AGENTS.md의 안전 규칙이 우선한다. aorch가 분해·제공자 선택·위임·변경 검사·검증을 조정한다. 작은 작업은 현재 에이전트가 직접 수행한다. 독립 작업이나 문맥 분리가 유리할 때만 위임하며 최소 작업 수, 양쪽 제공자 참여, 점수표 또는 계획 파일을 항상 요구하지 않는다.

## Ownership and evidence

최초 수신자가 리드로서 범위, 검증과 통합을 책임진다. 병렬 작업은 겹치지 않는 소유 경로와 격리된 worktree를 사용한다. 기존 사용자 변경을 덮어쓰거나 정리·stash·reset·커밋하지 않는다. 커밋은 사용자 명시 요청 시에만 한다. 인계는 수정 경로, diff 또는 요청된 커밋, 정확한 검증 결과, 남은 한계와 다음 작업을 전달한다. 완료된 작업은 다시 실행하지 않는다.

## Existing workboards

사용자가 작업보드를 요청했거나 현재 미션에 보드가 있으면 workboard-flow를 적용한다. 완료된 과거 보드의 권한은 새 작업에 적용하지 않는다. 문서 lease를 확인·획득하고 다시 읽은 뒤 최소 변경만 하고 즉시 해제한다. 다른 작성자의 lease를 빼앗지 않는다. 하나의 ready 작업만 claim하고 기록된 소유 경로를 지킨다.

상태는 proposed → ready → in_progress → review → integrated → done, 필요하면 blocked다. 검토에는 실제 변경과 검증 증거가 필요하다. 커밋이 요청되지 않았으면 pending/uncommitted라고 기록하고 커밋을 만들지 않는다. 기존 보드의 integrated/done 조건에 커밋이 필요하면 그 조건을 충족했다고 표시하지 않는다. 리드만 검토된 변경을 통합하며 해당 프로젝트 필수 검증을 실행한다.

## Review and input

안전 중요 변경은 구현자와 다른 검토자가 승인한다. P0/P1 결함은 통합을 차단한다. 일반 변경은 관련 검증과 자체 검토로 완료하며 위험하거나 불확실하면 독립 검토를 추가한다. 반복 실패는 원인과 증거를 다시 확인하고 도움이 되는 경우 독립 진단을 받는다. 형식적인 교차 제공자 호출을 강제하지 않는다.

필수 Claude 전용 기능은 실행 전에 Claude로 라우팅한다. 제공자·사용량·도구 접근을 확인할 수 없으면 필요한 조치를 명시하고 중단한다. blocked.inputRequest의 질문은 현재 사용자 대화에서 받고, 답변과 현재 변경·완료 증거를 전달해 미완료 작업만 재개한다. 승인 거절은 그대로 보존한다.

## Safety and verification

실거래·시장가·자동운용은 기본 비활성, BROKER_MODE=mock이다. 비밀·계좌 정보 접근이나 기록을 금지하며 외부 connector 단위 테스트는 fake/offline fixture를 쓴다. risk gate, kill switch, idempotency, order state machine, audit, reconciliation과 리서치/거래 import 경계는 우회하지 않는다. LLM/RL은 주문을 생성·승인·제출하지 않는다. 안전 테스트를 약화하지 않는다. 구체적인 불변식과 backend·smoke·frontend 필수 명령은 AGENTS.md와 CLAUDE.md를 따른다.

기존 점수표와 작업보드 템플릿은 필요할 때 참고하는 기록 도구다. 본문 이전 버전은 이번 전역 마이그레이션 백업에서 복구할 수 있다.
