# Skills Reference

This document describes the skills available in this project.

Skills are task-specific instruction sets that guide agent behaviour for recurring workflows.

---

## Environment-level skills

These are provided by the Antigravity environment. They are available in every project without any local configuration.

Do not copy these into this repository. Do not try to recreate them locally.

| Skill | Purpose |
|---|---|
| **Antigravity built-in skills** | Core capabilities: browser automation, environment checks, verification tooling, and a growing library of domain-specific skills. |

---

## Project-local skills

These live in `.agents/skills/` and travel with the repository.

A local skill exists when there is a recurring workflow that deserves its own reusable instruction set, whether project-specific or sourced from a well-regarded external collection.

### Engineering workflow (Matt Pocock)

Sourced from [mattpocock/skills](https://github.com/mattpocock/skills). These encode a disciplined engineering loop: idea → spec → tickets → implement → review.

| Skill | Location | Purpose |
|---|---|---|
| `ask-matt` | `.agents/skills/ask-matt/SKILL.md` | Skill router. When you're unsure which skill fits, start here. |
| `implement` | `.agents/skills/implement/SKILL.md` | Implement work from a spec or ticket. Drives TDD and closes with code-review. |
| `tdd` | `.agents/skills/tdd/SKILL.md` | Red → green loop. What a good test is, where tests go, anti-patterns, loop rules. |
| `code-review` | `.agents/skills/code-review/SKILL.md` | Two-axis review (Standards + Spec) of a diff since a fixed point. |
| `to-spec` | `.agents/skills/to-spec/SKILL.md` | Turn a conversation into a spec and publish to the issue tracker. |
| `triage` | `.agents/skills/triage/SKILL.md` | Move issues through triage roles. Write agent-ready briefs. |
| `diagnosing-bugs` | `.agents/skills/diagnosing-bugs/SKILL.md` | Hard-bug diagnosis loop. Builds a tight feedback loop before theorising. |
| `prototype` | `.agents/skills/prototype/SKILL.md` | Throwaway code to answer a design question (logic or UI). |

### Frontend design (Anthropic)

Sourced from [anthropics/skills](https://github.com/anthropics/skills).

| Skill | Location | Purpose |
|---|---|---|
| `frontend-design` | `.agents/skills/frontend-design/SKILL.md` | Distinctive, intentional UI design. Covers palette, typography, layout, and copy. Avoids templated defaults. |

### Project-specific

These are specific to this starter and the workflows it encodes.

| Skill | Location | Purpose |
|---|---|---|
| `design-system` | `.agents/skills/design-system/SKILL.md` | Keep UI implementation aligned with the project design system. Grounding layer for `frontend-design`. |
| `browser-verification` | `.agents/skills/browser-verification/SKILL.md` | Repeatable workflow for verifying visual and interactive changes in a real browser. |
| `project-bootstrap` | `.agents/skills/project-bootstrap/SKILL.md` | Structured process for initializing a real project from this starter. |

---

## When to create a new local skill

Create a local skill when:
- The same multi-step workflow recurs across multiple sessions or steps.
- The workflow is specific enough to this project that a general skill would not apply.
- The workflow is not already covered by an available environment skill.

Do not create a local skill:
- As a convenience copy of an environment skill.
- Speculatively, before the workflow has actually recurred.
- To document simple conventions that belong in `AGENTS.md` or `CONTEXT.md`.

---

## How skills should work together

Skills should complement, not duplicate, each other.

Example: for a UI task, use both the `design-system` skill (project-specific token and component grounding) and the `frontend-design` skill (general technique and aesthetic direction). They operate at different levels of specificity and reinforce each other.

Example: for feature work, use `ask-matt` to orient, `to-spec` to crystallise, and `implement` + `tdd` to build. Close with `code-review`.

If two skills give contradictory instructions, the more specific one takes precedence. Project-local skills take precedence over general skills for project-specific concerns.
