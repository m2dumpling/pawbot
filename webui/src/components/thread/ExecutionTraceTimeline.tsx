import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  CircleDashed,
  Wrench,
  XCircle,
} from "lucide-react";

import type { ExecutionTraceEvent } from "@/lib/types";
import { cn } from "@/lib/utils";

type Translate = ReturnType<typeof useTranslation>["t"];

function tx(
  t: Translate,
  key: string,
  fallback: string,
  values?: Record<string, unknown>,
): string {
  return t(key, { defaultValue: fallback, ...(values ?? {}) });
}

function formatJson(value: unknown): string {
  const encoded = JSON.stringify(value, null, 2);
  return encoded === undefined ? String(value) : encoded;
}

function compactText(value: string, maxLength = 720): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  return normalized.length > maxLength
    ? `${normalized.slice(0, maxLength).trimEnd()}…`
    : normalized;
}

function displayValue(value: unknown): string {
  return typeof value === "string" ? value : formatJson(value);
}

function formatDuration(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  const milliseconds = Math.max(0, Math.round(value));
  return milliseconds < 1000 ? `${milliseconds}ms` : `${(milliseconds / 1000).toFixed(1)}s`;
}

function stageLabel(stage: string, t: Translate): string {
  return tx(t, `settings.enhancements.trace.stage.${stage}`, stage || tx(
    t,
    "settings.enhancements.trace.stage.unknown",
    "Unknown stage",
  ));
}

function humanizeStatus(value: unknown, t: Translate): string {
  switch (String(value ?? "").toLowerCase()) {
    case "completed":
    case "received":
    case "succeeded":
    case "accepted":
    case "done":
    case "success":
      return tx(t, "settings.enhancements.trace.status.completed", "Completed");
    case "error":
    case "failed":
      return tx(t, "settings.enhancements.trace.status.failed", "Failed");
    case "cancelled":
    case "unknown_side_effect":
    case "blocked":
    case "incomplete":
      return tx(t, "settings.enhancements.trace.status.uncertain", "Needs attention");
    case "running":
    case "started":
    case "pending":
    case "in_progress":
      return tx(t, "settings.enhancements.trace.status.running", "Running");
    case "planned":
      return tx(t, "settings.enhancements.trace.status.planned", "Planned");
    case "retrying":
      return tx(t, "settings.enhancements.trace.status.retrying", "Retrying");
    case "waiting":
      return tx(t, "settings.enhancements.trace.status.waiting", "Waiting");
    case "approved":
      return tx(t, "settings.enhancements.trace.status.approved", "Approved");
    case "denied":
      return tx(t, "settings.enhancements.trace.status.denied", "Denied");
    default:
      return String(value ?? "") || tx(t, "settings.enhancements.trace.status.notReported", "Not reported");
  }
}

