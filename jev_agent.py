# /// script
# requires-python = ">=3.11"
# dependencies = ["openai>=1.40", "httpx[socks]>=0.27"]
# ///
"""jev-agent: minimal agent harness for the Jev-filter experiment.

3 tools (bash / read / apply_patch). Every tool result except `read` is
filtered by Jev before it enters the model context: the output is split
into chunks, Jev scores each chunk "is this part needed for the task",
needed chunks stay verbatim and the rest is replaced by an elision marker
pointing at the raw output saved under .jev-store/. `read` is never
filtered — it is the retrieval path back to full output.

Usage:
  AI_GATEWAY_API_KEY=... DEEPSEEK_API_KEY=... \
  uv run jev_agent.py --task instruction.md --workdir ./task --jev on
"""
import argparse
import json
import os
import signal
import ssl
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
from openai import DefaultHttpxClient, OpenAI

# The runta egress MITM cert lacks an Authority Key Identifier; Python >=3.13
# turns on VERIFY_X509_STRICT by default and rejects it.
TLS = ssl.create_default_context()
TLS.verify_flags &= ~ssl.VERIFY_X509_STRICT

CHUNK_CHARS = 2000          # line-boundary split target
JEV_BATCH_CHARS = 100_000   # output_parts budget per Jev call (~25k tokens)
READ_LIMIT = 400            # default lines per read call

SYSTEM_PROMPT = """You are an agent completing a task inside a working directory.
Tools available: bash, read, apply_patch. Nothing else.

Tool outputs (except read) are pre-filtered for relevance: parts judged
unnecessary for the task are replaced by a marker
"⟨… N chars elided → full output: PATH ⟩". The complete original output
is always saved at PATH inside the working directory; call read on that
path whenever you need the omitted parts.

Work autonomously until the task is done, then reply with a short summary."""

TOOLS = [
    {
        "type": "function",
        "name": "bash",
        "description": (
            "Run a shell command in the working directory; returns stdout+stderr+exit code. "
            "The output may be filtered: parts irrelevant to the task are replaced by "
            "'⟨… N chars elided → full output: PATH ⟩'. The full original output is saved "
            "at PATH; use the read tool on it to see everything."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run."},
                "timeout": {"type": "integer", "description": "Seconds before kill (default 120)."},
            },
            "required": ["command"],
        },
    },
    {
        "type": "function",
        "name": "read",
        "description": (
            "Read a file and return its contents with line numbers. Use offset/limit to page "
            "through large files. This is the only way to see file content unfiltered: "
            "everything else (including 'cat' via bash) may be filtered. Reads saved raw "
            "tool outputs under .jev-store/. This tool's output is never filtered."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path, absolute or relative to the working directory."},
                "offset": {"type": "integer", "description": "0-based line offset (default 0)."},
                "limit": {"type": "integer", "description": "Max lines to return (default 400)."},
            },
            "required": ["path"],
        },
    },
    {
        "type": "function",
        "name": "apply_patch",
        "description": (
            "Apply a patch to files in the working directory. Format (Codex grammar):\n"
            "*** Begin Patch\n"
            "*** Add File: path\n"
            "+new line\n"
            "*** Update File: path\n"
            "@@ optional anchor\n"
            " context line (kept, space prefix)\n"
            "-removed line\n"
            "+added line\n"
            "*** Delete File: path\n"
            "*** End Patch\n"
            "Use this to create, edit, or delete files. Returns a summary or the error."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "patch": {"type": "string", "description": "Patch text in *** Begin Patch ... *** End Patch format."},
            },
            "required": ["patch"],
        },
    },
]


def log_event(logf, event: dict):
    if logf:
        logf.write(json.dumps(event, ensure_ascii=False) + "\n")
        logf.flush()


def chunk_output(text: str, max_chars: int = CHUNK_CHARS) -> list[str]:
    chunks, cur, size = [], [], 0
    for line in text.splitlines(keepends=True):
        if cur and size + len(line) > max_chars:
            chunks.append("".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line)
    if cur:
        chunks.append("".join(cur))
    return chunks or [""]


