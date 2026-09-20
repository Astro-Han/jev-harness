# jev-harness

A minimal coding agent that filters every tool result through a small relevance
model ([Jev](https://vercel.com/ai-gateway/models/jev)) before the main model
sees it, plus an A/B harness that runs the same tasks with filtering on and off.

The question: can a clean, filtered context plus a cheap model match a
conventional harness at lower cost?

On 30 tasks (Terminal-Bench 2.1 + DeepSWE), one run per arm, with
`deepseek-flash` as the main model:

| | pass@1 | cost | cost per passing task |
|---|---|---|---|
| Jev on | **25 / 30** | $2.82 | **$0.113** |
| Jev off | 22 / 30 | $2.80 | $0.127 |

The gap is entirely on DeepSWE (6/9 vs 3/9); on Terminal-Bench the two arms tie
at 19/21. See [RESULTS.md](RESULTS.md) for per-task numbers and caveats — with
one run per arm this is a prototype signal, not a significance claim.

## How it works

- Main model over the OpenAI Responses API (`deepseek-flash` supports it natively).
- Three tools only: `bash`, `read`, `apply_patch`.
- Every tool result except `read` goes through Jev first: the output is split
  into ~2k-char chunks on line boundaries, and each chunk gets one boolean
  question — *does this part contain information the agent is looking for in
  this step, or otherwise needs to complete the task?* Chunks are kept at
  p > 0.5; dropped runs collapse into `⟨… N chars elided → full output: PATH ⟩`.
- The raw output is always stored at `.jev-store/<call_id>.txt`, and `read` can
  retrieve it. Filtering is a routing decision, never destruction.
- Jev failures fail open: the unfiltered output is returned and the error logged.

### Intent is what makes the filter work

The filter state is `{task, intent, action, output_parts}`, where **intent is
the agent's own reasoning from the turn that issued the tool call** — *why* it
ran the command.

Relevance is a property of the step, not of the task. `sed -n '360,500p'
vm/vmStmt.go` looks like noise against a task description, but it is exactly
what the agent just asked for. Without intent, Jev scored such deliberate reads
at p≈0.1–0.4 and elided them whole; the agent then saw only the marker and
`read` the raw output straight back.

In the first 11 on-arm runs, 49 elided outputs were read back — 39 of them
fully elided — returning ~281k of the ~285k characters cut. Net saving: zero,
plus the extra turns. Replaying the same 63 decisions with intent added, fully
elided outputs that had been read back fell from 33 to 12, and most of the
remainder were off-task probes (setup logs, web searches) where dropping is
correct.

Two smaller rules come from the same evidence:

- **Contains-signal, not majority-noise.** A chunk is kept if even one line in
  it is useful. The "is this part noise?" framing tested worse: it drops a chunk
  that is 99% digits with one ERROR line.
- **Single-chunk outputs skip Jev entirely.** With one part, filtering is
  all-or-nothing, and dropping a whole short output (a 46-char `Success` went
  this way) is the costliest error direction.

## Usage

```bash
export DEEPSEEK_API_KEY=...       # or set MODEL / DEEPSEEK_BASE_URL to swap model/gateway
export AI_GATEWAY_API_KEY=vck_... # Vercel AI Gateway key

uv run jev_agent.py --task instruction.md --workdir ./task-env --jev on  --log run-on.jsonl
uv run jev_agent.py --task instruction.md --workdir ./task-env --jev off --log run-off.jsonl
```

`--jev off` is the control arm: same harness, no filtering. The JSONL log
records per-chunk probabilities, keep/drop decisions, latency, model usage, and
`tool_io.shown` — the exact text the model saw, enough to rebuild the trajectory.

In an eval runner, pass keys as `--secrets FILE` instead: the agent loads the
file and deletes it, so its own `bash` tool cannot find them. This is not
hypothetical — agents `cat`-ed a sourced secrets file and used the gateway key
to call other models.

### Running the A/B evaluation

`run_task.sh` runs one task in both arms inside a container on a
[Runta](https://runta.dev) runtime, then grades it with the benchmark's own
verifier; `run_batch.sh` drives a set of tasks, launching each task's two arms
simultaneously so both see the same machine load. `summarize.py` pairs the runs
and prints pass/cost per task.

```bash
./run_batch.sh <runner> <tasks-in-parallel> [task...]
python3 summarize.py runs/
```

The task set is [frontier-harness-eval](https://runta.dev): 21 Terminal-Bench
2.1 tasks and 9 DeepSWE tasks. Runs are unranked internal comparisons, not
leaderboard submissions.

## Protocol notes (verified against the live DeepSeek Responses API)

- `function_call_output` must echo `call.call_id`, not the item `id`.
- **All response items for a turn must enter `input` before any
  `function_call_output`.** Interleaving an output between function calls
  detaches the later calls from their reasoning, and the next request is
  rejected with 400 ("reasoning_text in the thinking mode must be passed back").
- `apply_patch` uses the Codex patch grammar (`*** Begin Patch` / `Add File` /
  `Update File` with `@@` anchors / `Move to` / `Delete File` / `End Patch`).
  Unlike Codex it stays a JSON function wrapper `{patch: ...}`, because DeepSeek
  custom-tool support is unverified. Patches are validated in memory before any
  write, paths are confined to the workdir, and CRLF / non-UTF-8 files
  round-trip byte-safely.
- Python ≥3.13 enables `VERIFY_X509_STRICT` by default, which rejects a MITM
  egress certificate that carries no Authority Key Identifier. The agent clears
  that one flag rather than disabling verification.

## Status

A prototype. One run per arm, a single main model, and a 30-task set: enough to
show the approach works end to end and to size the effect, not enough to claim
it generalizes.

## License

Apache 2.0