function eventLabel(event: ExecutionTraceEvent, t: Translate): string {
  const name = String(event.event ?? "");
  const iteration = Number(event.iteration);
  const round = Number.isFinite(iteration)
    ? tx(t, "settings.enhancements.trace.iteration", "Round {{number}}", { number: iteration + 1 })
    : "";
  const tool = String(event.tool_name ?? "");
  const stage = stageLabel(String(event.stage ?? ""), t);
  switch (name) {
    case "turn.accepted":
      return tx(t, "settings.enhancements.trace.turnAccepted", "Turn accepted");
    case "agent.started":
      return tx(t, "settings.enhancements.trace.agentStarted", "Agent started");
    case "agent.completed":
      return tx(t, "settings.enhancements.trace.agentCompleted", "Agent completed");
    case "agent.error":
      return tx(t, "settings.enhancements.trace.agentError", "Agent failed");
    case "agent.finalized":
      return tx(t, "settings.enhancements.trace.agentFinalized", "Agent interrupted");
    case "stage.started":
      return tx(t, "settings.enhancements.trace.stageStarted", "Stage started · {{stage}}", { stage });
    case "stage.completed":
      return tx(t, "settings.enhancements.trace.stageCompleted", "Stage completed · {{stage}}", { stage });
    case "stage.failed":
      return tx(t, "settings.enhancements.trace.stageFailed", "Stage failed · {{stage}}", { stage });
    case "stage.cancelled":
      return tx(t, "settings.enhancements.trace.stageCancelled", "Stage cancelled · {{stage}}", { stage });
    case "iteration.started":
      return tx(t, "settings.enhancements.trace.iterationStarted", "Agent round started · {{round}}", { round });
    case "iteration.completed":
      return tx(t, "settings.enhancements.trace.iterationCompleted", "Agent round completed · {{round}}", { round });
    case "context.ready":
      return tx(t, "settings.enhancements.trace.contextReady", "Context prepared");
    case "checkpoint.saved":
      return tx(t, "settings.enhancements.trace.checkpointSaved", "Recovery checkpoint saved");
    case "recovery.checkpoint_restored":
      return tx(t, "settings.enhancements.trace.checkpointRestored", "Resumed from checkpoint");
    case "recovery.interruption_restored":
      return tx(t, "settings.enhancements.trace.interruptionRestored", "Recovered interrupted work");
    case "budget.exhausted":
      return tx(t, "settings.enhancements.trace.budgetExhausted", "Execution budget reached");
    case "task.verification":
      return tx(t, "settings.enhancements.trace.taskVerificationEvent", "Task completion check · {{status}}", {
        status: event.verification_status === "passed"
          ? tx(t, "settings.enhancements.trace.taskPassed", "Passed")
          : event.verification_status === "failed"
            ? tx(t, "settings.enhancements.trace.taskFailed", "Failed")
            : tx(t, "settings.enhancements.trace.taskNotEvaluable", "Not evaluable"),
      });
    case "llm.request_started":
      return tx(t, "settings.enhancements.trace.modelRequestStarted", "Model request started · {{round}}", { round });
    case "llm.response":
      return tx(t, "settings.enhancements.trace.modelResponse", "Model response · {{round}}", { round });
    case "llm.request_failed":
      return tx(t, "settings.enhancements.trace.modelRequestFailed", "Model request failed · {{round}}", { round });
    case "llm.retry":
      return tx(t, "settings.enhancements.trace.modelRetry", "Model request retry · {{round}}", { round });
    case "tool.planned":
      return tx(t, "settings.enhancements.trace.toolPlanned", "Tool planned · {{tool}}", { tool });
    case "tool.started":
      return tx(t, "settings.enhancements.trace.toolStarted", "Tool started · {{tool}}", { tool });
    case "tool.finished":
      return tx(t, "settings.enhancements.trace.toolFinished", "Tool finished · {{tool}}", { tool });
    case "tool.cancelled":
      return tx(t, "settings.enhancements.trace.toolCancelled", "Tool cancelled · {{tool}}", { tool });
    case "tool.approval_requested":
      return tx(t, "settings.enhancements.trace.approvalRequested", "Approval requested · {{tool}}", { tool });
    case "tool.approval_resolved":
      return tx(t, "settings.enhancements.trace.approvalResolved", "Approval resolved · {{tool}}", { tool });
    case "provider_tool.started":
      return tx(t, "settings.enhancements.trace.providerToolStarted", "Provider tool started · {{tool}}", { tool });
    case "provider_tool.completed":
      return tx(t, "settings.enhancements.trace.providerToolCompleted", "Provider tool completed · {{tool}}", { tool });
    case "provider_tool.error":
      return tx(t, "settings.enhancements.trace.providerToolError", "Provider tool failed · {{tool}}", { tool });
    case "turn.completed":
      return tx(t, "settings.enhancements.trace.turnCompleted", "Turn completed");
    case "turn.failed":
      return tx(t, "settings.enhancements.trace.turnFailed", "Turn failed");
    case "turn.cancelled":
      return tx(t, "settings.enhancements.trace.turnCancelled", "Turn cancelled");
    case "turn.incomplete":
      return tx(t, "settings.enhancements.trace.turnIncomplete", "Turn incomplete");
    default:
      return name || tx(t, "settings.enhancements.trace.unknown", "Execution event");
  }
}

