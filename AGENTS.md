# AGENTS.md — FreeCADEnhanced

Instructions for AI coding agents (Claude Code, Codex, Gemini/Antigravity, …) working in this repo.
`CLAUDE.md` only imports this file; add rules here.

## Git workflow
- **Every feature → its own branch + a pull request against `main`.** Commit and push often on that
  branch.
- **Open the PR yourself when the work is pushed.** Don't end a session by suggesting one and leaving
  a pushed branch behind. (2026-09-13: four branches here sat without PRs until a sweep.)
- **One feature per branch.** Unrelated work goes on its own branch and PR, even if it came up in the
  same session. (The Freeform workbench branch also ended up carrying the unrelated RoboPrint
  workbench.)
- **Don't merge another unmerged feature branch into yours.** If you need its work, start from that
  branch, open the PR against it (not `main`), and say in the PR body which PR must merge first. When
  that one merges, bring `main` into yours and retarget to `main`. (The VR CAD branch merged in the
  still-open XR workbench branch from #1, so its PR had to be based on that branch.)
- **Before you finish, make sure the branch is up to date with `main`.** Merge or rebase `main` in,
  resolve conflicts, and re-run the tests, so the PR is mergeable as-is.
- PR body = **what / why / how to verify**.
- Don't `--no-verify`, skip hooks or force-push unless explicitly asked.
- Issues are disabled on this repo; proposals go in the relevant module's doc folder.
