# Research agents: market, investment, strategist and designer teams, security gate

Read-only research for the owner. Nothing here is a trading input: the
package `quantpilot/services/research_agents/` cannot import execution,
operator, signal, portfolio, risk, broker, harness or API modules
(`tach.toml`, `quantpilot/tests/unit/test_research_agents_boundary.py`,
acceptance matrix §1 "Research isolation"). Candidates are proposed by the
machine and decided by the human with `/invest-judge`; they never enter the
approval pool.

Plan and decision logs: `docs/plans/2026-09-04-research-agent-teams.md` (market, investment, gate) and
`docs/plans/2026-09-14-strategist-designer-teams.md` (strategist, designer). The designer package
`quantpilot/services/strategy_design/` carries the same import boundary plus read access to the backtest engine.

## Teams

| Agent (`.claude/agents/`) | Input | Output | Model |
|---|---|---|---|
| `qp-market-price-flow-analyst` | evidence `snapshot` | 5 sections: index, sector rotation, investor flows, watchlist outliers, things to verify | `QUANTPILOT_RESEARCH_MODEL` (opus) |
| `qp-market-macro-news-analyst` | evidence `news` + 3-line snapshot summary | 4 sections: headline clusters with A/B/C source grade, macro variables, watchlist-related, unconfirmed | opus |
| `qp-market-editor` | both analysts | JSON `{slack_text (≤12 lines), note_markdown (+ ## 출처)}` | opus |
| `qp-invest-theme-scout` | theme sentence, last 5 market notes, evidence, watchlist | JSON `candidates[]` (≤5: symbol, name, why, vault citations, news ids) | opus |
| `qp-invest-stock-researcher` | one candidate, its snapshot row and headlines, code-computed base rates | the ledger's 7 sections as a draft (every forecast has a deadline and a resolution source) | opus |
| `qp-invest-refuter` | researcher draft | support / surviving refutations (3 lenses) / rejected refutations / blind spots / draft defects | `QUANTPILOT_RESEARCH_JUDGE_MODEL` (fable) |
| `qp-invest-portfolio-direction` | open decision records (frontmatter + 무효화 조건), last 5 market notes, today's candidates | conflicts, concentration, do-not-touch, things to verify | opus |
| `qp-strat-macro-regime` | code-computed `macro` evidence (ECOS/FRED series stats, growth×inflation quadrant, GPR) | regime call, rates/FX/liquidity table, unverified/missing, things to verify — observations only | opus |
| `qp-strat-geopolitics` | GPR summary + macro headlines | GPR reading, issue clusters with a named Korean transmission path and source grade, unconfirmed | opus |
| `qp-strat-scenario-writer` | both strategist analyses + open decision ids | JSON: 2–4 scenarios (probability, precedents, one observable change factor, dated question + resolution source, invalidation id/threshold/cadence/source) | opus |
| `qp-strat-refuter` | the two analyses only — **never the scenarios** | arguments that break (3 lenses), rejected refutations, blind spots, base-rate objections, open questions | fable |
| `qp-strat-editor` | everything above + evidence path | JSON `{slack_text (≤12 lines), note_markdown}` with a `## 반증 대조` section mapping refutations to scenarios | opus |
| `qp-design-market-structure` | code-computed `market_structure` evidence (breadth, vol regime, correlation, sector momentum, flow z-scores, per-symbol table) | market regime, structural observations, ≤3 hypothesis candidates, sample limits | opus |
| `qp-design-strategy-designer` | hypothesis + structure analysis + rule grammar | JSON `StrategyRecipe` draft in the code-evaluable grammar, graded sources, risk matrix (absorbs `quant-recipe-architect`, `source-curator-agent`) | opus |
| `qp-design-backtest-forensics` | recipe + **code-run** backtest report (metrics, purged walk-forward, PSR/DSR/MinTRL) | findings JSON, confidence, recommended action; quotes statistics, never computes them (absorbs `backtest-forensics-agent`) | fable |
| `qp-design-risk-gate` | recipe sizing/risk blocks + backtest metrics | pass/block JSON with per-check evidence (absorbs `risk-gatekeeper-agent`) | fable |
| `qp-security-gate` | `.security-gate/<ts>/` evidence + diff | `SecurityVerdict` JSON (pass/block + findings) | fable, via `/ship` — see `docs/security_gate.md` |

Discipline shared by every agent: numbers are computed by code and quoted
verbatim; news is cited by evidence `id` and the URL in the evidence file
only; the foundation vault is consulted read-only and cited as `[[노트명]]`;
no buy/sell/sizing language; `.env` and credentials are never opened.

## Data

