# Operational Proof Plan — Stock Bounded Paper + Crypto ETH/BTC Paper

Date: 2026-04-14

## Scope

This report turns the governed runtime requirements into an operational-proof plan using fresh repo/runtime evidence.

### Governing sources
- `docs/governance/eod-profit-taking.md`
- `docs/governance/crypto-paper-promotion-checklist.md`
- `notes/stock_bounded_paper_runbook.md`
- `config/strategies/stock-bounded-paper.toml`
- `config/strategies/crypto.toml`
- `config/strategies/catalog.toml`
- `data/strategies/eth-btc-ratio-mean-reversion-v5/paper-trading.yaml`

## Definition of operational proof complete

A pod is operationally proven only when all of the following are true:

1. Config intent is explicit and explainable.
2. The runtime path is healthy now, not only historically.
3. Decisions, trades, positions, and exits are attributable to the pod.
4. EOD flatten and exit protection are evidenced by deployed operational controls.
5. A fresh PASS / SOFT PASS / FAIL classification can be supported from current logs and artifacts.

---

## Fresh evidence captured

### 1) Pod inventory
Command:
```bash
python -m llm_quant.cli pods list
```

Observed:
- `crypto`
- `crypto-ethbtc-paper`
- `default`
- `nearclose-broker-smoke`
- `stock-bounded-paper` (created during this session)

### 2) Stock bounded pod creation
Command:
```bash
python -m llm_quant.cli pods create stock-bounded-paper --strategy custom --capital 1000
```

Observed:
```text
OK Pod stock-bounded-paper created (strategy=custom, capital=$1,000.00)
```

### 3) Stock bounded dry-run evidence
From fresh terminal output:
```bash
python -m llm_quant.cli run --pod stock-bounded-paper --broker paper --dry-run
```

Observed:
- market data fetched successfully
- paper portfolio initialized for `stock-bounded-paper`
- market context built cleanly
- `candidate_stocks` path resolved into `soxx-qqq-lead-lag` behavior
- governed decision path completed
- result was a valid no-trade / governor-rejected dry-run:
  - `QQQ HOLD`
  - reasoning indicated `Governor reject/omit`
- dry-run ended successfully:
  - `DRY RUN -- no trades executed.`

Classification:
- **Dry-run PASS**
- This also qualifies as a valid runbook-style no-trade rehearsal result.

### 4) Crypto runtime status
Commands:
```bash
python -m llm_quant.cli crypto status --pod crypto-ethbtc-paper
systemctl --user status llm-quant-crypto-ethbtc-paper.timer --no-pager -l
journalctl --user -u llm-quant-crypto-ethbtc-paper.service -n 20 --no-pager -l
```

Observed before remediation:
- no pod-specific overlay file at `config/strategies/crypto-ethbtc-paper.toml`
- effective runtime fell back to base config instead of crypto-specific governed settings
- `Intraday Enabled: True | RTH Guard: True`
- repeated service failures on `Alpaca GET /v2/clock failed: Not Found`

Observed after remediation:
- explicit pod overlay now exists at `config/strategies/crypto-ethbtc-paper.toml`
- effective runtime now reports:
  - `Intraday Enabled: True | RTH Guard: False`
- latest crypto bar advanced to `2026-04-14 19:50:00`
- `Last Run` remains stale at `2026-04-08 19:50:00`
- stale warning still present because fresh deterministic run-path evidence has not yet been written

Classification:
- **Runtime health partial**
- The incorrect equities-session guard dependency is no longer the governing config intent.
- The crash-loop root cause was a missing pod overlay, not only the absence of a crypto-specific guard bypass in code.
- Operational proof is still incomplete because fresh `intraday_context_snapshots` / run attribution have not yet advanced.

### 5) Crypto paper evaluation refresh
Command:
```bash
.venv/bin/python scripts/update_crypto_paper_eval.py --slug eth-btc-ratio-mean-reversion-v5 --pod crypto-ethbtc-paper
```

