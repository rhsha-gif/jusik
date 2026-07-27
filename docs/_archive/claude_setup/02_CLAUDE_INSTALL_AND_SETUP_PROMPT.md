# QuantPilot — Claude Code Install and Setup Prompt

**Document:** 02_CLAUDE_INSTALL_AND_SETUP_PROMPT.md  
**Purpose:** Step-by-step instructions to install Claude Code and activate the QuantPilot recipe environment

---

## Prerequisites

- Windows 11 with PowerShell
- Node.js 24+ (`node --version`)
- Python 3.11+ (`python --version`)

---

## Step 1: Install Claude Code CLI

```powershell
# Install globally via npm
npm install -g @anthropic-ai/claude-code

# Verify installation
claude --version
```

If npm install is restricted, use the local install:
```powershell
npm install @anthropic-ai/claude-code
.\node_modules\.bin\claude --version
```

---

## Step 2: Authenticate

```powershell
claude auth login
```

Follow the browser OAuth flow. No API keys need to be stored in the repository.

---

## Step 3: Navigate to the Project

```powershell
cd "C:\Users\goyan\OneDrive\문서\코덱스\주식트레이더"
```

---

## Step 4: Verify Project Instructions Are Loaded

```powershell
claude --print "What is your role in QuantPilot?"
```

Expected response should reference: "quant recipe architect", "Level 3-4 recipes", and "no broker order placement."

If Claude does not reference these, check that `CLAUDE.md` exists in the current directory.

---

## Step 5: Verify Skills Are Recognized

Start an interactive session:
```powershell
claude
```

Then type:
```
/fable5-level34
```

Claude should ask for a strategy hypothesis and recipe level.

---

## Step 6: Verify Settings Are Applied

```powershell
python -m json.tool .claude/settings.json
```

Expected output: valid JSON with `permissions.deny` containing `.env` and `secrets/**` rules.

---

## Step 7: Run the First Recipe

In the Claude Code session:
```
/fable5-level34
```

Provide hypothesis when asked, e.g.:
> "Korean small-cap momentum strategy: buy stocks in the top decile of 12-1 month momentum with positive earnings revision, weekly rebalance, daily frequency data."

The recipe will be written to:
```
docs/quant_recipes/fable5_level_3_4_autopilot_recipe.md
```

---

## Step 8: Review the Recipe

```
/review-quant-recipe
```

---

## Step 9: Write the Codex Handoff

```
/write-codex-handoff
```

Output:
```
docs/claude/codex_level_3_4_handoff_from_fable5.md
```

---

## Troubleshooting

| Issue | Resolution |
|---|---|
| `claude: command not found` | Add npm global bin to PATH or use full path `~/.local/bin/claude.exe` |
| Settings not applied | Verify `.claude/settings.json` is valid JSON (`python -m json.tool .claude/settings.json`) |
| Skills not triggering | Skills must be in `.claude/skills/<name>/SKILL.md` format |
| `CLAUDE.md not found` | Must run `claude` from the project root directory |

---

## Safety Reminder

This setup **does not enable live trading**.  
`BrokerMode` defaults to `mock`.  
`OperationReport.live_trading_enabled` is `False`.  
No broker credentials are stored or accessed during recipe design.
