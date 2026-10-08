# Design Philosophy

This document defines the design principles for this project.

It is not a pixel-level specification. It establishes intent and constraints.

The design system evolves at the project level. This starter provides principles, not a rigid theme.

---

## Principles

**Modern.** Use current, capable patterns. Avoid dated conventions.

**Minimal.** Every element earns its place. Default to less.

**Calm.** Interfaces should not demand attention. They should serve their user.

**Precise.** Spacing, alignment, and proportion are intentional. Approximate is not good enough.

**Content-first.** The interface frames the content, not the other way around.

**Consistent over novel.** Predictable patterns build confidence. Novelty for its own sake erodes it.

---

## Visual language

This project strongly prefers the visual language associated with high-quality shadcn-style interfaces:

- Clean, neutral foundations.
- Restrained use of color.
- Deliberate typographic hierarchy.
- Functional spacing.
- Components that look designed without appearing over-engineered.

---

## Typography

- Use a well-chosen typeface — not the browser default.
- Establish a clear typographic scale and stick to it.
- Heading hierarchy should communicate structure, not decoration.
- Body text should be readable at its intended size.
- Line length matters. Avoid lines that are too long or too short.

## Spacing

- Use a systematic spacing scale (e.g. 4px base unit).
- Be consistent. Don't mix arbitrary pixel values with scale values.
- White space is structural, not decorative.

## Color

- Establish a palette of purposeful colors.
- Use status colors semantically: error means error, success means success.
- Avoid introducing new colors to "add interest."
- Prefer neutral surfaces. Reserve accent colors for meaningful actions.

## Borders and surfaces

- Keep borders restrained. One or two weights maximum.
- Avoid excessive shadows.
- Avoid unnecessary card containers.
- Avoid excessive rounding. A slightly rounded corner is fine; pill-shaped containers are rarely appropriate in application interfaces.

---

## What to avoid

These patterns add visual complexity without adding clarity. Avoid them unless there is a specific, well-reasoned justification.

- Decorative gradients.
- Glass / frosted effects used for decoration rather than function.
- Giant hero sections in application interfaces.
- Drop shadows on every element.
- Excessive badge / pill usage.
- Unnecessary animation.
- Visual treatments added merely to make a screen appear "designed."

---

## Components

- Reuse existing project components before creating new ones.
- Prefer shadcn/Base UI primitives where appropriate.
- Compose primitives before building custom ones.
- A new component is justified only when existing primitives cannot compose into the required behaviour.

## Design tokens

- Centralize design tokens (colors, spacing, radius, typography).
- Do not hardcode values inline when a token exists.
- Changes to visual constants should flow from the token definition, not from find-and-replace.

---

## Verification

Visual changes must be verified in a real browser before a step is considered complete.

See `.agents/skills/browser-verification/SKILL.md` for the standard workflow.
