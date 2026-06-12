# QuantPilot — Reference Basis for Recipe Design

**Document:** 05_REFERENCE_BASIS.md  
**Purpose:** Canonical sources that Claude/Fable5 must consult when designing recipes  
**External verification:** Required before use in production recipes — mark any unchecked source as `[PENDING VERIFICATION]`

---

## Claude Code Official Documentation

| Resource | URL | Relevance |
|---|---|---|
| Claude Code overview | https://docs.anthropic.com/en/docs/claude-code/overview | Claude Code CLI setup, CLAUDE.md, settings |
| CLAUDE.md reference | https://docs.anthropic.com/en/docs/claude-code/memory | Project instructions format |
| Skills and subagents | https://docs.anthropic.com/en/docs/claude-code/sub-agents | `.claude/agents/`, `.claude/skills/` |
| Slash commands | https://docs.anthropic.com/en/docs/claude-code/slash-commands | `.claude/commands/` |
| MCP configuration | https://docs.anthropic.com/en/docs/claude-code/mcp | MCP server setup and safety |
| Settings and permissions | https://docs.anthropic.com/en/docs/claude-code/settings | `.claude/settings.json` allow/deny rules |

---

## Quantitative Framework References

### Microsoft Qlib
- **Purpose:** Quant research platform, alpha signal library, backtest engine, portfolio optimization
- **Repo:** https://github.com/microsoft/qlib
- **Docs:** https://qlib.readthedocs.io/en/latest/
- **Key features for QuantPilot:** `Alpha158`/`Alpha360` feature sets, LightGBM/LSTM model zoo, `TimeSeriesSplit`, `RollingTrain` walk-forward
- **Cite as:** Yang Liu et al. (2020). "Qlib: An AI-oriented Quantitative Investment Platform." arXiv:2009.11189

### vectorbt
- **Purpose:** Vectorized backtesting, signal analysis, portfolio simulation
- **Repo:** https://github.com/polakowo/vectorbt
- **Docs:** https://vectorbt.dev/
- **Key features for QuantPilot:** Fast parameter sweep, `Portfolio.from_signals()`, drawdown analysis
- **Note:** Use `vectorbt.pro` features only if license allows; document version used

### Backtrader
- **Purpose:** Event-driven backtesting, custom indicator development
- **Repo:** https://github.com/mementum/backtrader
- **Docs:** https://www.backtrader.com/docu/
- **Key features for QuantPilot:** `Cerebro`, `Strategy`, `Analyzer` classes; slippage and commission modeling

### QuantConnect LEAN
- **Purpose:** Institutional-grade backtesting engine, multi-asset, multi-frequency
- **Repo:** https://github.com/QuantConnect/Lean
- **Docs:** https://www.quantconnect.com/docs/
- **Key features for QuantPilot:** Realistic fill modeling, survivorship-bias-free universe

### NautilusTrader
- **Purpose:** High-performance algo trading platform, backtesting and live execution
- **Repo:** https://github.com/nautechsystems/nautilus_trader
- **Docs:** https://nautilustrader.io/docs/
- **Key features for QuantPilot:** Rust-core performance, event-driven architecture matching production

### PyPortfolioOpt
- **Purpose:** Portfolio optimization (Markowitz, Black-Litterman, HRP, CLA)
- **Repo:** https://github.com/robertmartin8/PyPortfolioOpt
- **Docs:** https://pyportfolioopt.readthedocs.io/
- **Key features for QuantPilot:** `EfficientFrontier`, `BlackLittermanModel`, `HRPOpt`, `risk_models`, `expected_returns`

### FinRL
- **Purpose:** Deep RL for quantitative finance
- **Repo:** https://github.com/AI4Finance-Foundation/FinRL
- **Paper:** Liu et al. (2021). "FinRL: A Deep Reinforcement Learning Library for Automated Stock Trading in Quantitative Finance." arXiv:2011.09607
- **Key features for QuantPilot:** PPO/SAC/DDPG implementations for portfolio allocation; `target_weight` action space

---

## Portfolio Theory References

### Markowitz (1952) — Mean-Variance Optimization
- Markowitz, H. (1952). "Portfolio Selection." *Journal of Finance*, 7(1), 77–91.
- Foundation of efficient frontier, covariance-based optimization
- Limitations: sensitivity to estimation error, lookback period dependence

### Black-Litterman (1990)
- Black, F., & Litterman, R. (1990). "Asset Allocation: Combining Investor Views with Market Equilibrium." Goldman Sachs Fixed Income Research.
- Blends market equilibrium weights with investor views
- Reduces Markowitz estimation error; requires explicit view specification in recipes

### Hierarchical Risk Parity (HRP)
- De Prado, M. L. (2016). "Building Diversified Portfolios that Outperform Out-of-Sample." *Journal of Portfolio Management*, 42(4), 59–69.
- Uses hierarchical clustering to avoid covariance matrix inversion
- More robust to estimation error than Markowitz; recommended for QuantPilot Level 4

---

## Backtest Validity References

### Walk-Forward Validation
- Pardo, R. (2008). *The Evaluation and Optimization of Trading Strategies* (2nd ed.). Wiley.
- Standard: ≥ 5 windows, in-sample ≥ 3× out-of-sample length

### Deflated Sharpe Ratio (DSR)
- Bailey, D. H., & López de Prado, M. (2014). "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality." *Journal of Portfolio Management*, 40(5), 94–107.
- Adjusts observed Sharpe for: non-normality, number of trials, in-sample length
- **Required for all QuantPilot recipe reviews via the `backtest-forensics` skill**

### Probability of Backtest Overfitting (PBO)
- Bailey, D. H., Borwein, J., López de Prado, M., & Zhu, Q. J. (2016). "The Probability of Backtest Overfitting." *Journal of Computational Finance*, 20(4), 39–69.
- Uses combinatorially symmetric cross-validation (CSCV)
- PBO < 0.5 is the minimum threshold for recipe approval

### Common Backtest Failure Modes
- Arnott, R., et al. (2019). "Alice's Adventures in Factorland: Three Blunders That Plague Factor Investing." *Journal of Portfolio Management*, 45(4), 18–36.
- López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley. (Chapters 7–11)

---

## Key Factor Literature for Signal Design

| Factor | Primary Reference |
|---|---|
| Momentum | Jegadeesh, N., & Titman, S. (1993). *Journal of Finance*, 48(1), 65–91 |
| Value (B/M) | Fama, E. F., & French, K. R. (1992). *Journal of Finance*, 47(2), 427–465 |
| Quality / Profitability | Novy-Marx, R. (2013). *Journal of Financial Economics*, 108(1), 1–28 |
| Low Volatility | Frazzini, A., & Pedersen, L. H. (2014). *Journal of Financial Economics*, 111(1), 1–23 |
| Trend Following | Moskowitz, T., Ooi, Y. H., & Pedersen, L. H. (2012). *Journal of Financial Economics*, 104(2), 228–250 |
| Carry | Koijen, R. S., Moskowitz, T. J., Pedersen, L. H., & Vrugt, E. B. (2018). *Journal of Financial Economics*, 127(2), 197–225 |

---

## Verification Status

All URLs above should be verified before citing in production recipes.  
Mark unverified sources as `[PENDING VERIFICATION]` in recipe YAML `sources` blocks.  
Internet access required for real-time verification — if unavailable at recipe authoring time, flag for offline check.
