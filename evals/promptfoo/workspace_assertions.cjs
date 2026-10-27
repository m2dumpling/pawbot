// Promptfoo graders consume observed runtime and independent verifier evidence.
const result = (failures) => ({
  pass: failures.length === 0,
  score: failures.length === 0 ? 1 : 0,
  reason: failures.length ? failures.join("; ") : "Passed",
});
const metadata = (context) => context?.providerResponse?.metadata ?? {};

function health(_output, context) {
  const m = metadata(context);
  const failures = [];
  if (m.execution_status !== "completed" || m.stop_reason !== "completed") {
    failures.push(`Execution did not complete: ${m.stop_reason ?? "unknown"}`);
  }
  if (m.simulation !== (context?.vars?.allow_simulation === true)) {
    failures.push("Simulation/live evidence does not match this suite");
  }
  if (!m.trial_id || !m.trace || !m.transcript) failures.push("Missing trial or trace evidence");
  if (!Array.isArray(m.tool_calls) || !Array.isArray(m.tool_events)) failures.push("Missing tool trajectory");
  return result(failures);
}

function trajectory(_output, context) {
  const m = metadata(context);
  if (!Array.isArray(m.boundary_failures)) return result(["Missing boundary evidence"]);
  return result(m.boundary_failures);
}

function outcome(_output, context) {
  const validation = metadata(context).validation;
  if (!validation || typeof validation.passed !== "boolean") return result(["Missing external outcome oracle"]);
  if (validation.status === "unavailable") return result(["External outcome oracle unavailable"]);
  return result(validation.passed ? [] : validation.failures?.length ? validation.failures : ["External outcome failed"]);
}

module.exports = { health, trajectory, outcome };
