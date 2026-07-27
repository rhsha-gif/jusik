# QuantPilot — Claude Code 설치 및 설정 가이드 (한국어)

**문서:** 06_CLAUDE_SETUP_GUIDE_KO.md  
**목적:** QuantPilot 퀀트 레시피 환경을 위한 Claude Code 설치 및 활성화 방법 안내

---

## 개요

QuantPilot에서 Claude(Fable5)는 **퀀트 레시피 설계자** 역할을 합니다.

- **Codex**: 코드 구현 담당 (브로커 연결, 백테스트 실행, API 서버)
- **Claude/Fable5**: 레시피 설계 담당 (전략 명세, 리스크 매트릭스, RL 계약, Codex 핸드오프 문서)

---

## 사전 요구사항

- Windows 11
- Node.js 24+ (`node --version`으로 확인)
- Python 3.11+ (`python --version`으로 확인)

---

## 1단계: Claude Code CLI 설치

```powershell
npm install -g @anthropic-ai/claude-code
claude --version
```

---

## 2단계: 인증

```powershell
claude auth login
```

브라우저 OAuth 인증 흐름을 따릅니다.  
API 키를 저장소에 저장하지 마십시오.

---

## 3단계: 프로젝트 디렉토리로 이동

```powershell
cd "C:\Users\goyan\OneDrive\문서\코덱스\주식트레이더"
```

---

## 4단계: 설정 확인

```powershell
python -m json.tool .claude/settings.json
```

출력에 `.env`, `secrets/**`에 대한 `deny` 규칙이 포함되어야 합니다.

---

## 5단계: 첫 번째 레시피 실행

Claude Code 세션 시작:
```powershell
claude
```

슬래시 커맨드 실행:
```
/fable5-level34
```

전략 가설 예시:
> "한국 소형주 모멘텀 전략: 12-1개월 모멘텀 상위 10%이면서 이익 수정이 양수인 종목 매수, 주간 리밸런싱"

---

## 레시피 파이프라인 전체 흐름

```
1. /fable5-level34       → docs/quant_recipes/fable5_level_3_4_autopilot_recipe.md 생성
2. /review-quant-recipe  → docs/claude/recipe_review_report.md 생성
3. /write-codex-handoff  → docs/claude/codex_level_3_4_handoff_from_fable5.md 생성
4. Codex에 핸드오프 전달  → 구현 시작
```

---

## 안전 규칙 요약 (한국어)

1. Claude는 브로커 주문을 직접 실행할 수 없습니다.
2. Claude는 실제 거래 코드를 작성하지 않습니다.
3. Claude는 `.env`, `secrets/**` 등 비밀 파일에 접근하지 않습니다.
4. Claude 출력은 레시피 명세서이며, 실행 권한이 없습니다.
5. 모든 거래 행위는 Codex가 구현한 리스크 게이트, 주문 상태 머신, 승인 규칙을 통과해야 합니다.
6. RL 출력은 `target_weight_delta` 또는 `strategy_selection`만 허용됩니다.
7. 백테스트 결과는 미래 수익을 보장하지 않습니다.
8. 실거래는 기본적으로 비활성화 상태입니다 (`BrokerMode.mock`, `live_trading_enabled: false`).

---

## 지원

문제가 발생하면 다음을 확인하십시오:
- `CLAUDE.md`가 프로젝트 루트에 존재하는지
- `.claude/settings.json`이 유효한 JSON인지
- 스킬 파일이 `.claude/skills/<name>/SKILL.md` 형식인지
- `claude` 명령을 프로젝트 루트 디렉토리에서 실행하는지

---

## 중요 면책 조항

이 설정은 실거래를 활성화하지 않습니다.  
모든 거래 결정은 Codex가 구현한 안전 게이트와 사용자 승인을 통과해야 합니다.  
과거 백테스트 성과는 미래 수익을 보장하지 않습니다.
