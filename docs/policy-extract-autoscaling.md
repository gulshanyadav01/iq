# policy_extract capacity and autoscaling

`policy_extract` completes about 367 docs/hour against about 465/hour arriving, a structural shortfall of about 27% (measured 7–9 Oct 2026 at concurrency 10).

The app-side changes reclaim utilisation the serial task was losing:

- the pages-in-flight gate,
- the OCR/LLM split,
- moving the reconciler to the default queue.

Closing the remaining gap means adding MinerU GPU capacity. This document is the strategy for doing that automatically.

**Principle:** pages are the unit everywhere. The MinerU GPU fleet sets throughput, so it is the only thing scaled on demand. Celery workers are sized _from_ GPU capacity, never from queue depth.

## Why not scale workers on queue depth

More workers than MinerU's page capacity just wait on the gate. Without the gate, they push MinerU past its sweep peak. At 11.8 pages/doc, 8 docs in flight is already about 92 pages, and concurrency 12 reaches about 110. Docs/hour is also the wrong yardstick: it moves with document mix, and both sides have reported false regressions from it.

## App-side pieces (this repo)

| Piece                                                              | Where                                                 | Flag (default)                              |
| ------------------------------------------------------------------ | ----------------------------------------------------- | ------------------------------------------- |
| Pages-in-flight gate (FIFO + bounded backfill, 60s renewed leases) | `backend/core/services/mineru_gate.py`                | `POLICY_EXTRACT_PAGE_GATE` (`false`)        |
| Gate capacity: Redis `mineru:pages_capacity`, else env             | same                                                  | `POLICY_EXTRACT_MAX_PAGES_IN_FLIGHT` (`75`) |
| OCR → LLM split with markdown checkpoint                           | `policy_extract/tasks.py` `_run_ocr` / `_run_llm_map` | `POLICY_EXTRACT_SPLIT_LLM` (`false`)        |
| Capacity metrics, published every minute                           | `policy_extract/services/capacity_metrics.py`         | `POLICY_EXTRACT_METRICS_ENABLED` (`false`)  |
| Reconciler on the default `celery` queue                           | `config/settings.py` beat schedule                    | none                                        |

The metrics are published to CloudWatch namespace `InsurIQ/PolicyExtract`:

| Metric                            | Meaning                                                            |
| --------------------------------- | ------------------------------------------------------------------ |
| `QueueDepthDocs`                  | `LLEN policy_extract`                                              |
| `LlmQueueDepth`                   | `LLEN policy_extract_llm`                                          |
| `MeanPagesPerDoc`                 | admitted pages ÷ admitted docs over 15 min                         |
| `BacklogPages`                    | QueueDepthDocs × MeanPagesPerDoc                                   |
| `PagesInFlight` / `PagesCapacity` | gate state                                                         |
| `PagesPerHour`                    | pages MinerU finished over 15 min                                  |
| `EstimatedLagHours`               | BacklogPages ÷ PagesPerHour, capped at 72. **The scaling signal.** |

The gate's counters are kept even with the gate off, so the rates can be measured before it is turned on. CloudWatch uses the host's instance role. The `AWS_*` env keys are the storage credentials and are deliberately skipped.

## Capacity model

- `C_node`: pages in flight one GPU node sustains at peak throughput. Start at 75, MinerU's sweep peak, and refine it with the full sweep curve.
- `P_node`: pages/hour one node sustains at `C_node`. Measure it from `PagesPerHour` with one node and the gate on.
- Fleet capacity: `mineru:pages_capacity = healthy_nodes × C_node`. Hold back pages for the ungated callers with `POLICY_EXTRACT_GATE_RESERVE_PAGES`.
- Break-even: `ceil(arrival_pages_per_hour ÷ P_node)`. At 465 docs/hour × 6.25 pages/doc that is about 2,900 pages/hour, plus headroom to drain.

## GPU fleet (MinerU ASG)