Observed:
- artifact updated successfully

Fresh artifact:
- `updated_at: 2026-04-14T20:38:52+00:00`
- `days_observed: 10`
- `closed_trades: 0`
- `sharpe: 0.0`
- `gate_status.passed: false`

Classification:
- **Promotion gate FAIL**
- Reasons remain:
  - `days_observed=10 < min_days=30`
  - `closed_trades=0 < min_trades=50`
  - `sharpe=0.000 < sharpe_floor=0.600`

### 6) Crypto timer/service health
Commands:
```bash
systemctl --user status llm-quant-crypto-ethbtc-paper.timer --no-pager -l
journalctl --user -u llm-quant-crypto-ethbtc-paper.service -n 100 --no-pager -l
```

Observed before remediation:
- timer is active and has a future trigger
- service was repeatedly failing every 5 minutes
- repeated failure signature:
  - `FAIL Alpaca clock check failed: Alpaca GET /v2/clock failed: Not Found`

Observed after remediation:
- timer remains active and waiting on the 5-minute cadence
- a fresh manual service invocation no longer emitted the missing-overlay warning
- the most recent manual invocation completed with:
  - `Intraday slot 2026-04-14T21:00:00+00:00 already executed — skipping.`
- older timer-driven failures before the overlay fix remain in the journal

Interpretation:
- the crypto candidate pod is no longer proven to be in the exact same crash loop caused by the missing crypto overlay / equities RTH guard mismatch
- however, current runtime evidence is still insufficient to classify the pod as fully healthy because the fresh run-path telemetry has not advanced

Classification:
- **Scheduler active, remediation partially validated**
- Checklist section 1 and 2 still do **not** pass overall until fresh timer-driven success and fresh run attribution are captured.

### 7) EOD flatten deployment evidence
Files inspected:
- `scripts/systemd/llm-quant-eod-flat.timer`
- `scripts/systemd/llm-quant-eod-flat.service`

Definitions observed:
- default equity timer:
  - `OnCalendar=Mon..Fri *-*-* 15:55:00`
  - `Timezone=America/New_York`
- default equity service:
  - `ExecStart=/home/ty/Documents/llm-quant/llm-quant/.venv/bin/pq eod-flat --pod default`
- stock bounded equity service/timer assets now exist under `scripts/systemd/`:
  - `llm-quant-eod-flat-stock-bounded-paper.service`
  - `llm-quant-eod-flat-stock-bounded-paper.timer`
  - both target `pq eod-flat --pod stock-bounded-paper` on the same 15:55 ET cadence

Runtime status:
```bash
systemctl --user status llm-quant-eod-flat.timer --no-pager -l
systemctl --user status llm-quant-eod-flat-stock-bounded-paper.timer --no-pager -l
journalctl --user -u llm-quant-eod-flat.service -n 20 --no-pager -l
```

Observed before remediation:
- `llm-quant-eod-flat.timer` was loaded but **disabled**
- `llm-quant-eod-flat.timer` was **inactive (dead)**

Observed after remediation:
- `llm-quant-eod-flat.timer` is now **enabled** and **active (waiting)**
- `llm-quant-eod-flat-stock-bounded-paper.timer` is now **enabled** and **active (waiting)**
- both show the next trigger at `2026-04-15 15:55:00 CDT`
- enabling the new stock timer required installing the new unit files under `/home/ty/.config/systemd/user/`
- the default EOD service was manually exercised and failed with:
  - `FAIL Alpaca clock check failed: Alpaca GET /v2/clock failed: Not Found`
- root cause in repo/config was identified:
  - the host `.env` configured `ALPACA_PAPER_URL=https://paper-api.alpaca.markets/v2`
  - the Alpaca client also appends `/v2/...` paths internally
  - this produced requests such as `https://paper-api.alpaca.markets/v2/v2/clock`, which explains the host-observed `Not Found`
- systemd emitted:
  - `Unknown key name 'Timezone' in section 'Timer', ignoring.`
