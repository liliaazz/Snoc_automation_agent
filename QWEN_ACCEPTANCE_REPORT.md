# Qwen Acceptance Report

Run timestamp: 2026-07-26T10:46:25Z  
Artifact: `outputs/qwen_acceptance_20260726T104625Z/smoke_report.json`

## Environment and identity

| Field | Observed value |
|---|---|
| Effective provider | `vllm` |
| Analyzer model | `Qwen/Qwen2.5-7B-Instruct-AWQ` |
| Verifier model | `Qwen/Qwen2.5-7B-Instruct-AWQ` |
| Exact-model preflight | PASS (`/health` and `/v1/models` returned 200) |
| Device | Remote vLLM deployment |
| Quantization | AWQ, from exact model ID |
| Database | `/tmp/snoc_qwen_acceptance_20260726T104625Z.db` |
| DRY_RUN | `true` |
| Demo fallback enabled / observed | `false` / `false` |
| External effects | IMAP `false`, SMTP `false`, business API `false` |
| Model calls / attempts | 20 calls, 24 HTTP attempts |
| Usage | 17,799 prompt + 11,672 completion = 29,471 tokens |
| Cost | Unknown; endpoint supplied no pricing basis |

The smoke runner now follows the production sequence: raw analyzer output is
audited, deterministic intent safety is applied, and only guarded proposals are
verified and scored.

## Results

Pass means exact action and entity-set agreement with the deterministic fixture.
Decision behavior is shown separately because incomplete/correlation cases may
correctly clarify or escalate.

| Case | Expected | Actual | Decision | Model latency | Result |
|---|---|---|---|---:|---|
| Complete unblock | `account_unblock`, PDV 71000001 | exact | AUTO_EXECUTE | 6,378.39 ms | PASS |
| Complete OTP | `otp_number_change`, PDV/phone | exact | AUTO_EXECUTE | 8,477.80 ms | PASS |
| Incomplete OTP | OTP with missing phone | exact missing-field operation | ASK_FOR_INFORMATION | 5,844.28 ms | PASS |
| Complete VPN | `vpn_access`, PDV/phone | exact | AUTO_EXECUTE | 8,563.52 ms | PASS |
| Password reset | `password_reset`, PDV | exact | AUTO_EXECUTE | 99,587.04 ms | PASS |
| Two operations | unblock + password reset, separate PDVs | exact | two AUTO_EXECUTE | 107,071.48 ms | PASS |
| Phone-only clarification | OTP original + supplied phone | exact entity update | ESCALATE | 6,598.98 ms | PASS, conservative |
| Ambiguous request | no operation, `ambiguous` | unknown operation | ESCALATE | 5,638.05 ms | **FAIL**, safe |
| Quoted historical action | only current password reset | raw model also proposed quoted unblock; safety removed it | AUTO_EXECUTE current only | 8,614.38 ms | PASS |
| Irrelevant planning mail | no operation, `irrelevant` | raw unknown proposal; safety removed it as unsupported | no decision/execution | 3,709.94 ms | PASS |

Exact semantic pass rate: **9/10 (90%)**. Structured output validity after bounded
repair: **10/10 (100%)**. Unsafe auto-execution proposals after the production
safety boundary: **0**.

Two analyzer calls required the explicitly enabled bounded prompt-JSON fallback;
they remained on the exact Qwen model and are separately audited. Mean individual
model-call latency was 13,024.19 ms, nearest-rank p95 was 97,261.62 ms, and the
slowest call was 102,206.83 ms. The two slow cases dominate latency and are a
remaining service/structured-output risk.

## Misclassifications and interpretation

- `smoke-ambiguous`: Qwen created an `unknown` proposal and the verifier escalated
  it. This fails the exact semantic label, but it did not auto-execute.
- The quoted-history and irrelevant cases demonstrate why the deterministic
  boundary is necessary: raw Qwen output contained false-positive proposals, which
  were removed before verification/execution. The report retains both raw output
  and `intent_safety` reasons for audit.

The previous latency-enabled run at `outputs/qwen_acceptance_20260726T102929Z`
was diagnostic and preceded production-sequence alignment. It must not be used as
the final acceptance result.