class JevFilter:
    """Scores each output chunk via Jev on Vercel AI Gateway; keeps chunks that
    contain needed information (p > 0.5), elides the rest."""

    def __init__(self, task: str, logf=None):
        self.task = task
        self.logf = logf
        self.base = os.environ.get("JEV_BASE_URL", "https://ai-gateway.vercel.sh/v4/ai")
        self.model = os.environ.get("JEV_MODEL", "typesafe-ai/jev")
        self.http = httpx.Client(
            timeout=60,
            verify=TLS,
            headers={
                "Authorization": f"Bearer {os.environ['AI_GATEWAY_API_KEY']}",
                "ai-gateway-protocol-version": "0.0.1",
                "ai-evaluation-model-specification-version": "4",
                "ai-model-id": self.model,
            },
        )

    def judge(self, action: str, intent: str, chunks: list[str], offset: int) -> list[float]:
        questions = {
            f"keep_{offset + i}": {
                "type": "boolean",
                "instructions": f"Does output part {offset + i} contain information the agent is looking for in this step (see intent), or otherwise needs to complete the task?",
            }
            for i in range(len(chunks))
        }
        state = {
            "task": self.task,
            "intent": intent,
            "action": action,
            "output_parts": {str(offset + i): c for i, c in enumerate(chunks)},
        }
        t0 = time.time()
        r = self.http.post(
            f"{self.base}/evaluation-model",
            json={"state": state, "questions": questions},
        )
        r.raise_for_status()
        data = r.json()
        answers = data.get("answers") or {}
        probs = []
        for i in range(len(chunks)):
            a = answers.get(f"keep_{offset + i}") or {}
            p = a.get("probability")
            if p is None:
                raise ValueError(f"missing probability for keep_{offset + i}: {a!r}")
            probs.append(float(p))
        log_event(self.logf, {
            "type": "jev_call", "chunks": len(chunks),
            "chars": sum(len(c) for c in chunks),
            "latency_ms": int((time.time() - t0) * 1000),
            "usage": data.get("usage"),
            "probs": probs,
        })
        return probs

    def probabilities(self, action: str, intent: str, chunks: list[str]) -> list[float]:
        probs, batch, bsize, start = [], [], 0, 0
        for c in chunks:
            if batch and bsize + len(c) > JEV_BATCH_CHARS:
                probs += self.judge(action, intent, batch, start)
                start += len(batch)
                batch, bsize = [], 0
            batch.append(c)
            bsize += len(c)
        if batch:
            probs += self.judge(action, intent, batch, start)
        return probs

    def filter(self, result: str, action: str, intent: str, store_dir: Path, tag: str) -> str:
        chunks = chunk_output(result)
        # a single-chunk result gives Jev only an all-or-nothing choice, and
        # dropping a whole short output (e.g. "Success") is the costly error
        if len(chunks) <= 1:
            return result
        try:
            store_dir.mkdir(parents=True, exist_ok=True)
            # keep harness files out of the submitted diff and git status
            (store_dir / ".gitignore").write_text("*\n")
            store_path = store_dir / f"{tag}.txt"
            store_path.write_text(result)
            probs = self.probabilities(action, intent, chunks)
        except Exception as e:
            log_event(self.logf, {"type": "jev_error", "error": repr(e), "tag": tag})
            return result  # fail open: unfiltered rather than losing output
        kept = [p > 0.5 for p in probs]
        log_event(self.logf, {
            "type": "tool_result", "tag": tag, "action": action,
            "store": str(store_path),
            "chunks": [{"chars": len(c), "p": p, "kept": k}
                       for c, p, k in zip(chunks, probs, kept)],
        })
        if all(kept):
            return result
        parts, i = [], 0
        while i < len(chunks):
            if kept[i]:
                parts.append(chunks[i])
                i += 1
                continue
            j, n = i, 0
            while j < len(chunks) and not kept[j]:
                n += len(chunks[j])
                j += 1
            parts.append(f"⟨… {n} chars elided → full output: {store_path} ⟩\n")
            i = j
        return "".join(parts)