- remediation applied in repo:
  - `src/llm_quant/broker/alpaca.py` now normalizes a trailing `/v2` from `ALPACA_PAPER_URL` before building endpoint paths
- regression evidence:
  - `pytest -q tests/test_alpaca_client.py tests/test_cli_operational_controls.py`
  - `5 passed`

Classification:
- **EOD operational proof partial**
- EOD flatten now has explicit installed coverage for the equity/session-based pods `default` and `stock-bounded-paper`, and both timers are active.
- Crypto is not covered by these stock-session units; `pq eod-flat` already treats crypto as a separate semantic path and can return `disabled_for_crypto`.
- The prior `/v2/clock` failure is now reduced from an unexplained host-only issue to a repo/config mismatch with a concrete fix and tests.
- Operational proof is still incomplete because post-fix successful `default` and `stock-bounded-paper` EOD execution evidence was not yet captured in this session.

---

## Current pod status

## `stock-bounded-paper`

### What is now proven
- pod overlay exists and matches runbook intent
- pod has now been instantiated
- pod appears in pod inventory
- dry-run completed successfully
- governed no-trade outcome was explainable
- bounded capital initialization behaved as expected

### What is not yet proven
- supervised non-dry paper session for this pod
- fresh decision/trade/position inspection after a real paper run
- partial TP / trailing / stop behavior from an actual position lifecycle
- EOD flatten for this pod as a deployed operating control

### Current classification
- **Operational proof partial**
- **Runbook state: ready for supervised paper session**
- If judged only against the stock runbook to date:
  - dry-run = PASS
  - full operational proof = not complete

## `crypto-ethbtc-paper`

### What is now proven
- pod exists
- timer is installed and waiting on a 5-minute cadence
- refreshed promotion artifact confirms the pod is not promotion-ready

### What is now disproven
- healthy current runtime
- fresh bars
- successful current service runs
- readiness for promotion

### Current classification
- **Operational proof partial**
- **Promotion readiness FAIL**
- `promoted_crypto` must remain empty

---

## Service environment and credential propagation evidence

- `Closed by runtime evidence` — the repo's live-relevant systemd user services explicitly read the same repo-local environment file used by manual CLI runs:
  - `scripts/systemd/llm-quant-intraday.service`
  - `scripts/systemd/llm-quant-crypto.service`
  - `scripts/systemd/llm-quant-eod-flat.service`
  - `scripts/systemd/llm-quant-eod-flat-stock-bounded-paper.service`
  - each defines `EnvironmentFile=/home/ty/Documents/llm-quant/llm-quant/.env`
- `Closed by runtime evidence` — installed user-unit definitions match the repo units for the relevant services:
  - `systemctl --user cat llm-quant-intraday.service llm-quant-crypto.service llm-quant-eod-flat.service llm-quant-eod-flat-stock-bounded-paper.service`
  - observed installed units under `/home/ty/.config/systemd/user/` with the same `EnvironmentFile=/home/ty/Documents/llm-quant/llm-quant/.env`
- `Closed by runtime evidence` — manual shell sourcing and systemd unit metadata both point to the same credential source:
  - manual check:
    - `manual_ALPACA_API_KEY=present`
    - `manual_ALPACA_SECRET_KEY=present`
    - `manual_ANTHROPIC_API_KEY=present`
  - systemd check:
    - `systemctl --user show llm-quant-intraday.service -p EnvironmentFiles -p Environment`
    - `systemctl --user show llm-quant-eod-flat-stock-bounded-paper.service -p EnvironmentFiles -p Environment`
    - observed `EnvironmentFiles=/home/ty/Documents/llm-quant/llm-quant/.env (ignore_errors=no)`
