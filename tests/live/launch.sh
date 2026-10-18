#!/usr/bin/env bash
# Start a test agent for one live scenario.
# Usage: tests/live/launch.sh <claude|codex> <scenario.md> [herdr-session]
# Prints: the sandbox folder, the Herdr workspace and the pane.
set -euo pipefail
runtime=$1; scenario=$2; session=${3:-${HERDR_SESSION:-default}}
here=$(cd "$(dirname "$0")" && pwd)
name=$(basename "$scenario" .md)
dir=$(mktemp -d "/tmp/approvals-live-$name-XXXX")
cp "$here/sandbox/AGENTS.md" "$here/sandbox/CLAUDE.md" "$dir/"
read -r ws pane < <(herdr --session "$session" workspace create --cwd "$dir" --label "approvals-live-$name" --no-focus |
  python3 -c 'import json,sys; r=json.load(sys.stdin)["result"]; print(r["workspace"]["workspace_id"], r["root_pane"]["pane_id"])')
if [ "$runtime" = claude ]; then launch="aim claude run sonnet"; ready="bypass permissions"; else launch="aim codex run"; ready="for shortcuts"; fi
herdr --session "$session" pane run "$pane" "cd $dir && export APPROVALS_TEST=1 && $launch" >/dev/null
for _ in $(seq 1 60); do
  sleep 3
  screen=$(herdr --session "$session" pane read "$pane" --source recent-unwrapped --lines 25 2>/dev/null || true)
  if grep -q "Yes, I trust this folder" <<<"$screen"; then herdr --session "$session" pane send-keys "$pane" down enter >/dev/null; continue; fi
  if grep -qi "trust the contents of this directory" <<<"$screen"; then herdr --session "$session" pane send-keys "$pane" enter >/dev/null; continue; fi
  grep -q "$ready" <<<"$screen" && break
done
grep -q "$ready" <<<"$screen" || { echo "agent didn't come up in $pane"; exit 1; }
herdr --session "$session" pane run "$pane" "$(tr '\n' ' ' < "$scenario")" >/dev/null
echo "$dir $ws $pane"
