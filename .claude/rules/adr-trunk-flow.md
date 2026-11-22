---
paths:
  - ".github/workflows/release.yml"
  - ".github/workflows/make-magic-repro.yml"
  - "scripts/check_versions.py"
  - "scripts/merge-stacked"
  - "scripts/stack_hygiene.py"
  - "scripts/check_base.py"
  - ".claude/skills/mm-change/SKILL.md"
  - ".claude/rules/changes.md"
  - "AGENTS.md"
  - "CONTRIBUTING.md"
  - "docs/adr/0001-trunk-based-flow-and-releases.md"
---

# Trunk-based flow and releases

The git flow and release model are a recorded decision: trunk-based, `main` is the
integration point, a release is a `release/<x.y.z>` version-bump PR plus a human tag.

<trunk-based-flow>

- You are touching the flow's machinery or its docs. The **decision and rationale**
  live in `docs/adr/0001-trunk-based-flow-and-releases.md`; the **operative rules** in
  `AGENTS.md` (§ How a change reaches main, § Releases), the **procedure** in
  `.claude/skills/mm-change/SKILL.md`, the **CI / merge constraints** in
  `.claude/rules/changes.md`.
- If your change alters the flow or release *decision* (not just wording), update
  ADR 0001 **and** the operative rules in the same change — one fact, one place, kept
  in sync. Never re-describe the flow in a new doc; link to the ADR.
- Never introduce a long-lived `develop` or release-accumulation branch. Features
  package on `main`; validate a feature *combination* before `main` with a feature
  flag or a `gh stack`, never a standing branch (ADR 0001).

</trunk-based-flow>