- `Closed by runtime evidence` — CLI/manual runs and service runs are wired to compatible environment-loading paths rather than different config mechanisms:
  - `src/llm_quant/utils/env.py` loads `.env` from the working directory for manual CLI execution when variables are not already present
  - the systemd services set `WorkingDirectory=/home/ty/Documents/llm-quant/llm-quant` and independently inject the same file with `EnvironmentFile=.../.env`
  - this is environment parity, not a separate service-only credential path
- `Closed by runtime evidence` — Alpaca credentials are the exact required broker credentials for live-relevant runtime paths:
  - `src/llm_quant/broker/alpaca.py` raises `Missing ALPACA_API_KEY or ALPACA_SECRET_KEY in env`
  - `docs/governance/eod-profit-taking.md` operational checklist requires:
    - `ALPACA_API_KEY`
    - `ALPACA_SECRET_KEY`
    - `ALPACA_PAPER_URL`
- `Closed by runtime evidence` — Anthropic credential presence is required only for overlay-authenticated paths, not for all broker/EOD paths:
  - `src/llm_quant/brain/overlay.py` reports overlay availability from `ANTHROPIC_API_KEY`
  - `src/llm_quant/brain/engine.py` notes Claude client initialization reads `ANTHROPIC_API_KEY` from environment
  - `README.md` quick-start requires adding `ANTHROPIC_API_KEY` to `.env`
  - current host `.env` contains `ANTHROPIC_API_KEY`, so there is no present service-vs-manual credential propagation mismatch on that path
- `Closed by runtime evidence` — no documentation/code conflict was found for service credential propagation:
  - docs requiring Alpaca credentials for EOD/runtime align with code
  - runtime units consume the same `.env` file as manual runs
  - the previously observed Alpaca `/v2/clock` failure was already traced in this report to `ALPACA_PAPER_URL` content, not to missing service environment propagation
- Classification for the service-environment gap:
  - no current evidence supports `Open due to environment/credential mismatch`
  - current status is `Closed by runtime evidence`

## Remaining work to reach operational proof complete

## Priority 1 — Fix crypto runtime blocker

### Objective
Restore successful fresh execution for `crypto-ethbtc-paper`.

### Blocking evidence
Repeated failure:
```text
FAIL Alpaca clock check failed: Alpaca GET /v2/clock failed: Not Found
```

### Required work
1. Identify why the crypto pod is calling an Alpaca clock path that returns 404 / Not Found.
2. Confirm whether:
   - environment variables point to the wrong Alpaca host,
   - crypto path is incorrectly reusing an equities-only clock assumption,
   - runtime should bypass or reinterpret the clock check for this crypto pod.
3. Re-run:
   ```bash
   python -m llm_quant.cli crypto status --pod crypto-ethbtc-paper
   ```
4. Re-check service logs until:
   - no repeated `FAILURE`
   - latest bar becomes fresh
   - current run timestamps advance

### Exit criteria
- fresh bar age
- no stale warning
- service runs successfully on its next scheduled slots
- deterministic governed path is visible in fresh logs

## Priority 2 — Run supervised stock bounded paper session

### Objective
Move `stock-bounded-paper` from dry-run proof to operational proof.

### Required work
1. Run:
   ```bash
   python -m llm_quant.cli run --pod stock-bounded-paper --broker paper
   ```
2. Inspect normal repo evidence surfaces for:
   - fresh decisions for `stock-bounded-paper`
   - any paper trades/fills attributable to the pod
   - resulting positions within:
     - `max_positions = 1`
     - `max_trades_per_session = 1`
     - tiny size relative to capital
     - high idle cash / reserve preservation
3. Classify result:
   - PASS if audited trade/no-trade behavior is healthy and attributable
   - SOFT PASS if no trade occurs but fresh decision path and bounded-risk behavior are clean
   - FAIL if the run path, attribution, or bounded controls break

### Exit criteria
- at least one supervised paper run completed
- resulting evidence bundle is attributable and auditable
- bounded-risk checks hold

## Priority 3 — Prove EOD flatten operationally

### Objective
Convert EOD flatten from documented capability into active operational proof.

