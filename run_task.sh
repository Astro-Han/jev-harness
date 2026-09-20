#!/bin/bash
# Run one frontier-harness-eval task inside a shared Runta runtime (docker-in-runtime).
# Agent + verifier both run via a static `uv` binary copied into the task container —
# no python/pip needed in the task image.
# Usage: run_task.sh <task-name> <on|off> [runner-name] [tasks-repo-dir]
set -uo pipefail
# braces make bash parse the whole script before running it: editing this file
# mid-batch otherwise corrupts every in-flight run (bash reads by byte offset)
{
TASK="$1"; ARM="${2:-on}"
RUNNER="${3:-jev-eval-runner}"
REPO="${4:-/tmp/frontier-harness-eval}"
TASKDIR="$REPO/tasks/$TASK"
HERE="$(cd "$(dirname "$0")" && pwd)"
SECRETS="${JEV_SECRETS:-/tmp/jev-secrets.env}"
CT="t-${TASK}-${ARM}"
RDIR="/root/eval/${TASK}-${ARM}"

DOCKER_IMAGE=$(grep 'docker_image' "$TASKDIR/task.toml" | cut -d'"' -f2)
WORKDIR=$(grep '^workdir' "$TASKDIR/task.toml" | cut -d'"' -f2)
WORKDIR=${WORKDIR:-/app}
TIMEOUT=$(grep -A1 '\[agent\]' "$TASKDIR/task.toml" | grep timeout_sec | awk '{print $3}' | cut -d. -f1)
TIMEOUT=${TIMEOUT:-1200}
# task.toml [environment.env] PATH (e.g. swe-bench images ship /app/.venv)
ENV_PATH=$(grep 'PATH' "$TASKDIR/task.toml" | cut -d'"' -f2)
TESTS_LOCAL="/tmp/tb-tests/$TASK"

echo "== [$CT] image $DOCKER_IMAGE workdir $WORKDIR timeout ${TIMEOUT}s"

# fresh container
runta exec "$RUNNER" -- docker rm -f "$CT" >/dev/null 2>&1
runta exec "$RUNNER" -- docker run -d --name "$CT" -w "$WORKDIR" "$DOCKER_IMAGE" sleep infinity >/dev/null || { echo "!! [$CT] docker run failed"; exit 1; }

# python + certs inside container
runta exec "$RUNNER" -- docker cp /usr/local/share/ca-certificates/runta-egress.crt "$CT:/root/runta-egress.crt" >/dev/null
runta exec "$RUNNER" -- docker exec "$CT" bash -lc '(
if command -v apt-get >/dev/null; then
  apt-get update -qq && apt-get install -y -qq python3 python3-pip ca-certificates curl
elif command -v apk >/dev/null; then
  apk add --quiet python3 py3-pip ca-certificates curl
elif command -v yum >/dev/null; then
  yum install -y -q python3 python3-pip ca-certificates curl
fi
cat /root/runta-egress.crt >> /etc/ssl/certs/ca-certificates.crt 2>/dev/null || cp /root/runta-egress.crt /etc/ssl/certs/ca-certificates.crt
# pip ships its own CA bundle, so appending the egress cert to the system one
# is not enough: images whose pip is not Debian-patched fail TLS without this
export PIP_CERT=/etc/ssl/certs/ca-certificates.crt
python3 -m pip install -q --break-system-packages openai pytest "httpx[socks]" 2>/dev/null || python3 -m pip install -q openai pytest "httpx[socks]"
) >/dev/null 2>&1' >/dev/null 2>&1
# runta exec may return while remote cmd still runs — wait until python3 exists.
# concurrent containers make apt/pip slow: retry setup once if deps never land.
deps_ok() {
  runta exec "$RUNNER" -- docker exec "$CT" bash -lc 'command -v python3 >/dev/null && python3 -c "import openai, httpx" 2>/dev/null' >/dev/null 2>&1
}
for i in $(seq 1 36); do deps_ok && break; sleep 5; done
if ! deps_ok; then
  echo "!! [$CT] deps missing after wait, rerunning setup"
  runta exec "$RUNNER" -- docker exec "$CT" bash -lc 'export PIP_CERT=/etc/ssl/certs/ca-certificates.crt; python3 -m pip install -q --break-system-packages openai pytest "httpx[socks]" 2>/dev/null || python3 -m pip install -q openai pytest "httpx[socks]"' >/dev/null 2>&1
  for i in $(seq 1 24); do deps_ok && break; sleep 5; done
