# Engineering Guideline

Behavioral guidelines for working in this codebase. Bias toward correctness of
understanding over speed of implementation; for trivial tasks, use judgment.

## 1. Question & inspect the ask

Clarify intent before writing code — but only when it changes what you build.

- State assumptions explicitly. If an assumption has an obvious default or is
  inferable from existing conventions, state it and proceed; don't block on a
  question.
- Ask only when the answer would change the implementation. If interpretations
  genuinely diverge, surface them — don't silently pick one.
- Name real ambiguity instead of guessing; call out a simpler approach when one
  exists.
- Push back when requirements are unclear, contradictory, or over-constrained.

## 2. Build less (scope)

The smallest correct solution. Nothing speculative.

- No features beyond what was explicitly requested.
- No abstractions, configurability, or "future-proofing" for single-use code.
- Prefer straightforward logic over flexible frameworks.
- If 50 lines can replace 200, simplify aggressively.

## 3. Surgical changes (minimal diff)

Every changed line maps directly to the request.

- Touch only what the task requires; don't refactor, reformat, or "improve"
  unrelated or adjacent code, even if it's suboptimal.
- Match existing patterns and boundaries; follow established conventions and
  avoid inventing new patterns unless asked.
- Reuse existing types and utilities instead of creating new ones.
- Clean up only the orphans your change creates; never delete pre-existing dead
  code unless asked.
- Notice an unrelated issue? Mention it — don't fix it unless asked.

## 4. Test- & validation-driven

Define correctness before implementing.

- Where practical, reproduce a bug with a failing test first, then fix.
- For UI / agent / browser code that isn't unit-testable, define the observable
  success criteria up front and verify by running it.
- Validation → enumerate the invalid cases, then implement the checks.
- Refactor → tests pass before and after.
- Prefer incremental verification over large blind changes; loop until behavior
  is confirmed.

## 5. Write it flat (structure)

Favor readability and local reasoning over abstraction.

- Keep functions small (generally <40 lines) and control flow linear and
  traceable.
- Avoid deep inheritance, unnecessary layering, indirection, and magic/implicit
  behavior.
- Keep related logic close; code should be understandable without jumping across
  many files.
- Compose only when it removes real duplication.
- Comments only when the logic is non-obvious.

## 6. Run commands through uv

uv owns the environment (`.venv/`). Invoke tools via `uv run`, not `.venv/bin/python`.

- `uv run pytest`, `uv run python examples/jira_board_example.py`, `uv run ruff check`.
- `uv sync` after dependency changes. Never call `.venv/bin/python` or `pip` directly.