def step_intent(output, limit: int = 2000) -> str:
    """The agent's reasoning and commentary for this turn: why it ran the tools."""
    parts = []
    for item in output:
        if item.type == "reasoning":
            parts += [c.text for c in (item.content or [])]
        elif item.type == "message":
            parts += [getattr(c, "text", "") for c in item.content]
    return " ".join(p for p in parts if p)[-limit:]


def load_secrets(path: str) -> None:
    # read then delete: keys must not sit in a file or in /proc/<pid>/environ
    # where the agent's own bash tool could find them
    p = Path(path)
    for line in p.read_text().splitlines():
        k, sep, v = line.partition("=")
        if sep and not k.lstrip().startswith("#"):
            os.environ[k.strip()] = v.strip()
    p.unlink()


def _clean_env() -> dict:
    return {k: v for k, v in os.environ.items()
            if not any(s in k for s in ("_API_KEY", "_TOKEN", "_SECRET"))}


def run_bash(args: dict, workdir: Path) -> str:
    cmd = args.get("command")
    if not isinstance(cmd, str) or not cmd:
        return "bash failed: 'command' is required"
    t = args.get("timeout")
    timeout = max(1, min(int(t), 600)) if t is not None else 120
    t0 = time.time()
    p = subprocess.Popen(
        cmd, shell=True, cwd=workdir, env=_clean_env(),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, errors="replace", start_new_session=True,
    )
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = p.communicate()
        return (f"Wall time: {timeout:.1f} seconds\nProcess killed after timeout\n"
                f"Output:\n{out or ''}{err or ''}")
    return (f"Wall time: {time.time() - t0:.1f} seconds\n"
            f"Process exited with code {p.returncode}\nOutput:\n{out or ''}{err or ''}")


def run_read(args: dict, workdir: Path) -> str:
    try:
        path = _resolve(workdir, args.get("path") or "")
        lines = path.read_text(errors="replace").splitlines()
    except (OSError, ValueError) as e:
        return f"read failed: {e}"
    try:
        offset = max(0, int(args.get("offset") or 0))
        limit = min(int(args.get("limit") or READ_LIMIT), 2000)
    except (TypeError, ValueError):
        return "read failed: offset/limit must be integers"
    sel = lines[offset:offset + limit]
    if not sel:
        return f"[file has {len(lines)} lines; nothing at offset {offset}]"
    body = "\n".join(f"{offset + i + 1}\t{l}" for i, l in enumerate(sel))
    if offset + len(sel) < len(lines):
        body += f"\n[{len(lines)} lines total; next offset {offset + len(sel)}]"
    return body


def _resolve(workdir: Path, rel: str) -> Path:
    p = Path(rel)
    p = (p if p.is_absolute() else workdir / p).resolve()
    if not p.is_relative_to(workdir.resolve()):
        raise ValueError(f"path escapes workdir: {rel}")
    return p


def _find(lines: list[str], pattern: list[str], start: int) -> int:
    for j in range(start, len(lines) - len(pattern) + 1):
        if lines[j:j + len(pattern)] == pattern:
            return j
    return -1


