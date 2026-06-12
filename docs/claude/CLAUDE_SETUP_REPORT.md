# QuantPilot — Claude/Fable5 Setup Report

**Report date:** 2026-06-12  
**Setup engineer:** QuantPilot Fable5 setup agent (claude-sonnet-4-6)  
**Project root:** `C:\Users\goyan\OneDrive\문서\코덱스\주식트레이더`

---

## 1. Files Created

| File | Purpose |
|---|---|
| `CLAUDE.md` | Project instructions: mission, stage order, role boundary, 11 safety rules, recipe schema, key schema references |
| `.claude/settings.json` | Permission allow/deny rules + safety env flags |
| `.claude/skills/fable5-level34-recipe/SKILL.md` | Level 3–4 recipe pipeline orchestration |
| `.claude/skills/quant-source-synthesis/SKILL.md` | Academic + practitioner source ranking |
| `.claude/skills/risk-matrix-designer/SKILL.md` | Fractional Kelly, drawdown, circuit breakers |
| `.claude/skills/backtest-forensics/SKILL.md` | Bias audit + Deflated Sharpe + PBO |
| `.claude/skills/rl-contract-designer/SKILL.md` | RL reward contract for Level 4 |
| `.claude/skills/codex-handoff-writer/SKILL.md` | Codex GIVEN/WHEN/THEN task spec |
| `.claude/agents/quant-recipe-architect.md` | Primary pipeline orchestrator |
| `.claude/agents/backtest-forensics-agent.md` | Adversarial backtest auditor |
| `.claude/agents/risk-gatekeeper-agent.md` | Risk parameter enforcer |
| `.claude/agents/rl-research-contract-agent.md` | RL contract specialist |
| `.claude/agents/source-curator-agent.md` | Research librarian |
| `.claude/commands/fable5-level34.md` | `/fable5-level34` slash command |
| `.claude/commands/review-quant-recipe.md` | `/review-quant-recipe` slash command |
| `.claude/commands/write-codex-handoff.md` | `/write-codex-handoff` slash command |
| `docs/claude/01_CLAUDE_RECOMMENDED_PLUGINS_AND_SKILLS.md` | Skill and MCP catalogue |
| `docs/claude/02_CLAUDE_INSTALL_AND_SETUP_PROMPT.md` | Step-by-step install guide |
| `docs/claude/03_MCP_SETUP_GUIDE.md` | Safe MCP server setup instructions |
| `docs/claude/04_CLAUDE_SAFETY_RULES.md` | Full text of 11 safety rules with enforcement details |
| `docs/claude/05_REFERENCE_BASIS.md` | Canonical quant framework and paper references |
| `docs/claude/06_CLAUDE_SETUP_GUIDE_KO.md` | Korean-language setup guide |
| `docs/claude/recipe_review_report.md` | Review report template (filled by `/review-quant-recipe`) |
| `docs/claude/codex_level_3_4_handoff_from_fable5.md` | Codex handoff template (filled by `/write-codex-handoff`) |
| `docs/quant_recipes/fable5_level_3_4_autopilot_recipe.md` | Recipe template (filled by `/fable5-level34`) |

---

## 2. Files Modified

| File | Change | Backup |
|---|---|---|
| `CLAUDE.md` | Enhanced with 11 safety rules, stages 0/0.5/1–4, `schemas.py` key references | `CLAUDE.md.bak` |
| `.claude/settings.json` | Added env vars (`QUANTPILOT_BROKER_MODE`, `QUANTPILOT_EXECUTION_MODE`), refined deny rules | Overwritten |

---

## 3. Backups Created

| Backup | Original | Reason |
|---|---|---|
| `CLAUDE.md.bak` | `CLAUDE.md` | Prior to merge with enhanced safety rules |

---

## 4. Validations Run

