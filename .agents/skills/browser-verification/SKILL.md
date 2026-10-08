---
name: browser-verification
description: >
  Repeatable browser verification workflow for visual and interactive changes.
  Use whenever a step involves UI changes, new routes, or interactive behaviour.
---

# Browser Verification Skill

Visual verification requires a real browser. Do not claim a UI change is correct without looking at it.

---

## Workflow

### 1. Start the application

Run the project's development server. Check `CONTEXT.md` or the project README for the specific command.

Common examples:
```
npm run dev
pnpm dev
python -m uvicorn app:app --reload
```

Confirm the server started without errors before proceeding.

### 2. Navigate to affected routes

Go to each route or page affected by the change. Do not only check the happy path.

Check:
- The primary affected view.
- Any adjacent views that share components with what changed.
- Any routes that link to or from the changed view.

### 3. Verify important interactions

For interactive changes:
- Click all affected buttons and controls.
- Trigger relevant state changes (loading, empty, error, success).
- Verify that hover and focus states render correctly.
- Test keyboard navigation if the interaction involves keyboard-accessible elements.

### 4. Check rendered states

For components that have multiple states, verify each one:
- Default / idle.
- Loading (if applicable).
- Empty / zero results (if applicable).
- Error state (if applicable).
- Success / complete (if applicable).

### 5. Check responsive behaviour

For layout changes, verify at relevant breakpoints:
- Desktop (1280px+).
- Tablet (768px).
- Mobile (375px or similar).

Use browser DevTools device emulation for this. Only check breakpoints relevant to the project.

### 6. Inspect computed styles when accuracy matters

For precise visual changes, open DevTools and inspect:
- Computed spacing (margin, padding).
- Applied color values.
- Font size and line-height.
- Any unexpected inherited styles.

This is especially important when confirming that design tokens are being used correctly rather than ad-hoc values.

### 7. Capture evidence where appropriate

For significant visual changes, take a screenshot using the available DevTools MCP or screenshot capability.

### 8. Report actual results

In the step log (`docs/steps/`), report:
- What was checked.
- What passed.
- What failed or looked incorrect.
- Any outstanding issues.

Do not write "verified" without describing what was actually looked at.