def _update_lines(old: list[str], body: list[str]) -> list[str] | str:
    """Apply an Update File body to `old` (list of lines).

    Grammar (Codex): '@@ <anchor>' — hunk matches strictly after the anchor's
    line; ' ' context, '-' remove, '+' add, bare line = context;
    '*** End of File' — hunk must land at EOF. Pure '+' hunks insert after the
    anchor line (or at EOF when unanchored)."""
    # split into hunks on @@ anchors; '*** End of File' terminates the current
    # hunk and marks it EOF-anchored (Codex: eof_line ends a change)
    hunks, anchor, cur = [], None, []
    for l in body:
        if l.startswith("@@"):
            if cur or anchor is not None:
                hunks.append((anchor, False, cur))
            anchor, cur = l[2:].strip() or None, []
        elif l.startswith("*** End of File"):
            if cur or anchor is not None:
                hunks.append((anchor, True, cur))
            anchor, cur = None, []
        else:
            cur.append(l)
    if cur or anchor is not None:
        hunks.append((anchor, False, cur))

    pos = 0
    for anchor, eof, hunk in hunks:
        if anchor is not None:
            hits = [j for j in range(pos, len(old)) if old[j].strip() == anchor or anchor in old[j]]
            if not hits:
                return f"context '{anchor}' not found"
            pos = hits[0] + 1  # hunk must land strictly after the anchor line
        search, replace = [], []
        for l in hunk:
            if l.startswith(" "):
                search.append(l[1:]); replace.append(l[1:])
            elif l.startswith("-"):
                search.append(l[1:])
            elif l.startswith("+"):
                replace.append(l[1:])
            else:
                search.append(l); replace.append(l)
        if not search:
            idx = len(old) if eof else pos
            old[idx:idx] = replace
            pos = idx + len(replace)
            continue
        if eof:
            j = len(old) - len(search)
            if j < pos or j < 0 or old[j:] != search:
                return f"expected lines at end of file: {search[:3]!r}"
            old[j:] = replace
            pos = j + len(replace)
            continue
        j = _find(old, search, pos)
        if j < 0:
            return f"hunk not found: {search[:3]!r}"
        old[j:j + len(search)] = replace
        pos = j + len(replace)
    return old


