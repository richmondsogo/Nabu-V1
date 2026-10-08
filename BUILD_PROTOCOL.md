# Build Protocol

This document defines the standard development methodology for this repository.

It is a tool for maintaining clarity and a useful history, not a bureaucratic checklist.

---

## Roles

### Planner

The Planner owns understanding and scope.

Responsibilities:
- Understand requirements fully before proposing implementation.
- Identify ambiguity and resolve it before work begins.
- Decide the scope of each step.
- Propose architecture and approach.
- Review implementation against the plan.
- Decide whether a step is complete.

### Builder

The Builder owns implementation and verification.

Responsibilities:
- Implement the scope that was approved — nothing more.
- Do not independently expand requirements.
- Run all relevant verification steps.
- Report actual results, not assumed results.

These roles can be held by the same agent within a single conversation, or split across conversations for larger steps.

---

## The standard loop

```
Understand
    ↓
Plan
    ↓
Approve
    ↓
Implement
    ↓
Verify
    ↓
Review diff
    ↓
Record
    ↓
Checkpoint
```

**Understand** — Read existing code, documentation, and constraints before forming an opinion.

**Plan** — Write a numbered plan. Surface uncertainties. Make the scope explicit.

**Approve** — Confirm the plan with the human before modifying files.

**Implement** — Execute the approved plan. One scope at a time.

**Verify** — Run tests, checks, and visual inspection. Do not skip this.

**Review diff** — Run `git diff`. Read every changed line. Confirm no unintended changes exist.

**Record** — Update or create the relevant step log in `docs/steps/`. Record discoveries and decisions.

**Checkpoint** — Commit with a clear, descriptive message.

---

## Important rules

### Planning

- Plan before implementing. An agent that starts modifying files before understanding the problem is operating incorrectly.
- One meaningful scope at a time. Scope creep accumulates silently and is hard to reverse.
- If the right answer is unclear, investigate before guessing.

### Conversations

- Fresh builder conversations are useful for major steps. Long conversations accumulate stale context.
- When restarting, read the relevant step log and `CONTEXT.md` first.

### External systems

- Investigate APIs, schemas, and external dependencies before guessing at their behaviour.
- If documentation exists, read it. Do not assume.

### Debugging

- Diagnose before fixing. Understand why something is broken before changing it.
- A fix that suppresses symptoms without resolving the cause is not a fix.

### Architecture

- Record significant decisions in `docs/adr/`.
- Use the ADR template in `docs/adr/0000-template.md`.
- Decisions that are not recorded will be rediscovered expensively later.

### Frontend / visual work

- Verify browser work visually. Do not claim visual changes are correct without looking at them.
- Use the browser-verification skill for structured visual checks.
- Follow `DESIGN.md`. Do not improvise design decisions.

### History

- Write commit messages that explain what changed and why.
- Keep the history navigable. A reader should be able to follow the evolution of the project.

### Infrastructure

- Do not create infrastructure before there is a demonstrated need.
- Speculative infrastructure creates maintenance burden without delivering value.

---

## Using fresh conversations

For substantial steps, start a fresh builder conversation and provide:

1. A link to the relevant step plan (`docs/steps/NN-step-name.md`).
2. A link to `CONTEXT.md`.
3. A link to `AGENTS.md`.
4. Any other documents specifically relevant to this step.

This keeps each conversation focused and prevents context drift.
