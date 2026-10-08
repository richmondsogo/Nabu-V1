# Engineering Starter

A reusable engineering operating system for software projects built with Antigravity.

---

## What this is

A starting point that encodes a coherent engineering workflow, agent instructions, design philosophy, documentation structure, verification discipline, and lightweight environment tooling.

It is not an application. It does not contain React, FastAPI, a database, authentication, or deployment infrastructure.

## What it is not

- A project scaffold generator.
- A component library.
- A framework.
- A collection of boilerplate to copy-paste.

## Why it exists

Starting a new project should not require reconstructing an entire engineering workflow from scratch. This starter captures the decisions, conventions, and tooling that make agent-assisted development coherent — so they travel with you into every project.

---

## Starting a project from this starter

1. Copy or clone this repository into your new project directory.
2. Delete `README.md` and replace it with a project-specific one.
3. Complete `CONTEXT.md` with project-specific domain information.
4. Update `DESIGN.md` with any project-specific design tokens or rules.
5. Work through the new project checklist below.
6. Run `scripts/bootstrap.ps1` to confirm your environment is ready.
7. Create `docs/steps/00-plan.md` using the step template.
8. Begin implementation using the Build Protocol.

### New project checklist

```
[ ] Define project context (CONTEXT.md)
[ ] Confirm technology stack
[ ] Identify required MCPs (docs/agents/mcps.md)
[ ] Identify relevant skills (docs/agents/skills.md)
[ ] Establish project-specific design details (DESIGN.md)
[ ] Create initial step plan (docs/steps/00-plan.md)
[ ] Begin implementation
[ ] Verify every meaningful step
```

Use the project-bootstrap skill (`.agents/skills/project-bootstrap/SKILL.md`) to help with this process.

---

## How the agent workflow works

The agent reads [`AGENTS.md`](./AGENTS.md) first. That document is the working agreement.

The full development methodology is in [`BUILD_PROTOCOL.md`](./BUILD_PROTOCOL.md).

The loop is:

```
Understand → Plan → Approve → Implement → Verify → Review diff → Record → Checkpoint
```

---

## Key documents

| Document | Purpose |
|---|---|
| [`AGENTS.md`](./AGENTS.md) | Agent working agreement |
| [`BUILD_PROTOCOL.md`](./BUILD_PROTOCOL.md) | Full development methodology |
| [`CONTEXT.md`](./CONTEXT.md) | Project domain, users, constraints, non-goals |
| [`DESIGN.md`](./DESIGN.md) | Design principles and rules |

---

## Project documentation

| Location | Contains |
|---|---|
| `docs/adr/` | Architecture Decision Records |
| `docs/steps/` | Step-by-step engineering log |
| `docs/agents/skills.md` | Available skills |
| `docs/agents/mcps.md` | Available MCPs |
| `docs/agents/issue-tracker.md` | Issue tracking approach |
| `docs/agents/domain.md` | Domain knowledge guidance |

---

## Skills

Skills are task-specific instruction sets for the agent.

**Environment-level skills** (provided by Antigravity, not this repository):
- Antigravity built-in skills
- Matt Pocock engineering skills
- Anthropic frontend-design skill

**Project-local skills** (in `.agents/skills/`):
- `design-system` — Keep UI work aligned with the project design system.
- `browser-verification` — Standard browser verification workflow.
- `project-bootstrap` — Initialize a real project from this starter.

See [`docs/agents/skills.md`](./docs/agents/skills.md) for the full reference.

## MCPs

MCPs provide external capabilities to the agent.

**Environment-level MCPs** (already available):
- shadcn MCP
- GitHub MCP

**Project-specific MCPs** are documented in `.agents/mcp_config.json` (not committed — see `.agents/mcp_config.json.example`).

See [`docs/agents/mcps.md`](./docs/agents/mcps.md) for the full reference.

---

## Design

Design principles are in [`DESIGN.md`](./DESIGN.md).

The short version: modern, minimal, calm, content-first. Prefer shadcn-style interfaces. Centralize tokens. Avoid visual noise.

Visual changes must be verified in a browser before a step is complete.

---

## Verification

Verification is required at every meaningful step.

The standard workflow:
1. Run relevant tests and checks.
2. `git status` — confirm only expected files changed.
3. `git diff` — inspect the diff line by line.
4. Run the application (when relevant).
5. Verify in a browser (when relevant).
6. Report what was actually checked.

`scripts/verify.ps1` is the conventional entry point. Real projects customize it for their stack.

---

## Scripts

| Script | Purpose |
|---|---|
| `scripts/bootstrap.ps1` | Environment inspection and setup guidance |
| `scripts/check-env.ps1` | Quick environment health check |
| `scripts/verify.ps1` | Project verification entry point |
