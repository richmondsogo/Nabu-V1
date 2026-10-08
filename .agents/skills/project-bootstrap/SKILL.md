---
name: project-bootstrap
description: >
  Initialize a real project from this starter.
  Use when setting up a new project that copies from this engineering starter.
---

# Project Bootstrap Skill

This skill helps you correctly initialize a real project from the engineering starter.

It is not a code generator. It is a structured process for establishing the foundations a project needs before implementation begins.

---

## Step 1: Understand the starter

Read these documents before doing anything else:

- `AGENTS.md` — the agent working agreement.
- `BUILD_PROTOCOL.md` — the development methodology.
- `CONTEXT.md` — the project context template (currently blank).
- `DESIGN.md` — the design philosophy.
- `docs/agents/skills.md` — available skills.
- `docs/agents/mcps.md` — available MCPs.

---

## Step 2: Identify the project type

Answer these questions before writing code:

- What is being built? (web app, API, CLI, library, data pipeline, other?)
- Who is it for?
- What problem does it solve?
- What is explicitly out of scope?

Write brief, honest answers. Vague answers will produce incorrect implementation decisions later.

---

## Step 3: Identify the required stack

Based on the project type, identify the technology stack:

- Runtime / language.
- Framework (if needed — do not assume one is needed).
- Key dependencies (only those needed for the core problem).
- Development tooling (linting, formatting, testing).

Do not add speculative dependencies. A dependency is justified when a concrete need for it exists.

---

## Step 4: Complete CONTEXT.md

Fill in `CONTEXT.md` completely before writing any application code.

Every section should be answered, even if briefly. A project with an incomplete `CONTEXT.md` will cause agents to rediscover the same context repeatedly.

Pay particular attention to:
- **Domain terms** — define words the project uses in a specific way.
- **Non-goals** — be explicit about what is not being built.
- **Constraints** — hard limits the implementation must respect.

---

## Step 5: Identify required MCPs

Check `docs/agents/mcps.md` for existing environment MCPs.

Then ask: does this project need capabilities that are not already available?

For each candidate MCP:
- Is there a concrete need for it?
- Is the capability not already available via an existing MCP or skill?
- Is the MCP well-maintained and appropriate for this project?

If yes to all three, add it to `.agents/mcp_config.json` (copy from `.agents/mcp_config.json.example`).

Never add an MCP speculatively.

---

## Step 6: Identify relevant skills

Check `docs/agents/skills.md` for available skills.

Identify which ones are relevant to this project type:
- Frontend work? → design-system, browser-verification, Anthropic frontend-design.
- TypeScript/JavaScript? → Matt Pocock skills.
- General agent work? → Antigravity built-in skills.

Identify if any project-specific recurring workflows exist that are not covered by available skills. If so, note them — they may warrant a new local skill later, but only after the pattern has been observed, not speculatively.

---

## Step 7: Update DESIGN.md (for projects with UI)

If the project has a user interface:

- Define the project's color palette (add to token definitions).
- Define the typeface.
- Define the spacing scale.
- Note any project-specific design constraints not already in the starter's DESIGN.md.

Do not remove the principles section. Add project-specific details below it.

---

## Step 8: Create the first step plan

Create `docs/steps/00-plan.md` using the template at `docs/steps/00-plan-template.md`.

This is the initial planning document. It should contain:
- What the first meaningful step of implementation is.
- The scope of that step (narrow — not "build the app").
- Any open questions that must be answered before implementation begins.

---

## Step 9: Replace the README

The starter README describes the starter, not your project. Replace it with a project-specific README once the above steps are complete.

The project README should describe:
- What the project is.
- How to set it up locally.
- How to run it.
- How to run tests.

---

## What to avoid

- Do not start writing application code before `CONTEXT.md` is complete.
- Do not add dependencies without a concrete, immediate reason.
- Do not create infrastructure (databases, deployment config, CI pipelines) before the application logic needs them.
- Do not speculate about future requirements and build for them.
