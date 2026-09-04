# Security gate (ship mode)

The ship gate is the first of three planned security modes. It runs before a
commit when the staged diff touches an order path, a credential surface or the
research packages. The other two modes — a pre-flight check before the first
real paper-server connection (trufflehog + pip-audit, manual) and an operations
watch once a paper server emits logs — are deferred until those systems exist
(`docs/plans/2026-09-04-research-agent-teams.md`, "이월").

## When it runs

`/ship` looks for a project agent whose frontmatter has `ship_triggers:`
(`.claude/agents/qp-security-gate.md`). If any staged file matches one of those
globs, `/ship` runs the evidence script and then the agent, before committing:

| Trigger | Why |
|---|---|
| `quantpilot/packages/core/execution/**`, `packages/brokers/**`, `core/risk/**`, `core/operator/**` | order path, risk gate, kill switch, single POST authority |
| `quantpilot/services/api/**` | the only network surface; operator secret handling |
| `quantpilot/services/research_agents/**`, `quantpilot/jobs/**` | LLM runners; must stay unable to import trading code |
| `.env*`, `.mcp.json`, `.claude/**` | credential names, MCP servers, agent tool permissions |
| `tach.toml`, `pyproject.toml` | the boundary contract and the dependency set |

## Manual run

```powershell
powershell -ExecutionPolicy Bypass -File scripts\security-gate.ps1 -Staged   # staged changes
powershell -ExecutionPolicy Bypass -File scripts\security-gate.ps1           # whole worktree
```

The script writes `.security-gate\<timestamp>\{summary.json,gitleaks.json,semgrep.json,tach.txt,diff.patch}`.
It exits 1 only when a tool is missing or fails to run (fail-closed); findings
are counted in `summary.json` and judged by the agent. Tools:

- gitleaks 8.30 (`scoop install gitleaks`), run with `--redact` so no secret value lands in the report
- semgrep 1.176 and tach 0.35 in `%USERPROFILE%\.local\share\aorch-tools\.venv` (`uv pip install --python <venv>\Scripts\python.exe semgrep tach`)

Then, in a Claude Code session at the repository root, ask the `qp-security-gate`
agent to judge that directory. It answers with a `SecurityVerdict` JSON
(`verdict`, `findings[]`, `files_opened[]`).

## When it blocks

`/ship` does not commit. It reports the findings as a table and asks:
fix and re-run / commit anyway under the user's responsibility / stop. The
block criteria are listed in the agent file and come from the standing
invariants in `docs/roadmap_acceptance_matrix.md` §1.
