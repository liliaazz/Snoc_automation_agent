# Frontend–Backend API Contract Matrix

Audit date: 2026-07-26. Source of truth: `frontend/src/services/backendApi.js`, its
callers, and the FastAPI route table. No visual frontend source was changed.

`period` is the frontend's canonical query name. Dashboard routes temporarily also
accept `range`; values are validated as `day`, `week`, `month`, or `year`. Conflicting
aliases return 400 and invalid values return 422.

| Frontend caller | Method and path | Input | Stable response fields | Backend route / status | Test |
|---|---|---|---|---|---|
| `useDashboard.dashboardSummary` | `GET /api/snoc/dashboard/summary` | `period` or `range` | `generatedAt`, `operational`, `dataQuality`; operational separates emails, requests, and operations | `dashboard_router.dashboard_summary` / verified | `TestDashboardRouter.test_dashboard_summary`, period tests |
| `useDashboard.dashboardTrends` | `GET /api/snoc/dashboard/trends` | period alias | `items[]` with date/request/resolution counts | `dashboard_trends` / verified | `test_dashboard_trends` |
| `useDashboard.dashboardIntents` | `GET /api/snoc/dashboard/intents` | period alias | `items[]` | `dashboard_intents` / verified | `test_dashboard_intents` |
| `useDashboard.dashboardRecent` | `GET /api/snoc/dashboard/recent` | period alias | `items[]` | `dashboard_recent` / verified | `test_dashboard_recent` |
| Legacy dashboard fallback | `GET /api/dashboard` | none | `stats`, `requests`, `alerts`, `agent_active` | `app.dashboard` / verified populated | `TestDashboard` |
| Known compatibility caller | `GET /api/snoc/dashboard` | none | legacy dashboard-compatible payload | `dashboard_router.legacy_dashboard` / route verified | backend acceptance proxy smoke |
| Data-quality executive | `GET /api/snoc/dq/executive` | none | `completeness`, `accuracy`, `timeliness`, `consistency` | `dq_executive` / verified | `test_dq_executive` |
| Data-quality dimensions | `GET /api/snoc/dq/dimensions` | none | `items[]` | `dq_dimensions` / verified | `test_dq_dimensions` |
| Data-quality rules | `GET /api/snoc/dq/rules` | none | `items[]` | `dq_rules` / verified | `test_dq_rules` |
| Model card | `GET /api/snoc/model/snapshot` | none | provider/model, availability, last success, recent runs, errors, latency, throughput, dry-run and fallback; classification metrics are `null` without labels | `model_snapshot` / verified empty and naive SQLite time | `test_model_snapshot*` |
| Workflow panel | `GET /api/snoc/workflow/health` | none | `status`, `agents` | `workflow_health` / verified | `test_workflow_health` |
| Runtime badge | `GET /api/snoc/frontend/runtime` | none | execution/model runtime state | `frontend_runtime` / implemented and verified | frontend integration parametrization |
| Confidence analytics | `GET /api/snoc/frontend/analytics/confidence` | none | real aggregate rows | `confidence_analytics` / implemented and verified | frontend integration parametrization |
| Missing-entity analytics | `GET /api/snoc/frontend/analytics/missing-entities` | none | real aggregate rows | `missing_entity_analytics` / implemented and verified | frontend integration parametrization |
| Execution analytics | `GET /api/snoc/frontend/analytics/executions` | none | real aggregate rows | `execution_analytics` / implemented and verified | frontend integration parametrization |
| Audit drawer | `GET /api/snoc/frontend/requests/{public_reference}/trace` | URL reference | bounded request/email/operation/decision/execution/outbox/model trace | `request_trace` / implemented, populated and 404 verified | `test_populated_request_trace_is_retrievable_and_secret_free` |
| Audit drawer fallback | `GET /api/requests/{public_reference}/pipeline` | URL reference | request pipeline or 404 | `request_pipeline` / verified | `TestPipeline` |
| Escalation service | `GET /api/escalations` | none | `escalations[]` | `list_escalations` / verified | `TestEscalations` |
| Escalation service | `POST /api/escalations/{request_id}/resolve` | `{decision,note}` | resolution status; 404 for unknown ID | `resolve_escalation` / verified | `test_resolve_nonexistent_returns_404` plus acceptance scenarios |
| Configuration service | `GET /api/whitelist` | none | `entries[]` | `list_whitelist` / verified | `TestWhitelist` |
| Configuration service | `POST /api/whitelist` | `{email,zone}` | `status` | `add_whitelist` / verified | `test_add_to_whitelist` |
| Configuration service | `DELETE /api/whitelist/{email}` | URL email | deletion status | `remove_whitelist` / route verified | backend acceptance |
| User service | `GET /api/accounts` | none | `accounts[]`, with no password field | `list_accounts` / verified | `test_get_accounts` |
| User service | `POST /api/accounts` | username/password/role payload | `status` | `create_account` / verified | `test_create_account` |
| User service | `PUT /api/accounts/{username}` | changed account fields | `status` | `update_account` / verified | `test_update_account` |
| User service | `DELETE /api/accounts/{username}` | none | `status`; 404 unknown | `delete_account` / verified | `test_delete_account*` |
| User service | `POST /api/accounts/{username}/toggle` | none | `active` | `toggle_account` / verified | `test_toggle_account` |
| Dashboard header | `POST /api/agent-toggle` | none | `agent_active` | `toggle_agent` / verified | `TestAgentToggle` |
| Email service | `POST /api/simulate-inbox` | none | `processed` | `simulate_inbox` / verified DRY_RUN | `TestSimulateInbox` |
| Health client | `GET /health/live` | none | `status` | `health_live` / verified | `test_live` |
| Deployment readiness | `GET /health/ready` | none | `status` or dependency failure | `health_ready` / verified | `test_ready` |
| Operational scraper | `GET /metrics` | none | Prometheus text | `metrics` / verified | `TestMetrics` |
| Dashboard diagnostics | `GET /metrics/summary` | none | `counters`, `gauges`, `histograms` | `metrics_summary` / verified | `TestMetrics` |

Additional backend-only route `POST /api/alerts/{alert_id}/dismiss` remains available
for legacy consumers. Strings in `frontend/src/utils/enrichRecord.js` such as
`POST /api/vpn/access` are display-only audit labels, not network calls.

Empty-database behavior is covered for the model snapshot and the aggregate query
implementations; populated behavior is covered by the seeded API fixture. The
backend acceptance script also starts Uvicorn and Vite and exercises API calls
through the Vite proxy.
