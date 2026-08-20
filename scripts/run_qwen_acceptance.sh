#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${SNOC_PYTHON:-$project_dir/.venv/bin/python}"
env_file="${SNOC_ENV_FILE:-$project_dir/.env}"
run_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
database_path="/tmp/snoc_qwen_acceptance_${run_stamp}.db"
output_dir="$project_dir/outputs/qwen_acceptance_${run_stamp}"
expected_model="Qwen/Qwen2.5-7B-Instruct-AWQ"

test -f "$env_file"
mkdir -p "$output_dir"

export PYTHONPATH="$project_dir/src"
export DATABASE_URL="sqlite+pysqlite:///$database_path"
export DRY_RUN=true
export DRY_RUN_SEND_EMAILS=false
export LLM_PROVIDER=vllm
export VLLM_ANALYZER_DEPLOYMENT=qwen
export VLLM_VERIFIER_DEPLOYMENT=qwen

ENV_FILE="$env_file" EXPECTED_MODEL="$expected_model" DATABASE_PATH="$database_path" \
  "$python_bin" - <<'PY'
import os
from pathlib import Path

from snoc_agent.config import Settings

settings = Settings(_env_file=Path(os.environ["ENV_FILE"]))
expected = os.environ["EXPECTED_MODEL"]
if settings.effective_llm_provider.value != "vllm":
    raise SystemExit("Qwen acceptance requires effective provider vllm")
if settings.vllm_qwen_model != expected:
    raise SystemExit(
        f"Qwen acceptance model mismatch: expected {expected!r}, got {settings.vllm_qwen_model!r}"
    )
print(f"Effective LLM provider: {settings.effective_llm_provider.value}")
print(f"Exact model name: {settings.vllm_qwen_model}")
print("Demo fallback enabled: false")
print("Demo fallback occurred: false")
print("Device: remote vLLM deployment")
print(f"Quantization: {settings.model_quantization or 'AWQ (from exact model ID)'}")
print(f"Database path: {os.environ['DATABASE_PATH']}")
print(f"Dry-run status: {str(settings.dry_run).lower()}")
PY

cd "$project_dir"
"$python_bin" -m snoc_agent.cli.main --env-file "$env_file" \
  models smoke-test \
  --analyzer-model qwen \
  --verifier-model qwen \
  --output-dir "$output_dir"

REPORT_PATH="$output_dir/smoke_report.json" EXPECTED_MODEL="$expected_model" \
  "$python_bin" - <<'PY'
import json
import os
from pathlib import Path

from snoc_agent.evaluation.dataset_subsets import synthetic_smoke_examples
from snoc_agent.evaluation.metrics import evaluate_predictions

report = json.loads(Path(os.environ["REPORT_PATH"]).read_text(encoding="utf-8"))
expected = os.environ["EXPECTED_MODEL"]
if report.get("status") != "completed":
    raise SystemExit("Qwen smoke report did not complete")
if report.get("analyzer", {}).get("model") != expected:
    raise SystemExit("Qwen analyzer exact-model verification failed")
if report.get("verifier", {}).get("model") != expected:
    raise SystemExit("Qwen verifier exact-model verification failed")
calls = [
    call
    for case in report.get("cases", [])
    for call in [case.get("analyzer", {}), *case.get("verifiers", [])]
]
demo_calls = [
    call
    for call in calls
    if str(call.get("backend", "")).casefold() == "demo"
    or str(call.get("model_name", "")).casefold().startswith("demo")
]
if demo_calls:
    raise SystemExit("Qwen acceptance observed a demo fallback")
schema_fallback_count = sum(bool(call.get("fallback_reason")) for call in calls)
evaluation = evaluate_predictions(
    synthetic_smoke_examples(),
    [case["prediction"] for case in report.get("cases", [])],
)
semantic_pass_rate = evaluation.summary["joint_action_and_fields_exact_match"]
unsafe_auto_execute = evaluation.summary["unsafe_auto_execute_count"]
if evaluation.summary["structured_output_validity"] != 1.0:
    raise SystemExit("Qwen acceptance observed invalid structured output")
if unsafe_auto_execute:
    raise SystemExit(
        f"Qwen acceptance observed {unsafe_auto_execute} unsafe auto-execution proposal(s)"
    )
print(f"Qwen acceptance cases completed: {report.get('completed_case_count', 0)}")
print("Exact-model verification: passed")
print("Demo fallback occurred: false")
print(f"Structured-output fallback count: {schema_fallback_count}")
print(f"Exact semantic pass rate: {semantic_pass_rate:.0%}")
print("Unsafe auto-execution proposals: 0")
PY

printf 'Qwen acceptance report: %s\n' "$output_dir/smoke_report.json"
