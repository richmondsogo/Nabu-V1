---
name: ask-matt
description: Ask which skill or flow fits your situation. A router over the engineering skills in this repo.
disable-model-invocation: true
---

# Ask Matt

You don't remember every skill, so ask.

A **flow** is a path through the skills. Most paths run along one **main flow**, and two **on-ramps** merge onto it. Everything else is standalone, or a vocabulary layer that runs underneath.

## The main flow: idea → ship

The route most work travels. You have an idea and want it built.

1. **`/grill-with-docs`** sharpens the idea by interview. Start here whenever you are **working in a working directory**: it's stateful, retaining what it learns in `GLOSSARY.md` and ADRs. (No working directory? Use `/grill-me` instead. Both run the same `/grilling` primitive; `grill-with-docs` is the one that leaves a paper trail.)

2. **Branch: can you settle every question in conversation?** If a question needs a runnable answer (state, business logic, a UI you have to see), detour through a **`/prototype`**:
   - Use `/prototype` to answer the question with throwaway code.
   - Reference what you learned and carry the decision forward.

3. **Branch: is this a multi-session build?**
   - **Yes** → **`/to-spec`** (turn the thread into a spec), then split it into tracer-bullet tickets, each declaring its blocking edges. Then work the tickets via **`/implement`** per ticket, clearing context between each one. Each ticket is self-contained.
   - **No** → **`/implement`** right here, in the same context window.

   Either way, the code gets built by driving **`/tdd`** (one red-green slice at a time) and closes out with **`/code-review`**, a two-axis review (Standards + Spec) of the diff.

   When the work goes up as a pull request, **`/pr`** shapes the body: the smallest visual that shows the change, before/after evidence that it works, and a one-way or two-way door call.

4. **`/retro`** closes the loop. After a build, it looks back over the session and suggests changes to the agent's **environment**, not the code: navigation pointers, automated checks, the coding standards `/code-review` enforces, steering files, tooling. Mechanical mistakes become deterministic checks; judgement calls become coding standards. The next build then starts from a better environment.

### Context hygiene

Keep steps 1–3 in **one unbroken context window** (don't compact or clear until after tickets are written) so the grilling, spec, and tickets all build on the same thinking. Each `/implement` then starts fresh, working from the ticket.

## On-ramps

A starting situation that generates work, then merges onto the main flow.

- **Bugs and requests piling up** → **`/triage`**. It moves issues through triage roles and produces agent-ready issues, which **`/implement`** later picks up.

  Triage is only for issues **you didn't create**: bug reports, incoming feature requests, anything that arrives raw.

- **Something's broken** → **`/diagnosing-bugs`**. For the hard ones: the bug that resists a first glance, the intermittent flake, the regression that crept in between two known-good states. It refuses to theorise until it has a **tight feedback loop** (one command that already goes red on *this* bug), then fixes with a regression test.

## Codebase health

Not feature work, just upkeep.

- **`/improve-codebase-architecture`** runs whenever you have a spare moment to keep the codebase good for agents to operate in. It surfaces **deepening opportunities**; picking one _generates an idea_ you can take into the main flow.

## Vocabulary layers

Two references that run *beneath* the other skills, each the single source of truth for its vocabulary:

- **`/domain-modeling`**: sharpen the project's *domain* language — challenge a fuzzy term, resolve an overloaded word, record a hard-to-reverse decision as an ADR.
- **`/codebase-design`**: the deep-module vocabulary (module, interface, depth, seam, adapter, leverage, locality) for designing a module's *shape*.

## Standalone flows

Off the main flow entirely.

- **`/prototype`**: a small, throwaway program that answers one design question.
- **`/research`**: delegate reading legwork to a **background agent** that investigates a question against primary sources, then leaves a cited Markdown file in the repo.
- **`/tdd`**: build a concrete behaviour test-first without a full spec.
- **`/code-review`**: review a branch or PR against a fixed point.