fi
deps_ok || { echo "!! [$CT] deps failed"; exit 1; }
runta exec "$RUNNER" -- docker exec "$CT" bash -lc 'python3 -c "import openai, httpx; print(\"deps ok\")"' 2>&1 | tail -1

# stage files: agent stuff -> /root/agent/, secrets -> /root/secrets.env (kept out of pulled results)
runta exec "$RUNNER" -- sh -c "rm -rf '$RDIR' && mkdir -p '$RDIR/in' '$RDIR/out'" >/dev/null
runta cp "$HERE/jev_agent.py" "$RUNNER:$RDIR/in/jev_agent.py" >/dev/null
runta cp "$TASKDIR/instruction.md" "$RUNNER:$RDIR/in/instruction.md" >/dev/null
# tests stay outside in/: everything in in/ is visible to the agent.
# a dropped cp leaves the verifier without test.sh and the run scores nothing
if [ -d "$TESTS_LOCAL" ]; then
  for i in 1 2 3; do
    runta exec "$RUNNER" -- test -f "$RDIR/tests/test.sh" >/dev/null 2>&1 && break
    runta cp "$TESTS_LOCAL" "$RUNNER:$RDIR/tests" >/dev/null 2>&1
  done
  runta exec "$RUNNER" -- test -f "$RDIR/tests/test.sh" >/dev/null 2>&1 || { echo "!! [$CT] tests staging failed"; exit 1; }
fi
runta cp "$SECRETS" "$RUNNER:$RDIR/secrets.env" >/dev/null
runta exec "$RUNNER" -- docker cp "$RDIR/in/." "$CT:/root/agent/" >/dev/null
runta exec "$RUNNER" -- docker cp "$RDIR/secrets.env" "$CT:/root/secrets.env" >/dev/null
# concurrent arms race on staging — verify the payload landed, retry once
for i in 1 2 3; do
  runta exec "$RUNNER" -- docker exec "$CT" bash -lc 'test -f /root/agent/jev_agent.py && test -f /root/agent/instruction.md && test -f /root/secrets.env' >/dev/null 2>&1 && break
  echo "!! [$CT] staging incomplete, retry $i"
  runta cp "$HERE/jev_agent.py" "$RUNNER:$RDIR/in/jev_agent.py" >/dev/null
  runta cp "$TASKDIR/instruction.md" "$RUNNER:$RDIR/in/instruction.md" >/dev/null
  runta cp "$SECRETS" "$RUNNER:$RDIR/secrets.env" >/dev/null
  runta exec "$RUNNER" -- docker cp "$RDIR/in/." "$CT:/root/agent/" >/dev/null
  runta exec "$RUNNER" -- docker cp "$RDIR/secrets.env" "$CT:/root/secrets.env" >/dev/null
  sleep 5
done
runta exec "$RUNNER" -- docker exec "$CT" bash -lc 'test -f /root/agent/jev_agent.py && test -f /root/agent/instruction.md' >/dev/null 2>&1 || { echo "!! [$CT] staging failed"; exit 1; }

# run agent with system python (PEP668-sidelined via --break-system-packages install)
echo "== [$CT] running agent (jev $ARM)"
runta exec "$RUNNER" -- docker exec "$CT" bash -lc "cd $WORKDIR && export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt && export PATH=${ENV_PATH:-\$PATH} && timeout $TIMEOUT python3 /root/agent/jev_agent.py --secrets /root/secrets.env --task /root/agent/instruction.md --workdir $WORKDIR --jev $ARM --log /root/agent/run.jsonl --max-turns 150 > /root/agent/final.txt 2>&1; echo agent_rc=\$?"

