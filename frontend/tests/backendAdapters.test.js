import assert from "node:assert/strict";
import test from "node:test";

import adapters from "../src/services/backendAdapters.js";
import backendApi from "../src/services/backendApi.js";

test("recent rows preserve real execution, outbox, duration, and missing-field telemetry", () => {
  const [row] = adapters.normalizeRecentRows({
    items: [
      {
        request_id: "SNOC-REQ-ABC",
        request_type: "otp_number_change",
        request_status: "pending",
        execution_status: "NEEDS_INFORMATION",
        decision: "REQUEST_INFORMATION",
        sender: "requester@example.test",
        recipient: "snoc@example.test",
        duration_ms: 3210,
        missing_fields: ["phone"],
        reply_recipient: "requester@example.test",
        reply_subject: "More information required",
        reply_text: "Please provide the OTP phone.",
        reply_status: "pending",
        entities: { pdv_code: "12345678", phone: null },
        metadata: {
          execution_details: {
            endpoint: "/otp/12345678",
            status: "failed",
            latency_ms: 87,
            request_payload: { pdv_code: "12345678" },
            response_body: { error: "not-called" },
          },
        },
      },
    ],
  });

  assert.equal(row.processingDurationMs, 3210);
  assert.equal(row.publicReference, "SNOC-REQ-ABC");
  assert.equal(row.recipient, "snoc@example.test");
  assert.equal(row.execution.durationMs, 87);
  assert.deepEqual(row.execution.requestPayload, { pdv_code: "12345678" });
  assert.deepEqual(row.execution.responsePayload, { error: "not-called" });
  assert.equal(row.reply.status, "pending");
  assert.equal(row.reply.to, "requester@example.test");
  assert.deepEqual(row.entities.missingFields, ["phone"]);
  assert.match(row.validationError, /OTP\/phone/);
});

test("recent rows do not invent recipients, replies, or execution payloads", () => {
  const [row] = adapters.normalizeRecentRows({
    items: [
      {
        request_id: "REQ-LEGACY-1",
        request_type: "vpn_access",
        sender: "requester@example.test",
        metadata: {
          execution_details: {
            endpoint: "/vpn",
            status: "pending",
          },
        },
      },
    ],
  });

  assert.equal(row.publicReference, "REQ-LEGACY-1");
  assert.equal(row.recipient, null);
  assert.equal(row.reply.to, null);
  assert.equal(row.reply.status, "Not sent");
  assert.equal(row.execution.requestPayload, null);
  assert.equal(row.execution.responsePayload, null);
});

test("email-only security events stay non-operational and keep the real email id", () => {
  const [row] = adapters.normalizeRecentRows({
    items: [
      {
        record_type: "email_security_event",
        request_id: null,
        email_message_id: "769474f5-4aa7-41f0-a419-e6020e9cd999",
        operation_id: null,
        execution_occurred: false,
        request_status: "UNAUTHORIZED",
        execution_status: null,
        decision: "REJECT",
        sender: "unknown@example.test",
        authorization_allowed: false,
        validation_error: "sender_not_whitelisted",
      },
    ],
  });

  assert.equal(row.id, "769474f5-4aa7-41f0-a419-e6020e9cd999");
  assert.equal(row.emailId, "769474f5-4aa7-41f0-a419-e6020e9cd999");
  assert.equal(row.publicReference, null);
  assert.equal(row.nonOperational, true);
  assert.equal(row.executionOccurred, false);
  assert.equal(row.execution, null);
  assert.equal(row.status, "Unauthorized");
  assert.equal(row.reply.status, "Not sent");
});

test("model success-rate KPI uses measured structured-output validity without inventing F1", () => {
  const model = adapters.normalizeModel({
    available: true,
    provider: "vllm",
    modelName: "model-a",
    accuracy: null,
    f1Score: null,
    structuredOutputValidityRate: 97.5,
    datasetRows: 70,
  });

  assert.equal(model.accuracy, 97.5);
  assert.equal(model.macroF1, null);
  assert.equal(model.datasetRows, 70);
  assert.equal(model.provider, "vllm");
});

