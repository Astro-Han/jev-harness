# TB4 tool-result filtering: outcome and trajectory analysis

## Decision

**The current Jev integration did not demonstrate enough value to justify
default adoption or further large-scale runs. This experiment is closed.**

With `deepseek-flash`, a minimal agent harness, and a 3,600-second / 150-turn
limit per arm, the filtered arm had fewer successful tasks and higher
cumulative input usage and estimated model cost. These observations support
stopping investment in this implementation. They do not establish that Jev is
generally ineffective, or that relevance filtering caused the success-rate
difference.

Each task had only one run per arm. Trajectory inspection found output-budget
failures, environment limitations, and divergent execution paths. Fixing those
problems would make a future experiment easier to interpret; it would not, by
itself, establish a benefit from Jev. Another experiment would need a specific
mechanism or use case with a credible benefit over its added calls, latency,
readbacks, and failure handling.

## Scope and results

The TB4 manifest contained 66 tasks. A scored pair requires both arms to have
a trajectory and a parseable verifier reward: 49 tasks qualified. Partial
scores require test counts in both arms: 42 pairs qualified. Missing scores
are excluded, not treated as model failures. This is a custom harness
experiment, not an official leaderboard submission.

| Metric | Jev on | Jev off | Difference |
|---|---:|---:|---:|
| Full task success, 49 pairs | 2/49 (4.1%) | 5/49 (10.2%) | -6.1 percentage points |
| Mean per-task test pass fraction, 42 pairs | 44.27% | 48.04% | -3.77 percentage points |
| Cumulative input tokens, 49 pairs | 528,867,156 | 473,139,900 | +11.78% |
| Estimated model cost, 49 pairs | $13.0004 | $12.0181 | +8.17% |
| Model turns, 49 pairs | 3,803 | 3,578 | +6.29% |

The [per-task metric snapshot](tb4-results-2026-09-22.json) includes the full
manifest, inclusion flags, rewards, test counts, and recorded usage. Raw
trajectories and task workspaces remain local; references below identify the
inspected records, not publicly downloadable traces. The snapshot supports
aggregate arithmetic, but does not independently reproduce trajectory claims.

Input tokens are summed over requests, including cache hits. They are not
unique tokens or peak context length. Estimates use the experiment's fixed
rates per million tokens: $0.006 cached input, $0.30 uncached input, $1.20
output, and $0.042 Jev input. They are not settled invoices, exclude CPU,
memory, and storage, and do not include every failed or superseded attempt.

Only five pairs disagree on full success. Jev on alone passes
`interleaved-vigenere`; off alone passes `mp-checkpoint-consolidation`,
`mvcc-lsm-compaction`, `uefi-bootkit`, and `vf2-speedup-networkx`.
The exact two-sided McNemar p-value is 0.375. This does not establish a
statistically significant degradation, and non-significance does not establish
equivalence.

## What the trajectories establish

### Unbounded output caused a concrete failure

In `mvcc-lsm-compaction-on`, a generated fuzz test emitted 39,560,684 characters
of repetitive mismatch logs. Jev returned HTTP 400; the fail-open path passed
the entire output to the main model. The next request contained 15,828,174
tokens against a 1,048,576-token limit. The harness retried the same invalid
request three times before exiting. The on arm passed 10/15 tests; off passed
15/15. Evidence: `runs/mvcc-lsm-compaction-on/out/run.jsonl`, lines 39–43, and
the corresponding `final.txt` exception.

The supported chain is **unbounded tool output → filter error → unbounded
fallback → context overflow**. The server-side reason for the initial Jev 400
was not recorded. No output was successfully elided in this on-arm run, so
this is not evidence that Jev deleted necessary information. A shared harness
defect was triggered by one arm's execution path.

There is also a concrete chunk-size problem. `chunk_output` groups lines but
does not split oversized individual lines, and the batching code cannot split
an oversized chunk. Offline application of that function to the recorded
output at `legacy-utility-triage-on/out/run.jsonl:242` produces a 206,400-character
chunk, exceeding the roughly 100k-character batch target. This confirms a size
constraint failure, not the cause of every recorded HTTP 400.

### A few divergent paths dominate the input difference

The net input increase is 55.727M tokens. `legacy-utility-triage` alone adds
41.73M, about 74.9% of that net difference. Its on arm repeatedly searched for
a legacy workstation/VNC service and scanned system files and network ports.
Outputs of roughly 220k, 412k, and 944k characters entered the conversation
without reduction and were carried into subsequent requests. Off also searched
the environment, but produced much less output. This is consistent with the
recorded sidecar-support limitations; the service deployment was not independently
rechecked during the final offline analysis.

More spending is not always wasted work: `interleaved-vigenere` on used 150
turns and succeeded, while off used 35 and failed. On spent an additional
$0.543 and 28.34M input tokens on that task. The aggregate 225 additional turns
cannot all be attributed to filtering-induced readbacks.

### Four off-only successes are not four proven filtering mistakes

| Task | Evidence from the on trajectory | Attribution limit |
|---|---|---|
| `mvcc-lsm-compaction` | Unbounded fallback caused context overflow; no successful elision | Not an information-deletion failure |
| `mp-checkpoint-consolidation` | Only 248 characters were dropped: four `grep: ... binary file matches` notices. Main source files were read through unfiltered `read` calls | No evidence that this deletion caused the different consolidation strategy |
| `uefi-bootkit` | A 2,449-character string scan was elided and read back on the next turn; on reached 150 turns while still debugging, off used 126 | A real readback cost, but not a proven decisive cause of failure |
| `vf2-speedup-networkx` | A 2,664-character source fragment was dropped, followed by later source reads. On ends after 29 model turns with no final event and an empty `final.txt`; off used 122 turns | The termination cause is not sufficiently recorded; do not label it a timeout or a normal completion |

