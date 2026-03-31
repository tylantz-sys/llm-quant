# Decision Telemetry (Full Trace)

This doc explains how to capture **inputs + outputs** for every decision and
how to verify the dashboard is live.

## Readiness Checklist

Use Grafana (Docker) with API base:

```
http://host.docker.internal:8000
```

Verify:
- `GET /health` returns status ok
- `GET /runs` returns rows
- `GET /decisions` returns rows

## What Gets Logged

Each run logs:
- **Decision metadata** (`llm_decisions`): model, tokens, regime, cost
- **Context JSON** (`decision_contexts`): full market/portfolio context
- **Prompts** (`llm_prompt_logs`): system + user prompts
- **Raw model response** (`llm_decisions.raw_response`)
- **Parsed signals** (trades + `/decisions/{id}` parser)

## Always-Log Mode (After Hours)

When `execution.log_decisions_when_rth_closed = true`, intraday runs will:
- Build context
- Render prompts
- Call the model
- Log telemetry
- **Skip execution** (no orders placed)

## Grafana Panel Targets

Recommended queries:
- `/decisions?limit=20`
- `/decisions/{id}`
- `/decision-contexts?decision_id={id}`
- `/prompt-logs?decision_id={id}`
- `/intraday-context?limit=20`

## Systemd (API Service)

A unit file is provided at:
- `scripts/systemd/llm-quant-dashboard.service`

Install it to user systemd if needed:

```
mkdir -p ~/.config/systemd/user
cp scripts/systemd/llm-quant-dashboard.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now llm-quant-dashboard.service
```
