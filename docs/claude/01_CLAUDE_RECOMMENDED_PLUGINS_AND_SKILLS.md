# QuantPilot — Recommended Claude Code Plugins and Skills

**Document:** 01_CLAUDE_RECOMMENDED_PLUGINS_AND_SKILLS.md  
**Purpose:** Catalogue of recommended Claude Code plugins, MCP servers, and project-local skills for the QuantPilot workflow

---

## Project-Local Skills (Installed)

These skills are installed in `.claude/skills/` and are available immediately.

| Skill | Trigger | Purpose |
|---|---|---|
| `fable5-level34-recipe` | `/fable5-level34`, "design a strategy" | Full Level 3–4 recipe pipeline |
| `quant-source-synthesis` | "find sources", "research this strategy" | Academic + practitioner source curation |
| `risk-matrix-designer` | "design risk matrix", "position sizing" | Fractional Kelly, drawdown limits, circuit breakers |
| `backtest-forensics` | "audit backtest", "look-ahead bias" | Bias audit + Deflated Sharpe + PBO check |
| `rl-contract-designer` | "rl contract", "reward function" | RL reward contract for Level 4 recipes |
| `codex-handoff-writer` | "write codex handoff", "implementation spec" | Codex task specification from finalized recipe |

## Project-Local Commands (Installed)

These commands are in `.claude/commands/` and are invoked as `/command-name`.

| Command | Purpose | Agents invoked |
|---|---|---|
| `/fable5-level34` | Design a Level 3 or 4 strategy recipe end-to-end | `quant-recipe-architect` (orchestrates all recipe skills) |
| `/review-quant-recipe` | Audit an existing recipe for bias, risk, and source quality | `backtest-forensics-agent` → `risk-gatekeeper-agent` → `source-curator-agent` |
| `/write-codex-handoff` | Convert an approved recipe into a Codex implementation task spec | `codex-handoff-writer` skill |
| `/review-level-impl` | Review a completed implementation level for safety, tests, and docs | `risk-gate-auditor` → `test-auditor` → `operator-runbook-reviewer` |

---

## Recommended MCP Servers

### Priority 1: GitHub Official MCP

**Purpose:** Read recipe PRs, create issues for recipe review findings, link handoff documents to Codex tasks  
**Install:** `claude mcp add github --scope project`  
**Required secrets:** `GITHUB_TOKEN` (read + issue-write PAT, no code-write scope)  
**Permission scope:** Read repo, create/update issues, read PR comments  
**Why safe:** No write access to production code; no broker connections  
**How to remove:** `claude mcp remove github`; revoke PAT in GitHub settings

### Priority 2: Playwright MCP

**Purpose:** Fetch academic papers (SSRN, JSTOR abstracts), practitioner research pages, Qlib/vectorbt docs for source synthesis  
**Install:** `claude mcp add playwright`  
**Required secrets:** None  
**Permission scope:** Browser automation, read-only web fetching  
**Why safe:** No credentials; no trading portals; read-only  
**How to remove:** `claude mcp remove playwright`

### Priority 3: Read-Only Filesystem Context MCP

**Purpose:** Serve `docs/` and `quantpilot/packages/core/schemas.py` as context for recipe authoring without requiring full Read tool access  
**Install:** Configure in `.claude/mcp.json` with `allowedPaths: ["docs/", "quantpilot/packages/core/schemas.py"]`  
**Required secrets:** None  
**Permission scope:** Read `docs/**`, `quantpilot/packages/core/schemas.py` only  
**Why safe:** Local files only; no network; no secrets paths  
**How to remove:** Remove entry from `.claude/mcp.json`

### Deferred: Read-Only PostgreSQL MCP

**Purpose:** Query paper trading backtest results stored in DB for forensics review  
**Status:** Deferred until paper trading is running  
**Required secrets:** Read-only DB credentials (SELECT only role)  
**Safety requirement:** Must use a dedicated read-only DB user; never the application write user  
**How to remove:** `claude mcp remove postgres`; drop the read-only DB user

---

## Explicitly Not Recommended

These MCP servers must never be connected to the QuantPilot project:

| Server | Reason |
|---|---|
| Any broker API MCP (Alpaca, IBKR, Binance, KIS) | Would allow direct order placement — violates Rule 1 |
| Any trading portal MCP | Real-money risk |
| Any write-access DB MCP | Could modify audit logs or order records |
| Any secrets manager MCP | Would expose broker credentials to the LLM context |

---

## Recommended Claude Code Extensions (IDE)

| Extension | Purpose |
|---|---|
| VS Code Claude Code extension | CLAUDE.md awareness, inline slash commands |
| JetBrains Claude Code plugin | Same, for PyCharm users |

Both extensions respect `.claude/settings.json` deny rules.

---

## Claude CLI Verification

Check Claude CLI availability:
```bash
# From the project directory
~/.local/bin/claude.exe --version
# Or if on PATH:
claude --version
```

Expected: `claude X.Y.Z` (any recent version)  
If missing: install from https://claude.ai/code
