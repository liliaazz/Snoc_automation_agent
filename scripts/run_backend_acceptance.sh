#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${SNOC_PYTHON:-$project_dir/.venv/bin/python}"
run_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
database_path="/tmp/snoc_backend_acceptance_${run_stamp}.db"
api_log="/tmp/snoc_backend_acceptance_api_${run_stamp}.log"
vite_log="/tmp/snoc_backend_acceptance_vite_${run_stamp}.log"
api_pid=""
vite_pid=""

cleanup() {
  if [[ -n "$vite_pid" ]]; then kill "$vite_pid" 2>/dev/null || true; fi
  if [[ -n "$api_pid" ]]; then kill "$api_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT

export PYTHONPATH="$project_dir/src"
export DATABASE_URL="sqlite+pysqlite:///$database_path"
export DRY_RUN=true
export DRY_RUN_SEND_EMAILS=false
export LLM_PROVIDER=demo

cd "$project_dir"
"$python_bin" -m alembic upgrade head
"$python_bin" -m pytest -q \
  tests/unit/test_intent_safety.py \
  tests/unit/test_datetime_utils.py \
  tests/unit/test_hardening_matrix.py \
  tests/unit/test_api.py \
  tests/unit/test_business_api.py \
  tests/integration/test_acceptance_scenarios.py \
  tests/integration/test_execution_failure_escalation.py

"$python_bin" -m uvicorn snoc_agent.api.app:create_app \
  --factory --host 127.0.0.1 --port 8000 >"$api_log" 2>&1 &
api_pid="$!"

for _ in {1..30}; do
  if curl --fail --silent http://127.0.0.1:8000/health/ready >/dev/null; then break; fi
  sleep 1
done
curl --fail --silent http://127.0.0.1:8000/health/ready >/dev/null

cd "$project_dir/frontend"
npm run dev -- --host 127.0.0.1 --port 4173 >"$vite_log" 2>&1 &
vite_pid="$!"
for _ in {1..30}; do
  if curl --fail --silent http://127.0.0.1:4173/health/live >/dev/null; then break; fi
  sleep 1
done

curl --fail --silent http://127.0.0.1:4173/health/live >/dev/null
curl --fail --silent "http://127.0.0.1:4173/api/snoc/dashboard/summary?range=week" >/dev/null
curl --fail --silent http://127.0.0.1:4173/api/snoc/model/snapshot >/dev/null
curl --fail --silent http://127.0.0.1:4173/api/snoc/frontend/runtime >/dev/null

printf 'Backend acceptance passed.\nDatabase: %s\n' "$database_path"
