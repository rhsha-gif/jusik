# QuantPilot — MCP Setup Guide

**Document:** 03_MCP_SETUP_GUIDE.md  
**Purpose:** Instructions for safely adding MCP servers to the QuantPilot Claude Code environment

---

## What is MCP in this Context?

Model Context Protocol (MCP) servers extend Claude Code's tool access. In QuantPilot, MCP servers are used for:
- Reading academic papers and documentation (Playwright)
- Managing recipe review issues (GitHub)
- Querying paper trading results (PostgreSQL, deferred)

MCP servers must never be used to connect to live broker accounts.

---

## Current MCP Status

**Installed:** None  
**Recommended:** GitHub MCP, Playwright MCP (see below)  
**Forbidden:** Any broker API MCP, any real-money portal MCP

---

## Installing the GitHub MCP (Recommended — Priority 1)

```powershell
# From the project directory
claude mcp add github --scope project
```

When prompted:
1. Create a GitHub Personal Access Token with scopes: `repo:read`, `issues:write`
2. Do NOT grant: `repo:write`, `admin`, `delete_repo`, `workflow`
3. Store the token only in the Claude MCP credential store — never in `.env` or committed files

**Verify:**
```powershell
claude mcp list
```

**Remove:**
```powershell
claude mcp remove github
# Then revoke the PAT at: https://github.com/settings/tokens
```

---

## Installing the Playwright MCP (Recommended — Priority 2)

```powershell
claude mcp add playwright
```

No secrets required. Playwright fetches public web pages for source synthesis.

**Verify:** In a Claude session, ask "fetch the abstract of this SSRN paper: [URL]"

**Remove:**
```powershell
claude mcp remove playwright
```

---

## Configuring a Local Filesystem Context MCP

Add to `.claude/mcp.json` (create if not present):

```json
{
  "servers": {
    "quant-docs": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "docs/", "quantpilot/packages/core/schemas.py"],
      "description": "Read-only access to docs and core schemas for recipe authoring",
      "scope": "project"
    }
  }
}
```

This gives Claude fast access to recipe docs and the Pydantic schemas without requiring explicit Read tool calls.

---

## Deferred: PostgreSQL MCP (After Paper Trading Is Running)

```powershell
# Only run this after paper trading is operational and a read-only DB user exists
claude mcp add postgres --scope project
```

**Required setup before adding:**
1. Create a PostgreSQL role: `CREATE ROLE claude_reader WITH LOGIN PASSWORD '...' NOSUPERUSER NOCREATEDB NOCREATEROLE;`
2. Grant read-only: `GRANT SELECT ON ALL TABLES IN SCHEMA public TO claude_reader;`
3. Store connection string only in Claude MCP credential store
4. Verify the role cannot: INSERT, UPDATE, DELETE, or DROP

**Remove:**
```powershell
claude mcp remove postgres
# Then: DROP ROLE claude_reader;
```

---

## MCP Safety Rules

1. **Never connect a broker API as an MCP server.** This would give Claude direct order placement capability, violating Safety Rule 1.
2. **Never store MCP secrets in `.env` or committed files.** Use Claude's credential store only.
3. **Review MCP tool permissions** before approving any MCP tool call that writes to external systems.
4. **Audit MCP access logs** periodically: `claude mcp logs`
5. **Remove unused MCPs** — dormant MCP connections are unnecessary attack surface.

---

## MCP Configuration File Location

Project-scoped MCP config: `.claude/mcp.json`  
User-scoped MCP config: `~/.claude/mcp.json`

QuantPilot MCPs should be project-scoped to avoid affecting other projects.
