# BioSimulations legacy services — operations notes

Notes on the **legacy** biosimulations.org stack (the `biosimulations/biosimulations`
Nx monorepo, `../biosimulations`), kept here because this repo (`platform`) is the
migration target for those capabilities. Anything we rebuild here should not inherit
the failure modes documented below.

---

## Investigation 2026-08-28 — "Simulation Service" heartbeat flapping

### Symptom

BetterStack heartbeat monitor **"Simulation Service"**
(`https://uptime.betterstack.com/api/v1/heartbeat/oonpXfsb5R3ncAu8JkXFzndp`,
*expected every 1 hour*) reports chronic downtime:

| Window | Availability | Incidents |
|---|---|---|
| Today | 18.4% | 2 |
| Last 7 days | 82.5% | 17 |
| Last 30 days | 91.8% | 45 |
| Last 365 days | 89.3% | **759** |

759 incidents/year ≈ 2 per day, every day, for a year.

### Conclusion

**The service is not down. The monitor is measuring GitHub Actions' cron scheduler,
not biosimulations.org.**

At the time of investigation the monitor had been "down 2 h 51 m" while the API was
healthy:

```
https://api.biosimulations.org/health         200  0.63s
https://api.biosimulations.org/               200  0.22s
https://api.biosimulators.org/simulators/latest  200  1.53s
```

…and every recent run of the workflow that emits the heartbeat had concluded
**success**. The heartbeat simply had not been *sent*, because GitHub never
triggered the workflow.

### How the heartbeat is produced

`.github/workflows/testSimulationsWorking.yaml` in `biosimulations/biosimulations`:

```yaml
on:
  schedule:
    - cron: '*/5 * * * *'      # nominally every 5 minutes
jobs:
  testSimulations:             # submits a real simulation to prod and polls it
    ...
    run: ./tools/submit-example-simulation-runs \
           --runbiosimulations-deployment org \
           --biosimulators-deployment org \
           --test true \
           --example "Repressilator (Elowitz & Leibler, Nature, 2000; SBML; CVODE; tellurium)"
  sendHeartbeat:
    needs: testSimulations
    steps:
      - if: ${{ success() }}
        run: curl ${{ secrets.SIMULATIONS_HEARTBEAT }}
```

So the heartbeat fires **only** when a GitHub-hosted scheduled run both *happens*
and *passes*. Missing heartbeat ⇒ BetterStack incident. There is no distinction
between "the service broke" and "GitHub didn't run the job".

This is the only high-frequency scheduled workflow in the repo — every other
`schedule:` there is weekly or yearly. `SIMULATIONS_HEARTBEAT` is the only
heartbeat secret in the repo.

### Root cause: GitHub Actions never honours the `*/5` cron

