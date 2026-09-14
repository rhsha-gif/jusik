---
name: aorch-manager-map
description: 프로젝트를 최종 관리자의 눈으로 보는 지도(어떻게 돌아가나·사람이 어디서 개입하나·무엇이 돼 있고 빈칸인가·어디를 개선하나)를 aorch 태스크 하나로 만들어 아티팩트로 발행하는 런북. 사용자가 "관리자 지도", "프로젝트가 어떻게 돌아가는지 보여줘", "워크플로 점검", "빈칸 찾아줘", "운영 지도"라고 할 때 사용한다. 코드 구조·의존 그래프·핫스팟 같은 개발자 지표 요청에는 쓰지 않는다.
---

# 관리자 지도 (manager map)

지도는 렌더러가 아니라 **대조**다. 프로젝트가 이미 갖고 있는 관리자용 문서(현황판·수용 매트릭스·런북·체크리스트)를 코드(라우트·잡·상태 enum·플래그·스텁)와 맞춰 보고, 문서가 덮지 않는 곳과 문서와 코드가 어긋난 곳을 갭으로 드러낸다. 추출 스크립트·모델 파일은 두지 않는다(2026-09-03 포니테일 판정: 수십 개 노드에 파이프라인은 산출물보다 크다). Fable을 핀한 worker 태스크 하나가 아래 절차대로 읽고 곧바로 HTML을 쓴다. 반복 실행이 필요해지면 그때 존재 목록만 스크립트화한다.

## 0. 입력 확정 (오케스트레이터)

- `<project-root>`, 그리고 관리자용 문서 5종의 경로: `<status-doc>`(현황판, 영역별 ✅/🟡/❌), `<acceptance-doc>`(불변식·게이트 표, 코드 앵커 포함), `<runbook>`("한 번의 실행이 무엇을 하나" 단계 서술), `<checklists>`(안전·활성화 체크리스트). 없는 문서는 "없음"으로 적고 그 자체를 갭으로 삼는다.
- 열지 말 것: 개인 자료 폴더(볼트, `Me/`, 개인 기록). objective에 명시한다.
- 출력 디렉터리 `<out>` = `docs/manager-map/<project>/<YYYY-MM-DD>/`(추적 대상, 작음). 태스크의 allowedScope는 이 디렉터리뿐.

## 1. 플랜

`examples/plan-manager-map.json`을 복사해 치환자를 채운다. 태스크 `M1-manager-map`: agentRole `worker`(auditor 프리셋은 Write 금지), kind `writing`, complexity `critical`, `allowedProfileIds: ["claude-fable-apex"]`, verificationCommands는 `node scripts/manager-map-check.mjs <out>`. `--dry-run`으로 anthropic/fable 라우팅을 확인하고 git bash `nohup … &`로 dispatch한다(런북 `aorch-model-upgrade` §4·§6 함정 그대로).

## 2. 태스크가 하는 일 (objective 정본)