function eventTone(event: ExecutionTraceEvent): string {
  const status = String(event.status ?? "").toLowerCase();
  if (["error", "failed"].includes(status)) {
    return "border-red-200 bg-red-50/75 dark:border-red-900 dark:bg-red-950/25";
  }
  if (["cancelled", "unknown_side_effect", "blocked", "incomplete", "denied"].includes(status)) {
    return "border-amber-200 bg-amber-50/75 dark:border-amber-900 dark:bg-amber-950/25";
  }
  if (String(event.event ?? "").startsWith("tool.") || String(event.event ?? "").startsWith("provider_tool.")) {
    return "border-orange-200 bg-orange-50/55 dark:border-orange-900 dark:bg-orange-950/20";
  }
  if (String(event.event ?? "").startsWith("llm.")) {
    return "border-blue-200 bg-blue-50/55 dark:border-blue-900 dark:bg-blue-950/20";
  }
  return "border-border/70 bg-background/70";
}

function eventIcon(event: ExecutionTraceEvent) {
  const name = String(event.event ?? "");
  const status = String(event.status ?? "").toLowerCase();
  if (["error", "failed"].includes(status)) return <XCircle className="h-4 w-4 text-red-600" />;
  if (["cancelled", "unknown_side_effect", "blocked", "incomplete", "denied"].includes(status)) {
    return <AlertTriangle className="h-4 w-4 text-amber-600" />;
  }
  if (["running", "planned", "retrying", "waiting"].includes(status)) {
    return <CircleDashed className="h-4 w-4 text-blue-600" />;
  }
  if (name.startsWith("tool.") || name.startsWith("provider_tool.")) return <Wrench className="h-4 w-4 text-orange-600" />;
  if (name.startsWith("llm.")) return <Bot className="h-4 w-4 text-blue-600" />;
  return <CheckCircle2 className="h-4 w-4 text-emerald-600" />;
}

const FIELD_LABELS: Record<string, string> = {
  event: "Event type",
  model: "Model",
  provider: "Provider",
  stage: "Stage",
  iteration: "Agent round",
  call_id: "Call ID",
  approval_id: "Approval ID",
  attempt: "Attempt",
  retry_attempt: "Retry attempt",
  status: "Status",
  duration_ms: "Duration",
  generation_ms: "Generation time",
  ttft_ms: "Time to first token",
  initial_message_count: "Initial messages",
  message_count: "Messages in context",
  model_message_count: "Messages sent to model",
  history_message_count: "History messages",
  runtime_context_block_count: "Runtime context blocks",
  provider_state_resumable: "Provider state resumable",
  context_window_tokens: "Context window",
  tools_available: "Tools available",
  session_ready: "Session ready",
  ephemeral: "Ephemeral turn",
  summary_created: "Summary created",
  command_handled: "Command handled",
  session_persisted: "Session persisted",
  persisted: "Persisted",
  latency_ms: "Turn latency",
  response_prepared: "Response prepared",
  response_chars: "Response characters",
  final_content_chars: "Final output characters",
  final_content_preview: "Final output preview",
  tool_name: "Tool",
  tool_names: "Tools selected",
  tool_count: "Tool count",
  argument_keys: "Argument keys",
  arguments_preview: "Arguments preview",
  content_chars: "Visible output characters",
  content_preview: "Visible output preview",
  reasoning_chars: "Reasoning characters",
  reasoning_preview: "Reasoning preview",
  finish_reason: "Finish reason",
  usage: "Token usage",
  tool_capabilities: "Capabilities",
  read_only: "Read-only",
  concurrency_safe: "Concurrency safe",
  exclusive: "Exclusive tool",
  lifecycle_state: "Tool state",
  side_effect: "Side effect",
  result_type: "Result type",
  result_chars: "Result characters",
  result_preview: "Result preview",
  operation_id: "Operation ID",
  recovery_required: "Recovery confirmation required",
  recovery_resolution: "Recovery decision",
  recovery_strategy: "Recovery strategy",
  reversible: "Reversible",
  receipt_supported: "Receipt supported",
  receipt: "Execution receipt",
  side_effect_class: "Side-effect class",
  idempotency: "Idempotency",
  verification_status: "Task check status",
  verification_completed: "Task completed",
  verification_attempt: "Task check attempt",
  verification_reason: "Task check reason",
  verification_failures: "Failed checks",
  stop_reason: "Stop reason",
  reason: "Reason",
  budget: "Budget snapshot",
  detail: "Detail",
  error: "Error",
};