The checkpoint fragment was checked against its saved `.jev-store` original.
UEFI's elision and immediate readback appear at lines 70 and 73–74 of its on-arm
JSONL. VF2's elision appears at line 28; later source reads occur at zero-based
turns 10 and 11. These observations narrow plausible causes but do not replace
controlled reruns.

### Filtering and readbacks need careful denominators

Across the 49 scored on arms:

- 877 successful Jev calls covered 5,966,498 characters and recorded about
  939 seconds of cumulative call latency; there were 18 `jev_error` events.
- 287 tool outputs had some content elided. They dropped 1,230,126 original
  characters; inserted markers reduce net savings to 1,199,681 characters.
- Raw tool output totaled 51,475,161 characters, versus 50,275,480 shown:
  a net reduction of 2.33%.
- There were 70 explicit `read` calls into `.jev-store`, presenting 333,776
  characters. This excludes bash readbacks and is not the amount of previously
  dropped content restored: saved files also contain retained text and `read`
  adds line numbers.

The single 39.6MB overflow output dominates the denominator. Neither the 2.33%
net reduction nor the approximately 11.6% successfully judged fraction should
be interpreted as a typical per-task rate. Character reduction also does not
directly measure token savings. `read`, commands mentioning `.jev-store`, and
single-chunk outputs bypass filtering in the evaluated implementation.

## Incomplete tasks and comparison limits

Seventeen tasks did not form scored pairs:

| Group | Tasks |
|---|---|
| GPU unavailable | `fp8-rmsnorm-gemm`, `jax-speedrun-gpu`, `math-eval-grader` |
| FreeCAD build failures | `freecad-impeller`, `freecad-platform-drawing`, `freecad-spring-clip` |
| Dependency failure during interrupted recovery | `glycan-ms2-elucidation` |
| Final recovery batch unfinished | `gsea-proteomics`, `hof-topology-interpenetration`, `html-js-filter`, `intrastat-meldung`, `risk-scorer-replay`, `roy-polymorph-cn`, `rs-archive-clone` |
| No valid verifier reward | `live-database-cutover`, `payments-pipeline-fix`, `vpp-loss-divergence` |

The last group was attributed in the operational handoff to collect-hook
dependencies and verifier interruption; those remote causes were not
independently rechecked before closure. GSEA commands could not be dispatched
during platform migration. Unfinished tasks are not all model failures.

The harness exposes only `bash`, `read`, and `apply_patch`. Its time/turn limits
and incomplete environment support limit representativeness. Results must not
be compared directly with official runs using different harnesses and budgets.

A separate older 15-pair `tb-partial` subset scored 13/15 on and 14/15 off.
It is not pooled with TB4 or treated as an independent replication of the
entire [earlier 30-task experiment](RESULTS.md).

## Corrections to interim summaries

Earlier $14.14/$13.39 estimates and roughly 587M/542M input totals included
unscored tasks. They must not be presented as costs for the 49 scored pairs.
All principal usage and cost figures above use the same 49-pair subset.

The interim 55.1%/61.2% partial-score summary could not be reproduced under
the final paired definition. It is replaced by 44.27%/48.04%, the macro-average
over 42 pairs with test counts in both arms. The earlier count of 144 readbacks
is also not retained: a substring search for `.jev-store` incorrectly includes
commands that merely exclude that directory from searches.

## Infrastructure cost and closure

The experiment VM had 16 vCPUs, 32 GiB RAM, and a 256 GiB disk, with automatic
idle suspension disabled. At the [published Runta rates](https://runta.com/pricing/)
checked on 2026-09-22, that configuration costs approximately $1.35 per running
hour. Shutdown stops CPU and memory charges but leaves approximately $0.66/day
of disk charges; deletion ends that runtime's resource charges.

The organization-level daily usage totals for September 19–22 summed to
$55.04 at inspection. The dashboard marked usage as stale, and those totals
include earlier experiments and potentially other historical VMs. They are
not a settled invoice for this TB4 VM and must not be combined with the model
estimates as an exact experiment total.

Avoidable overhead included failed result transfers leading to reruns,
resource reservation while waiting, no automatic shutdown after completion,
and a tmux prefix-match bug that made a recovery session wait for itself.
The initially reported six-hour idle interval was overstated: it included
time when the preceding batch was still running. The confirmed unnecessary
wait after that batch ended was about 77 minutes, approximately $1.74 at the
running rate; exact billing requires state-level usage records.

The VM was shut down and then deleted with explicit authorization. A subsequent
lookup returned `NOT_FOUND`. Local scored trajectories, rewards, and this report
were verified before deletion; no complete remote-disk backup is claimed.
Automatic reruns are disabled. No new experiment was run for this analysis.

Future work, if justified by a new hypothesis, should first address environment
preflight, bounded output and fallback behavior, reliable exit records, verified
artifact collection, and a total time/cost limit with automatic shutdown.
These are experiment-quality requirements, not evidence that Jev will win once
they are fixed.
