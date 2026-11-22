# 0001 — Trunk-based flow and releases

**Status:** Accepted · 2026-11-22

## Context

make-magic needs one defined way for a change — and a release — to reach `main`.
The two broad shapes are **GitFlow** (long-lived `develop` + `release/*` branches that
work accumulates on) and **trunk-based** development (every change lands on `main`
directly; a release is a marker on `main`'s history). The agentic repo config was
modeled on the raelabs trunk-based exemplar, and the tooling (uv, `gh stack`) was
chosen by bake-off for that flow.

## Decision

**Trunk-based. `main` is the single integration point.**

- Every change lands on `main` through its own `<type>/<slug>` PR, green on `ci-ok`,
  squash-merged with linear history; the author never merges — a reviewer does.
- **Features package into a release by being on `main`.** They come together *on*
  `main`, continuously — not on a staging or integration branch. **There is no
  long-lived `develop` or release-accumulation branch.**
- **A release is a `release/<x.y.z>` version-bump PR**, then a human pushes the
  `v<x.y.z>` tag. It ships everything merged to `main` since the previous tag. The
  `repro` gate and the full suite run on that PR — i.e. against assembled `main` — as
  the holistic pre-release regression check.
- **To validate a set of features *together before* `main`**, use a **feature flag**
  (merge dark, enable at/after release) or a **`gh stack`** (the top of the stack is
  the combination). Never a branch that features accumulate on.
- Dependent work stacks with `gh stack` and merges via `scripts/merge-stacked`.

This record is the **why**. The **operative rules** live in `AGENTS.md`
(§ How a change reaches main, § Releases), the **procedure** in
`.claude/skills/mm-change/SKILL.md`, and the **CI / merge constraints** in
`.claude/rules/changes.md` — linked, never copied. Change them together with this
record; `.claude/rules/adr-trunk-flow.md` path-triggers the reminder.

## Alternatives rejected

- **GitFlow** (`develop` + `release/*` accumulation branches): reintroduces the
  long-lived-branch drift and merge conflicts trunk-based avoids, and contradicts the
  exemplar. The one thing it offers — an off-`main` quarantine to validate a feature
  *set* before it lands — is covered by feature flags and `gh stack` without a
  standing branch.

## Consequences

- `main` must always be releasable → enforced by `ci-ok` on every PR.
- There is no off-`main` quarantine for a feature combination → use a flag or a stack.
- A release needs no ceremony beyond a version-bump PR and a human tag push; the
  generated changelog is bounded to `previous-tag..new-tag`.