const FIELD_TRANSLATION_KEYS: Record<string, string> = {
  event: "eventType",
  finish_reason: "finishReason",
  tool_names: "toolNames",
  tool_count: "toolCount",
  context_window_tokens: "contextWindow",
  model_message_count: "modelMessages",
  message_count: "messageCount",
  stop_reason: "stopReason",
  side_effect: "sideEffect",
  lifecycle_state: "lifecycleState",
  tools_used: "toolsUsed",
  usage: "usage",
  tool_capabilities: "toolCapabilities",
  read_only: "readOnly",
  concurrency_safe: "concurrencySafe",
  exclusive: "exclusive",
  reason: "reason",
  retry_attempt: "retryAttempt",
  pending_tool_count: "pendingToolCount",
  completed_tool_count: "completedToolCount",
  iteration: "agentRound",
  call_id: "callId",
  approval_id: "approvalId",
  duration_ms: "duration",
  generation_ms: "generationTime",
  ttft_ms: "timeToFirstToken",
  initial_message_count: "initialMessages",
  history_message_count: "historyMessages",
  runtime_context_block_count: "runtimeContextBlocks",
  provider_state_resumable: "providerStateResumable",
  tools_available: "toolsAvailable",
  session_ready: "sessionReady",
  ephemeral: "ephemeral",
  summary_created: "summaryCreated",
  command_handled: "commandHandled",
  session_persisted: "sessionPersisted",
  persisted: "persisted",
  latency_ms: "turnLatency",
  response_prepared: "responsePrepared",
  response_chars: "responseChars",
  final_content_chars: "finalContentChars",
  final_content_preview: "finalContentPreview",
  tool_name: "tool",
  argument_keys: "argumentKeys",
  arguments_preview: "argumentsPreview",
  content_chars: "contentChars",
  content_preview: "contentPreview",
  reasoning_chars: "reasoningChars",
  reasoning_preview: "reasoningPreview",
  result_type: "resultType",
  result_chars: "resultChars",
  result_preview: "resultPreview",
  status: "status",
  stage: "stage",
  model: "model",
  provider: "provider",
  attempt: "attempt",
  operation_id: "operationId",
  recovery_required: "recoveryRequired",
  recovery_resolution: "recoveryResolution",
  recovery_strategy: "recoveryStrategy",
  reversible: "reversible",
  receipt_supported: "receiptSupported",
  receipt: "receipt",
  side_effect_class: "sideEffectClass",
  idempotency: "idempotency",
  verification_status: "verificationStatus",
  verification_completed: "verificationCompleted",
  verification_attempt: "verificationAttempt",
  verification_reason: "verificationReason",
  verification_failures: "verificationFailures",
};

function fieldLabel(key: string, t: Translate): string {
  const translated = t(
    `settings.enhancements.trace.fields.${FIELD_TRANSLATION_KEYS[key] || key}`,
    { defaultValue: "" },
  );
  return translated || FIELD_LABELS[key] || key;
}

function TraceValue({
  value,
  maxLength = 720,
  t,
}: {
  value: unknown;
  maxLength?: number;
  t: Translate;
}) {
  const fullText = displayValue(value);
  const preview = compactText(fullText, maxLength);
  const canExpand = fullText.length > maxLength;
  return (
    <>
      <div className="max-h-28 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-5 text-foreground">
        {preview || "—"}
      </div>
      {canExpand ? (
        <details className="mt-1">
          <summary className="cursor-pointer text-[11px] text-muted-foreground">{tx(t, "settings.enhancements.trace.fullValue", "Show full value")}</summary>
          <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-muted/50 p-2 font-mono text-[11px] leading-5 text-foreground">
            {fullText}
          </pre>
        </details>
      ) : null}
    </>
  );
}

