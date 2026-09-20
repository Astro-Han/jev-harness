# Results

30 tasks from frontier-harness-eval (21 Terminal-Bench 2.1 + 9 DeepSWE), two
arms of the same harness — Jev filtering on and off — with `deepseek-flash` as
the main model. One run per arm per task, September 2026.

**Headline:** pass@1 25/30 with Jev vs 22/30 without, at the same total cost
($2.82 vs $2.80), so cost per passing task falls from $0.127 to $0.113. The
whole gap is on DeepSWE; Terminal-Bench ties.

Costs use DeepSeek peak prices ($0.006/M cached input, $0.30/M uncached input,
$1.20/M output) plus Jev at $0.042/M input. Jev itself was ~1% of the on-arm
cost.


### Terminal-Bench 2.1 (21 tasks)

| task | Jev on | Jev off | cost on | cost off |
|---|---|---|---|---|
| build-cython-ext | ✅ | ✅ | $0.092 | $0.066 |
| chess-best-move | ✅ | ✅ | $0.081 | $0.063 |
| code-from-image | ✅ | ✅ | $0.106 | $0.139 |
| constraints-scheduling | ✅ | ✅ | $0.012 | $0.010 |
| db-wal-recovery | ✅ | ✅ | $0.030 | $0.006 |
| dna-insert | ✅ | ✅ | $0.171 | $0.041 |
| extract-elf | ✅ | ❌ | $0.064 | $0.123 |
| gcode-to-text | ❌ | ❌ | $0.169 | $0.190 |
| git-leak-recovery | ✅ | ✅ | $0.063 | $0.005 |
| kv-store-grpc | ✅ | ✅ | $0.009 | $0.003 |
| largest-eigenval | ✅ | ✅ | $0.122 | $0.237 |
| log-summary-date-ranges | ✅ | ✅ | $0.005 | $0.008 |
| merge-diff-arc-agi-task | ✅ | ✅ | $0.014 | $0.018 |
| modernize-scientific-stack | ✅ | ✅ | $0.006 | $0.005 |
| multi-source-data-merger | ✅ | ✅ | $0.016 | $0.006 |
| openssl-selfsigned-cert | ✅ | ✅ | $0.008 | $0.008 |
| polyglot-c-py | ✅ | ✅ | $0.042 | $0.042 |
| regex-log | ✅ | ✅ | $0.033 | $0.034 |
| sanitize-git-repo | ❌ | ✅ | $0.039 | $0.070 |
| sqlite-db-truncate | ✅ | ✅ | $0.010 | $0.015 |
| vulnerable-secret | ✅ | ✅ | $0.007 | $0.008 |
| **total (21)** | **19** | **19** | **$1.099** | **$1.097** |

### DeepSWE (9 tasks)

| task | Jev on | Jev off | cost on | cost off |
|---|---|---|---|---|
| anko-typed-variable-bindings | ✅ | ❌ | $0.136 | $0.173 |
| arktype-json-schema-refs-dependencies | ❌ | ❌ | $0.172 | $0.193 |
| expr-try-catch-errors | ✅ | ✅ | $0.299 | $0.282 |
| fastapi-deprecation-response-headers | ✅ | ✅ | $0.143 | $0.123 |
| httpx-multipart-response-parsing | ❌ | ❌ | $0.199 | $0.163 |
| katex-multicolumn-array-spans | ✅ | ❌ | $0.159 | $0.131 |
| meriyah-explicit-resource-declarations | ✅ | ❌ | $0.166 | $0.280 |
| python-statemachine-state-data-scoping | ✅ | ❌ | $0.245 | $0.212 |
| scc-bounded-memory-spilling | ❌ | ✅ | $0.202 | $0.150 |
| **total (9)** | **6** | **3** | **$1.720** | **$1.707** |

**All 30:** pass 25/30 vs 22/30 · cost $2.819 vs $2.805 · per pass $0.113 vs $0.127

## Reading this honestly

- **Not statistically significant.** Only 7 of 30 tasks disagree between the
  arms (5 to Jev, 2 against); a sign test gives p≈0.45. One run per arm, and
  same-task variance between runs was large — build-cython-ext's on-arm took 54
  turns in one run and 81 in another.
- **The effect tracks how much there is to filter.** On Terminal-Bench, Jev cut
  ~5% of tool output characters and the arms tied 19/21. On DeepSWE it cut more
  and won 6/9 vs 3/9. The clearest case is `meriyah`: the off arm was shown
  1.46M characters of tool output, hit the 20-minute wall, and scored 0; the on
  arm finished at ~60% of its cost.
- **Both failure directions occur.** The off arm won `scc` (the on arm broke 2
  pre-existing tests) and `sanitize-git-repo`.
- **Infrastructure failures were rerun, not counted.** Dropped file copies, a
  pip TLS failure against a MITM egress cert, and one missing task file produced
  scoreless runs; those pairs were rerun after the fix. No failed-infrastructure
  run is scored as a task failure.
- **Agents cheat when unwatched.** In early runs one agent read a sourced
  secrets file and used the gateway key to call another model for OCR; another
  fetched a task's reference solution from the web. Keys now load-and-delete,
  and tests are staged outside the agent's reach. Those early runs were
  discarded.