`biosimulations/biosimulations` is a **public repo on free-tier Actions**. GitHub
documents `schedule` as best-effort ("can be delayed during periods of high load…
some queued jobs may be dropped"). Measured delivery rate against the nominal
288 runs/day:

| Period | Runs | Runs/day | % of nominal |
|---|---|---|---|
| 2026-06-01 → 06-30 | 748 | 25 | 8.7% |
| 2026-07-01 → 07-15 | 503 | 33 | 12% |
| 2026-07-16 → 07-28 | 486 | 37 | 13% |
| 2026-07-29 → 08-14 | 779 | 46 | 16% |
| 2026-08-15 → 08-28 | 1053 | 75 | 26% |

Best month observed is ~26% delivery; the long-run average is ~10–15%. Recent
days collapsed much further:

| Day | Runs | % of nominal |
|---|---|---|
| 2026-08-22 | 100 | 34% |
| 2026-08-23 | 97 | 33% |
| 2026-08-24 | 77 | 26% |
| 2026-08-25 | 51 | 17% |
| 2026-08-26 | 57 | 20% |
| 2026-08-27 | **9** | 3% |
| 2026-08-28 (partial) | **5** | 3% |

Gap statistics over the last ~200 runs (2026-08-24 → 08-28):

- min 2.9 min, **median 17.9 min**, mean 30.5 min, **max 246 min**
- **17 of 199 gaps exceeded 60 minutes** — i.e. 17 guaranteed BetterStack incidents,
  which matches the reported "17 incidents in the last 7 days" almost exactly.

Worst recent gaps, all bracketed by **successful** runs:

```
246.5 min  ending 2026-08-28T15:58:55Z
238.2 min  ending 2026-08-28T11:52:26Z
225.7 min  ending 2026-08-27T16:51:09Z
213.4 min  ending 2026-08-27T13:05:26Z
210.5 min  ending 2026-08-27T20:21:39Z
```

Corroborating evidence that this is scheduler drift, not our jobs:

- Queue delay (`created_at` → `started_at`) is **0 s** on every run — the runs that
  do fire start immediately. The loss is entirely GitHub declining to *create* runs.
- `created_at` minute-of-hour is uniformly spread across `mod 5 = {0,1,2,3,4}`
  (39/32/35/41/53). A real `*/5` cron would land on `mod 5 == 0` every time.
  GitHub is firing at arbitrary minutes.
- Job durations are ~55–70 s, so overlap/self-contention is not the cause.
- The workflow's `state` is `active`; it has not been auto-disabled.

**A 1-hour heartbeat expectation is unsatisfiable by a GitHub Actions cron.** The
monitor has been misconfigured since it was created, which is why the all-time
incident count is 759.

### Secondary finding: real failures are rare, and badly reported

Over the last 200 runs: 185 success, 13 failure, 1 startup_failure. The failures
cluster into two short windows, and neither is "the API is down":

**2026-08-24 19:56 → 22:23 (11 consecutive failures).** `tools/submit-example-simulation-runs`
shells out to `curl` to POST `/runs` and then `json.loads()` the stdout:

```
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
RuntimeError: Simulation could not be run on https://api.biosimulations.org/runs:
```

The error message is **empty** because `curl` returned no body. The script invokes
`curl` with no `--fail`, no `--max-time`, no `--retry`, and never inspects
`process.returncode` or the HTTP status — so a DNS blip, a TLS reset, a 502 from
the ingress and a genuine API outage are all indistinguishable, and all report as
an empty string.

**2026-08-26 15:26 → 16:00 (2 failures + 1 startup_failure).** Job `cancelled`
after 15 min. Cause is the polling timeout: `--test true` puts the script into
`monitor_runs()`, which polls the run until `SUCCEEDED`, bounded by `--timeout`
(**default 999 s ≈ 16.6 min**; the workflow never overrides it). When the
simulation queue is backed up the run exceeds 999 s and the job fails. One run on
2026-08-26T18:01 took **2772 s (46 min)** and still passed. So "the simulation
took longer than 16 minutes" pages exactly like "the platform is broken".

Minor bug in `monitor_runs()`: on `raise_for_status()` failure it removes the run
from `pending_runs` and appends to `failed_runs`, then falls through to
`sim = response.json()` on the same failed response — which will itself raise.

### What this costs

Every trigger submits a **real simulation run to production** (`publishToBioSimulations: false`,
so it isn't published, but it does consume queue/compute). At ~75 runs/day that is
a steady synthetic load on the legacy cluster.

---

## Recommendations

Ordered by value. (1) and (2) stop the false pages; the rest are hygiene.

**Status as of 2026-09-04** — items 2, 3, 5, 6 and 7 were implemented in
`biosimulations/biosimulations` and merged to `dev`:

| # | Recommendation | Status |
|---|---|---|
| 1 | Split API-liveness from end-to-end probe | **Open** — needs a new BetterStack HTTP monitor |
| 2 | Report failure explicitly via `/fail` | **Done** — [#4914](https://github.com/biosimulations/biosimulations/pull/4914), refined in [#4915](https://github.com/biosimulations/biosimulations/pull/4915) |
| 3 | Honest cron interval | **Partly done** — cron is now `*/15` (#4914). The monitor's 1 h period still needs raising to ≥ 6 h, which is a BetterStack setting, not a repo change |
| 4 | Move the scheduler off GitHub Actions | **Open** — the durable fix; see *Implications* below |
| 5 | Fix the submit script's error reporting | **Done** — [#4915](https://github.com/biosimulations/biosimulations/pull/4915) |
| 6 | Separate "timed out" from "failed" | **Done** — [#4915](https://github.com/biosimulations/biosimulations/pull/4915); exit 1 = failure, exit 2 = timeout |
| 7 | Add a `concurrency:` group | **Done** — [#4914](https://github.com/biosimulations/biosimulations/pull/4914) |

The two open items are the ones that cannot be fixed from inside the repo (1, and
the monitor-period half of 3) and the one that replaces the mechanism outright (4).

1. **Split "is the API up?" from "does an end-to-end simulation work?"**
   These have wildly different natural periods and should not share a monitor.
   - API availability → a plain BetterStack **HTTP monitor** on
     `https://api.biosimulations.org/health`, checked every 30–60 s. This is the
     real availability number, and it is not hostage to GitHub.
   - End-to-end simulation → keep the workflow, but on its own heartbeat with a
     grace period GitHub can actually meet.

2. **Report failure explicitly instead of relying on a missed heartbeat.**
   BetterStack supports `POST <heartbeat-url>/fail` (and `/<exit-code>`). Change
   the workflow so a *failed* test posts `/fail` and a passing test posts the bare
   URL. Then:
   - `/fail` ⇒ the service genuinely broke (actionable page),
   - missed heartbeat ⇒ GitHub didn't run the job (not a page, or a much lazier one).

   ```yaml
     sendHeartbeat:
       needs: testSimulations
       if: always()
       runs-on: ubuntu-latest
       steps:
         - run: |
             if [ "${{ needs.testSimulations.result }}" = "success" ]; then
               curl -fsS --retry 3 "${{ secrets.SIMULATIONS_HEARTBEAT }}"
             else
               curl -fsS --retry 3 "${{ secrets.SIMULATIONS_HEARTBEAT }}/fail"
             fi
   ```

3. **Set the heartbeat period to reality.** With measured delivery of 3–34% of a
   `*/5` cron, the observed p100 gap is ~4 h and quiet days deliver ~5 runs. If the
   heartbeat must stay on GitHub Actions, the expectation needs to be **≥ 6 h**
   (12 h to be safe), not 1 h. Also drop the cron to something honest —
   `*/15` or `0 * * * *` — since `*/5` is fiction and just burns Actions capacity.

4. **Better: move the scheduler off GitHub Actions.** A Kubernetes `CronJob` in the
   cluster that already hosts the service gives an exact cadence and can keep a
   1-hour (or tighter) heartbeat honestly. This is the approach to adopt for
   `platform` rather than porting the Actions cron forward.

5. **Fix the submit script's error reporting** (`tools/submit-example-simulation-runs`):
   use `requests.post()` (the script already depends on `requests`) or at minimum
   `curl --fail --show-error --max-time 30 --retry 3`, check the return code, and
   include the HTTP status in the `RuntimeError`. Today an outage and a network
   flake produce the same empty message.

6. **Separate "timed out" from "failed."** `--timeout` defaults to 999 s while real
   runs have been observed at 2772 s. Either raise it (and pass it explicitly in
   the workflow) or exit with a distinct code so slow-queue conditions don't page
   as breakage.

7. **Add a `concurrency:` group** to the workflow so long runs can't stack up.

### What the implemented shape looks like

After #4914 and #4915 the workflow no longer reports a bare pass/fail. The probe
maps its exit code to a *verdict*, and reports nothing at all when it cannot
tell — a timeout, a run cancelled by the concurrency group, or a job that died
before the probe ran (broken checkout, failed `pip install`):

| Probe exit | Verdict | Reported to the monitor |
|---|---|---|
| 0 | `success` | heartbeat |
| 1 | `failure` | `/fail` |
| 2 | `timeout` | nothing — silence, absorbed by the grace period |
| job died first | *(none)* | nothing |

That is the property worth preserving in any replacement: **a monitor should
only ever hear a verdict the probe actually reached.** Silence must mean "we
don't know", never "we're fine" and never "we're broken".

---

## Implications for `platform`

- Don't port the GitHub-Actions-cron heartbeat pattern into this repo. Synthetic
  end-to-end checks belong on an in-cluster scheduler (`CronJob`), with GitHub
  Actions reserved for CI.
- Keep the two monitor classes distinct from day one: a cheap, high-frequency HTTP
  liveness check against the API, and a low-frequency end-to-end simulation probe
  that reports pass/fail explicitly rather than by silence.
- Any probe that submits real work to production should say so loudly, be rate
  limited, and be tagged so its runs are excluded from user-facing listings and
  statistics.

---

## Reference: how to re-run this analysis

```bash
cd ../biosimulations

# recent runs + conclusions
gh run list --workflow=testSimulationsWorking.yaml --limit 100

# delivery rate for an arbitrary window
gh api "repos/biosimulations/biosimulations/actions/workflows/\
testSimulationsWorking.yaml/runs?per_page=1&created=2026-08-15..2026-08-28" \
  --jq '.total_count'

# is the workflow still enabled?
gh api repos/biosimulations/biosimulations/actions/workflows/testSimulationsWorking.yaml \
  --jq '{name,state}'

# why did a specific run fail?
gh api repos/biosimulations/biosimulations/actions/runs/<id>/jobs \
  --jq '.jobs[] | "\(.name) \(.conclusion) \(.started_at)->\(.completed_at)"'
gh run view <id> --log-failed

# is the service actually up?
curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" https://api.biosimulations.org/health
```