def run_apply_patch(args: dict, workdir: Path) -> str:
    patch = args.get("patch")
    if not isinstance(patch, str):
        return "apply failed: 'patch' argument is required"
    lines = patch.splitlines()
    if not lines or lines[0].strip() != "*** Begin Patch":
        return "apply failed: patch must start with '*** Begin Patch'"
    # parse into ops first; nothing is written until the whole patch validates
    ops, i = [], 1
    while i < len(lines):
        l = lines[i]
        if l.strip() == "*** End Patch":
            break
        if l.startswith("*** Add File: ") or l.startswith("*** Delete File: ") or l.startswith("*** Update File: "):
            op, rel = l[4:l.index(": ", 4)].lower(), l[l.index(": ", 4) + 2:].strip()
            i += 1
            move_to = None
            if i < len(lines) and lines[i].startswith("*** Move to: "):
                move_to = lines[i][len("*** Move to: "):].strip()
                i += 1
            body = []
            while i < len(lines) and not (
                lines[i].startswith("*** ")
                and not lines[i].startswith("*** End of File")
            ):
                body.append(lines[i])
                i += 1
            ops.append((op, rel, move_to, body))
            continue
        if not l.strip():
            i += 1
            continue
        return f"apply failed: unexpected line {i + 1}: {l!r}"
    if not ops:
        return "apply failed: empty patch"

    # validate + compute results in memory, then commit
    writes, deletes, summary = [], [], []
    try:
        for op, rel, move_to, body in ops:
            if op == "add file":
                if _resolve(workdir, rel).exists():
                    return f"apply failed: Add File {rel}: file already exists"
                for bl in body:
                    if not bl.startswith("+"):
                        return f"apply failed: Add File {rel}: expected '+' line, got {bl!r}"
                writes.append((rel, "\n".join(x[1:] for x in body) + "\n"))
                summary.append(f"A {rel}")
            elif op == "delete file":
                if not _resolve(workdir, rel).is_file():
                    return f"apply failed: Delete File {rel}: no such file"
                deletes.append(rel)
                summary.append(f"D {rel}")
            else:
                src = _resolve(workdir, rel)
                if not src.is_file():
                    return f"apply failed: Update File {rel}: no such file"
                text = src.read_bytes().decode("utf-8", errors="surrogateescape")
                nl = "\r\n" if "\r\n" in text else "\n"
                new = _update_lines(text.splitlines(), body)
                if isinstance(new, str):
                    return f"apply failed: {rel}: {new}"
                dst = move_to or rel
                _resolve(workdir, dst)
                writes.append((dst, nl.join(new) + nl))
                if move_to and move_to != rel:
                    deletes.append(rel)
                summary.append(f"M {dst}")
    except ValueError as e:
        return f"apply failed: {e}"

    for rel in deletes:
        _resolve(workdir, rel).unlink()
    for rel, content in writes:
        p = _resolve(workdir, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content.encode("utf-8", errors="surrogateescape"))
    return "Success. Updated the following files:\n" + "\n".join(summary)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True, help="Task instruction text or path to a file containing it")
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--jev", choices=["on", "off"], default="on")
    ap.add_argument("--model", default=os.environ.get("MODEL", "deepseek-flash"))
    ap.add_argument("--max-turns", type=int, default=100)
    ap.add_argument("--log", help="JSONL log path")
    ap.add_argument("--secrets", help="KEY=VALUE file with API keys; deleted after loading")
    args = ap.parse_args()
    if args.secrets:
        load_secrets(args.secrets)

    workdir = Path(args.workdir).resolve()
    try:
        task_path = Path(args.task)
        task = task_path.read_text() if task_path.exists() else args.task
    except OSError:
        task = args.task
    store_dir = workdir / ".jev-store"
    logf = open(args.log, "a") if args.log else None

    jev = JevFilter(task, logf) if args.jev == "on" else None
    client = OpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        http_client=DefaultHttpxClient(verify=TLS),
    )
    dispatch = {"bash": run_bash, "read": run_read, "apply_patch": run_apply_patch}

    log_event(logf, {"type": "run", "task": task, "model": args.model,
                     "jev": args.jev, "workdir": str(workdir), "ts": int(time.time())})
    input_items = [{"role": "user", "content": task}]
    final_text = ""
    for turn in range(args.max_turns):
        resp = None
        for attempt in range(4):
            try:
                resp = client.responses.create(
                    model=args.model, instructions=SYSTEM_PROMPT,
                    input=input_items, tools=TOOLS,
                )
                break
            except Exception as e:
                if attempt == 3:
                    raise
                wait = 2 ** attempt * 5
                log_event(logf, {"type": "api_retry", "n": turn,
                                 "attempt": attempt, "error": repr(e), "wait": wait})
                time.sleep(wait)
        log_event(logf, {"type": "turn", "n": turn, "usage": resp.usage.model_dump() if resp.usage else None})
        log_event(logf, {"type": "response", "n": turn,
                         "output": [i.model_dump(exclude_none=True) for i in resp.output]})
        # DeepSeek requires all response items to precede all call outputs:
        # an output interleaved between function_calls detaches the later
        # calls from their reasoning and the next request is rejected.
        input_items += [i.model_dump(exclude_none=True) for i in resp.output]
        intent = step_intent(resp.output)
        for item in resp.output:
            if item.type != "function_call":
                continue
            fn = dispatch.get(item.name)
            cargs = None
            if fn is None:
                raw = f"tool error: unknown tool {item.name!r}; available: bash, read, apply_patch"
            else:
                try:
                    cargs = json.loads(item.arguments or "{}")
                    raw = fn(cargs, workdir)
                except Exception as e:
                    raw = f"tool error: {e!r}"
            # a bash read-back (tail/grep on .jev-store) is as deliberate as `read`
            if jev and item.name != "read" and ".jev-store" not in (item.arguments or ""):
                argstr = (json.dumps(cargs, ensure_ascii=False) if cargs is not None
                          else item.arguments or "")[:500]
                shown = jev.filter(raw, f"{item.name}: {argstr}", intent, store_dir, item.call_id)
            else:
                shown = raw
            log_event(logf, {"type": "tool_io", "n": turn, "name": item.name,
                             "call_id": item.call_id, "args": item.arguments,
                             "raw_len": len(raw), "shown": shown})
            input_items.append({
                "type": "function_call_output",
                "call_id": item.call_id,
                "output": shown,
            })
        if not any(i.type == "function_call" for i in resp.output):
            final_text = resp.output_text
            break
    print(final_text or "[max turns reached]")
    log_event(logf, {"type": "final", "text": final_text})
    if logf:
        logf.close()


if __name__ == "__main__":
    main()