### Current blocker
- the timers are now enabled/active
- the host-level `/v2/clock` failure was traced to `ALPACA_PAPER_URL` including a trailing `/v2` while the client also appended `/v2/...`
- repo normalization has been patched, but post-fix successful service execution evidence is still outstanding
- explicit packaging now exists for `default` and `stock-bounded-paper`, but only timer activation — not a successful execution artifact — has been proven for `stock-bounded-paper`

### Required work
1. Use the explicit equity-session coverage now defined in repo assets:
   - `default`
   - `stock-bounded-paper`
   - crypto excluded unless separately defined
2. Enable/install the intended timer(s).
3. Capture logs from an actual scheduled run or controlled manual invocation.
4. Record whether early-close handling needs an operational note.

### Exit criteria
- timer enabled and active
- recent service execution evidence captured
- target pod coverage is explicit
- audit notes explain whether crypto uses stock-style EOD semantics or not

## Priority 4 — Close audit ambiguities

### A. Crypto overlay inheritance
Current warning:
```text
No strategy overlay for pod 'crypto-ethbtc-paper' at .../config/strategies/crypto-ethbtc-paper.toml
```

Need one explicit choice:
- document that inheritance from `config/strategies/crypto.toml` is intentional, or
- add a dedicated pod overlay file for audit clarity

### B. Crypto EOD semantics
Need one explicit choice:
- crypto does not use stock-style session flatten
- crypto uses shared policy vocabulary only
- crypto has a distinct operational flatten rule

---

## Completion checklist

## Stock pod complete when
- [x] pod exists
- [x] dry-run passes
- [ ] supervised paper session completes
- [ ] decisions/trades/positions are attributable
- [ ] bounded-risk behavior is confirmed from live paper evidence
- [ ] exit protection / EOD behavior is evidenced, not inferred

## Crypto pod complete when
- [x] refreshed paper-eval artifact exists
- [ ] timer-driven service stops failing
- [ ] fresh bars are present
- [ ] current runtime path is healthy
- [ ] deterministic governed run path is evidenced in fresh logs
- [ ] gate thresholds pass before any promotion action

## Shared operational control complete when
- [x] EOD flatten timer is enabled and active
- [ ] recent service execution evidence exists
- [x] pod coverage is explicit
- [x] crypto overlay and EOD semantics are clarified

---

## Recommended immediate execution order

1. Fix the crypto Alpaca clock failure.
2. Re-run crypto status until stale-bar clears.
3. Confirm fresh governed crypto run logs.
4. Run a supervised `stock-bounded-paper` paper session.
5. Inspect paper decision/trade/position evidence for that stock pod.
6. Enable and validate the intended EOD flatten timer/service coverage.
7. Write a final PASS / SOFT PASS / FAIL operational-proof report for both pods.

---

## Focused remediation plan for the two open items

## Open item 1 — `crypto-ethbtc-paper`: operational FAIL, promotion FAIL

### Root-cause objective
Convert the current crash loop into a healthy timer-driven runtime.

### Known blocker
Fresh journal evidence showed repeated failures on:
```text
FAIL Alpaca clock check failed: Alpaca GET /v2/clock failed: Not Found
```

### Root cause identified
- `.env` set `ALPACA_PAPER_URL=https://paper-api.alpaca.markets/v2`
- `AlpacaClient` also appends `/v2/...` endpoint paths internally
- this yields malformed request targets such as `/v2/v2/clock`
- this explains why the failure presented as a host-level operational issue even though a repo-side normalization bug still existed

### Action plan
1. **Deploy the endpoint normalization fix**
   - `src/llm_quant/broker/alpaca.py` now strips a trailing `/v2` from `ALPACA_PAPER_URL`
   - regression tests were added in `tests/test_alpaca_client.py`

2. **Re-verify immediately**
   - `systemctl --user status llm-quant-crypto-ethbtc-paper.timer --no-pager`
   - `journalctl --user -u llm-quant-crypto-ethbtc-paper.service -n 100 --no-pager`
   - `python -m llm_quant.cli crypto status --pod crypto-ethbtc-paper`

