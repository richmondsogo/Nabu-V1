# MCP Reference

This document describes the MCP (Model Context Protocol) servers available in this project and how to work with them.

---

## Environment-level MCPs

These are provided by the Antigravity environment and are available in every project without any configuration.

| MCP | Purpose |
|---|---|
| **shadcn MCP** | Browse, search, and install shadcn/ui components. Use for all UI component work. |
| **GitHub MCP** | Interact with GitHub repositories — issues, PRs, code search. |

Do not redeclare these in `.agents/mcp_config.json`. They are already present.

---

## Project-specific MCPs

Project-specific MCPs are configured in `.agents/mcp_config.json`.

That file is gitignored. The structure is documented in `.agents/mcp_config.json.example`.

When setting up a new project, copy the example file and fill in only what the project actually needs.

---

## When to add an MCP

```
Need an external capability
        ↓
Check if an existing environment MCP covers it
        ↓
Use existing MCP if appropriate
        ↓
Only add a new MCP when there is a concrete, demonstrated need
```

A new MCP is justified when:
- The capability is not available through any existing MCP.
- The project will use it concretely (not speculatively).
- The MCP is well-maintained and appropriate for the project.

Do not add MCPs "just in case." Each MCP is a dependency with maintenance and security implications.

---

## When not to add an MCP

- When an existing MCP already covers the need.
- When the task can be accomplished with standard tooling (scripts, CLI commands).
- When the project does not yet have a concrete need for the capability.
- When the MCP would require hardcoded credentials.

---

## UI component workflow

```
Need a UI component
        ↓
Inspect existing project components first
        ↓
Use shadcn MCP to find an appropriate primitive
        ↓
Install and compose it
        ↓
Follow DESIGN.md
```

---

## Security

- Never commit API keys, tokens, or credentials to `.agents/mcp_config.json` or any other file.
- Use environment variables for all sensitive values.
- The `.agents/mcp_config.json` file is gitignored for this reason.
- Prefer the minimum required permissions for any MCP.
- Review what permissions an MCP requests before installing it.