| Source | What | Credential | Module |
|---|---|---|---|
| Naver Finance public endpoints (`m.stock.naver.com/api`, `fchart.stock.naver.com`) | KOSPI/KOSDAQ daily bars, today's index-level investor net buying (억원), industry group changes, stock daily bars, ticker names | none | `collectors/naver_market.py` |
| Naver news search | headlines for watchlist names + 코스피/코스닥/금리/환율 | NAVER API HUB `NCP_APIGW_API_KEY_ID`/`NCP_APIGW_API_KEY` (preferred; the same pair the `naver-search` MCP uses) or legacy `NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET` | `collectors/naver_news.py` |

pykrx was audited and rejected on 2026-09-04 (KRX now requires a personal
login for everything except stock bars). DART, ECOS, KOSIS and FRED are
deferred until the brief has been read for a week.

The watchlist is `quantpilot/services/research_agents/config/watchlist.json`
(15 KRX names to start); edit it freely, the job reloads it every run.

## Jobs

Both jobs run from the repository root with the project venv and read
credentials from the process environment only.

```powershell
$py = ".\.venv\Scripts\python.exe"
& $py -m quantpilot.jobs.run_market_brief --dry-run            # collect + evidence file only
& $py -m quantpilot.jobs.run_market_brief --no-post            # + agents, nothing published
& $py -m quantpilot.jobs.run_market_brief --skip-news --no-slack   # no news credentials / no webhook yet: note only
& $py -m quantpilot.jobs.run_market_brief                      # + ledger note + Slack
& $py -m quantpilot.jobs.run_invest_research --symbol 005930   # researcher → refuter → direction → candidate note
& $py -m quantpilot.jobs.run_invest_research --theme "AI 메모리 수요" --max-candidates 3
```

Strategist and designer jobs (same venv, same credential loader):

```powershell
& $py -m quantpilot.jobs.run_macro_outlook --dry-run                 # ECOS/FRED/GPR + headlines -> evidence only
& $py -m quantpilot.jobs.run_macro_outlook --no-slack                # strategist team -> research/<date>-macro-outlook.md
& $py -m quantpilot.jobs.run_macro_outlook --list-items 722Y001      # verify an ECOS stat code (needs ECOS_API_KEY)
& $py -m quantpilot.jobs.run_strategy_design --hypothesis "..." --dry-run   # market-structure evidence only
& $py -m quantpilot.jobs.run_strategy_design --hypothesis "..."             # designer team -> draft recipe + backtest + note
```

Runbooks: `.agents/aorch/skills/qp-strategist/SKILL.md`, `.agents/aorch/skills/qp-designer/SKILL.md`
(both also show the aorch `agentId` plan shape for re-running one role). The macro outlook is
scheduled for Sunday 20:00 by `scripts/register-macro-outlook-task.ps1`; the design job is manual.
Macro series codes live in `services/research_agents/config/macro_series.json`; every ECOS code was
verified against `StatisticItemList` on 2026-09-15 (`--list-items`). GPR is an `.xls`: the `research`
extra (`xlrd`, BSD) parses it, otherwise a hand-dropped `local_data/gpr.csv` is used, otherwise the
source is skipped and named in the note. GDELT DOC (no key; one request per 8 s client-side, one
25 s back-off on HTTP 429, else the theme is noted as unavailable) supplies per-theme attention
ratios and a few C-grade articles; Manifold (no key for reads, non-commercial personal use) supplies
open binary-market probabilities that the scenario writer treats as a calibration reference only.
Install the extra with `uv pip install --python .venv/Scripts/python.exe -e ".[research]"` or
`uv pip install --python .venv/Scripts/python.exe xlrd==2.0.2`.

Exit codes: 0 ok (or a closed day), 2 collection failed, 3 an agent produced
nothing (or, for the design job, the recipe failed code validation twice), 4 publishing failed. Evidence and logs land in
`.research_agents_out/` (gitignored). The market note is written before the
Slack post, so a webhook failure still leaves the brief in the ledger; a
second run on the same day refuses to overwrite unless `--force`.

Credentials stay outside the repository. `scripts/run-with-env.ps1` loads them into the job's process only (values never printed) from `-EnvFile` (optionally `-Only KEY,KEY`), from `-FromClaudeMcp <server>` (the `env` block of an MCP server in `~/.claude.json`, e.g. `naver-search`), or from a sources list at `%USERPROFILE%\.quantpilot-research.sources` (`envfile=<path>|only=K1,K2` and `claude-mcp=<server>` lines). Put the job command after `--%` (PowerShell stop-parsing) or its own `--out-dir`/`--dry-run` flags are read as script parameters. The scheduler wrapper uses the sources list, so the scheduled task needs no user-level environment variables. The sources list routes credentials: keep it in your profile (never in the repository or OneDrive-shared folders), readable only by your account, and list only the keys each job needs (`only=`). The agent process itself never receives these variables (`runner.agent_environment`).

Scheduling: `scripts/register-market-brief-task.ps1` registers a weekday
16:10 task that runs `scripts/run-market-brief.cmd`, which loads credentials
through the sources list above.

## Environment

