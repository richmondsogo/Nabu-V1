---
name: design-system
description: >
  Keep UI implementation aligned with the project design system.
  Use when creating or modifying any visual component, layout, or styling.
---

# Design System Skill

This skill ensures UI implementation stays coherent with the established project design system.

It complements Anthropic's frontend-design skill, which handles general frontend design patterns.
Use both together: this skill provides project-specific grounding; the frontend-design skill provides broader technique.

---

## Before touching UI

1. **Read `DESIGN.md`** — understand the project's principles, constraints, and what to avoid.

2. **Inspect existing components** — look in the project's component directory before creating anything new.
   - Is there an existing component that does what you need?
   - Can an existing component be composed to cover the case?
   - Only create a new component when existing ones genuinely cannot serve the need.

3. **Check existing design tokens** — colors, spacing, radius, typography.
   - Do not introduce new values inline when a token exists.
   - Do not invent new tokens without a clear reason.

---

## When implementing UI

4. **Prefer shadcn/Base UI primitives** — use the shadcn MCP to find and install appropriate components.
   ```
   Need a component
       ↓
   Check existing project components
       ↓
   Use shadcn MCP to find a suitable primitive
       ↓
   Install and compose it
       ↓
   Apply project tokens, not custom ad-hoc values
   ```

5. **Reuse established interaction patterns** — don't improvise hover states, focus rings, or transitions if the project already has them.

6. **Avoid unnecessary visual novelty** — do not add gradients, shadows, glass effects, or decorative flourishes unless they are already part of the design system and serve a clear purpose.

---

## After implementing UI

7. **Verify in the browser** — use the browser-verification skill (`.agents/skills/browser-verification/SKILL.md`) to confirm:
   - The component renders correctly.
   - Spacing and typography match the design system.
   - Responsive behaviour is acceptable.
   - No unintended visual regressions exist on surrounding elements.

8. **Review the diff** — inspect every line of changed CSS/styles and confirm no ad-hoc values crept in.
