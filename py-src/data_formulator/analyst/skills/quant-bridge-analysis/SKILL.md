---
name: quant-bridge-analysis
description: >-
  Call quant-bridge's read-only analytical tools directly -- volatility
  (GARCH), regime (HMM), seasonal bias, dealer key levels, Monte Carlo
  forecasts, signal significance tests, model scoreboard, Trevo survivors,
  ad-hoc SQL over Jetson datasets, and the research record (chainlog/registry)
  -- so a chart or answer can be built on a real computed signal, not just
  whatever raw table happens to be loaded in the workspace.
when_to_use: >-
  The user's question needs a DERIVED signal (volatility regime, HMM state,
  seasonal edge, key levels, forecast distribution, whether a feature actually
  predicts forward return, how a model scores against naive persistence, what
  the genetic search's survivors look like) rather than a transform of data
  already loaded. Also use query_sql when the workspace tables don't cover
  what's needed and a live Jetson dataset does. Not for loading a whole new
  table into the workspace as a source -- that's the data-loading skill (the
  quant_bridge connector, list_data/probe_data).
always_on: false
tools:
  - qb_garch_volatility
  - qb_markov_regime
  - qb_seasonal_bias
  - qb_key_levels
  - qb_monte_carlo_forecast
  - qb_signal_significance_test
  - qb_model_scoreboard
  - qb_trevo_survivors
  - qb_lean_validation
  - qb_query_sql
  - qb_provide_ai_context
  - qb_live_bars
  - qb_live_pairs
  - qb_source_graph
  - qb_chainlog_query
  - qb_registry_show
  - qb_mcl_compute_wilson_ci
  - qb_mcl_discretize_feature
  - qb_mcl_anchor_event
actions: []
---

# Skill: quant-bridge analysis

These tools call **quant-bridge** (`http://localhost:3100`, or
`$QUANT_BRIDGE_BASE_URL`), the one process on this box that touches the
Jetson medallion pipeline. Every one of these calls is a straight pass-through
to `POST /api/tool/<name>` -- the exact same dispatch the research-harness UI
and the Claude Code chat in VSCodium use, so a number you get here is a number
those would also get. There is no separate implementation to drift.

All 19 tools here are **read-only**. quant-bridge blocks the mutating half of
its catalog over REST by default (`annotate_chart`, `chainlog_append`,
`registry_record`, `export_arrow_ipc`) -- they are not in this skill's list at
all, deliberately. This skill can look and compute; it cannot draw on a live
chart or write to the research record.

## What each tool is for

- **`qb_garch_volatility`** -- real GARCH(1,1) fit for a pair. A missing fit
  comes back explicit, never silently as zero vol.
- **`qb_markov_regime`** -- the live HMM classifiers' regime-transition
  matrix. Always pick one vocabulary and stay in it.
- **`qb_seasonal_bias`** -- two distinct things: full-sample (has lookahead,
  descriptive only) vs point-in-time. Read which one you got before using it
  as a forward-looking claim.
- **`qb_key_levels`** -- dealer PDH/PDL/PDM/PWH/PWL, 20D range, gamma
  strikes, session anchors.
- **`qb_monte_carlo_forecast`** -- resamples the pair's REAL historical
  hourly returns with replacement; not a lognormal/parametric assumption.
- **`qb_signal_significance_test`** -- does a signal actually predict the
  anti-lookahead forward return in `walk_forward_labels`, or is that apparent
  edge noise? This is the tool that turns "looks interesting" into a number.
- **`qb_model_scoreboard`** -- scores the Jetson's time-series models
  against their own naive persistence baseline.
- **`qb_trevo_survivors`** -- survivors of the weekly genetic search over
  boolean feature trees. Their train/oos Sharpe is the GA's own, not
  independently validated -- see `qb_lean_validation` for that.
- **`qb_lean_validation`** -- ADVISORY only, nothing here gates execution.
  Walks a survivor's fire array bar-by-bar through LEAN.
- **`qb_query_sql`** -- one read-only SQL statement over Jetson datasets in
  an in-memory DuckDB. Use `DATASET_NAME` as the table name (see the tool's
  own description for the exact convention). The most flexible tool here --
  reach for it when nothing else fits.
- **`qb_provide_ai_context`** -- compact markdown of a pair's current state,
  grouped by source. Good first call when you don't yet know what's
  interesting about a pair.
- **`qb_live_bars`** / **`qb_live_pairs`** -- the live tick pipeline. Crosses
  are synthesized from two majors, not observed quotes -- say so if it
  matters to what you're building.
- **`qb_source_graph`** -- the dataset registry built from `sources/` notes:
  which groups exist, which datasets each covers.
- **`qb_chainlog_query`** -- search the shared append-only research record
  Gemini, Claude and Colab jobs all write to. Check here before re-deriving
  something that may already have a verdict.
- **`qb_registry_show`** -- every recorded test of one feature, with the
  chainlog entry each cites.
- **`qb_mcl_compute_wilson_ci`** -- asymmetric CI for wins/total; use instead
  of a normal approximation on small samples.
- **`qb_mcl_discretize_feature`** -- bins a feature into k buckets using only
  data available at each bar (point-in-time, not full-sample quantiles).
- **`qb_mcl_anchor_event`** -- locates session transitions (LONDON_OPEN,
  US_ORB_1000, NY_*) as anchor points for an event study.

## Discipline: what this skill's output is, and is not

**What you compute or read here is a candidate observation, not a finding.**
The user's research loop (`fx-research/newjob.py`, stages 1-7, chainlog,
registry) is where a claim actually gets tested end to end -- point-in-time
correctness, a real baseline, non-overlapping windows, an honest verdict. A
number from `qb_signal_significance_test` or a chart built on
`qb_garch_volatility` is a good basis for a report or a visualization *in this
session*, but never state it as an established result on its own. If what you
found looks worth pursuing, say plainly that it should go through
`newjob.py` before anyone acts on it -- do not soften that into a finding
because the number looked clean.

`qb_chainlog_query` / `qb_registry_show` are the exception: those ARE already
the record. Reading them is reading a finding, not manufacturing one.

## Practical notes

- If a call fails with "quant-bridge unreachable", the `bridge: serve :3100`
  task isn't running -- say so plainly rather than falling back to a stale
  workspace table without flagging the substitution.
- Prefer `qb_provide_ai_context` or `qb_key_levels` to orient before reaching
  for `qb_query_sql` blind. Use `query_sql` when the shaped tools don't cover
  what's needed.
- Results are computed values (JSON), not workspace tables -- to chart one,
  pull the numbers into a script via `execute_python_script` (already
  always-on) and hand the resulting frame to `visualize`, or, for something
  worth writing up, follow with `write_report` once the `report` skill is
  loaded.
