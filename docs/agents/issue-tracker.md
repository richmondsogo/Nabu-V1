# Issue Tracking

This document describes how project issues and tasks should be tracked.

---

## The starter does not prescribe a tool

Different projects have different needs. Do not assume GitHub Issues is the right choice.

Choose the tracking mechanism that fits the project:

| Approach | When it fits |
|---|---|
| **GitHub Issues** | The project is hosted on GitHub and the team uses issues and PRs as the primary workflow surface. |
| **Linear** | A product team or multiple contributors need structured issue management with priorities and cycles. |
| **Plain files** | A solo or small project where a `TODO.md` or entries in step documents is sufficient. |
| **Jira / other** | The project is part of an organization that already has an established tracker. |

---

## Minimum expectation

Regardless of tool, the project should have a clear answer to:

- Where do bugs get reported?
- Where do feature requests live?
- How does a new contributor know what to work on?

---

## Integration with step documents

During active development, `docs/steps/` step documents can carry outstanding issues in the **Unresolved Issues** section. These should be promoted to the project tracker when they become concrete work items rather than ephemeral notes.

---

## When using GitHub Issues

Reference issue numbers in commit messages and PRs. Keep issue descriptions current as understanding evolves — a stale issue is worse than no issue.

The GitHub MCP (available in the Antigravity environment) can be used to create, read, and update issues directly from agent conversations.
