# AGENTS.md

This file is the working agreement for any agent operating in this repository.

Read it before touching anything.

---

## Methodology

The standard development loop is defined in [`BUILD_PROTOCOL.md`](./BUILD_PROTOCOL.md).

The short form:

```
Branch → Understand → Plan → Approve → Implement (granular commits) → Verify → Review diff → Record → PR & Merge
```

---

## Branch and Pull Request Workflow

Every step or task MUST follow this workflow:
1. **Dedicated Branch**: Never develop directly on `master`. Every step must be executed on its own feature branch (e.g., `feat/step-15-db-and-resource-lifecycle`).
2. **Granular Commits**: Do not make a single monolithic commit at the end. Make plenty of meaningful, granular commits under the branch as progress is achieved (e.g. schema/helpers, implementation, tests, step log).
3. **Dedicated Pull Request**: Every step must have its own Pull Request (PR) opened against `master`.
4. **Merge to Master**: The step branch is only merged into `master` after full verification (`pytest`, diff inspection) is complete.

---

## Before you implement anything

1. **Read the relevant documentation.**
   - [`CONTEXT.md`](./CONTEXT.md) — project domain, constraints, non-goals.
   - [`DESIGN.md`](./DESIGN.md) — design principles and rules.
   - [`docs/steps/`](./docs/steps/) — prior step logs.
   - [`docs/adr/`](./docs/adr/) — architecture decisions.

2. **Inspect the existing repository** before making assumptions about structure, patterns, or dependencies.

3. **Investigate uncertainty before implementing.** If the right answer is unclear, look it up or ask. Do not guess and proceed.

4. **Write a numbered implementation plan** before making file changes.

5. **Wait for approval** before modifying files when operating under the Build Protocol.

---

## During implementation

- Work on one scoped task at a time.
- Do not silently expand scope.
- Prefer existing project patterns over inventing new ones.
- Prefer existing dependencies and components before introducing new ones.
- Do not introduce infrastructure there is no demonstrated need for.
- Do not modify files unrelated to the current task.
- Never expose, log, or commit secrets, credentials, or API keys.
- Preserve the project design system. See [`DESIGN.md`](./DESIGN.md).
- Use available skills and MCPs when they materially improve the task. See [`docs/agents/skills.md`](./docs/agents/skills.md) and [`docs/agents/mcps.md`](./docs/agents/mcps.md).

---

## After implementation

Actual verification is required. Saying something works is not evidence it works.

1. Run relevant tests and checks.
2. Run `git status` — confirm only expected files changed.
3. Run `git diff` — inspect every line of the diff.
4. Start the application and verify it runs, when relevant.
5. Open a browser and verify visible changes, when relevant.
6. Report exactly what was verified and how.

**Verification is not complete until you have done it, not merely written code.**

---

## Key references

| Document | Purpose |
|---|---|
| [`BUILD_PROTOCOL.md`](./BUILD_PROTOCOL.md) | Full development methodology |
| [`CONTEXT.md`](./CONTEXT.md) | Project domain and constraints |
| [`DESIGN.md`](./DESIGN.md) | Design philosophy and rules |
| [`docs/adr/`](./docs/adr/) | Architecture decisions |
| [`docs/steps/`](./docs/steps/) | Step-by-step engineering log |
| [`docs/agents/skills.md`](./docs/agents/skills.md) | Available skills |
| [`docs/agents/mcps.md`](./docs/agents/mcps.md) | Available MCPs |
