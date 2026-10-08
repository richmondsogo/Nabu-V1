# Domain Knowledge

Domain knowledge belongs in `CONTEXT.md`.

This document explains why and how to keep it accurate.

---

## CONTEXT.md is the authoritative source

All project-specific domain information — terminology, actors, constraints, non-goals — lives in [`CONTEXT.md`](../../CONTEXT.md).

Agents read `CONTEXT.md` at the start of each session to orient themselves without having to rediscover context from code or conversation history.

If an agent has to ask a basic question about what a term means or what is in scope, `CONTEXT.md` is incomplete.

---

## Keeping domain knowledge current

Domain knowledge drifts when it is not maintained.

Update `CONTEXT.md` when:
- A term is introduced or redefined.
- A constraint changes.
- A non-goal becomes a goal (or vice versa).
- An important decision is made that affects how the project understands itself.

---

## Relationship to ADRs

`CONTEXT.md` captures what the project is.

`docs/adr/` captures why it became that way.

They are complementary. An important decision that changes the domain model should update `CONTEXT.md` and create (or reference) an ADR.

When in doubt: if something would help an agent working on this project for the first time understand the problem space, it belongs in `CONTEXT.md`.

---

## Anti-patterns

- **Stale terms** — a glossary that describes how the project was designed six months ago is misleading. Keep it current.
- **Missing non-goals** — if a reasonable person might assume something is in scope when it is not, state the non-goal explicitly.
- **Implicit constraints** — constraints that exist only in the original author's head will be violated by an agent working from documentation alone.