test("formal escalations carry business context and suppress duplicate synthetic rows", () => {
  const rows = adapters.normalizeEscalations(
    {
      escalations: [
        {
          id: "esc-1",
          public_reference: "SNOC-REQ-ABC",
          request_type: "password_reset",
          sender: "requester@example.test",
          pdv_code: "12345678",
          confidence: 91,
          summary: "Manual review",
          status: "open",
        },
      ],
    },
    [
      {
        id: "audit-1",
        publicReference: "SNOC-REQ-ABC",
        emailId: "SNOC-REQ-ABC",
        intent: "Password Reset",
        status: "Escalated",
      },
    ],
  );

  assert.equal(rows.length, 1);
  assert.equal(rows[0].publicReference, "SNOC-REQ-ABC");
  assert.equal(rows[0].intent, "Password Reset");
  assert.equal(rows[0].entity, "12345678");
});

test("workflow agent metrics and percentage confidence fallback stay typed", () => {
  const workflow = adapters.normalizeWorkflow({
    agents: {
      nlu: {
        status: "healthy",
        latencyMs: 125,
        processed: 4,
        errors: 1,
        lastSuccess: "2026-07-28T10:00:00Z",
      },
    },
  });
  const confidence = adapters.normalizeConfidenceAnalytics(null, [
    { confidence: 95 },
    { confidence: 0.75 },
    { confidence: null },
  ]);

  assert.deepEqual(workflow[0], {
    id: "nlu",
    title: "Nlu agent",
    status: "Healthy",
    processed: 4,
    errors: 1,
    averageMs: 125,
    lastSuccess: "2026-07-28T10:00:00Z",
  });
  assert.equal(confidence.measuredOperations, 2);
  assert.equal(confidence.unmeasuredOperations, 1);
  assert.equal(confidence.averageAnalyzerConfidence, 0.85);
});

test("runtime exposes the real inbound mailbox instead of a demo address", () => {
  const runtime = adapters.normalizeRuntime({
    imap_configured: true,
    inbox_address: "agent@example.test",
    imap_mailbox: "INBOX",
    imap_search_criterion: "UNSEEN",
  });

  assert.equal(runtime.imapConfigured, true);
  assert.equal(runtime.inboxAddress, "agent@example.test");
  assert.equal(runtime.imapMailbox, "INBOX");
  assert.equal(runtime.imapSearchCriterion, "UNSEEN");
});

test("analytics API keeps the current hook signature and sends an explicit period", async () => {
  const originalFetch = globalThis.fetch;
  let capturedUrl = null;
  let capturedSignal = null;
  globalThis.fetch = async (url, options) => {
    capturedUrl = String(url);
    capturedSignal = options.signal;
    return new Response(JSON.stringify({ rows: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  try {
    const controller = new AbortController();
    await backendApi.executionAnalytics(controller.signal);
    assert.equal(
      capturedUrl,
      "/api/snoc/frontend/analytics/executions?period=week",
    );
    assert.equal(capturedSignal, controller.signal);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("analytics API forwards a selected non-default period", async () => {
  const originalFetch = globalThis.fetch;
  const urls = [];
  globalThis.fetch = async (url) => {
    urls.push(String(url));
    return new Response(JSON.stringify({ rows: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  try {
    await Promise.all([
      backendApi.confidenceAnalytics("month"),
      backendApi.missingEntityAnalytics("month"),
      backendApi.executionAnalytics("month"),
    ]);
    assert.deepEqual(urls.sort(), [
      "/api/snoc/frontend/analytics/confidence?period=month",
      "/api/snoc/frontend/analytics/executions?period=month",
      "/api/snoc/frontend/analytics/missing-entities?period=month",
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("dashboard API maps the Today control to the backend day period", async () => {
  const originalFetch = globalThis.fetch;
  let capturedUrl = null;
  globalThis.fetch = async (url) => {
    capturedUrl = String(url);
    return new Response(JSON.stringify({ operational: {} }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  try {
    await backendApi.dashboardSummary("today");
    assert.equal(capturedUrl, "/api/snoc/dashboard/summary?period=day");
  } finally {
    globalThis.fetch = originalFetch;
  }
});
