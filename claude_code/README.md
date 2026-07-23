# Claude Code workflows

Version-controlled home for named Claude Code Workflows. These files are the
**source of truth**; they are symlinked into `~/.claude/workflows/` so Claude Code
resolves them as slash commands (`/deep-research`, `/deep-research-lite`).

## Workflows

| File | Command | Angles | Fetch | Verify votes | Verify claims | Rough cost/run* |
|------|---------|:------:|:-----:|:------------:|:-------------:|-----------------|
| `deep-research.js` | `/deep-research` | 5 | 15 | 3 (2/3 kills) | 25 | ~2.0M tokens · ~100 agents · ~20 min |
| `deep-research-lite.js` | `/deep-research-lite` | 3 | 8 | 1 (1 kills) | 12 | ~1/3 of full |

\* Measured across 93 historical `deep-research` runs (92/93 completed).

Both run the same pipeline: **Scope** (decompose into search angles) →
**Search** (one WebSearch agent per angle) → **Fetch** (URL-dedup, WebFetch,
extract falsifiable claims) → **Verify** (adversarial skeptic votes; a claim
survives only if not refuted) → **Synthesize** (merge duplicates, rank by
confidence, cite sources). Pass the research question as `args`.

`deep-research-lite` trades rigor for cost: a single default-refute skeptic vote
is more aggressive at killing claims (lower recall). Use the full `deep-research`
when correctness matters; use lite for quick, cheaper lookups.

## Install / update

```bash
# install (idempotent) — point ~/.claude/workflows at these files
mkdir -p ~/.claude/workflows
ln -sf "$PWD/deep-research.js"      ~/.claude/workflows/deep-research.js
ln -sf "$PWD/deep-research-lite.js" ~/.claude/workflows/deep-research-lite.js
```

To update a workflow, edit the file **here** and commit — the symlink means the
change is live immediately. If a new run produces an improved script, copy it
over the file here and commit.

## Provenance

Authored by Claude via the Claude Code Workflow tool (adapted from a "bughunter"
find→verify→synthesize pattern), not downloaded. `deep-research.js` was
canonicalized on 2026-07-22 from the newest of 94 saved run copies
(`deep-research-wf_7383feda-e00.js`, 2026-07-21). `deep-research-lite.js` is a
parameter-reduced fork of it.
