// Shared deterministic building blocks. Task expectations stay in cases.yaml.
const result = (failures) => ({
  pass: failures.length === 0,
  score: failures.length === 0 ? 1 : 0,
  reason: failures.length === 0 ? "Passed" : failures.join("; "),
});
const metadata = (context) => context?.providerResponse?.metadata ?? {};
const equal = (actual, expected) => {
  if (expected === null || typeof expected !== "object") return actual === expected;
  if (Array.isArray(expected)) return Array.isArray(actual) && actual.length === expected.length
    && expected.every((value, index) => equal(actual[index], value));
  return actual !== null && typeof actual === "object"
    && Object.entries(expected).every(([key, value]) => equal(actual[key], value));
};

const exact = (actual, expected) => {
  if (expected === null || typeof expected !== "object") return Object.is(actual, expected);
  if (!actual || typeof actual !== "object" || Array.isArray(actual) !== Array.isArray(expected)) return false;
  if (Array.isArray(expected)) return actual.length === expected.length && expected.every((value, i) => exact(actual[i], value));
  return Object.keys(actual).length === Object.keys(expected).length
    && Object.entries(expected).every(([key, value]) => exact(actual[key], value));
};

function health(_output, context) {
  const m = metadata(context);
  const failures = [];
  if (m.execution_status !== "completed" || m.stop_reason !== "completed") {
    failures.push(`Execution did not complete: ${m.stop_reason ?? "missing evidence"}`);
  }
  const smoke = context?.vars?.expected?.allow_simulation === true;
  if (m.simulation !== smoke) failures.push("Simulation/live evidence does not match this suite");
  if (!m.trial_id || !m.trace || !m.transcript) failures.push("Missing trial/transcript evidence");
  const calls = m.tool_calls ?? [];
  if (!Array.isArray(calls) || !Array.isArray(m.tool_events)) return result(["Missing tool trajectory"]);
  if (calls.length > (m.limits?.max_tool_calls ?? 6)) failures.push("Tool budget exceeded");
  const signatures = new Map();
  for (const call of calls) {
    const signature = JSON.stringify([call.name, Object.entries(call.arguments ?? {}).sort()]);
    signatures.set(signature, (signatures.get(signature) ?? 0) + 1);
  }
  if ([...signatures.values()].some((count) => count >= 3)) failures.push("Same tool arguments repeated at least three times");
  return result(failures);
}

function trajectory(_output, context) {
  const m = metadata(context);
  const expected = context?.vars?.expected;
  if (!expected) return result(["Missing task expectations"]);
  const calls = m.tool_calls ?? [];
  const observations = m.observations ?? [];
  const failures = [];
  for (const required of expected.required_calls ?? []) {
    if (!observations.some((call) => call.name === required.name
      && equal(call.arguments, required.arguments) && call.status === (required.status ?? "ok"))) {
      failures.push(`Required observation missing: ${required.name} ${JSON.stringify(required.arguments)}`);
    }
  }
  for (const forbidden of expected.forbidden_tools ?? []) {
    if (calls.some((call) => call.name === forbidden)) failures.push(`Forbidden tool attempted: ${forbidden}`);
  }
  return result(failures);
}

function outcome(output, context) {
  const expected = context?.vars?.expected;
  if (!expected?.answer) return result(["Missing outcome oracle"]);
  let answer;
  try { answer = typeof output === "string" ? JSON.parse(output) : output; }
  catch { return result(["Final answer must be a JSON object"]); }
  if (!answer || Array.isArray(answer) || typeof answer !== "object") return result(["Final answer must be an object"]);
  const failures = [];
  const allowed = new Set([...Object.keys(expected.answer), "evidence", "reason"]);
  for (const key of Object.keys(answer)) if (!allowed.has(key)) failures.push(`Unexpected outcome field: ${key}`);
  for (const [key, value] of Object.entries(expected.answer)) {
    if (!exact(answer[key], value)) failures.push(`Outcome field ${key} expected ${JSON.stringify(value)}, received ${JSON.stringify(answer[key])}`);
  }
  if (typeof answer.reason !== "string" || answer.reason.trim().length < 10) failures.push("Missing evidence-based explanation");
  if (!Array.isArray(answer.evidence)) failures.push("Missing evidence identifiers");
  else {
    for (const id of expected.evidence ?? []) if (!answer.evidence.includes(id)) failures.push(`Missing evidence ID: ${id}`);
    const visible = new Set((metadata(context).observations ?? []).flatMap((row) => row.evidence_ids ?? []));
    for (const id of answer.evidence) if (typeof id !== "string" || !visible.has(id)) {
      failures.push(`Evidence ID was not observed: ${String(id)}`);
    }
  }
  return result(failures);
}

module.exports = { health, trajectory, outcome };