- **Image:** a launch template from a baked AMI, with model weights on the AMI or a pre-warmed snapshot, never downloaded at boot. An ASG **warm pool** of stopped instances cuts cold start to about 1–2 min. Nodes register with the existing ALB target group.
- **Scale out:** step scaling on `EstimatedLagHours`. Lag > 1h for 3 min adds 1 node; lag > 3h adds 2. Cooldown equals the measured warm-up.
- **Scale in:** lag < 0.25h **and** `PagesInFlight < 0.5 × PagesCapacity` for 30 min removes 1 node. Scale in slowly; the daily cycle refills the queue.
- **Scheduled baseline:** the queue peaks around 00:16 India time and drains by 10:00. Arrivals run 2–9/hour between 02:00 and 08:00 India time. Raise the scheduled `min` for the business-day ramp and drop it to 1 after 02:00 India time.
- **Bounds:** `min = 1` (never zero OCR capacity). `max` is set by budget, starting at about 2× the break-even node count.
- **Capacity sync** keeps the gate consistent with the fleet. An EventBridge rule on ASG lifecycle events triggers a Lambda, which `SET`s `mineru:pages_capacity` from the target group's healthy-host count:
  - Launch: raise the key only once the target is _healthy_.
  - Terminate, via an `EC2_INSTANCE_TERMINATING` hook with a 10 min heartbeat:
    1. Lower the key.
    2. Deregister the node. The ALB deregistration delay is ≥ 420s, matching MinerU's `proxy_read_timeout`.
    3. Complete the lifecycle action.
  - Every 5 min, re-sync the key from the healthy-host count as a failsafe. If the key is missing, the gate falls back to the env value for one node.
- **Alarms:**
  - MinerU 5xx/504 rate (baseline 21 in 59h).
  - `PagesInFlight > PagesCapacity`, which means a gate bug.
  - `EstimatedLagHours > 6`.
  - Metrics missing for 5 min.

## Where documents wait: the two queues

More workers than GPU room does not mean more load on MinerU. Each document passes two waiting areas, both in Redis:

| Waiting area                       | Who is in it                                                                  | Cost                                 |
| ---------------------------------- | ----------------------------------------------------------------------------- | ------------------------------------ |
| Celery queue `policy_extract`      | Documents no worker has picked up yet                                         | Nothing                              |
| Gate wait line `mineru:gate:queue` | Documents a worker holds, PDF downloaded and prechecked, waiting for GPU room | One idle worker each: memory, no CPU |

A worker picks up one document (prefetch 1), downloads it, counts its pages, then asks the gate for room. The gate admits it only while `pages in flight + its pages ≤ capacity`; everyone else sleeps in the wait line and re-checks every 0.5–1s. When a MinerU call finishes, its pages are released and the next document in line goes within a second. The Gemini stages run outside the gate.

Example, 48 workers and 1 GPU (75 pages): about 12 documents are at the GPU, about 36 wait in the gate line, the rest stay in the Celery queue. MinerU never sees more than 75 pages. When a second GPU comes up, capacity becomes 150 and the next ~12 in line are admitted immediately.

A document that waits longer than `POLICY_EXTRACT_GATE_MAX_WAIT` (240s) goes back to the Celery queue after a 5–30s delay as a fresh message. That does **not** count against the MinerU retry budget, so waiting for GPU room can never fail a document.

## Sizing workers for the GPU budget

Size OCR workers for the **maximum** GPU count, not the current one. Waiting workers are cheap, and they are what lets new GPU capacity be used the moment it appears.

OCR workers ≈ GPU page capacity ÷ mean pages per document (÷ a further 0.78 with the split off). At 75 pages per GPU and 6.25 pages per document:

| GPUs | Page capacity | OCR workers, split on | OCR workers, split off | RAM, split on (~0.5 GB each) |
| ---- | ------------- | --------------------- | ---------------------- | ---------------------------- |
| 1    | 75            | 12                    | 16                     | ~6 GB                        |
| 2    | 150           | 24                    | 31                     | ~12 GB                       |
| 4    | 300           | 48                    | 62                     | ~24 GB                       |

**Rule: raise `POLICY_EXTRACT_CONCURRENCY` to the max-fleet value before enabling GPU autoscaling.** Otherwise new GPUs sit idle: at the current default of 10, workers cannot even fill one GPU (10 × 6.25 ≈ 62 pages < 75). Re-check the table when `MeanPagesPerDoc` drifts, since a lighter mix needs more workers for the same pages.

If one host cannot hold the max-fleet count, either add worker hosts in step with GPUs (below), or try Celery's `--autoscale=<max>,<min>` on one host to shed idle workers overnight. The gate still caps MinerU load either way. `--autoscale` is untested with `acks_late` and prefetch 1, so run it on the e2e mock first.

## Celery workers (docker compose on EC2)

- **OCR workers** (`celery_policy_extract`): a static count, sized for the GPU fleet's _max_ capacity:
  - With the split on: `POLICY_EXTRACT_CONCURRENCY ≥ max_nodes × C_node ÷ pages_per_doc`.
  - Before the split: divide by a further 0.78.
  - Children waiting on the gate are idle and cheap: about 512 MB RAM each, no CPU.
  - If one host's RAM cannot hold that, add a worker ASG whose desired count the same capacity-sync Lambda sets to `ceil(nodes ÷ nodes_per_worker_host)`. Do not give it a scaling loop of its own.
