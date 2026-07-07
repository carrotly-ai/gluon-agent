---
name: verify-loop-work
description: Verify a unit of work end-to-end before reporting it done or requesting loop completion. Use this in any agent-loop iteration (surveyor, executor, fixer, verifier) before finishing — a successful edit is not evidence the work is correct. Encodes the two-tier verification discipline (deterministic gate + reviewer-grade qualitative check) that keeps autonomous loops trustworthy.
---

# Verifying loop work

You are an iteration inside an autonomous agent loop. No human will review your
work before the next iteration builds on it, so **you are the reviewer**. A
clean edit, a created file, or "it looks right" is NOT evidence the work is
done. Verify the way a careful teammate would before you finish or call
`loop_complete`.

Verification has two tiers — do both when they apply:

## 1. Deterministic gate (the objective's exit code)

If the task or loop declares a `verify_cmd`, RUN IT YOURSELF and read the exit
code before finishing. Exit 0 is the bar; anything else means the work is not
done — fix the underlying cause and rerun, do not hand back partial work.

If no gate is declared, find the project's own check and run it:
- code change → run the test suite / linter / type-checker the repo already uses
  (look in `pyproject.toml`, `package.json`, `Makefile`, CI config);
- a script or CLI change → execute it on a representative input and check the
  result, not just that it ran;
- data/config change → validate/parse it.

State the command you ran and its result in your summary. "Tests pass" without
naming the command is not evidence.

## 2. Reviewer-grade qualitative check

The gate proves it doesn't break; this proves it's actually right and complete:
- Re-read the acceptance criteria in your task prompt and confirm each one is
  met — not "started", met.
- Check for collateral damage: did you change anything the task didn't ask for?
  Are there new warnings, dead code, or half-finished edits?
- For a UI change, verify it the way a user would: open the page, interact with
  the control, confirm the state change, check the browser console for new
  errors. (Use browser tooling if available.)
- Confirm your work is committed to the repository at repo-relative paths — work
  left in a stray directory or an uncommitted worktree is orphaned and will not
  integrate.

## If anything fails

Fix the underlying problem and rerun from step 1. Never report a change as
complete, and never call `loop_complete`, on partially verified work. If you
cannot make it pass, enqueue a focused follow-up task (with the failing output)
rather than claiming success.

## Encode what you learned

If verification caught a class of mistake that will recur, don't just fix this
instance — note it so the loop's next iterations (and future loops) avoid it:
tighten the task's `verify_cmd`, or add the check to this skill.