3. **Collect fresh healthy-run evidence**
   - timer still active with future trigger
   - no repeated `FAILURE`
   - last run and latest bar timestamps advancing
   - stale warning removed
   - governed path visible in logs:
     - candidate generation
     - governor / overlay audit
     - risk filter after governor

4. **Refresh promotion evidence after runtime recovery**
   - rerun:
     ```bash
     .venv/bin/python scripts/update_crypto_paper_eval.py --slug eth-btc-ratio-mean-reversion-v5 --pod crypto-ethbtc-paper
     ```
   - keep `promoted_crypto = []` unless strict gates pass

### Exit criteria
This item is complete only when:
- the 5-minute service stops crash-looping
- `crypto status` becomes fresh
- stale-bar warning disappears
- a fresh governed runtime path is evidenced in logs
- the pod is reclassified to:
  - **operationally healthy but promotion-ineligible**, or
  - **promotion-validation ready**

### Expected near-term outcome
Given the current metrics, the realistic first target is:
- **operationally healthy**
- **still not promotion-ready**

## Open item 2 — EOD flatten: documented/packaged, but not active operational proof

### Deployment objective
Convert EOD flatten from repo assets into an enabled, evidenced operational control.

### Known blocker
Fresh status originally showed disabled/inactive timers, which has now been remediated.
The remaining blocker is narrower:
- the timer units are enabled/active
- the `/v2/clock` failure was traced to a malformed Alpaca base URL with a duplicated `/v2`
- no recent successful post-fix EOD execution evidence is captured yet for either equity pod

### Action plan
1. **Use explicit pod coverage already justified by repo evidence**
   - `default` via `llm-quant-eod-flat.service` / `.timer`
   - `stock-bounded-paper` via `llm-quant-eod-flat-stock-bounded-paper.service` / `.timer`
   - exclude crypto from these stock-session units unless a separate crypto control is defined

2. **Enable the intended timer(s)**
   - `systemctl --user daemon-reload`
   - `systemctl --user enable --now llm-quant-eod-flat.timer`
   - `systemctl --user enable --now llm-quant-eod-flat-stock-bounded-paper.timer`

3. **Prove execution**
   Collect:
   - `systemctl --user status ...timer --no-pager`
   - `journalctl --user -u ...service -n 50 --no-pager`
   - evidence of:
     - scheduled invocation
     - successful command execution
     - explainable no-op when there are no positions
     - or cancel / flatten / snapshot behavior when there are positions

4. **Close semantics gaps**
   Add a short operational note covering:
   - early-close behavior
   - which pods are covered
   - crypto exclusion from the stock-session timers
   - what successful “nothing to flatten” looks like in logs

### Exit criteria
This item is complete only when:
- the intended timer is enabled and active
- future trigger is visible
- at least one successful service execution is logged
- pod coverage is explicit
- crypto inclusion/exclusion is documented, not implied

## Immediate execution order for the two open items

1. Trace and fix the crypto Alpaca clock failure.
2. Reconfirm fresh crypto status and healthy journal output.
3. Refresh the crypto paper-eval artifact again after healthy runs resume.
4. Decide EOD flatten pod coverage.
5. Enable the relevant EOD timer/service units.
6. Capture one successful EOD service execution or explainable no-op.
7. Update this report with the new PASS / FAIL classifications.

## Bottom line

The repo is policy-aligned, but operational proof is still incomplete.

- `stock-bounded-paper` has advanced from config-only intent to instantiated + dry-run-proven, but still needs a supervised paper session and operational EOD proof.
- `crypto-ethbtc-paper` is not merely below promotion thresholds; it is currently failing in the timer-driven runtime due to an Alpaca clock error and therefore cannot accumulate valid fresh evidence.
- EOD flatten is documented and packaged, but not currently enabled as an active control for the relevant pods.

