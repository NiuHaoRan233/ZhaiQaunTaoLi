---
name: maker-strategy-session
description: Quickly resume this repository's maker-strategy context and conduct causal gap analysis. Use when the user says to start or continue discussing strategy, explains a market-making judgment, or asks whether a maker model handled an order-book case correctly. Do not use for unrelated repository maintenance.
---

# Maker Strategy Session

Resume the durable strategy state without rereading every long strategy document on every session.

## Start the session

1. From the repository root, run:

   ```powershell
   .\.venv\Scripts\python.exe .agents\skills\maker-strategy-session\scripts\check_bootstrap.py
   ```

2. When the checker reports `STATUS=current`, read
   `docs/策略会话启动摘要.md` completely. Do not read the three long strategy documents
   completely merely as a session ritual.
3. When the checker reports `STATUS=stale`, read the summary for orientation, then read
   each reported changed source document completely before discussing or changing the
   strategy. Refresh the summary and its fingerprints after reconciling it with the
   authoritative sources.
4. Briefly tell the user that the current state is loaded and invite the market case.
   When the user already supplied a bond, time, or judgment, proceed directly instead
   of asking them to repeat it.

The summary is a startup index, not a fourth strategy authority. The handbook remains
the durable record of the user's reasoning, the formal specification defines intended
implementable behavior, and the model registry owns immutable IDs, ancestry, assignments,
and evidence.

## Discuss a case

- Use `rg` on the three authoritative documents for the case's concepts, prices, time,
  model ID, or section names. Read the matching sections and enough surrounding text to
  resolve the question.
- Inspect the surrounding causal market data before classifying a discussed case as a
  missed correct action, an incorrect action, already-correct behavior, or a broader
  principle. Treat the user's commentary as a partial audit, not exhaustive labeling.
- Escalate to a complete source-document read only when the bootstrap is stale, the
  relevant sections conflict, the case depends on dispersed historical reasoning, or
  the user requests a comprehensive audit.
- Keep first-position, queue, Windfall, whale, railway, and anchored-liquidity branches
  separate. A conclusion for one branch does not authorize changing another.
- Do not manufacture a rule or code change when existing behavior already matches the
  user's reasoning. Clarify materially ambiguous exploratory comments before encoding
  them.

## Preserve the next session

After confirmed strategy knowledge or assignments change:

1. Update authoritative documents in their normal order: handbook first, then formal
   specification, model registry, implementation, configuration, and tests as applicable.
2. Update `docs/策略会话启动摘要.md` last so it reflects the final state, including
   current models, high-value principles, open questions, and lookup routes.
3. Run the checker with `--print-hashes`, replace all three stored fingerprints in the
   summary, then run the default check and require `STATUS=current`.

Do not refresh fingerprints without first making the summary semantically consistent
with the changed sources. Workflow-only edits do not require a trading-model version;
any decision or execution-path change still follows the repository's versioning rules.
