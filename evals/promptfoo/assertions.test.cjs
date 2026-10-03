const test = require("node:test");
const assert = require("node:assert/strict");
const { health, trajectory, outcome } = require("./assertions.cjs");

const answer = { verdict: "reconcile", facts: { committed: null }, evidence: ["R-1"], reason: "The write has no receipt, so its outcome remains unknown." };
function context(overrides = {}) {
  return {
    vars: { expected: { answer: { verdict: "reconcile", facts: { committed: null } }, evidence: ["R-1"], required_calls: [{ name: "lookup", arguments: { id: "R-1" } }], forbidden_tools: ["write_record"] } },
    providerResponse: { metadata: {
      execution_status: "completed", stop_reason: "completed", simulation: false,
      trial_id: "trial", trace: "events.jsonl", transcript: "transcript.json",
      tool_calls: [{ name: "lookup", arguments: { id: "R-1" } }],
      tool_events: [{ name: "lookup", status: "ok" }],
      observations: [{ name: "lookup", arguments: { id: "R-1" }, status: "ok", evidence_ids: ["R-1"] }],
      ...overrides,
    } },
  };
}
test("correct decision passes despite different explanation wording", () => {
  const c = context();
  for (const grader of [health, trajectory, outcome]) assert.equal(grader(JSON.stringify(answer), c).pass, true);
  assert.equal(outcome(JSON.stringify({ ...answer, reason: "Check the remote record before sending another request." }), c).pass, true);
});
test("fabricated completion fails a held-out outcome oracle", () => {
  assert.equal(outcome(JSON.stringify({ ...answer, verdict: "completed", facts: { committed: true } }), context()).pass, false);
});
test("correct-looking text cannot substitute for missing tool evidence", () => {
  assert.equal(trajectory(JSON.stringify(answer), context({ observations: [] })).pass, false);
  assert.equal(outcome(JSON.stringify(answer), context({ observations: [] })).pass, false);
});
test("wrong arguments fail required observations", () => {
  assert.equal(trajectory("", context({ observations: [{ name: "lookup", arguments: { id: "R-2" }, status: "ok" }] })).pass, false);
});
test("denied write attempts still fail trajectory", () => {
  assert.equal(trajectory("", context({ tool_calls: [{ name: "write_record", arguments: {} }] })).pass, false);
});
test("limited and simulated runs cannot pass a live quality gate", () => {
  assert.equal(health("", context({ stop_reason: "max_tool_calls", execution_status: "limited" })).pass, false);
  assert.equal(health("", context({ simulation: true })).pass, false);
});
test("loop guard ignores object key order", () => {
  const tool_calls = [
    { name: "lookup", arguments: { id: "R-1", section: "a" } },
    { name: "lookup", arguments: { section: "a", id: "R-1" } },
    { name: "lookup", arguments: { id: "R-1", section: "a" } },
  ];
  assert.equal(health("", context({ tool_calls })).pass, false);
});
test("malformed JSON and invented evidence are rejected", () => {
  assert.equal(outcome("done", context()).pass, false);
  assert.equal(outcome(JSON.stringify({ ...answer, evidence: ["R-1", "made-up"] }), context()).pass, false);
});
test("unrequested factual claims cannot hide beside the held-out fields", () => {
  assert.equal(outcome(JSON.stringify({ ...answer, facts: { committed: null, verified: true } }), context()).pass, false);
  assert.equal(outcome(JSON.stringify({ ...answer, verified_claim: "write succeeded" }), context()).pass, false);
});