1. **존재 목록**을 기계적으로 뽑는다(인라인 명령, 스크립트 없음): 라우트 `grep -rn "@router\.\(get\|post\|put\|delete\|patch\)" <api routers dir>`, 잡 `ls <jobs dir>/*.py` + 각 파일 docstring 첫 줄, 상태 enum `grep -n "class .*(str, Enum)" <schemas>`, 안전 플래그 `grep -rn "_ENABLED\|BROKER_MODE\|DATA_MODE" <settings/gatekeeper>`, 스텁 `grep -rn "NotImplementedError\|TODO\|fixture" <src>`, UI 페이지 `ls <web pages dir>`.
2. **문서 5종을 읽는다.** 현황판의 상태 표, 수용 매트릭스의 불변식·게이트 표(코드 앵커 포함), 런북의 실행 단계, 체크리스트의 미완 항목.
3. **흐름도 1개**: 런북이 서술하는 실행 사이클을 단계별로 나열하고 각 단계에 (a) 코드 앵커 `파일:줄`, (b) 행위자 — 사람 / 시스템 / 외부(브로커·데이터·알림), (c) 통제점 — 플래그·킬스위치·승인·체크리스트, (d) 존재 ✓/✗(라우트·잡·페이지가 실제로 있는가), (e) 런북 서술과 코드 동작의 의미 일치 신뢰도 high/medium/low + 근거 한 줄. Mermaid `flowchart LR`, `subgraph` 세 개를 스윔레인(사람/시스템/외부)으로 쓴다. 노드 수는 30 이하.
4. **히트맵**: 수용 매트릭스에 게이트×상태 표가 있으면 그대로 옮긴다(재계산 금지, 출처 절 링크). 그 표에 없는 도메인(라우터 이름 기준)은 도메인 × (라우트 존재 / 잡 존재 / UI 페이지 존재) 3열 존재 체크로 채운다. 셀 클릭은 갭 목록의 해당 행으로.
5. **갭 목록** `gaps.json`(findings 7필드 + 필요 시 `confidence`): 신호 4종 — 문서–코드 불일치(현황판이 ✅인데 코드에 없음, 코드에 있는데 현황판·런북에 없음), 스텁·잠김·실서버 미검증, 흐름의 끊긴 고리(단계에 라우트·잡·페이지가 없음, API만 있고 UI 없음 또는 반대), 수동 단계(미완 체크리스트 항목, 수동 실행 잡). severity: 끊긴 고리·불일치 high, 스텁·잠김 standard, 수동 단계 low. axis: 불일치·끊긴 고리 correctness, 스텁·잠김·수동 usage. location은 `파일:줄` 또는 `문서#절`. proposal은 한 문장.
6. **HTML 1장** `index.html`: 외부 리소스 0(폰트·스크립트 없음, Mermaid는 `<pre class="mermaid">`로 두면 아티팩트 호스트가 그린다). 첫 화면 = 히트맵 + 갭 수 4종. 그 아래 흐름도, 갭 표(심각도 정렬). 존재 사실 배지(`data-kind="fact"`, ✓/✗)와 판단 배지(`data-kind="judgement"`, 신뢰도)는 모양을 다르게. 두 테마 토큰(`:root`, `prefers-color-scheme` 가드, `[data-theme]`). 개발자 지도 링크 자리 하나. doctype·html·head·body 래퍼 없이 `<title>`·`<style>`·본문만(아티팩트 규칙).
7. receipt `summary`에 흐름도 단계 수·갭 수·신뢰도 low 건수, `unresolvedRisks`에 열지 못한 문서·판단 못 한 단계.
8. **비전문가가 읽는 페이지다** (2026-09-03 1회차 판정 "가시성 부족·설명 필요·흐름도 보기 어려움"에서 되먹임): 맨 위에 "이 지도를 읽는 법"(네 질문이 어느 절에 있는지)과 용어집(게이트·Level·오퍼레이터·플래그·mock/paper/live·실행 모드·승인 티켓·킬스위치·배지 뜻). 히트맵 표마다 **한 줄 요약**("여섯 관문 중 둘만 통과")과 관문·열·도메인의 뜻을 평문 목록으로 표 앞에. 흐름도는 **두 장으로 나눈다** — 정상 경로(런북 단계 번호 + 짧은 한국어 라벨, 노드 12 이하)와 멈춤·예외·재개 — 스윔레인은 `subgraph` 대신 `classDef` 색(사람/시스템/외부)으로, 코드 앵커는 노드에 넣지 말고 대조표에만. 그림 아래 단계별 서술 7문장(각 문장에 관련 갭 링크).

## 3. 게이트와 발행 (오케스트레이터)

- `node scripts/manager-map-check.mjs <out>`: gaps.json 7필드·enum·location 형식, index.html의 mermaid 블록·런북 단계 키워드·외부 URL 0·배지 두 종류.
- HTML을 직접 읽어 흐름도 단계가 런북과 대응하는지 본다. 어긋나면 receipt를 근거로 태스크를 다시 돌리지 말고 objective의 단계 정의를 고쳐 재dispatch한다.
- Artifact 발행(favicon 🧭, 제목 `<Project> 운영 지도`). URL을 계획 문서 "실측" 절에.

## 4. 측정과 후속

- AskUserQuestion: 갭 상위 10개 중 현황판·체크리스트로 이미 알던 것이 아닌 **새 발견 수**(0~2 / 3~5 / 6~8 / 9~10) + 세 화면 판정(히트맵·흐름도·갭 목록: 그대로 / 손볼 것 / 빼도 됨).
- 새 발견 ≤2: 대조 장치를 줄이고 현황판 링크 지도로 축소. ≥3: 2차 순서 — 두 번째 흐름(권위 문서가 없는 것부터, 예: 승인 레일) → 실제 전이 표가 있는 상태 기계 → 반복 실행이 필요해지면 존재 목록 스크립트화 → 다음 프로젝트.
- 갭 적용은 `aorch-model-upgrade` §5~§6과 같다: gaps.json을 findings로 보고 AskUserQuestion 선택 → `examples/plan-model-upgrade-apply.json` 복사.

## 5. 함정

- auditor 역할은 파일을 못 쓴다. 산출물을 쓰는 태스크는 worker + 모델 핀.
- 히트맵 축을 "도메인 × 레벨"로 억지로 만들지 않는다. 레벨 개념이 없는 도메인은 존재 체크 3열이 진실이다.
- 존재 사실과 판단을 한 종류의 배지로 섞으면 갭 목록 전체의 신뢰가 떨어진다. 판단에는 반드시 근거 인용.
- dispatch가 도는 동안 워크트리의 어떤 파일도(미추적 계획 문서 포함) 편집하지 않는다. change guard가 범위 밖 변경으로 잡아 run을 실패 처리한다(2026-09-03 1회차 실측: 리드가 계획 문서 체크박스를 갱신해 실패, 산출물은 게이트 통과라 채택).
- 워커의 판단 신뢰도 low 항목은 receipt의 unresolvedRisks에 함께 나온다. 갭 목록의 low 항목은 사용자에게 보이되 '판단' 배지로 구분되므로 그대로 둔다.

<!-- aorch-generated: skill:aorch-manager-map; mode=native; edit integrations/shared/definitions.json -->