| Variable | Purpose | Default |
|---|---|---|
| `NCP_APIGW_API_KEY_ID`, `NCP_APIGW_API_KEY` | Naver news search, API HUB pair (preferred) | one pair required for news |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | Naver news search, legacy pair (support ends 2027-06) | |
| `QUANTPILOT_SLACK_WEBHOOK_URL` | incoming webhook (https); wins when set | one delivery path required to post |
| `SLACK_BOT_TOKEN` + `QUANTPILOT_SLACK_CHANNEL` (or `SLACK_ALLOWED_USER_ID` = DM to yourself) | bot-token path via chat.postMessage; the SecondBrain weekly bot works here | |
| `QUANTPILOT_LEDGER_ROOT` | private ledger root | `~/investment-decisions` |
| `QUANTPILOT_RESEARCH_MODEL` | analysts, editors, scout, researcher, direction, scenario writer, strategy designer | `opus` |
| `QUANTPILOT_RESEARCH_JUDGE_MODEL` | refuters, backtest forensics, risk gate, security gate | `fable` |
| `KRX_HOLIDAYS` | comma-separated closed days (shared with the data providers) | empty |
| `QUANTPILOT_RESEARCH_PARALLEL` | headless agents a pipeline stage may run at once (each boots its own MCP servers; set 1 on a memory-starved machine) | `2` |
| `ECOS_API_KEY` | Bank of Korea ECOS StatisticSearch (strategist macro series) | optional; source skipped when absent. Lives in `%USERPROFILE%\.quantpilot-macro.env`, routed by the sources list |
| `FRED_API_KEY` | FRED series/observations (strategist macro series; the classic `api.stlouisfed.org/fred` endpoint) | optional; source skipped when absent. Same env file |

## Notes in the ledger

- `~/investment-decisions/market/YYYY-MM-DD.md` — `type: market-brief`, frontmatter carries the evidence path and `signal_input: false`.
- `~/investment-decisions/candidates/YYYY-MM-DD-<symbol>.md` — `type: candidate`, `status: proposed`. Sections: the researcher's 7, then `## 반증 검토`, `## 방향 메모`, `## 출처`. Feed it to `/invest-judge`; a decision record is a new file per the ledger README, never an edit of the candidate note.

- `~/investment-decisions/research/YYYY-MM-DD-macro-outlook.md` — `type: macro-outlook`, frontmatter carries the evidence and scenarios JSON paths and the code regime label. Scenario invalidation conditions use the ledger's `ID / 임계값 / 판정 주기 / 출처` form so `invest-resolve` can score them; copy them into a new decision record, never edit the note.
- `~/investment-decisions/research/YYYY-MM-DD-strategy-<strategy_id>.md` — `type: strategy-design`, `promotion_status: draft` always; the recipe itself is `quantpilot/docs/strategy_specs/<strategy_id>.yaml` (code-pinned draft, limit orders only) and the trial count for the deflated Sharpe accumulates in `quantpilot/docs/strategy_specs/.trials.jsonl`.

Every text that leaves the machine passes `publish/scrub.py` (credential
values from the environment, bearer/`sk-` tokens, webhook URLs, long hex and
base64 runs, and any line carrying the `[봉인]` mark).

## Measurement

Week 1 (market brief): on at least 4 of 5 sessions the brief was read, and at
least once something was done because of it (watchlist edit, a follow-up
check, a candidate run). If not, fix delivery, timing or density before
adding data sources or roles.

Month 1 (investment team): at least one decision record in the ledger that
started as a candidate note.

Month 1 (strategist team, from the first Sunday run): at least 3 of 4 outlooks read, and at least one scenario invalidation condition recorded through `invest-resolve`. Month 1 (designer team): at least one draft recipe that survives forensics at `medium` or better, with the trials ledger filling; no strategy with a negative DSR is ever proposed for promotion.

First keyed macro collection: 2026-09-15 (7 ECOS + 9 FRED series, GPR through 2026-08, 18 Manifold markets; regime computed for the first time).

First real brief: 2026-09-04 22:26 (207 s, 60 headlines, Slack DM via the bot token). Week 1 starts on the next trading day.

## Deferred

- Second-tier data: dart-fss (DART key), PublicDataReader (ECOS/KOSIS/공공데이터), fredapi (FRED key) — after a week of read briefs, each through `/dependency-audit`.
- Security modes 2 and 3: pre-flight (trufflehog + pip-audit, manual, before the first real paper-server connection) and ops watch (JSON logs, heartbeat, drawdown alerts) once a paper server emits logs.
- Opaque-handle citation validator — once hallucinated citations are actually observed for two weeks.
- Investor-persona debate — only if a single refuter proves insufficient (the strategist team also uses one independent refuter, never a debate).
- ACLED is excluded (its terms forbid AI/ML use). GDELT and Manifold went live on 2026-09-15; GDELT rate limiting is the one source that can still drop out of a run.
- KRX ±30% price limits, tick size and VI in the backtest fill model — the forensics agent records their absence as an `info` finding on every recipe until then.
