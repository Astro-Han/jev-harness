"""Pair on/off runs under a runs dir and print per-task and total metrics.

Usage: python3 summarize.py [runs_dir]
"""
import json
import sys
from pathlib import Path

# USD per 1M tokens: deepseek-flash peak (cache hit / miss / output), Vercel AI Gateway typesafe-ai/jev
PRICE = dict(hit=0.006, miss=0.30, out=1.20, jev=0.042)


def reward(out: Path):
    for name in ("reward.json", "reward.txt"):
        p = out / "verifier" / name
        if p.exists():
            t = p.read_text().strip()
            try:
                v = json.loads(t)
                return float(v["reward"] if isinstance(v, dict) else v)
            except (ValueError, KeyError, TypeError):
                return None
    return None


def metrics(run: Path):
    out = run / "out"
    m = dict(reward=reward(out), turns=0, inp=0, cached=0, outp=0,
             raw=0, shown=0, jev_calls=0, jev_in=0, jev_ms=0, maxed=False)
    log = out / "run.jsonl"
    if not log.exists():
        return m
    for line in log.open():
        e = json.loads(line)
        t = e["type"]
        if t == "turn":
            u = e.get("usage") or {}
            m["turns"] += 1
            m["inp"] += u.get("input_tokens", 0)
            m["cached"] += (u.get("input_tokens_details") or {}).get("cached_tokens") or 0
            m["outp"] += u.get("output_tokens", 0)
        elif t == "tool_io":
            m["raw"] += e.get("raw_len", 0)
            m["shown"] += len(e.get("shown", ""))
        elif t == "jev_call":
            m["jev_calls"] += 1
            m["jev_in"] += (e.get("usage") or {}).get("inputTokens", 0)
            m["jev_ms"] += e.get("latency_ms", 0)
        elif t == "final":
            m["maxed"] = "max turns reached" in e.get("text", "")
    m["cost"] = (m["cached"] * PRICE["hit"] + (m["inp"] - m["cached"]) * PRICE["miss"]
                 + m["outp"] * PRICE["out"] + m["jev_in"] * PRICE["jev"]) / 1e6
    return m


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "runs")
    tasks = sorted({p.name.rsplit("-", 1)[0] for p in root.glob("*-on")} &
                   {p.name.rsplit("-", 1)[0] for p in root.glob("*-off")})
    cols = "task reward(on/off) turns input_tok(on/off) tool_chars_shown(on/off) cost_usd(on/off)"
    print(cols)
    tot = {a: dict(r=0, n=0, inp=0, outp=0, shown=0, raw=0, jev_in=0, cost=0) for a in ("on", "off")}
    for task in tasks:
        on, off = metrics(root / f"{task}-on"), metrics(root / f"{task}-off")
        print(f"{task:48s} {on['reward']}/{off['reward']}  "
              f"{on['turns']}/{off['turns']}  {on['inp']}/{off['inp']}  "
              f"{on['shown']}/{off['shown']}  {on['cost']:.3f}/{off['cost']:.3f}")
        for a, m in (("on", on), ("off", off)):
            # pass/fail per task: a fractional reward is partial credit, not a pass
            tot[a]["r"] += (m["reward"] or 0) >= 1
            tot[a]["n"] += m["reward"] is not None
            for k in ("inp", "outp", "shown", "raw", "jev_in", "cost"):
                tot[a][k] += m[k]
    print(f"\n{len(tasks)} paired tasks")
    for a in ("on", "off"):
        t = tot[a]
        print(f"{a:3s} pass {t['r']:.0f}/{t['n']}  input_tok {t['inp']}  output_tok {t['outp']}  "
              f"tool_chars raw {t['raw']} shown {t['shown']}  jev_in_tok {t['jev_in']}  "
              f"cost ${t['cost']:.3f}")


if __name__ == "__main__":
    main()