| Check | Command | Result |
|---|---|---|
| settings.json JSON validity | `python -m json.tool .claude/settings.json` | **PASS** |
| All .claude files present | `find .claude -maxdepth 4 -type f` | **PASS** — 15 files |
| docs/claude files present | `ls docs/claude/` | **PASS** — 9 files |
| docs/quant_recipes present | `ls docs/quant_recipes/` | **PASS** — 1 template file |
| CLAUDE.md present | `ls -la CLAUDE.md` | **PASS** — 7080 bytes |

---

## 5. Settings Validation Result

**settings.json: PASS**

Key allow rules: `Read(**)`, `Write(quantpilot/**)`, `Write(docs/**)`, `Bash(git *)`, `Bash(python -m pytest *)`, `Bash(make lint|test|format)`

Key deny rules:
- `Read(.env)`, `Read(.env.*)`, `Read(secrets/**)` — no secrets
- `Read(**/*.key)`, `Read(**/*.pem)`, `Read(**/*token*)`, `Read(**/*credential*)` — no credentials
- `Bash(curl *)`, `Bash(wget *)` — no arbitrary HTTP
- `Bash(pip install *)`, `Bash(npm install *)` — no unreviewed installs
- `Bash(docker run *)`, `Bash(docker exec *)` — no containers
- `Bash(python * --live *)`, `Bash(python * --broker *)` — no live invocations

Safety environment variables:
```
QUANTPILOT_LIVE_TRADING=false
QUANTPILOT_PAPER_ONLY=true
QUANTPILOT_BROKER_MODE=mock
QUANTPILOT_EXECUTION_MODE=approval_required
```

---

## 6. Claude CLI Availability

Claude CLI path: `C:\Users\goyan\.local\bin\claude.exe` (per CLAUDE.md environment config)  
PATH status: Not on current shell PATH  
**Action required:** Run `claude` as `~/.local/bin/claude.exe` from project root, or add to PATH

---

## 7. MCP / Plugin Availability

**Currently installed:** None  
**Recommended (not yet installed):**
- GitHub MCP (Priority 1) — `docs/claude/03_MCP_SETUP_GUIDE.md`
- Playwright MCP (Priority 2) — `docs/claude/03_MCP_SETUP_GUIDE.md`

**Forbidden:** Any broker API MCP, live trading portal MCP, real-money API MCP

---

## 8. Known Limitations

1. Claude CLI not on PATH — invoke as `C:\Users\goyan\.local\bin\claude.exe`
2. No MCP servers installed — source synthesis uses Claude's built-in web tools until Playwright MCP is added
3. No live backtest engine — recipe backtest protocols are design specs; Codex implements the actual runner
4. Recipe output files are templates — run the slash commands to fill them
5. RL contracts are design artifacts until Codex implements the Gymnasium/FinRL environment

---

## 9. Next Steps

1. **`/fable5-level34`** — generate Level 3–4 recipe → `docs/quant_recipes/fable5_level_3_4_autopilot_recipe.md`
2. **`/review-quant-recipe`** — forensics + risk + source review → `docs/claude/recipe_review_report.md`
3. **`/write-codex-handoff`** — Codex task spec → `docs/claude/codex_level_3_4_handoff_from_fable5.md`
4. Hand the handoff to Codex for Stage 3 implementation
5. (Optional) Install GitHub MCP and Playwright MCP

---

## Safety Status Summary

| Check | Status |
|---|---|
| Live trading enabled by this setup | **NO** |
| Broker credentials accessed | **NO** |
| Broker APIs called | **NO** |
| Fable5 direct order placement allowed | **NO** |
| `.env` readable by Claude | **NO** — deny rule active |
| `OperationReport.live_trading_enabled` default | **False** — `schemas.py:358` |
| `BrokerMode` default | **mock** — env var + schema |
| RL output type constraint | **target_weight_delta or strategy_selection only** |
| Fallback mode | **YES** — Level 2 or Level 3 approval_required |

---

**Claude/Fable5 setup completed.**  
**Live trading enabled: NO**  
**Next command: `/fable5-level34`**
