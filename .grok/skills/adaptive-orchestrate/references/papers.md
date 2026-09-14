# Scholarly evidence

Load paper-lookup for general literature work. Record verifiable title, authors, year, DOI/arXiv ID and the source actually consulted. Distinguish discovery metadata from verified full-text findings and never invent citation counts or relations. A small search needs no fixed researcher/reviewer pipeline.

When a task specifically requires the paper-researcher MCP workflow, select that role. `src/role-agent.js` supplies the checked-in server and enumerated tools only for that role: Claude receives `--mcp-config --strict-mcp-config`, Codex receives configuration overrides. Sci-Hub is never granted. If the required server is unavailable, report blocked; do not silently substitute another evidence channel. Other scholarly tasks may use official publisher, repository and API sources as allowed by their selected skill.

Use independent evidence review when uncertain claims affect the conclusion. `examples/plan-paper-search.json` is an optional example. Citation counts and search coverage depend on the live provider response; zero or missing data is not proof of no citations.