## Final closure policy and remaining verified gaps

### Stock overlay operating policy
- `Closed by explicit operating-policy decision` — live stock overlay auth is **required for launch**.
- Missing live overlay auth is a **launch blocker** for the stock sleeve.
- Deterministic fallback behavior remains a verified safety path, but it is **not** acceptable as the final live stock operating mode.

### Remaining verified gaps

#### 1. In-market-hours EOD flatten proof
- `Open due to market/session timing`
- What is already proven:
  - the stock-session EOD timers for `default` and `stock-bounded-paper` are installed and active
  - the patched EOD CLI path now returns an explainable after-hours no-op instead of the prior malformed Alpaca `/v2/clock` failure
- What still closes this item:
  - one **in-market-hours** timer/service execution for `default` and `stock-bounded-paper`
  - with either:
    - a real flatten event for an open paper position, or
    - an explainable in-window no-op while the pod is already flat

#### 2. Real bounded stock paper position lifecycle
- `Open due to market/session timing`
- What is already proven:
  - attributable stock decisions exist
  - supervised stock paper runs complete cleanly
  - bounded controls remain enforced
  - snapshots are persisted
- What still closes this item:
  - one attributable `stock-bounded-paper` paper entry
  - followed by persisted evidence of at least one downstream lifecycle state:
    - protected open position,
    - realized exit,
    - reconciliation event,
    - or EOD flatten outcome

#### 3. Stock overlay live behavior without fallback
- `Closed by runtime evidence`
- Policy remains explicit:
  - live stock overlay auth is required for launch
  - missing overlay auth is a launch blocker
- Fresh closure evidence captured in the actual launch environment:
  - shell loaded `.env` and confirmed:
    - `ANTHROPIC_API_KEY=present`
    - `ALPACA_API_KEY=present`
    - `ALPACA_SECRET_KEY=present`
  - supervised run:
    - `python -m llm_quant.cli run --pod stock-bounded-paper --broker paper`
  - runtime showed a real overlay call:
    - `HTTP Request: POST https://api.anthropic.com/v1/messages "HTTP/1.1 200 OK"`
  - runtime showed normal parser/decision flow:
    - `Parsed TradingDecision: date=2026-04-15, regime=risk_off (80.0%), 1 signals`
  - no fallback warning appeared
  - final audited result remained a governed no-trade:
    - `QQQ HOLD`
    - `Governor reject/omit`
  - snapshot persisted:
    - `Snapshot #2288 saved`
- Interpretation:
  - this closes the missing-auth / fallback ambiguity
  - the stock sleeve is now blocked by market-condition evidence gaps, not by overlay auth availability

## Single-session finish plan

The fastest governed path to close all remaining stock-side gaps is one live market session with this sequence:

1. **Pre-market / early session**
   - verify `ANTHROPIC_API_KEY` is present in the actual launch environment
   - verify `llm-quant-eod-flat.timer` and `llm-quant-eod-flat-stock-bounded-paper.timer` are active
   - confirm report capture targets before the session begins

2. **Mid-session**
   - run or observe `python -m llm_quant.cli run --pod stock-bounded-paper --broker paper`
   - capture whether a real overlay-authenticated adjudication occurs
   - if a paper entry occurs, immediately capture attributable decision / trade / position / snapshot evidence

3. **Pre-close / close**
   - inspect whether `stock-bounded-paper` has an open position
   - allow the actual 15:55 ET timer/service path to execute
   - capture journal evidence for either:
     - successful flatten, or
     - explainable in-window no-op

4. **Post-close**
   - append the same-day evidence into this report
   - reclassify the three remaining items as closed or blocked by one exact condition only

## Final launch interpretation

Until the three items above are closed:

- `stock-bounded-paper` is **safe and supervised-paper-runnable**
- `stock-bounded-paper` is **not yet fully live-ready**
- the remaining blockers are:
  - missing in-window EOD proof
  - missing real bounded position lifecycle proof