function eventDetails(event: ExecutionTraceEvent, t: Translate): ReactNode {
  const keys = [
    "event",
    "model",
    "provider",
    "stage",
    "iteration",
    "call_id",
    "approval_id",
    "attempt",
    "retry_attempt",
    "status",
    "duration_ms",
    "generation_ms",
    "ttft_ms",
    "initial_message_count",
    "message_count",
    "model_message_count",
    "history_message_count",
    "runtime_context_block_count",
    "provider_state_resumable",
    "context_window_tokens",
    "tools_available",
    "session_ready",
    "ephemeral",
    "summary_created",
    "command_handled",
    "session_persisted",
    "persisted",
    "latency_ms",
    "response_prepared",
    "response_chars",
    "final_content_chars",
    "final_content_preview",
    "tool_name",
    "tool_names",
    "tool_count",
    "argument_keys",
    "arguments_preview",
    "content_chars",
    "content_preview",
    "reasoning_chars",
    "reasoning_preview",
    "finish_reason",
    "usage",
    "tool_capabilities",
    "read_only",
    "concurrency_safe",
    "exclusive",
    "lifecycle_state",
    "side_effect",
    "operation_id",
    "side_effect_class",
    "idempotency",
    "recovery_strategy",
    "recovery_required",
    "recovery_resolution",
    "reversible",
    "receipt_supported",
    "receipt",
    "result_type",
    "result_chars",
    "result_preview",
    "stop_reason",
    "reason",
    "budget",
    "detail",
    "error",
    "verification_status",
    "verification_completed",
    "verification_attempt",
    "verification_reason",
    "verification_failures",
  ];
  const details = keys
    .filter((key) => event[key] !== undefined && event[key] !== null && event[key] !== "")
    .map((key) => [key, event[key]] as const);
  if (details.length === 0) {
    return <div className="text-[11px] text-muted-foreground">{tx(t, "settings.enhancements.trace.noDetails", "No additional details were recorded.")}</div>;
  }
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {details.map(([key, value]) => (
        <div key={key} className="min-w-0 rounded-lg border border-border/60 bg-background/75 px-3 py-2">
          <div className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">{fieldLabel(key, t)}</div>
          <div className="mt-1">
            <TraceValue value={key === "duration_ms" ? formatDuration(value) : value} t={t} />
          </div>
        </div>
      ))}
    </div>
  );
}

export function ExecutionTraceTimeline({
  events,
  className,
  emptyLabel,
}: {
  events: Array<Record<string, unknown>>;
  className?: string;
  emptyLabel?: string;
}) {
  const { t } = useTranslation();
  const visibleEvents = [...events]
    .filter((event) => String(event.event ?? "") !== "turn.accepted")
    .sort((left, right) => Number(left.sequence ?? 0) - Number(right.sequence ?? 0)) as ExecutionTraceEvent[];
  if (visibleEvents.length === 0) {
    return <div className={cn("rounded-lg border border-dashed border-border/70 px-3 py-4 text-xs text-muted-foreground", className)}>{emptyLabel || tx(t, "settings.enhancements.trace.empty", "No execution events yet.")}</div>;
  }
  return (
    <div className={cn("space-y-2", className)}>
      {visibleEvents.map((event, index) => {
        const duration = formatDuration(event.duration_ms);
        const sequence = Number(event.sequence);
        return (
          <details
            key={`${String(event.event ?? "event")}-${String(event.sequence ?? index)}`}
            className={cn("rounded-xl border", eventTone(event))}
          >
            <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2.5 text-xs [&::-webkit-details-marker]:hidden">
              {eventIcon(event)}
              <span className="min-w-0 flex-1 font-medium text-foreground">{eventLabel(event, t)}</span>
              {Number.isFinite(sequence) ? <span className="shrink-0 font-mono text-[10px] text-muted-foreground">#{sequence}</span> : null}
              {duration ? <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{duration}</span> : null}
              <span className="shrink-0 rounded-full bg-background/75 px-2 py-0.5 text-[10px] text-muted-foreground">{humanizeStatus(event.status, t)}</span>
            </summary>
            <div className="border-t border-inherit px-3 pb-3 pt-2">{eventDetails(event, t)}</div>
          </details>
        );
      })}
    </div>
  );
}
