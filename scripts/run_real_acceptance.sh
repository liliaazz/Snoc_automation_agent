#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${SNOC_ENV_FILE:-$project_dir/.env}"
export SNOC_ENV_FILE="$env_file"
compose=(docker compose --env-file "$env_file" -f "$project_dir/compose.yaml")
run_id="$(date -u +%Y%m%dT%H%M%SZ)"
output_dir="$project_dir/outputs/$run_id"
legacy_was_running=false
legacy_compose_file="${SNOC_LEGACY_COMPOSE_FILE:-}"
legacy_repo="${SNOC_LEGACY_REPO:-}"
export SNOC_TEST_RUN_ID="$run_id"
export APP_ENVIRONMENT=development
export SNOC_POSTGRES_VOLUME="snoc_langgraph_postgres_${run_id,,}"
export SNOC_COMPOSE_PROJECT_NAME="${SNOC_COMPOSE_PROJECT_NAME:-snoc-langgraph-${run_id,,}}"

if [[ -z "$legacy_repo" && -f "$project_dir/../../docker-compose.yml" ]]; then
  legacy_repo="$(cd "$project_dir/../.." && pwd)"
fi
if [[ -z "$legacy_compose_file" && -n "$legacy_repo" ]]; then
  legacy_compose_file="$legacy_repo/docker-compose.yml"
fi

python_bin="${SNOC_PYTHON:-}"
if [[ -z "$python_bin" && -x "$project_dir/.venv/bin/python" ]]; then
  python_bin="$project_dir/.venv/bin/python"
elif [[ -z "$python_bin" && -n "$legacy_repo" && -x "$legacy_repo/.venv/bin/python" ]]; then
  python_bin="$legacy_repo/.venv/bin/python"
elif [[ -z "$python_bin" ]]; then
  python_bin="$(command -v python3)"
fi

mkdir -p "$output_dir"

cleanup() {
  status=$?
  trap - EXIT
  "${compose[@]}" logs --no-color >"$output_dir/containers.log" 2>&1 || true
  "${compose[@]}" stop worker >/dev/null 2>&1 || true
  if [[ "$legacy_was_running" == "true" ]]; then
    docker compose -f "$legacy_compose_file" start worker >/dev/null
  fi
  printf 'acceptance_exit_status=%s\n' "$status"
  printf 'artifacts=%s\n' "$output_dir"
  exit "$status"
}
trap cleanup EXIT INT TERM

command -v docker >/dev/null
docker info >/dev/null
test -f "$env_file"
test -x "$python_bin"
if [[ -n "$legacy_compose_file" && ! -f "$legacy_compose_file" ]]; then
  printf 'legacy Compose file not found: %s\n' "$legacy_compose_file" >&2
  exit 1
fi
if [[ -n "$legacy_repo" && -f "$legacy_repo/legacy/pre_langchain_20260723/SHA256SUMS" ]]; then
  (
    cd "$legacy_repo"
    sha256sum -c legacy/pre_langchain_20260723/SHA256SUMS >/dev/null
  )
fi

ENV_FILE="$env_file" PYTHONPATH="$project_dir/src" "$python_bin" - <<'PY'
import os
from pathlib import Path

from dotenv import dotenv_values
from snoc_agent.config import Settings

env_file = Path(os.environ["ENV_FILE"])
values = dotenv_values(env_file)
settings = Settings(_env_file=env_file)
errors = []
if not settings.dry_run:
    errors.append("DRY_RUN must be true")
if str(settings.effective_llm_provider) != "vllm":
    errors.append("LLM_PROVIDER must resolve to vllm")
if not settings.imap_host or not settings.imap_username or not settings.imap_password.get_secret_value():
    errors.append("real IMAP credentials are incomplete")
if not settings.smtp_host or not settings.smtp_password.get_secret_value():
    errors.append("real SMTP credentials are incomplete")
if not values.get("SENDER_PASSWORD"):
    errors.append("SENDER_PASSWORD is required")
if "X-SNOC-Test-Run" not in settings.imap_search_criterion:
    errors.append("IMAP_SEARCH_CRITERION must be scoped to X-SNOC-Test-Run")
if not settings.effective_vllm_api_key:
    errors.append("VLLM_API_KEY is required")
if errors:
    raise SystemExit("preflight failed: " + "; ".join(errors))
print("configuration_preflight=passed")
PY

PYTHONPATH="$project_dir/src" "$python_bin" -m ruff check \
  "$project_dir/src" "$project_dir/tests" "$project_dir/scripts/docker_mail_journey.py"
PYTHONPATH="$project_dir/src" "$python_bin" -m mypy \
  --no-incremental "$project_dir/src/snoc_agent"
(
  cd "$project_dir"
  PYTHONPATH="$project_dir/src" "$python_bin" -m pytest -ra \
    -k "not test_dataset_builder_labels_194_demo_candidates_as_not_qwen"
)

"${compose[@]}" config --quiet
if [[ -n "$legacy_compose_file" ]] \
  && [[ -n "$(docker compose -f "$legacy_compose_file" ps -q worker)" ]]; then
  legacy_was_running=true
  docker compose -f "$legacy_compose_file" stop worker
fi

"${compose[@]}" build
"${compose[@]}" up -d postgres
"${compose[@]}" run --rm migrate
"${compose[@]}" run --rm migrate
"${compose[@]}" run --rm worker alembic check
"${compose[@]}" run --rm worker snoc-agent models check
"${compose[@]}" up -d worker dashboard

report="/app/outputs/$run_id/report.json"
"${compose[@]}" --profile test run --rm journey \
  python scripts/docker_mail_journey.py \
  --confirm-send \
  --timeout-seconds 600 \
  --output "$report" 2>&1 | tee "$output_dir/journey.log"

REPORT_PATH="$output_dir/report.json" "$python_bin" - <<'PY'
import json
import os
from pathlib import Path

report = json.loads(Path(os.environ["REPORT_PATH"]).read_text(encoding="utf-8"))
totals = report["totals"]
errors = []
if totals["scenarios"] != 6:
    errors.append(f"expected 6 scenarios, got {totals['scenarios']}")
if totals["passed"] != 6 or totals["failed"] != 0:
    errors.append(f"journeys did not all pass: {totals}")
if totals["threading_failures"] != 0:
    errors.append(f"threading failures: {totals['threading_failures']}")
for scenario in report["scenarios"]:
    audit = scenario["audit"]
    if any(not execution["dry_run"] for execution in audit["executions"]):
        errors.append(f"{scenario['scenario']} contains a non-dry-run execution")
    email_ids = {email["id"] for email in audit["emails"]}
    audited_email_ids = {
        run["inbound_email_id"]
        for run in audit["workflow_runs"]
        if run["status"] == "completed"
    }
    if missing := email_ids - audited_email_ids:
        errors.append(
            f"{scenario['scenario']} is missing completed workflow runs for {sorted(missing)}"
        )
    if any(run["status"] != "completed" for run in audit["workflow_runs"]):
        errors.append(f"{scenario['scenario']} contains an incomplete workflow run")
if errors:
    raise SystemExit("acceptance failed: " + "; ".join(errors))
print(json.dumps(totals, indent=2))
print("real_acceptance=passed")
PY