# verify (official verifier: tests/test.sh writes /logs/verifier/reward.{json,txt})
if [ -d "$TESTS_LOCAL" ]; then
  VT="$CT"
  # DeepSWE graders replay model.patch on a pristine base: grade in a fresh
  # container, not the one the agent modified
  if [ -f "$TESTS_LOCAL/grader.py" ]; then
    # diff against base_commit, not HEAD: agents sometimes commit their work
    BASE=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["base_commit"])' "$TESTS_LOCAL/config.json")
    runta exec "$RUNNER" -- docker exec "$CT" bash -lc "cd $WORKDIR && git add -A >/dev/null 2>&1; git diff --cached --binary $BASE > /root/agent/model.patch; wc -c /root/agent/model.patch"
    runta exec "$RUNNER" -- docker cp "$CT:/root/agent/model.patch" "$RDIR/model.patch" >/dev/null
    VT="$CT-grade"
    # the verifier image (tests/Dockerfile FROM) ships the report tooling
    VIMG=$(awk '/^FROM/{print $2; exit}' "$TESTS_LOCAL/Dockerfile" 2>/dev/null)
    runta exec "$RUNNER" -- docker rm -f "$VT" >/dev/null 2>&1
    runta exec "$RUNNER" -- docker run -d --name "$VT" -w "$WORKDIR" "${VIMG:-$DOCKER_IMAGE}" sleep infinity >/dev/null
    runta exec "$RUNNER" -- docker exec "$VT" bash -lc 'command -v python3 >/dev/null || { apt-get update -qq && apt-get install -y -qq python3; } >/dev/null 2>&1; mkdir -p /logs/artifacts'
    runta exec "$RUNNER" -- docker cp "$RDIR/model.patch" "$VT:/logs/artifacts/model.patch" >/dev/null
  fi
  runta exec "$RUNNER" -- docker exec "$VT" bash -lc 'mkdir -p /tests /logs/verifier /logs/artifacts && (command -v python >/dev/null || ln -sf "$(command -v python3)" /usr/local/bin/python)'
  runta exec "$RUNNER" -- docker cp "$RDIR/tests/." "$VT:/tests/" >/dev/null
  runta exec "$RUNNER" -- docker exec "$VT" bash -lc "cd $WORKDIR && export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt && export PATH=${ENV_PATH:-\$PATH} && bash /tests/test.sh > /logs/verifier/test-stdout.txt 2>&1; echo verifier_rc=\$?; cat /logs/verifier/reward.json /logs/verifier/reward.txt 2>/dev/null"
  runta exec "$RUNNER" -- docker cp "$VT:/logs/verifier/." "$RDIR/out/verifier/" >/dev/null 2>&1
  [ "$VT" != "$CT" ] && runta exec "$RUNNER" -- docker rm -f "$VT" >/dev/null 2>&1
fi

# pull results out
runta exec "$RUNNER" -- docker cp "$CT:/root/agent/." "$RDIR/out/" >/dev/null 2>&1
runta exec "$RUNNER" -- docker cp "$CT:$WORKDIR" "$RDIR/out/workdir" >/dev/null 2>&1
OUT="$HERE/runs/${TASK}-${ARM}"
mkdir -p "$OUT"
# runta cp drops the payload now and then; a silent miss looks like a scoreless run
for i in 1 2 3; do
  runta cp "$RUNNER:$RDIR/out" "$OUT/" >/dev/null 2>&1
  [ -f "$OUT/out/run.jsonl" ] && break
done
[ -f "$OUT/out/run.jsonl" ] || echo "!! [$CT] pulling results failed, kept on runner at $RDIR/out"
runta exec "$RUNNER" -- docker rm -f "$CT" >/dev/null 2>&1
# free the task image — runner disk is small and swe images are ~4GB each;
# fails harmlessly if the sibling arm's container still uses it
runta exec "$RUNNER" -- docker rmi "$DOCKER_IMAGE" ${VIMG:-} >/dev/null 2>&1 || true
echo "== [$CT] done -> $OUT"
exit
}
