# vLLM Qwen/Gemma inference

`LLM_PROVIDER=vllm` connects the analyzer and verifier to two independently hosted,
OpenAI-compatible vLLM servers. This is the only configured production model mode; deterministic
offline tests use `LLM_PROVIDER=demo`.

## Configuration

```dotenv
LLM_PROVIDER=vllm
VLLM_API_KEY=replace_me
VLLM_QWEN_BASE_URL=https://qwen.example.com/v1
VLLM_QWEN_MODEL=Qwen/Qwen2.5-7B-Instruct-AWQ
VLLM_GEMMA_BASE_URL=https://gemma.example.com/v1
VLLM_GEMMA_MODEL=google/gemma-4-12B-it
VLLM_ANALYZER_DEPLOYMENT=gemma
VLLM_QWEN3_30B_BASE_URL=https://qwen3-30b.example.com/v1
VLLM_QWEN3_30B_MODEL=stelterlab/Qwen3-30B-A3B-Instruct-2507-AWQ
VLLM_VERIFIER_DEPLOYMENT=gemma
VLLM_REQUEST_TIMEOUT_SECONDS=120
VLLM_MAX_RETRIES=2
VLLM_MAX_OUTPUT_TOKENS_ANALYZER=4096
VLLM_MAX_OUTPUT_TOKENS_VERIFIER=4096
VLLM_USE_JSON_SCHEMA=true
VLLM_ALLOW_JSON_OBJECT_FALLBACK=true
VLLM_ALLOW_PROMPT_JSON_FALLBACK=true
```

Keep the key and real endpoint addresses in `.env` or the process environment. The key is
represented as a secret in typed configuration and is excluded from model-run audit data. The
valid model IDs must exactly match the IDs advertised by each server. The application reports
alternatives and never silently rewrites or substitutes a configured model.

## Discovery and structured output

```bash
python -m snoc_agent.cli.main models list
python -m snoc_agent.cli.main models check
python -m snoc_agent.cli.main models smoke-test \
  --analyzer-model gemma \
  --verifier-model gemma \
  --output-dir outputs/evaluation/vllm_smoke
```

Discovery calls `/health` and `/v1/models` on all configured endpoints. The check command then makes one
minimal configured-analyzer chat request and one strict configured-verifier request. Runtime calls
attempt strict JSON Schema first, use JSON-object mode only after an explicit capability rejection,
and use prompt-enforced JSON only after both structured modes are explicitly rejected. Local
Pydantic validation always applies, and every fallback is recorded.

The servers do not receive provider-routing suffixes or automatic Qwen-specific
thinking parameters. Reasoning fields are stored separately and never parsed as final JSON.

## Matrix and selection

```bash
python -m snoc_agent.cli.main evaluate \
  --dataset outputs/evaluation/integration_smoke.jsonl \
  --matrix \
  --use-cache \
  --resume \
  --output-dir outputs/evaluation/vllm_matrix
```

The matrix contains the four Qwen/Gemma pairs plus Qwen3-30B self-check and cross-checks with Gemma. An analyzer runs once per
input/model, and each verifier runs once for each unique proposal/context. Persistent cache rows and
checkpoints survive separate CLI executions. `pair_selection.json` first requires zero unsafe
automatic decisions and zero validation-pass-but-wrong results, then compares coverage, structured
failure rate, disagreement rate, and latency. If no pair passes the safety gate, no pair is release
eligible and the application remains in dry-run.

The completed 10-case compact matrix selected Gemma/Gemma: both Gemma-analyzer pairs recorded zero
unsafe decisions, while Qwen/Qwen recorded two and Qwen/Gemma one. This limited synthetic result
chooses the current dry-run default; it does not certify production quality.

These endpoints expose usage but no pricing metadata. Token counts remain audited and cost remains
`unknown`; evaluation budget controls do not invent a vLLM price.

## Mailbox journey and audit dashboard

With `DRY_RUN=true` and `DRY_RUN_SEND_EMAILS=true`, the worker calls the configured models, makes
and persists decisions, records mock executions, and sends real clarification/completion emails.
It does not call the telecom business API.

```bash
docker compose up --build -d postgres worker dashboard
docker compose --profile test run --rm journey
```

The six synthetic French journeys use natural subjects and hidden `X-SNOC-Test-*` identifiers.
The incomplete OTP journey waits for the agent clarification, replies naturally in the same RFC
thread, and waits for completion or escalation. Gmail `X-GM-THRID`, `Message-ID`, `In-Reply-To`,
and `References` are verified and visible at <http://localhost:8502> together with every model,
policy, state, execution, and delivery audit stage.

## Tests

Normal tests never call these endpoints. The opt-in synthetic live probe requires both the key and
an explicit switch:

```bash
RUN_VLLM_LIVE_TESTS=true pytest -m vllm_live
```

This verifies health, exact model IDs, strict schema parsing, and usage metadata for both models.
It constructs no mail, SMTP, or business service.