- **LLM workers** (`celery_policy_extract_llm`): static, `POLICY_EXTRACT_LLM_CONCURRENCY=4` per OCR host. Gemini's rate limit is their ceiling. Alarm if `LlmQueueDepth` keeps growing for more than 15 min.
- **Restarts and scale-in:** keep `stop_grace_period: 300s`. A worker-ASG terminate hook needs a heartbeat ≥ `POLICY_EXTRACT_TIME_LIMIT` (1080s) so warm shutdown can finish in-flight tasks. `acks_late` plus `reject_on_worker_lost` cover a hard kill. Restart workers with `up -d --no-deps <service>` only; restarting the whole stack caused API 502s on 6 Oct.
- **Not scaled:** the API, beat (must stay a single instance), and the default `celery` worker. The reconciler and the metrics publisher both depend on the default worker.

## Rollout

Deploy each step in the 02:00–08:00 India time window. Allow a 5 min ramp, then judge it on a window of at least 30 min, in **pages/hour**, cross-checked against MinerU's own pages/hour for the same interval.

1. Reconciler routing and the concurrency default of 10. Low risk.
2. `POLICY_EXTRACT_METRICS_ENABLED=true` with the gate off. Collect a week of `MeanPagesPerDoc` and `PagesPerHour`.
3. `POLICY_EXTRACT_PAGE_GATE=true` at 75 pages with one node, and raise `POLICY_EXTRACT_CONCURRENCY` per the formula (about 16 for one GPU with the split off).
   - Success: `PagesInFlight` holds near capacity instead of about 78%, and pages/hour rises.
   - Flat pages/hour means the server was already saturated: lower the target.
4. Start `celery_policy_extract_llm`, then `POLICY_EXTRACT_SPLIT_LLM=true`.
5. Raise `POLICY_EXTRACT_CONCURRENCY` to the max-fleet value from the sizing table, then run the GPU ASG in shadow mode (alarms and dashboards, scaled by hand), then enable the step policies and the scheduled baseline.

Rollback for every step is its flag. With the split off, messages already on `policy_extract_llm` still drain as long as the LLM worker keeps running, so stop that worker only once its queue is empty.

## Deployment-repo handoff (`on-prem-insur-iq`)

- **Concurrency:**
  - `helm/insur-iq/values.yaml`: `celery.policyExtract.concurrency: 12 → 10`.
  - `deploy/ec2/docker-compose.app.yml`: default `:-10`.
  - `deploy/ec2/app.env.example` example `10`, and fix the stale comment in the live `app.env`.
- **New env**, added to the `&backend-env` anchor, Helm `config:`, and `app.env.example`:
  - `POLICY_EXTRACT_PAGE_GATE`, `POLICY_EXTRACT_MAX_PAGES_IN_FLIGHT`, `POLICY_EXTRACT_GATE_MAX_WAIT`, `POLICY_EXTRACT_GATE_RESERVE_PAGES`
  - `POLICY_EXTRACT_SPLIT_LLM`, `POLICY_EXTRACT_LLM_CONCURRENCY`
  - `POLICY_EXTRACT_METRICS_ENABLED`, `POLICY_EXTRACT_METRICS_REGION`
- **LLM worker:**
  - Helm: a `celery.policyExtractLlm` values entry (`queues: policy_extract_llm`, `concurrency: 4`), plus an `else if` branch in `templates/pdb.yaml`.
  - EC2: a `celery_policy_extract_llm` service running `entrypoint.sh celery-policy-extract-llm` (`*backend-env`, `stop_grace_period: 300s`, the GCP volume).
- **Keep the env var name `POLICY_EXTRACT_CONCURRENCY`.** Renaming it in the image, for example to the `CELERY_CONCURRENCY` that MinerU proposed, silently drops the production value.
- **Terraform:**
  - GPU ASG, launch template and AMI pipeline, warm pool.
  - Step and scheduled policies, lifecycle hooks, the EventBridge rule and the capacity-sync Lambda (in the VPC, with access to the broker Redis).
  - ALB deregistration delay ≥ 420s, the CloudWatch alarms above.
  - `cloudwatch:PutMetricData` on the worker host role.

## Still owed by MinerU

- The full sweep curve: throughput at each concurrency, with pages/doc and n per point. This sets `C_node` properly.
- Whether their own limiter gates requests or pages.
- Pages/hour for the intervals we measured.
