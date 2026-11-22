# Architecture Decision Records — repo & process

Durable **repo-level** decisions: the git flow, release process, CI contract, and
how the repository is run. One decision lives in one record; `AGENTS.md`,
`CONTRIBUTING.md`, and the skills/rules **link** here (and this links back to the
operative rules) — nothing is copied, so nothing drifts.

This is distinct from the **strategy-engine** ADRs under
[`plugins/make-magic/docs/adr/`](../../plugins/make-magic/docs/adr/README.md), which
record decisions about the Python pipeline's design, co-located with the code they
govern and surfaced by `.claude/rules/adr-*.md`.

| # | Title | Status |
|---|---|---|
| [0001](0001-trunk-based-flow-and-releases.md) | Trunk-based flow and releases | Accepted |
