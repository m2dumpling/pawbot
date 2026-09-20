import { useCallback, useEffect, useState, type ReactNode } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  Bot,
  Bug,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDashed,
  FileCheck2,
  Gauge,
  Info,
  Loader2,
  Maximize2,
  MessageSquare,
  PauseCircle,
  Play,
  Radio,
  RefreshCw,
  Square,
  Trash2,
  UserRound,
  Wrench,
  XCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ExecutionTraceTimeline } from "@/components/thread/ExecutionTraceTimeline";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  blackboxAddCandidateToEval,
  blackboxCandidates,
  blackboxDelete,
  blackboxDetail,
  blackboxList,
  blackboxReplay,
  blackboxPromoteCandidate,
  blackboxRejectCandidate,
  blackboxStart,
  blackboxStatus,
  blackboxStop,
  blackboxTokens,
  taskEvalList,
  taskEvalRun,
  traceDetail,
  traceList,
  type BlackboxBreakpoint,
  type BlackboxCandidate,
  type BlackboxDetail,
  type BlackboxRecording,
  type BlackboxReplayResult,
  type BlackboxStatus,
  type BlackboxTokens,
  type TaskEvalCase,
  type TaskEvalReport,
  type TraceDetail,
  type TraceSummary,
} from "@/lib/api";
import { useClient } from "@/providers/ClientProvider";

type Translate = TFunction;
type TraceFilter = "all" | "issues" | "slow";

function tx(
  t: Translate,
  key: string,
  fallback: string,
  values?: Record<string, unknown>,
): string {
  return t(key, { defaultValue: fallback, ...(values ?? {}) });
}

function scrollToSection(id: string): void {
  const element = document.getElementById(id);
  if (element && typeof element.scrollIntoView === "function") {
    element.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function StatCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-xl border border-settings-border bg-settings-surface p-4">
      <div className="text-xs uppercase tracking-wide text-settings-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-settings-foreground">{value}</div>
      {hint ? <div className="mt-1 text-xs text-settings-muted">{hint}</div> : null}
    </div>
  );
}

function recordingStatusLabel(recording: BlackboxRecording, t: Translate): string {
  return recording.status === "ready"
    ? tx(t, "settings.enhancements.status.ready", "Ready for offline validation")
    : tx(t, "settings.enhancements.status.incomplete", "Sample incomplete");
}

function generatedRecordingDisplayName(name: string, t: Translate): string {
  const match = /^(?:session|sample)-(\d{10,})$/.exec(name.trim());
  if (!match) return name;
  const timestamp = Number(match[1]);
  const formatted = Number.isFinite(timestamp)
    ? new Date(timestamp).toLocaleString()
    : "";
  return `${tx(t, "settings.enhancements.recording.generatedSample", "Regression sample")}${formatted ? ` · ${formatted}` : ""}`;
}

function recordingDisplayName(recording: BlackboxRecording, t: Translate): string {
  return generatedRecordingDisplayName(recording.name, t);
}

function recordingSessionLabel(recording: BlackboxRecording, t: Translate): string | null {
  const names = (recording.session_names ?? []).filter(Boolean);
  if (names.length === 0) return null;
  if (names.length === 1) {
    return `${tx(t, "settings.enhancements.recording.session", "Conversation")}: ${names[0]}`;
  }
  const visible = names.slice(0, 2).join(" · ");
  const suffix = names.length > 2
    ? ` · ${tx(t, "settings.enhancements.recording.moreSessions", "{{count}} more", { count: names.length - 2 })}`
    : "";
  return `${tx(t, "settings.enhancements.recording.sessions", "Conversations")}: ${visible}${suffix}`;
}

function recordingStatusMessage(recording: BlackboxRecording, t: Translate): string {
  switch (recording.reason) {
    case "missing_turn_file":
      return tx(t, "settings.enhancements.status.missingTurnFile", "Turn record is missing");
    case "unreadable":
      return tx(t, "settings.enhancements.status.unreadable", "Recording cannot be read");
    case "malformed":
      return tx(t, "settings.enhancements.status.malformed", "Recording format is invalid");
    case "no_valid_turns":
      return tx(t, "settings.enhancements.status.noValidTurns", "No valid turns were found");
    default:
      return recording.message || tx(t, "settings.enhancements.status.notRecorded", "Not recorded");
  }
}

function recordingStatusClass(recording: BlackboxRecording): string {
  return recording.status === "ready"
    ? "border-emerald-200 bg-emerald-50 text-emerald-700"
    : "border-amber-200 bg-amber-50 text-amber-700";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function formatJson(value: unknown): string {
  const result = JSON.stringify(value, null, 2);
  return result === undefined ? String(value) : result;
}

function messageText(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value.map(messageText).filter(Boolean).join("\n");
  }
  if (isRecord(value)) {
    if (value.content !== undefined) return messageText(value.content);
    if (value.text !== undefined) return messageText(value.text);
    if (value.thinking !== undefined) return messageText(value.thinking);
    if (value.reasoning !== undefined) return messageText(value.reasoning);
  }
  return value == null ? "" : formatJson(value);
}

function lastMessageText(messages: unknown, role: string): string {
  if (!Array.isArray(messages)) return "";
  for (const message of [...messages].reverse()) {
    if (!isRecord(message) || message.role !== role) continue;
    const content = messageText(message.content);
    if (content.trim()) return content;
  }
  return "";
}

function compactText(value: string, maxLength = 320, emptyValue = "Not recorded"): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (!normalized) return emptyValue;
  return normalized.length > maxLength
    ? `${normalized.slice(0, maxLength).trimEnd()}…`
    : normalized;
}

function formatDuration(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  const milliseconds = Math.max(0, Math.round(value));
  return milliseconds < 1000 ? `${milliseconds}ms` : `${(milliseconds / 1000).toFixed(1)}s`;
}

function humanizeStopReason(value: unknown, t: Translate): string {
  const reason = String(value ?? "").toLowerCase();
  switch (reason) {
    case "completed":
    case "stop":
      return tx(t, "settings.enhancements.stopReason.completed", "Completed");
    case "tool_calls":
      return tx(t, "settings.enhancements.stopReason.toolCalls", "Waiting for tool results");
    case "max_iterations":
      return tx(t, "settings.enhancements.stopReason.maxIterations", "Execution limit reached");
    case "cancelled":
    case "canceled":
      return tx(t, "settings.enhancements.stopReason.cancelled", "Cancelled");
    case "error":
      return tx(t, "settings.enhancements.stopReason.error", "Execution error");
    default:
      return reason && reason !== "unknown"
        ? reason
        : tx(t, "settings.enhancements.stopReason.unknown", "Not reported");
  }
}

function summarizeTools(events: Array<Record<string, unknown>>, t: Translate): string {
  const counts = new Map<string, number>();
  for (const event of events) {
    if (event.kind !== "tool") continue;
    const name = String(
      event.name ?? tx(t, "settings.enhancements.status.unnamedTool", "Unnamed tool"),
    );
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }
  if (counts.size === 0) return tx(t, "settings.enhancements.status.noTools", "No tools called");
  const entries = [...counts.entries()];
  const visible = entries.slice(0, 4).map(([name, count]) => `${name} × ${count}`);
  if (entries.length > visible.length) {
    visible.push(tx(t, "settings.enhancements.status.moreTools", "{{count}} more tool types", {
      count: entries.length - visible.length,
    }));
  }
  return visible.join(" · ");
}

function responseThinking(response: Record<string, unknown>): string {
  const reasoning = response.reasoning_content;
  if (typeof reasoning === "string" && reasoning.trim()) return reasoning;
  if (response.thinking_blocks !== undefined) return messageText(response.thinking_blocks);
  return "";
}

function responseToolNames(response: Record<string, unknown>): string[] {
  if (!Array.isArray(response.tool_calls)) return [];
  return response.tool_calls
    .map((call) => {
      if (!isRecord(call)) return "";
      if (typeof call.name === "string") return call.name;
      const functionCall = isRecord(call.function) ? call.function : null;
      return functionCall && typeof functionCall.name === "string" ? functionCall.name : "";
    })
    .filter(Boolean);
}

function humanizeToolStatus(value: unknown, t: Translate): string {
  switch (String(value ?? "").toLowerCase()) {
    case "ok":
      return tx(t, "settings.enhancements.toolStatus.ok", "Succeeded");
    case "error":
      return tx(t, "settings.enhancements.toolStatus.error", "Failed");
    case "blocked":
      return tx(t, "settings.enhancements.toolStatus.blocked", "Blocked");
    case "cancelled":
    case "canceled":
      return tx(t, "settings.enhancements.toolStatus.cancelled", "Cancelled");
    case "unknown":
      return tx(t, "settings.enhancements.toolStatus.unknown", "Uncertain");
    default:
      return String(value ?? tx(t, "settings.enhancements.toolStatus.notReported", "Not reported"));
  }
}

function numericCount(value: unknown): number {
  const count = typeof value === "number" ? value : Number(value);
  return Number.isFinite(count) && count > 0 ? count : 0;
}

function originalExecutionCounts(execution: unknown): {
  failedTools: number;
  providerErrors: number;
  unknownSideEffects: number;
} {
  const value = isRecord(execution) ? execution : {};
  return {
    failedTools: numericCount(value.failed_tool_count),
    providerErrors: numericCount(value.provider_error_count),
    unknownSideEffects: numericCount(value.unknown_side_effect_count),
  };
}

function originalExecutionHasIssue(execution: unknown): boolean {
  const counts = originalExecutionCounts(execution);
  const status = isRecord(execution) ? String(execution.status ?? "") : "";
  return counts.failedTools > 0
    || counts.providerErrors > 0
    || counts.unknownSideEffects > 0
    || (status !== "" && status !== "success");
}

function originalExecutionLabel(execution: unknown, t: Translate): string {
  const counts = originalExecutionCounts(execution);
  const status = isRecord(execution) ? String(execution.status ?? "") : "";
  if (counts.failedTools > 0 && counts.providerErrors > 0) {
    return tx(t, "settings.enhancements.original.multipleErrors", "Model and tool errors");
  }
  if (counts.failedTools > 0 || status === "tool_error") {
    return tx(t, "settings.enhancements.original.toolError", "Tool execution failed");
  }
  if (counts.providerErrors > 0 || status === "model_error") {
    return tx(t, "settings.enhancements.original.modelError", "Model request failed");
  }
  if (counts.unknownSideEffects > 0 || status === "unknown_side_effect") {
    return tx(t, "settings.enhancements.original.unknownSideEffect", "Tool side effect is uncertain");
  }
  switch (status) {
    case "cancelled":
      return tx(t, "settings.enhancements.original.cancelled", "Original run was cancelled");
    case "execution_error":
      return tx(t, "settings.enhancements.original.executionError", "Original run ended with an error");
    case "success":
      return tx(t, "settings.enhancements.original.success", "Completed without recorded errors");
    default:
      return tx(t, "settings.enhancements.original.unknown", "Original result not reported");
  }
}

function originalExecutionTone(execution: unknown): string {
  return originalExecutionHasIssue(execution)
    ? "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-200"
    : "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-200";
}

function taskEvaluationLabel(evaluation: unknown, t: Translate): string {
  const status = isRecord(evaluation) ? String(evaluation.status ?? "") : "";
  switch (status) {
    case "passed":
      return tx(t, "settings.enhancements.task.passed", "Task checks passed");
    case "failed":
      return tx(t, "settings.enhancements.task.failed", "Task checks failed");
    case "not_evaluable":
      return tx(t, "settings.enhancements.task.notEvaluable", "Task could not be evaluated");
    default:
      return tx(t, "settings.enhancements.task.notRecorded", "Task checks not recorded");
  }
}

function outcomeTaskLabel(outcome: unknown, t: Translate): string {
  const status = isRecord(outcome) ? String(outcome.task_status ?? "") : "";
  switch (status) {
    case "passed":
      return tx(t, "settings.enhancements.task.passed", "Task checks passed");
    case "failed":
      return tx(t, "settings.enhancements.task.failed", "Task checks failed");
    case "not_evaluable":
      return tx(t, "settings.enhancements.task.notEvaluable", "Task could not be evaluated");
    default:
      return tx(t, "settings.enhancements.task.notRecorded", "Task checks not recorded");
  }
}

function outcomeTaskTone(outcome: unknown): string {
  const status = isRecord(outcome) ? String(outcome.task_status ?? "") : "";
  return status === "passed"
    ? "border-emerald-200 bg-emerald-50 text-emerald-800"
    : status === "failed"
      ? "border-rose-200 bg-rose-50 text-rose-800"
      : "border-settings-border bg-background/70 text-settings-muted";
}

function outcomeSideEffectLabel(outcome: unknown, t: Translate): string {
  const status = isRecord(outcome) ? String(outcome.side_effect_status ?? "") : "";
  if (status === "unknown") {
    return tx(t, "settings.enhancements.original.unknownSideEffect", "Tool side effect is uncertain");
  }
  if (status === "confirmed") {
    return tx(t, "settings.enhancements.result.sideEffectConfirmed", "External action completed");
  }
  return tx(t, "settings.enhancements.result.sideEffectNone", "No external side effect recorded");
}

function outcomeSideEffectTone(outcome: unknown): string {
  return isRecord(outcome) && outcome.side_effect_status === "unknown"
    ? "border-amber-200 bg-amber-50 text-amber-800"
    : "border-settings-border bg-background/70 text-settings-muted";
}

function taskEvaluationTone(evaluation: unknown): string {
  const status = isRecord(evaluation) ? String(evaluation.status ?? "") : "";
  return status === "passed"
    ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-200"
    : "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-200";
}

function responseErrorDetails(response: Record<string, unknown>): {
  metadata: string;
  message: string;
} | null {
  const finishReason = String(response.finish_reason ?? "").toLowerCase();
  const statusCode = response.error_status_code;
  const kind = response.error_kind;
  const type = response.error_type;
  const code = response.error_code;
  const hasMetadata = [statusCode, kind, type, code].some((value) => value !== undefined && value !== null && value !== "");
  if (finishReason !== "error" && !hasMetadata) return null;
  const metadata = [
    statusCode !== undefined && statusCode !== null ? `HTTP ${String(statusCode)}` : "",
    kind ? `kind=${String(kind)}` : "",
    type ? `type=${String(type)}` : "",
    code ? `code=${String(code)}` : "",
  ].filter(Boolean).join(" · ");
  const content = typeof response.content === "string" ? response.content.trim() : "";
  return { metadata, message: content };
}

function displayValue(value: unknown): string {
  return typeof value === "string" ? value : formatJson(value);
}

function InlineRecordPreview({
  label,
  value,
  maxLength = 360,
}: {
  label: string;
  value: unknown;
  maxLength?: number;
}) {
  const { t } = useTranslation();
  const fullText = displayValue(value);
  const preview = compactText(
    fullText,
    maxLength,
    tx(t, "settings.enhancements.status.notRecorded", "Not recorded"),
  );
  const canExpand = fullText.trim().length > maxLength;
  return (
    <div className="min-w-0 rounded-lg border border-settings-border bg-background/70 px-3 py-2">
      <div className="text-[11px] font-medium text-settings-muted">{label}</div>
      <div className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-5 text-settings-foreground">
        {preview}
      </div>
      {canExpand ? (
        <details className="mt-2">
          <summary className="cursor-pointer text-[11px] text-settings-muted">
            {tx(t, "settings.enhancements.expand.fullContent", "Show full content")}
          </summary>
          <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded bg-settings-hover/50 p-2 font-mono text-[11px] leading-5 text-settings-foreground">
            {fullText}
          </pre>
        </details>
      ) : null}
    </div>
  );
}

function ReplayTimelineEvent({
  event,
  index,
}: {
  event: Record<string, unknown>;
  index: number;
}) {
  const { t } = useTranslation();
  const isTool = event.kind === "tool";
  const response = isRecord(event.response) ? event.response : null;
  const iterationNumber = Number(event.iteration);
  const iteration = Number.isFinite(iterationNumber) ? iterationNumber + 1 : null;
  const thinking = response ? responseThinking(response) : "";
  const toolNames = response ? responseToolNames(response) : [];
  const content = response && typeof response.content === "string" ? response.content : "";
  const providerError = response && !isTool ? responseErrorDetails(response) : null;
  const toolStatus = humanizeToolStatus(event.status, t);
  return (
    <div className={`relative rounded-xl border p-3 ${isTool
      ? "border-orange-200 bg-orange-50/45 dark:border-orange-900 dark:bg-orange-950/15"
      : "border-blue-200 bg-blue-50/45 dark:border-blue-900 dark:bg-blue-950/15"}`}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {isTool ? (
          <Wrench className="h-4 w-4 text-orange-600" />
        ) : (
          <Bot className="h-4 w-4 text-blue-600" />
        )}
        <span className="font-semibold text-settings-foreground">
          {isTool
            ? tx(t, "settings.enhancements.timeline.toolExecution", "Tool execution")
            : tx(t, "settings.enhancements.timeline.modelDecision", "Model decision")}
        </span>
        <span className="text-settings-muted">
          {tx(t, "settings.enhancements.timeline.step", "Step {{number}}", { number: index + 1 })}
        </span>
        {isTool ? (
          <span className="font-mono text-settings-foreground">
            {String(event.name ?? tx(t, "settings.enhancements.status.unnamedTool", "Unnamed tool"))}
          </span>
        ) : (
          <span className="text-settings-muted">
            {iteration == null
              ? tx(t, "settings.enhancements.timeline.unrecordedDecision", "Decision number not recorded")
              : tx(t, "settings.enhancements.timeline.decision", "Decision {{number}}", { number: iteration })}
          </span>
        )}
        <span className={`ml-auto rounded-full px-2 py-0.5 text-[11px] ${isTool
          ? event.status === "ok"
            ? "bg-emerald-100 text-emerald-800"
            : "bg-orange-100 text-orange-800"
          : "bg-blue-100 text-blue-800"}`}>
          {isTool ? toolStatus : humanizeStopReason(response?.finish_reason, t)}
        </span>
      </div>

      {isTool ? (
        <div className="mt-3 grid gap-2 lg:grid-cols-2">
          <InlineRecordPreview
            label={tx(t, "settings.enhancements.timeline.arguments", "Arguments")}
            value={event.args}
          />
          <InlineRecordPreview
            label={tx(t, "settings.enhancements.timeline.result", "Tool result")}
            value={event.result}
            maxLength={480}
          />
          {event.detail ? (
            <div className="lg:col-span-2 text-xs leading-5 text-settings-muted">
              <span className="font-medium">
                {tx(t, "settings.enhancements.timeline.detail", "Execution detail")}: {" "}
              </span>
              {String(event.detail)}
            </div>
          ) : null}
        </div>
      ) : (
        <div className="mt-3 space-y-2">
          {providerError ? (
            <div className="rounded-lg border border-red-300 bg-red-50 px-3 py-3 text-red-900 dark:border-red-900 dark:bg-red-950/25 dark:text-red-100">
              <div className="flex items-center gap-2 text-xs font-semibold">
                <AlertTriangle className="h-4 w-4 shrink-0 text-red-600 dark:text-red-300" />
                {tx(t, "settings.enhancements.timeline.providerError", "Model request failed")}
              </div>
              {providerError.metadata ? (
                <div className="mt-1 font-mono text-[11px] leading-5">{providerError.metadata}</div>
              ) : null}
              {providerError.message ? (
                <div className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap break-words text-xs leading-5">
                  {compactText(providerError.message, 720)}
                </div>
              ) : null}
            </div>
          ) : null}
          {thinking ? (
            <div className="rounded-lg border border-violet-200 bg-violet-50/80 px-3 py-2 dark:border-violet-900 dark:bg-violet-950/20">
              <div className="flex items-center gap-1.5 text-[11px] font-medium text-violet-800 dark:text-violet-200">
                <MessageSquare className="h-3.5 w-3.5" />
                {tx(t, "settings.enhancements.timeline.thinking", "Model thinking trace")}
              </div>
              <div className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-words text-xs leading-5 text-settings-foreground">
                {compactText(thinking, 720)}
              </div>
              {thinking.trim().length > 720 ? (
                <details className="mt-2">
                  <summary className="cursor-pointer text-[11px] text-violet-800 dark:text-violet-200">
                    {tx(t, "settings.enhancements.expand.fullThinking", "Show full thinking trace")}
                  </summary>
                  <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded bg-white/70 p-2 font-mono text-[11px] leading-5 text-settings-foreground dark:bg-black/20">
                    {thinking}
                  </pre>
                </details>
              ) : null}
            </div>
          ) : null}
          {toolNames.length > 0 ? (
            <div className="rounded-lg border border-settings-border bg-background/70 px-3 py-2 text-xs text-settings-foreground">
              <span className="font-medium">
                {tx(t, "settings.enhancements.timeline.modelCalls", "Model decided to call:")}
              </span>
              <span className="ml-1 font-mono">{toolNames.join(" · ")}</span>
            </div>
          ) : null}
          {content ? (
            <div className="rounded-lg border border-settings-border bg-background/70 px-3 py-2">
              <div className="text-[11px] font-medium text-settings-muted">
                {tx(t, "settings.enhancements.timeline.modelOutput", "Model output")}
              </div>
              <div className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words text-xs leading-5 text-settings-foreground">
                {compactText(content, 720)}
              </div>
              {content.trim().length > 720 ? (
                <details className="mt-2">
                  <summary className="cursor-pointer text-[11px] text-settings-muted">
                    {tx(t, "settings.enhancements.expand.fullModelOutput", "Show full model output")}
                  </summary>
                  <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded bg-settings-hover/50 p-2 text-xs leading-5 text-settings-foreground">
                    {content}
                  </pre>
                </details>
              ) : null}
            </div>
          ) : null}
          {!thinking && toolNames.length === 0 && !content ? (
            <div className="text-xs text-settings-muted">
              {tx(t, "settings.enhancements.timeline.noVisibleText", "The model returned no displayable text; the runner continued to the next step.")}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

function ConversationCard({
  icon,
  label,
  value,
  detail,
  children,
}: {
  icon: "user" | "assistant";
  label: string;
  value?: string;
  detail?: string;
  children?: ReactNode;
}) {
  return (
    <div className="h-fit min-w-0 rounded-xl border border-settings-border bg-background/70 p-3">
      <div className="flex items-center gap-2 text-[11px] font-medium text-settings-muted">
        {icon === "user" ? <UserRound className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
        {label}
      </div>
      {value ? <div className="mt-2 whitespace-pre-wrap break-words text-sm leading-5 text-settings-foreground">{value}</div> : null}
      {detail ? <div className="mt-1 text-[11px] leading-4 text-settings-muted">{detail}</div> : null}
      {children}
    </div>
  );
}

function ToolPolicySummary({
  events,
  t,
}: {
  events: Array<Record<string, unknown>>;
  t: Translate;
}) {
  const toolEvents = events.filter((event) => ["tool.started", "tool.finished"].includes(String(event.event ?? "")));
  if (toolEvents.length === 0) return null;
  const capabilities = [...new Set(toolEvents.flatMap((event) => (
    Array.isArray(event.tool_capabilities)
      ? event.tool_capabilities.filter((value): value is string => typeof value === "string")
      : []
  )))];
  const sideEffects = toolEvents
    .filter((event) => String(event.event ?? "") === "tool.finished")
    .reduce<Record<string, number>>((counts, event) => {
    const value = typeof event.side_effect === "string" ? event.side_effect : "unknown";
    counts[value] = (counts[value] ?? 0) + 1;
    return counts;
    }, {});
  const sideEffectLabel = (value: string): string => {
    switch (value) {
      case "none":
        return tx(t, "settings.enhancements.result.sideEffectNone", "read-only");
      case "may_have_occurred":
        return tx(t, "settings.enhancements.result.sideEffectPossible", "may have occurred");
      case "not_executed":
        return tx(t, "settings.enhancements.result.sideEffectIsolated", "isolated in replay");
      default:
        return tx(t, "settings.enhancements.result.sideEffectUnknown", "needs confirmation");
    }
  };
  return (
    <div className="rounded-xl border border-settings-border bg-background/55 p-3">
      <div className="flex items-center gap-2 text-xs font-semibold text-settings-foreground">
        <Wrench className="h-4 w-4 text-settings-muted" />
        {tx(t, "settings.enhancements.result.toolPolicyTitle", "Tool safety summary")}
      </div>
      <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
        <span className="rounded-full border border-settings-border bg-background/70 px-2 py-1 text-settings-muted">
          {tx(t, "settings.enhancements.result.capabilities", "Capabilities")} · {capabilities.length > 0 ? capabilities.join(" · ") : tx(t, "settings.enhancements.result.noCapabilities", "not reported")}
        </span>
        {Object.entries(sideEffects).map(([value, count]) => (
          <span key={value} className={`rounded-full border px-2 py-1 ${value === "may_have_occurred" || value === "unknown"
            ? "border-amber-200 bg-amber-50 text-amber-800"
            : "border-settings-border bg-background/70 text-settings-muted"}`}>
            {count} · {sideEffectLabel(value)}
          </span>
        ))}
      </div>
    </div>
  );
}

function structuredTraceLabel(event: Record<string, unknown>, t: Translate): string {
  const name = String(event.event ?? "");
  const iteration = Number(event.iteration);
  const stage = String(event.stage ?? "unknown");
  const stageName = tx(
    t,
    `settings.enhancements.trace.stage.${stage}`,
    stage,
  );
  const iterationLabel = Number.isFinite(iteration)
    ? tx(t, "settings.enhancements.trace.iteration", "Round {{number}}", { number: iteration + 1 })
    : "";
  switch (name) {
    case "stage.completed":
      return tx(t, "settings.enhancements.trace.stageCompleted", "Stage · {{stage}}", { stage: stageName });
    case "stage.started":
      return tx(t, "settings.enhancements.trace.stageStarted", "Stage started · {{stage}}", { stage: stageName });
    case "stage.failed":
      return tx(t, "settings.enhancements.trace.stageFailed", "Stage failed · {{stage}}", { stage: stageName });
    case "stage.cancelled":
      return tx(t, "settings.enhancements.trace.stageCancelled", "Stage cancelled · {{stage}}", { stage: stageName });
    case "llm.response":
      return tx(t, "settings.enhancements.trace.modelResponse", "Model response · {{round}}", { round: iterationLabel });
    case "llm.request_started":
      return tx(t, "settings.enhancements.trace.modelRequestStarted", "Model request started · {{round}}", { round: iterationLabel });
    case "llm.request_failed":
      return tx(t, "settings.enhancements.trace.modelRequestFailed", "Model request failed · {{round}}", { round: iterationLabel });
    case "llm.retry":
      return tx(t, "settings.enhancements.trace.modelRetry", "Model request retry · {{round}}", { round: iterationLabel });
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
    case "tool.planned":
      return tx(t, "settings.enhancements.trace.toolPlanned", "Tool planned · {{tool}}", { tool: String(event.tool_name ?? "unknown") });
    case "tool.started":
      return tx(t, "settings.enhancements.trace.toolStarted", "Tool started · {{tool}}", { tool: String(event.tool_name ?? "unknown") });
    case "tool.finished":
      return tx(t, "settings.enhancements.trace.toolFinished", "Tool result · {{tool}}", { tool: String(event.tool_name ?? "unknown") });
    case "tool.cancelled":
      return tx(t, "settings.enhancements.trace.toolCancelled", "Tool cancelled · {{tool}}", { tool: String(event.tool_name ?? "unknown") });
    case "tool.approval_requested":
      return tx(t, "settings.enhancements.trace.approvalRequested", "Approval requested · {{tool}}", { tool: String(event.tool_name ?? "unknown") });
    case "tool.approval_resolved":
      return tx(t, "settings.enhancements.trace.approvalResolved", "Approval resolved · {{tool}}", { tool: String(event.tool_name ?? "unknown") });
    case "provider_tool.started":
      return tx(t, "settings.enhancements.trace.providerToolStarted", "Provider tool started · {{tool}}", { tool: String(event.tool_name ?? "unknown") });
    case "provider_tool.completed":
      return tx(t, "settings.enhancements.trace.providerToolCompleted", "Provider tool completed · {{tool}}", { tool: String(event.tool_name ?? "unknown") });
    case "provider_tool.error":
      return tx(t, "settings.enhancements.trace.providerToolError", "Provider tool failed · {{tool}}", { tool: String(event.tool_name ?? "unknown") });
    case "iteration.completed":
      return tx(t, "settings.enhancements.trace.iterationCompleted", "Round completed · {{round}}", { round: iterationLabel });
    case "agent.completed":
      return tx(t, "settings.enhancements.trace.agentCompleted", "Agent execution completed");
    case "agent.error":
      return tx(t, "settings.enhancements.trace.agentError", "Agent execution failed");
    case "agent.finalized":
      return tx(t, "settings.enhancements.trace.agentFinalized", "Agent execution interrupted");
    case "turn.completed":
      return tx(t, "settings.enhancements.trace.turnCompleted", "Turn completed");
    case "turn.failed":
      return tx(t, "settings.enhancements.trace.turnFailed", "Turn failed");
    case "turn.cancelled":
      return tx(t, "settings.enhancements.trace.turnCancelled", "Turn cancelled");
    case "turn.incomplete":
      return tx(t, "settings.enhancements.trace.turnIncomplete", "Turn was interrupted");
    default:
      return name || tx(t, "settings.enhancements.trace.unknown", "Execution event");
  }
}

function structuredTraceStatus(event: Record<string, unknown>, t: Translate): string {
  const status = String(event.status ?? "").toLowerCase();
  switch (status) {
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
      return status || tx(t, "settings.enhancements.trace.status.notReported", "Not reported");
  }
}

function structuredTraceTone(event: Record<string, unknown>): string {
  const status = String(event.status ?? "").toLowerCase();
  if (["error", "failed"].includes(status)) {
    return "border-red-200 bg-red-50/70 dark:border-red-900 dark:bg-red-950/20";
  }
  if (["cancelled", "unknown_side_effect", "blocked", "denied", "incomplete"].includes(status)) {
    return "border-amber-200 bg-amber-50/70 dark:border-amber-900 dark:bg-amber-950/20";
  }
  if (String(event.event ?? "").startsWith("tool.") || String(event.event ?? "").startsWith("provider_tool.")) {
    return "border-orange-200 bg-orange-50/45 dark:border-orange-900 dark:bg-orange-950/15";
  }
  return "border-settings-border bg-background/70";
}

function structuredTraceIcon(event: Record<string, unknown>) {
  const name = String(event.event ?? "");
  const status = String(event.status ?? "").toLowerCase();
  if (["error", "failed"].includes(status)) return <XCircle className="h-4 w-4 text-red-600" />;
  if (["cancelled", "unknown_side_effect", "blocked", "denied", "incomplete"].includes(status)) return <AlertTriangle className="h-4 w-4 text-amber-600" />;
  if (["running", "planned", "retrying"].includes(status)) return <CircleDashed className="h-4 w-4 text-blue-600" />;
  if (name.startsWith("tool.") || name.startsWith("provider_tool.")) return <Wrench className="h-4 w-4 text-orange-600" />;
  if (name.startsWith("llm.")) return <Bot className="h-4 w-4 text-blue-600" />;
  return <CheckCircle2 className="h-4 w-4 text-emerald-600" />;
}

function structuredTraceDetails(event: Record<string, unknown>, t: Translate): ReactNode {
  const labels: Record<string, string> = {
    finish_reason: tx(t, "settings.enhancements.trace.fields.finishReason", "Finish reason"),
    tool_names: tx(t, "settings.enhancements.trace.fields.toolNames", "Tools selected"),
    tool_count: tx(t, "settings.enhancements.trace.fields.toolCount", "Tool count"),
    context_window_tokens: tx(t, "settings.enhancements.trace.fields.contextWindow", "Context window"),
    model_message_count: tx(t, "settings.enhancements.trace.fields.modelMessages", "Messages sent to model"),
    message_count: tx(t, "settings.enhancements.trace.fields.messageCount", "Message count"),
    stop_reason: tx(t, "settings.enhancements.trace.fields.stopReason", "Stop reason"),
    side_effect: tx(t, "settings.enhancements.trace.fields.sideEffect", "Side effect"),
    side_effect_class: tx(t, "settings.enhancements.trace.fields.sideEffectClass", "Side-effect class"),
    operation_id: tx(t, "settings.enhancements.trace.fields.operationId", "Operation ID"),
    idempotency: tx(t, "settings.enhancements.trace.fields.idempotency", "Idempotency"),
    recovery_strategy: tx(t, "settings.enhancements.trace.fields.recoveryStrategy", "Recovery strategy"),
    recovery_required: tx(t, "settings.enhancements.trace.fields.recoveryRequired", "Recovery confirmation required"),
    recovery_resolution: tx(t, "settings.enhancements.trace.fields.recoveryResolution", "Recovery decision"),
    reversible: tx(t, "settings.enhancements.trace.fields.reversible", "Reversible"),
    receipt_supported: tx(t, "settings.enhancements.trace.fields.receiptSupported", "Receipt supported"),
    receipt: tx(t, "settings.enhancements.trace.fields.receipt", "Execution receipt"),
    verification_status: tx(t, "settings.enhancements.trace.fields.verificationStatus", "Task check status"),
    verification_completed: tx(t, "settings.enhancements.trace.fields.verificationCompleted", "Task completed"),
    verification_attempt: tx(t, "settings.enhancements.trace.fields.verificationAttempt", "Task check attempt"),
    verification_reason: tx(t, "settings.enhancements.trace.fields.verificationReason", "Task check reason"),
    verification_failures: tx(t, "settings.enhancements.trace.fields.verificationFailures", "Failed checks"),
    lifecycle_state: tx(t, "settings.enhancements.trace.fields.lifecycleState", "Tool state"),
    tools_used: tx(t, "settings.enhancements.trace.fields.toolsUsed", "Tools used"),
    usage: tx(t, "settings.enhancements.trace.fields.usage", "Token usage"),
    tool_capabilities: tx(t, "settings.enhancements.trace.fields.toolCapabilities", "Capabilities"),
    read_only: tx(t, "settings.enhancements.trace.fields.readOnly", "Read-only"),
    concurrency_safe: tx(t, "settings.enhancements.trace.fields.concurrencySafe", "Concurrency safe"),
    exclusive: tx(t, "settings.enhancements.trace.fields.exclusive", "Exclusive tool"),
    reason: tx(t, "settings.enhancements.trace.fields.reason", "Reason"),
    retry_attempt: tx(t, "settings.enhancements.trace.fields.retryAttempt", "Retry attempt"),
    pending_tool_count: tx(t, "settings.enhancements.trace.fields.pendingToolCount", "Pending tools"),
    completed_tool_count: tx(t, "settings.enhancements.trace.fields.completedToolCount", "Completed tools"),
    model: tx(t, "settings.enhancements.trace.fields.model", "Model"),
    provider: tx(t, "settings.enhancements.trace.fields.provider", "Provider"),
    stage: tx(t, "settings.enhancements.trace.fields.stage", "Stage"),
    iteration: tx(t, "settings.enhancements.trace.fields.agentRound", "Agent round"),
    call_id: tx(t, "settings.enhancements.trace.fields.callId", "Call ID"),
    approval_id: tx(t, "settings.enhancements.trace.fields.approvalId", "Approval ID"),
    attempt: tx(t, "settings.enhancements.trace.fields.attempt", "Attempt"),
    status: tx(t, "settings.enhancements.trace.fields.status", "Status"),
    duration_ms: tx(t, "settings.enhancements.trace.fields.duration", "Duration"),
    generation_ms: tx(t, "settings.enhancements.trace.fields.generationTime", "Generation time"),
    ttft_ms: tx(t, "settings.enhancements.trace.fields.timeToFirstToken", "Time to first token"),
    initial_message_count: tx(t, "settings.enhancements.trace.fields.initialMessages", "Initial messages"),
    history_message_count: tx(t, "settings.enhancements.trace.fields.historyMessages", "History messages"),
    runtime_context_block_count: tx(t, "settings.enhancements.trace.fields.runtimeContextBlocks", "Runtime context blocks"),
    provider_state_resumable: tx(t, "settings.enhancements.trace.fields.providerStateResumable", "Provider state resumable"),
    tools_available: tx(t, "settings.enhancements.trace.fields.toolsAvailable", "Tools available"),
    session_ready: tx(t, "settings.enhancements.trace.fields.sessionReady", "Session ready"),
    ephemeral: tx(t, "settings.enhancements.trace.fields.ephemeral", "Ephemeral turn"),
    summary_created: tx(t, "settings.enhancements.trace.fields.summaryCreated", "Summary created"),
    command_handled: tx(t, "settings.enhancements.trace.fields.commandHandled", "Command handled"),
    session_persisted: tx(t, "settings.enhancements.trace.fields.sessionPersisted", "Session persisted"),
    persisted: tx(t, "settings.enhancements.trace.fields.persisted", "Persisted"),
    latency_ms: tx(t, "settings.enhancements.trace.fields.turnLatency", "Turn latency"),
    response_prepared: tx(t, "settings.enhancements.trace.fields.responsePrepared", "Response prepared"),
    response_chars: tx(t, "settings.enhancements.trace.fields.responseChars", "Response characters"),
    final_content_chars: tx(t, "settings.enhancements.trace.fields.finalContentChars", "Final output characters"),
    final_content_preview: tx(t, "settings.enhancements.trace.fields.finalContentPreview", "Final output preview"),
    tool_name: tx(t, "settings.enhancements.trace.fields.tool", "Tool"),
    argument_keys: tx(t, "settings.enhancements.trace.fields.argumentKeys", "Argument keys"),
    arguments_preview: tx(t, "settings.enhancements.trace.fields.argumentsPreview", "Arguments preview"),
    content_chars: tx(t, "settings.enhancements.trace.fields.contentChars", "Visible output characters"),
    content_preview: tx(t, "settings.enhancements.trace.fields.contentPreview", "Visible output preview"),
    reasoning_chars: tx(t, "settings.enhancements.trace.fields.reasoningChars", "Reasoning characters"),
    reasoning_preview: tx(t, "settings.enhancements.trace.fields.reasoningPreview", "Reasoning preview"),
    result_type: tx(t, "settings.enhancements.trace.fields.resultType", "Result type"),
    result_chars: tx(t, "settings.enhancements.trace.fields.resultChars", "Result characters"),
    result_preview: tx(t, "settings.enhancements.trace.fields.resultPreview", "Result preview"),
    budget: tx(t, "settings.enhancements.trace.fields.budget", "Budget snapshot"),
  };
  const details: Array<[string, unknown]> = [];
  for (const key of [
    "finish_reason",
    "model",
    "provider",
    "stage",
    "iteration",
    "call_id",
    "approval_id",
    "attempt",
    "status",
    "duration_ms",
    "generation_ms",
    "ttft_ms",
    "initial_message_count",
    "tool_names",
    "tool_count",
    "context_window_tokens",
    "model_message_count",
    "message_count",
    "history_message_count",
    "runtime_context_block_count",
    "provider_state_resumable",
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
    "argument_keys",
    "arguments_preview",
    "content_chars",
    "content_preview",
    "reasoning_chars",
    "reasoning_preview",
    "result_type",
    "result_chars",
    "result_preview",
    "budget",
    "stop_reason",
    "side_effect",
    "side_effect_class",
    "operation_id",
    "idempotency",
    "recovery_strategy",
    "recovery_required",
    "recovery_resolution",
    "reversible",
    "receipt_supported",
    "receipt",
    "lifecycle_state",
    "tools_used",
    "usage",
    "tool_capabilities",
    "read_only",
    "concurrency_safe",
    "exclusive",
    "verification_status",
    "verification_completed",
    "verification_attempt",
    "verification_reason",
    "verification_failures",
  ]) {
    if (event[key] !== undefined && event[key] !== null && event[key] !== "") {
       details.push([labels[key] ?? key, event[key]]);
    }
  }
  if (event.detail) details.push([tx(t, "settings.enhancements.trace.detail", "Detail"), event.detail]);
  if (event.error) details.push([tx(t, "settings.enhancements.trace.error", "Error"), event.error]);
  if (details.length === 0) return null;
  return (
    <div className="mt-2 grid gap-2 sm:grid-cols-2">
      {details.map(([label, value]) => (
        <InlineRecordPreview key={label} label={label} value={value} maxLength={360} />
      ))}
    </div>
  );
}

function StructuredTraceTimeline({ events }: { events: Array<Record<string, unknown>> }) {
  const { t } = useTranslation();
  const visibleEvents = [...events]
    .filter((event) => !["turn.accepted", "agent.started", "stage.started", "iteration.started"].includes(String(event.event ?? "")))
    .sort((left, right) => Number(left.sequence ?? 0) - Number(right.sequence ?? 0));
  return (
    <div className="space-y-2">
      {visibleEvents.map((event, index) => {
        const duration = Number(event.duration_ms);
        const hasDuration = Number.isFinite(duration) && duration >= 0;
        return (
          <details key={`${String(event.event ?? "event")}-${String(event.sequence ?? index)}`} className={`rounded-lg border ${structuredTraceTone(event)}`}>
            <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2.5 text-xs [&::-webkit-details-marker]:hidden">
              {structuredTraceIcon(event)}
              <span className="min-w-0 flex-1 font-medium text-settings-foreground">
                {structuredTraceLabel(event, t)}
              </span>
              {hasDuration ? <span className="shrink-0 font-mono text-[11px] text-settings-muted">{formatDuration(duration)}</span> : null}
              <span className="shrink-0 rounded-full bg-background/70 px-2 py-0.5 text-[11px] text-settings-muted">
                {structuredTraceStatus(event, t)}
              </span>
              <ChevronRight className="h-3.5 w-3.5 shrink-0 text-settings-muted" />
            </summary>
            <div className="border-t border-inherit px-3 pb-3 pt-1">
              {structuredTraceDetails(event, t) ?? (
                <div className="pt-2 text-[11px] text-settings-muted">
                  {tx(t, "settings.enhancements.trace.noDetails", "No additional details were recorded.")}
                </div>
              )}
            </div>
          </details>
        );
      })}
    </div>
  );
}

function ReplayTurnSummary({
  detail,
  replayOk,
  replayDiffCount,
  onFullscreen,
}: {
  detail: BlackboxDetail;
  replayOk: boolean;
  replayDiffCount: number;
  onFullscreen?: () => void;
}) {
  const { t } = useTranslation();
  const turn = detail.turn;
  const diagnostics = isRecord(detail.diagnostics) ? detail.diagnostics : {};
  const failedTools = Array.isArray(diagnostics.failed_tools) ? diagnostics.failed_tools : [];
  const unknownSideEffects = Array.isArray(diagnostics.unknown_side_effects)
    ? diagnostics.unknown_side_effects
    : [];
  const providerErrors = Array.isArray(diagnostics.provider_errors)
    ? diagnostics.provider_errors
    : [];
  const originalExecution = isRecord(diagnostics.original_execution)
    ? diagnostics.original_execution
    : {
      status: failedTools.length > 0
        ? "tool_error"
        : providerErrors.length > 0
          ? "model_error"
          : unknownSideEffects.length > 0
            ? "unknown_side_effect"
            : "success",
      ok: failedTools.length === 0 && providerErrors.length === 0 && unknownSideEffects.length === 0,
      failed_tool_count: failedTools.length,
      provider_error_count: providerErrors.length,
      unknown_side_effect_count: unknownSideEffects.length,
    };
  const taskEvaluation = isRecord(diagnostics.task_evaluation)
    ? diagnostics.task_evaluation
    : null;
  const originalOutcome = isRecord(diagnostics.original_outcome)
    ? diagnostics.original_outcome
    : null;
  const taskHasIssue = taskEvaluation
    && ["failed", "not_evaluable"].includes(String(taskEvaluation.status ?? ""));
  const userRequest = lastMessageText(turn.initial_messages, "user");
  const finalAnswer = typeof turn.final_content === "string" && turn.final_content.trim()
    ? turn.final_content
    : lastMessageText(turn.final_messages, "assistant");
  const originalCounts = originalExecutionCounts(originalExecution);
  const traceEvents = Array.isArray(detail.trace_events) ? detail.trace_events : [];
  const hasProblems = originalExecutionHasIssue(originalExecution)
    || failedTools.length > 0
    || providerErrors.length > 0
    || unknownSideEffects.length > 0
    || Boolean(taskHasIssue);
  const issueParts = [
    originalCounts.failedTools > 0
      ? tx(t, "settings.enhancements.turn.failedToolCount", "{{count}} tool call(s) failed", { count: originalCounts.failedTools })
      : "",
    originalCounts.providerErrors > 0
      ? tx(t, "settings.enhancements.turn.providerErrorCount", "{{count}} model request(s) failed", { count: originalCounts.providerErrors })
      : "",
    originalCounts.unknownSideEffects > 0
      ? tx(t, "settings.enhancements.turn.unknownSideEffectCount", "{{count}} tool result(s) are uncertain", { count: originalCounts.unknownSideEffects })
      : "",
    taskHasIssue
      ? taskEvaluationLabel(taskEvaluation, t)
      : "",
  ].filter(Boolean).join(" · ");
  const statusDescription = hasProblems
    ? `${issueParts || originalExecutionLabel(originalExecution, t)} ${tx(t, "settings.enhancements.turn.inspectRaw", "Inspect the readable trace or raw record for details.")}`
    : detail.counts.tool_calls > 0
      ? tx(t, "settings.enhancements.turn.toolSummary", "This run included {{tools}}.", {
        tools: summarizeTools(detail.events, t),
      })
      : tx(
        t,
        "settings.enhancements.turn.noToolSummary",
        "This task did not call a tool; the model produced the final answer directly.",
      );
  return (
    <div className="space-y-4 border-t border-settings-border bg-settings-hover/25 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-semibold text-settings-foreground">
            {tx(t, "settings.enhancements.turn.processTitle", "Turn execution")}
          </div>
          {detail.session_name ? (
            <div className="mt-0.5 truncate text-xs text-settings-muted">
              {tx(t, "settings.enhancements.turn.session", "Conversation")}: {detail.session_name}
            </div>
          ) : null}
          <div className="mt-0.5 font-mono text-[11px] text-settings-muted">{detail.turn_id}</div>
        </div>
        {onFullscreen ? (
          <Button type="button" variant="outline" size="sm" onClick={onFullscreen}>
            <Maximize2 className="mr-1.5 h-3.5 w-3.5" />
            {tx(t, "settings.enhancements.turn.rawRecord", "View raw record")}
          </Button>
        ) : null}
      </div>
      <div className="grid items-start gap-3 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
        <ConversationCard
          icon="user"
          label={tx(t, "settings.enhancements.turn.userMessage", "User request")}
          value={compactText(userRequest, 360, tx(t, "settings.enhancements.status.notRecorded", "Not recorded"))}
          detail={tx(t, "settings.enhancements.turn.userMessageDetail", "The content sent to the agent when this task started")}
        />
        <ConversationCard
          icon="assistant"
          label={tx(t, "settings.enhancements.turn.finalAnswer", "Final answer")}
          value={compactText(finalAnswer, 720, tx(t, "settings.enhancements.status.notRecorded", "Not recorded"))}
          detail={tx(t, "settings.enhancements.turn.finalAnswerDetail", "The content the agent showed to the user")}
        >
          {finalAnswer.trim().length > 720 ? (
            <details className="mt-2">
              <summary className="cursor-pointer text-[11px] text-settings-muted">
                {tx(t, "settings.enhancements.expand.fullAnswer", "Show full answer")}
              </summary>
              <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-words rounded bg-settings-hover/50 p-2 text-xs leading-5 text-settings-foreground">
                {finalAnswer}
              </pre>
            </details>
          ) : null}
        </ConversationCard>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded-full border border-settings-border bg-background/70 px-3 py-1 font-mono text-settings-foreground">
          {tx(t, "settings.enhancements.turn.model", "Model")} · {String(turn.model ?? tx(t, "settings.enhancements.status.notRecorded", "Not recorded"))}
        </span>
        <span className="rounded-full border border-settings-border bg-background/70 px-3 py-1 text-settings-foreground">
          {tx(t, "settings.enhancements.turn.modelDecisions", "Model decisions")} · {detail.counts.llm_responses}
        </span>
        <span className="rounded-full border border-settings-border bg-background/70 px-3 py-1 text-settings-foreground">
          {tx(t, "settings.enhancements.turn.toolExecutions", "Tool executions")} · {detail.counts.tool_calls}
        </span>
        <span className={`rounded-full border px-3 py-1 ${replayOk
          ? "border-emerald-200 bg-emerald-50 text-emerald-800"
          : "border-amber-200 bg-amber-50 text-amber-800"}`}>
          {tx(t, "settings.enhancements.result.replayLabel", "Replay result")} · {replayOk
            ? tx(t, "settings.enhancements.result.replayConsistent", "Consistent")
            : tx(t, "settings.enhancements.result.replayDifferencesShort", "{{count}} difference(s)", { count: replayDiffCount })}
        </span>
        <span className={`rounded-full border px-3 py-1 ${originalExecutionTone(originalExecution)}`}>
          {tx(t, "settings.enhancements.result.originalLabel", "Original execution")} · {originalExecutionLabel(originalExecution, t)}
        </span>
        <span className={`rounded-full border px-3 py-1 ${outcomeTaskTone(originalOutcome)}`}>
          {tx(t, "settings.enhancements.result.taskLabel", "Task verification")} · {outcomeTaskLabel(originalOutcome, t)}
        </span>
        <span className={`rounded-full border px-3 py-1 ${outcomeSideEffectTone(originalOutcome)}`}>
          {tx(t, "settings.enhancements.result.sideEffectLabel", "External effect")} · {outcomeSideEffectLabel(originalOutcome, t)}
        </span>
      </div>

      {taskEvaluation ? (
        <div className={"rounded-xl border px-3 py-3 text-xs " + taskEvaluationTone(taskEvaluation)}>
          <div className="font-medium">
            {tx(t, "settings.enhancements.result.taskLabel", "Task verification")}
          </div>
          <div className="mt-1">
            {taskEvaluationLabel(taskEvaluation, t)}
            {isRecord(taskEvaluation) && Array.isArray(taskEvaluation.failures) && taskEvaluation.failures.length > 0
              ? " · " + taskEvaluation.failures.join(" · ")
              : ""}
          </div>
        </div>
      ) : null}

      <ToolPolicySummary events={traceEvents} t={t} />

      <div className="rounded-xl border border-settings-border bg-background/55 p-3">
        <div className="flex items-start gap-2">
          <MessageSquare className="mt-0.5 h-4 w-4 shrink-0 text-settings-muted" />
          <div>
            <div className="text-sm font-semibold text-settings-foreground">
              {tx(t, "settings.enhancements.turn.traceTitle", "What happened in this turn")}
            </div>
            <div className="mt-1 text-xs leading-5 text-settings-muted">
              {tx(t, "settings.enhancements.turn.traceDescription", "The timeline follows the recorded order: model thinking, model decisions, tool calls, and tool results. Expand long values when needed. For the untouched JSON, open the raw record.")}
            </div>
          </div>
        </div>
        <div className="mt-3 space-y-2">
          {traceEvents.length > 0 ? (
            <StructuredTraceTimeline events={traceEvents} />
          ) : detail.events.length > 0 ? detail.events.map((event, index) => (
            <ReplayTimelineEvent key={`${String(event.kind ?? "event")}-${index}`} event={event} index={index} />
          )) : (
            <div className="rounded-lg border border-dashed border-settings-border px-3 py-4 text-xs text-settings-muted">
              {tx(t, "settings.enhancements.turn.noEvents", "This turn has no separate model or tool events. It may come from an older recording format.")}
            </div>
          )}
        </div>
      </div>

      <div className={`rounded-xl border px-3 py-3 text-xs leading-5 ${hasProblems
        ? "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-200"
        : "border-settings-border bg-background/70 text-settings-muted"}`}>
        <div className="font-medium text-settings-foreground">
          {tx(t, "settings.enhancements.turn.resultTitle", "Turn result")}
        </div>
        <div className="mt-1">{statusDescription} {tx(t, "settings.enhancements.turn.replaySafety", "Replay uses only the recorded model responses and tool results. It does not call the provider again or execute real tools.")}</div>
      </div>
    </div>
  );
}

function RawExecutionViewer({ detail }: { detail: BlackboxDetail }) {
  const { t } = useTranslation();
  const rawEvents = detail.events.map((event) => JSON.stringify(event)).join("\n");
  const rawTraceEvents = (detail.trace_events ?? []).map((event) => JSON.stringify(event)).join("\n");
  return (
    <div className="flex h-full min-h-0 flex-col bg-settings-surface">
      <div className="shrink-0 border-b border-settings-border px-6 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3 pr-12">
          <div>
            <div className="text-base font-semibold text-settings-foreground">
              {tx(t, "settings.enhancements.raw.title", "Complete raw record")}
            </div>
            <div className="mt-1 break-all font-mono text-xs text-settings-muted">{detail.turn_id}</div>
          </div>
          <div className="rounded-full bg-settings-hover px-3 py-1 text-xs text-settings-muted">
            {tx(t, "settings.enhancements.raw.badge", "No summary · No rewriting")}
          </div>
        </div>
        <p className="mt-3 max-w-4xl text-xs leading-5 text-settings-muted">
          {tx(t, "settings.enhancements.raw.description", "This is every JSON value saved for the turn: the turn envelope plus model and tool events in execution order. It may contain prompts, file paths, and other sensitive information.")}
        </p>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        <div className="mx-auto max-w-[1600px] space-y-5">
          <section>
            <div className="mb-2 flex items-center justify-between gap-2">
              <h4 className="text-sm font-semibold text-settings-foreground">
                {tx(t, "settings.enhancements.raw.turnRecord", "Turn envelope")}
              </h4>
              <span className="font-mono text-[11px] text-settings-muted">turns.jsonl · kind=turn</span>
            </div>
            <pre className="overflow-auto rounded-xl border border-settings-border bg-slate-950 p-4 font-mono text-[11px] leading-5 text-slate-100">
              {formatJson(detail.turn)}
            </pre>
          </section>

          {detail.trace_events && detail.trace_events.length > 0 ? (
            <section>
              <div className="mb-2 flex items-center justify-between gap-2">
                <h4 className="text-sm font-semibold text-settings-foreground">
                  {tx(t, "settings.enhancements.raw.traceEvents", "Structured execution timeline")}
                </h4>
                <span className="font-mono text-[11px] text-settings-muted">
                  {tx(t, "settings.enhancements.raw.traceEventCount", "events.jsonl · {{count}} events", { count: detail.trace_events.length })}
                </span>
              </div>
              <pre className="min-h-32 overflow-auto whitespace-pre-wrap break-all rounded-xl border border-settings-border bg-slate-950 p-4 font-mono text-[11px] leading-5 text-slate-100">
                {rawTraceEvents}
              </pre>
            </section>
          ) : null}

          <section>
            <div className="mb-2 flex items-center justify-between gap-2">
              <h4 className="text-sm font-semibold text-settings-foreground">
                {tx(t, "settings.enhancements.raw.events", "Raw execution events")}
              </h4>
              <span className="font-mono text-[11px] text-settings-muted">
                {tx(t, "settings.enhancements.raw.eventCount", "tools.jsonl · {{count}} events", { count: detail.events.length })}
              </span>
            </div>
            <pre className="min-h-32 overflow-auto whitespace-pre-wrap break-all rounded-xl border border-settings-border bg-slate-950 p-4 font-mono text-[11px] leading-5 text-slate-100">
              {rawEvents || tx(t, "settings.enhancements.raw.noEvents", "This turn has no separate model or tool events.")}
            </pre>
          </section>

          <div className="text-xs text-settings-muted">
            {tx(t, "settings.enhancements.raw.associatedFiles", "Files: turns.jsonl{{tools}}{{cassette}}{{events}}", {
              tools: detail.files.tools ? " · tools.jsonl" : "",
              cassette: detail.files.cassette ? ` · ${detail.files.cassette}` : "",
              events: detail.files.events ? " · events.jsonl" : "",
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

export function EnhancementsSettings() {
  const { t } = useTranslation();
  const { client } = useClient();

  const [status, setStatus] = useState<BlackboxStatus | null>(null);
  const [recordings, setRecordings] = useState<BlackboxRecording[]>([]);
  const [candidates, setCandidates] = useState<BlackboxCandidate[]>([]);
  const [evalCases, setEvalCases] = useState<TaskEvalCase[]>([]);
  const [evalReport, setEvalReport] = useState<TaskEvalReport | null>(null);
  const [tokens, setTokens] = useState<BlackboxTokens | null>(null);
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [traceFilter, setTraceFilter] = useState<TraceFilter>("all");
  const [selectedTrace, setSelectedTrace] = useState<TraceDetail | null>(null);
  const [traceLoading, setTraceLoading] = useState<string | null>(null);
  const [replay, setReplay] = useState<BlackboxReplayResult | null>(null);
  const [breakpoint, setBreakpoint] = useState<BlackboxBreakpoint | null>(null);
  const [breakAt, setBreakAt] = useState<string>("");
  const [expandedTurn, setExpandedTurn] = useState<string | null>(null);
  const [detailByTurn, setDetailByTurn] = useState<Record<string, BlackboxDetail>>({});
  const [detailLoading, setDetailLoading] = useState<string | null>(null);
  const [fullscreenDetail, setFullscreenDetail] = useState<BlackboxDetail | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [nextStatus, nextRecordings, nextCandidates, nextTokens] = await Promise.all([
        blackboxStatus(client),
        blackboxList(client),
        blackboxCandidates(client).catch(() => ({ candidates: [] })),
        blackboxTokens(client, null),
      ]);
      const nextTraces = await traceList(client, { filter: traceFilter }).catch(() => ({ traces: [], root: "" }));
      const nextEval = await taskEvalList(client).catch(() => ({ eval_set: "", version: 0, cases: [] }));
      setStatus(nextStatus);
      setRecordings(nextRecordings.recordings);
      setCandidates(nextCandidates.candidates);
      setTokens(nextTokens);
      setTraces(nextTraces.traces);
      setEvalCases(nextEval.cases);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [client, traceFilter]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    let refreshTimer: ReturnType<typeof setTimeout> | null = null;
    const subscribeTrace = client.onTrace;
    if (typeof subscribeTrace !== "function") return;
    const unsubscribe = subscribeTrace.call(client, (_chatId, trace) => {
      const turnId = typeof trace.turn_id === "string" ? trace.turn_id : null;
      if (turnId) {
        setSelectedTrace((current) => {
          if (current?.summary.turn_id !== turnId) return current;
          return { ...current, events: [...current.events, trace] };
        });
      }
      if (refreshTimer !== null) clearTimeout(refreshTimer);
      refreshTimer = setTimeout(() => void refresh(), 250);
    });
    return () => {
      unsubscribe();
      if (refreshTimer !== null) clearTimeout(refreshTimer);
    };
  }, [client, refresh]);

  async function start() {
    setBusy("start");
    setError(null);
    try {
      await blackboxStart(client, `sample-${Date.now()}`);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function stop() {
    setBusy("stop");
    setError(null);
    try {
      await blackboxStop(client);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function promoteCandidate(candidate: BlackboxCandidate) {
    setBusy(`promote:${candidate.candidate_id}`);
    setError(null);
    try {
      await blackboxPromoteCandidate(client, candidate.candidate_id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function addCandidateToEval(candidate: BlackboxCandidate) {
    setBusy(`eval-candidate:${candidate.candidate_id}`);
    setError(null);
    try {
      await blackboxAddCandidateToEval(client, candidate.candidate_id, candidate.candidate_id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function rejectCandidate(candidate: BlackboxCandidate) {
    setBusy(`reject:${candidate.candidate_id}`);
    setError(null);
    try {
      await blackboxRejectCandidate(client, candidate.candidate_id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function runTaskEval() {
    setBusy("eval");
    setError(null);
    try {
      setEvalReport(await taskEvalRun(client));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function replayDir(directory: string, withBreak?: number) {
    setBusy(`replay:${directory}`);
    setError(null);
    setReplay(null);
    setBreakpoint(null);
    setDetailByTurn({});
    setExpandedTurn(null);
    setFullscreenDetail(null);
    try {
      const result = await blackboxReplay(client, directory, withBreak);
      if ("breakpoint" in result) {
        setBreakpoint(result);
      } else {
        setReplay(result);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function toggleTurnDetail(turnId: string) {
    if (!replay) return;
    if (expandedTurn === turnId) {
      setExpandedTurn(null);
      return;
    }
    setExpandedTurn(turnId);
    if (detailByTurn[turnId]) return;
    setDetailLoading(turnId);
    setError(null);
    try {
      const detail = await blackboxDetail(client, replay.directory, turnId);
      setDetailByTurn((current) => ({ ...current, [turnId]: detail }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDetailLoading(null);
    }
  }

  async function deleteRecording(recording: BlackboxRecording) {
    const confirmed = window.confirm(
      tx(
        t,
        "settings.enhancements.recording.deleteConfirm",
        "Delete recording “{{name}}”? This removes its prompts, model responses, and tool results permanently.",
        { name: recordingDisplayName(recording, t) },
      ),
    );
    if (!confirmed) return;

    setBusy(`delete:${recording.directory}`);
    setError(null);
    try {
      await blackboxDelete(client, recording.directory);
      if (replay?.directory === recording.directory) setReplay(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function openTrace(trace: TraceSummary) {
    if (selectedTrace?.summary.id === trace.id) {
      setSelectedTrace(null);
      return;
    }
    setTraceLoading(trace.id);
    setError(null);
    try {
      setSelectedTrace(await traceDetail(client, trace.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setTraceLoading(null);
    }
  }

  const usagePct =
    tokens && tokens.context_window_tokens > 0 && tokens.usage_ratio != null
      ? Math.round(tokens.usage_ratio * 1000) / 10
      : null;
  const originalIssueTurns = replay?.original_issue_turns ?? 0;
  const originalTaskFailures = replay?.original_task_failures ?? 0;
  const replayTaskFailures = replay?.replay_task_failures ?? 0;

  return (
    <div className="flex flex-col gap-6 p-6">
      <section className="rounded-2xl border border-violet-200 bg-violet-50/70 p-5 dark:border-violet-900 dark:bg-violet-950/20">
        <div className="flex items-start gap-3">
          <Bug className="mt-0.5 h-5 w-5 shrink-0 text-violet-600" />
          <div className="min-w-0 flex-1">
            <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-violet-700 dark:text-violet-300">
              {tx(t, "settings.enhancements.kicker", "Agent execution workbench")}
            </div>
            <h2 className="mt-1 text-lg font-semibold text-settings-foreground">
              {tx(t, "settings.enhancements.title", "Agent execution & regression")}
            </h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-settings-foreground">
              {tx(t, "settings.enhancements.explainer", "See what each turn did, save a complete run when you need to reproduce a problem, then validate code changes offline.")}
            </p>
            <div className="mt-5 grid gap-3 text-xs text-settings-foreground lg:grid-cols-3">
              <div className="flex h-full flex-col rounded-xl border border-blue-200 border-l-4 bg-white/80 p-4 dark:border-blue-900 dark:bg-black/20">
                <div className="flex items-center justify-between gap-2">
                  <Gauge className="h-5 w-5 text-blue-600" />
                  <span className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-800 dark:border-blue-900 dark:bg-blue-950/30 dark:text-blue-300">
                    {tx(t, "settings.enhancements.modes.automatic", "Automatic")}
                  </span>
                </div>
                <div className="mt-3 font-semibold">{tx(t, "settings.enhancements.modes.liveTitle", "1. Live execution record")}</div>
                <div className="mt-1 flex-1 text-settings-muted">{tx(t, "settings.enhancements.modes.liveDetail", "Every turn gets a lightweight record of stages, timing, and failures. Full prompts and tool outputs are not copied here.")}</div>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="mt-3 w-fit px-0 text-xs"
                  onClick={() => scrollToSection("execution-traces")}
                >
                  {tx(t, "settings.enhancements.modes.liveAction", "View live records")}
                  <ChevronRight className="h-3.5 w-3.5" />
                </Button>
              </div>
              <div className="flex h-full flex-col rounded-xl border border-violet-200 border-l-4 bg-white/80 p-4 dark:border-violet-900 dark:bg-black/20">
                <div className="flex items-center justify-between gap-2">
                  <FileCheck2 className="h-5 w-5 text-violet-600" />
                  <span className="rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-[11px] font-medium text-violet-800 dark:border-violet-900 dark:bg-violet-950/30 dark:text-violet-300">
                    {tx(t, "settings.enhancements.modes.manual", "Manual")}
                  </span>
                </div>
                <div className="mt-3 font-semibold">{tx(t, "settings.enhancements.modes.sampleTitle", "2. Save a regression sample")}</div>
                <div className="mt-1 flex-1 text-settings-muted">{tx(t, "settings.enhancements.modes.sampleDetail", "Keep the full request, model responses, tool calls, and tool results from every session until you stop.")}</div>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="mt-3 w-fit px-0 text-xs"
                  onClick={() => scrollToSection("regression-samples")}
                >
                  {tx(t, "settings.enhancements.modes.sampleAction", "Save a sample")}
                  <ChevronRight className="h-3.5 w-3.5" />
                </Button>
              </div>
              <div className="flex h-full flex-col rounded-xl border border-emerald-200 border-l-4 bg-white/80 p-4 dark:border-emerald-900 dark:bg-black/20">
                <div className="flex items-center justify-between gap-2">
                  <Play className="h-5 w-5 text-emerald-600" />
                  <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-300">
                    {tx(t, "settings.enhancements.modes.offline", "Offline")}
                  </span>
                </div>
                <div className="mt-3 font-semibold">{tx(t, "settings.enhancements.modes.validationTitle", "3. Validate offline")}</div>
                <div className="mt-1 flex-1 text-settings-muted">{tx(t, "settings.enhancements.modes.validationDetail", "Use the saved responses and tool results to check the current orchestration without provider requests or real side effects.")}</div>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="mt-3 w-fit px-0 text-xs"
                  onClick={() => scrollToSection(replay ? "offline-validation" : "regression-samples")}
                >
                  {tx(t, "settings.enhancements.modes.validationAction", "Choose a sample")}
                  <ChevronRight className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          </div>
        </div>
      </section>

      {error ? (
        <div className="flex items-start gap-2 rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
          <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      <section id="regression-samples" className="scroll-mt-4 rounded-xl border border-settings-border bg-settings-surface p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Radio
              className={
                status?.recording
                  ? "h-5 w-5 text-emerald-500"
                  : "h-5 w-5 text-settings-muted"
              }
            />
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-base font-semibold text-settings-foreground">
                  {status?.recording
                    ? tx(t, "settings.enhancements.recording.activeTitle", "Saving a regression sample")
                    : tx(t, "settings.enhancements.recording.inactiveTitle", "Save a regression sample")}
                </h3>
                <span className="rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-[11px] font-medium text-violet-800 dark:border-violet-900 dark:bg-violet-950/30 dark:text-violet-300">
                  {tx(t, "settings.enhancements.modes.manual", "Manual")}
                </span>
              </div>
              <p className="text-xs text-settings-muted">
                {status?.recording
                  ? tx(t, "settings.enhancements.recording.activeHint", "Every turn from every session is added until you stop saving.")
                  : tx(t, "settings.enhancements.recording.inactiveHint", "Save the next execution you want to inspect, then stop when it is complete.")}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {status?.recording ? (
              <Button variant="secondary" size="sm" onClick={() => void stop()} disabled={busy !== null}>
                {busy === "stop" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Square className="h-4 w-4" />}
                {tx(t, "settings.enhancements.recording.stop", "Stop saving")}
              </Button>
            ) : (
              <Button variant="secondary" size="sm" onClick={() => void start()} disabled={busy !== null}>
                {busy === "start" ? <Loader2 className="h-4 w-4 animate-spin" /> : <CircleDashed className="h-4 w-4" />}
                {tx(t, "settings.enhancements.recording.start", "Save as regression sample")}
              </Button>
            )}
            <Button variant="ghost" size="sm" onClick={() => void refresh()} disabled={busy !== null}>
              <RefreshCw className="h-4 w-4" />
              {tx(t, "settings.enhancements.recording.refresh", "Refresh")}
            </Button>
          </div>
        </div>

        {status?.recording && status.directory ? (
          <div className="mb-4 rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:bg-emerald-950/20 dark:text-emerald-300">
            {tx(t, "settings.enhancements.recording.currentSample", "Current regression sample:")} {" "}
            <span className="font-medium">
              {generatedRecordingDisplayName(status.directory.split(/[\\/]/).pop() || "", t)}
            </span>
          </div>
        ) : null}

        {status?.recording ? (
          <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-300">
            {tx(t, "settings.enhancements.recording.allSessions", "Every session's turns are included from Save until Stop.")}
          </div>
        ) : null}

        {status?.rolling_enabled ? (
          <div className="mb-4 rounded-lg border border-blue-200 bg-blue-50 px-3 py-3 text-xs text-blue-900 dark:border-blue-900 dark:bg-blue-950/20 dark:text-blue-200">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-semibold">
                {tx(t, "settings.enhancements.recording.rollingTitle", "Automatic rolling replay buffer")}
              </span>
              <span>
                {tx(t, "settings.enhancements.recording.rollingLimit", "Keeps the latest {{count}} turns per conversation", {
                  count: status.rolling_max_turns_per_session ?? 20,
                })}
              </span>
            </div>
            <p className="mt-1 text-blue-800/80 dark:text-blue-200/80">
              {tx(t, "settings.enhancements.recording.rollingDescription", "Recent turns are kept locally so an unexpected failure can still be saved for replay. Normal turns are rotated out; flagged candidates stay until you decide.")}
            </p>
          </div>
        ) : null}

        {candidates.length > 0 ? (
          <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50/70 p-4 dark:border-amber-900 dark:bg-amber-950/15">
            <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
              <h4 className="text-sm font-semibold text-amber-950 dark:text-amber-100">
                {tx(t, "settings.enhancements.recording.candidatesTitle", "Candidate problem runs")}
              </h4>
              <span className="text-xs text-amber-900/70 dark:text-amber-200/70">
                {tx(t, "settings.enhancements.recording.candidatesDescription", "Failures kept automatically from the rolling buffer")}
              </span>
            </div>
            <div className="flex flex-col gap-2">
              {candidates.map((candidate) => (
                <div key={candidate.candidate_id} className="flex flex-col gap-2 rounded-lg border border-amber-200 bg-white/80 px-3 py-3 dark:border-amber-900 dark:bg-black/20 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-settings-foreground">{candidate.candidate_id}</div>
                    <div className="mt-1 text-xs text-settings-muted">
                      {(candidate.reasons ?? []).join(" · ") || tx(t, "settings.enhancements.recording.candidateUnknown", "Execution issue")}
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => void promoteCandidate(candidate)}
                      disabled={busy !== null}
                    >
                      {busy === `promote:${candidate.candidate_id}` ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileCheck2 className="h-4 w-4" />}
                      {tx(t, "settings.enhancements.recording.keepCandidate", "Keep for regression")}
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => void addCandidateToEval(candidate)}
                      disabled={busy !== null}
                    >
                      {busy === `eval-candidate:${candidate.candidate_id}` ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileCheck2 className="h-4 w-4" />}
                      {tx(t, "settings.enhancements.recording.addToEval", "Add to task eval")}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void rejectCandidate(candidate)}
                      disabled={busy !== null}
                    >
                      {busy === `reject:${candidate.candidate_id}` ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                      {tx(t, "settings.enhancements.recording.rejectCandidate", "Ignore")}
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {evalCases.length > 0 ? (
          <div className="mb-4 rounded-xl border border-violet-200 bg-violet-50/70 p-4 dark:border-violet-900 dark:bg-violet-950/15">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h4 className="text-sm font-semibold text-violet-950 dark:text-violet-100">
                  {tx(t, "settings.enhancements.recording.eval.title", "Agent task evaluation set")}
                </h4>
                <p className="mt-1 text-xs text-violet-900/70 dark:text-violet-200/70">
                  {tx(t, "settings.enhancements.recording.eval.description", "Run fixed, provider-free tasks to check task results and execution paths after a code change.")}
                </p>
              </div>
              <Button variant="outline" size="sm" onClick={() => void runTaskEval()} disabled={busy !== null}>
                {busy === "eval" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                {tx(t, "settings.enhancements.recording.eval.run", "Run evaluation")}
              </Button>
            </div>
            {evalReport ? (
              <div className="mt-3 rounded-lg border border-violet-200 bg-white/80 px-3 py-2 text-xs text-settings-foreground dark:border-violet-900 dark:bg-black/20">
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  <span>{tx(t, "settings.enhancements.recording.eval.summary", "Tasks: {{passed}}/{{total}} passed", { passed: evalReport.summary.task_passed, total: evalReport.summary.task_evaluable })}</span>
                  <span>{tx(t, "settings.enhancements.recording.eval.trajectory", "Trajectories: {{passed}}/{{total}} passed", { passed: evalReport.summary.trajectory_passed, total: evalReport.summary.total })}</span>
                  <span>{tx(t, "settings.enhancements.recording.eval.notEvaluable", "Not evaluable: {{count}}", { count: evalReport.summary.task_not_evaluable })}</span>
                </div>
                <div className="mt-2 flex flex-col gap-1">
                  {evalReport.cases.map((item) => (
                    <div key={item.id} className="flex flex-wrap items-center justify-between gap-2 rounded border border-settings-border px-2 py-1.5">
                      <span>{item.title}</span>
                      <span className="text-settings-muted">{item.task_status} · {item.trajectory_status} · {item.elapsed_ms}ms</span>
                    </div>
                  ))}
                  {(evalReport.custom_cases ?? []).map((item) => (
                    <div key={`custom-${item.id}`} className="flex flex-wrap items-center justify-between gap-2 rounded border border-violet-200 px-2 py-1.5 text-violet-900 dark:border-violet-900 dark:text-violet-200">
                      <span>{item.title || item.id || tx(t, "settings.enhancements.eval.customCase", "Custom recorded task")}</span>
                      <span className="text-settings-muted">{item.task_status ?? "not_evaluable"} · {item.trajectory_status ?? "unknown"}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="mb-4 flex items-center gap-2 rounded-lg border border-settings-border bg-settings-hover/40 px-3 py-2 text-xs text-settings-muted">
          <Info className="h-4 w-4 shrink-0" />
          {tx(t, "settings.enhancements.recording.offlineInfo", "The sample below is used for offline validation. It will not make a new model request or execute a real tool.")}
        </div>

        <div className="mb-3 flex flex-wrap items-center gap-2">
          <PauseCircle className="h-4 w-4 text-settings-muted" />
          <span className="text-xs text-settings-muted">
            {tx(t, "settings.enhancements.recording.breakpointPrefix", "Optional breakpoint: pause at model decision")}
          </span>
          <Input
            type="number"
            min={1}
            value={breakAt}
            onChange={(event) => setBreakAt(event.target.value)}
            placeholder="N"
            className="h-7 w-16"
          />
          <span className="text-xs text-settings-muted">
            {tx(t, "settings.enhancements.recording.breakpointSuffix", "")}
          </span>
        </div>

        {recordings.length > 0 ? (
          <div>
            <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
              <h4 className="text-sm font-semibold text-settings-foreground">
                {tx(t, "settings.enhancements.recording.savedTitle", "Saved regression samples")}
              </h4>
              <span className="text-xs text-settings-muted">
                {tx(t, "settings.enhancements.recording.savedDescription", "Choose a sample below to validate the current code offline.")}
              </span>
            </div>
            <div className="flex flex-col gap-2">
              {recordings.map((recording) => {
              const isReady = recording.status === "ready";
              const replayBusy = busy === `replay:${recording.directory}`;
              const deleteBusy = busy === `delete:${recording.directory}`;
              return (
                <div
                  key={recording.directory}
                  className="flex flex-col gap-3 rounded-xl border border-settings-border px-3 py-3 sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="flex min-w-0 items-start gap-3">
                    {isReady ? (
                      <FileCheck2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-500" />
                    ) : (
                      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />
                    )}
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm text-settings-foreground">
                          {recording.session_names?.length === 1
                            ? recording.session_names[0]
                            : recordingDisplayName(recording, t)}
                        </span>
                        <span className={`rounded-full border px-2 py-0.5 text-[11px] ${recordingStatusClass(recording)}`}>
                          {recordingStatusLabel(recording, t)}
                        </span>
                      </div>
                      <div className="mt-1 text-xs text-settings-muted">
                          {isReady
                            ? [
                              tx(t, "settings.enhancements.recording.savedTurns", "{{count}} turn(s) saved", { count: recording.turns }),
                              recordingSessionLabel(recording, t),
                            ].filter(Boolean).join(" · ")
                            : recordingStatusMessage(recording, t)}
                      </div>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => void replayDir(recording.directory, breakAt ? Number(breakAt) : undefined)}
                      disabled={busy !== null || !isReady}
                    >
                      {replayBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                      {breakAt
                        ? tx(t, "settings.enhancements.recording.breakpointReplay", "Validate to breakpoint")
                        : tx(t, "settings.enhancements.recording.replay", "Validate offline")}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void deleteRecording(recording)}
                      disabled={busy !== null}
                      title={tx(t, "settings.enhancements.recording.deleteConfirm", "Delete recording “{{name}}”?", { name: recordingDisplayName(recording, t) })}
                      aria-label={tx(t, "settings.enhancements.recording.deleteConfirm", "Delete recording “{{name}}”?", { name: recordingDisplayName(recording, t) })}
                    >
                      {deleteBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                    </Button>
                  </div>
                </div>
              );
              })}
            </div>
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-settings-border px-4 py-6 text-center text-sm text-settings-muted">
            {tx(t, "settings.enhancements.recording.empty", "No regression samples yet. Save a run, finish the task, then stop saving.")}
          </div>
        )}
      </section>

      <section id="execution-traces" className="scroll-mt-4 rounded-xl border border-settings-border bg-settings-surface p-5">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-start gap-2">
            <Gauge className="mt-0.5 h-5 w-5 text-settings-muted" />
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-base font-semibold text-settings-foreground">
                  {tx(t, "settings.enhancements.trace.recentTitle", "Live execution records")}
                </h3>
                <span className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-800 dark:border-blue-900 dark:bg-blue-950/30 dark:text-blue-300">
                  {tx(t, "settings.enhancements.modes.automatic", "Automatic")}
                </span>
              </div>
              <p className="mt-1 text-xs leading-5 text-settings-muted">
                {tx(t, "settings.enhancements.trace.recentDescription", "A lightweight record is created automatically for every turn. It shows stages, timing, and failures without copying the full prompt or tool output.")}
              </p>
            </div>
          </div>
          <Button variant="ghost" size="sm" onClick={() => void refresh()} disabled={busy !== null}>
            <RefreshCw className="h-4 w-4" />
            {tx(t, "settings.enhancements.recording.refresh", "Refresh")}
          </Button>
        </div>
        <div className="mb-3 flex flex-wrap gap-2" role="group" aria-label={tx(t, "settings.enhancements.trace.filterLabel", "Trace filter")}>
          {(["all", "issues", "slow"] as const).map((filter) => (
            <Button
              key={filter}
              type="button"
              variant={traceFilter === filter ? "secondary" : "ghost"}
              size="sm"
              onClick={() => setTraceFilter(filter)}
              disabled={busy !== null}
            >
              {filter === "all"
                ? tx(t, "settings.enhancements.trace.filterAll", "All")
                : filter === "issues"
                  ? tx(t, "settings.enhancements.trace.filterIssues", "Issues")
                  : tx(t, "settings.enhancements.trace.filterSlow", "Slow (≥2s)")}
            </Button>
          ))}
        </div>
        {traces.length > 0 ? (
          <div className="space-y-2">
            {traces.map((trace) => {
              const toolFailures = trace.tool_failure_count ?? 0;
              const providerErrors = trace.provider_error_count ?? 0;
              const uncertainSideEffects = trace.unknown_side_effect_count ?? 0;
              const hasFailure = trace.failure_count > 0 || trace.status === "error" || trace.status === "cancelled";
              const isActive = trace.status === "accepted" || trace.status === "running";
              const issueSummary = [
                toolFailures > 0
                  ? tx(t, "settings.enhancements.trace.toolFailures", "{{count}} tool failures", { count: toolFailures })
                  : "",
                providerErrors > 0
                  ? tx(t, "settings.enhancements.trace.providerErrors", "{{count}} model errors", { count: providerErrors })
                  : "",
                uncertainSideEffects > 0
                  ? tx(t, "settings.enhancements.trace.unknownSideEffects", "{{count}} uncertain side effects", { count: uncertainSideEffects })
                  : "",
              ].filter(Boolean).join(" · ");
              return (
                <div key={trace.id} className="rounded-lg border border-settings-border">
                  <button
                    type="button"
                    onClick={() => void openTrace(trace)}
                    className="flex w-full items-center gap-3 px-3 py-3 text-left hover:bg-settings-hover"
                  >
                    {hasFailure ? <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" /> : isActive ? <CircleDashed className="h-4 w-4 shrink-0 animate-spin text-blue-600" /> : <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />}
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-medium text-settings-foreground">
                        {trace.session_name || tx(t, "settings.enhancements.trace.unnamedSession", "Unnamed conversation")}
                      </span>
                      <span className="mt-1 block text-[11px] text-settings-muted">
                        {trace.event_count} {tx(t, "settings.enhancements.trace.events", "events")} · {trace.tool_count} {tx(t, "settings.enhancements.trace.tools", "tools")} {trace.duration_ms != null ? ` · ${formatDuration(trace.duration_ms)}` : ""}
                        {issueSummary ? ` · ${issueSummary}` : ""}
                      </span>
                    </span>
                    <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] ${hasFailure ? "border-amber-200 bg-amber-50 text-amber-800" : isActive ? "border-blue-200 bg-blue-50 text-blue-800" : "border-emerald-200 bg-emerald-50 text-emerald-800"}`}>
                      {hasFailure
                        ? tx(t, "settings.enhancements.trace.needsAttention", "Needs attention")
                        : isActive
                          ? tx(t, "settings.enhancements.trace.running", "Running")
                          : tx(t, "settings.enhancements.trace.completed", "Completed")}
                    </span>
                    {traceLoading === trace.id ? <Loader2 className="h-4 w-4 animate-spin text-settings-muted" /> : selectedTrace?.summary.id === trace.id ? <ChevronDown className="h-4 w-4 text-settings-muted" /> : <ChevronRight className="h-4 w-4 text-settings-muted" />}
                  </button>
                  {selectedTrace?.summary.id === trace.id ? (
                    <div className="border-t border-settings-border bg-settings-hover/25 p-3">
                      <ExecutionTraceTimeline events={selectedTrace.events} />
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-settings-border px-3 py-4 text-xs text-settings-muted">
            {tx(t, "settings.enhancements.trace.empty", "No execution traces are available yet.")}
          </div>
        )}
      </section>

      {replay ? (
        <section id="offline-validation" className="scroll-mt-4 rounded-xl border border-settings-border bg-settings-surface p-5">
          <div className="flex items-start gap-3">
            <Play className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" />
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-base font-semibold text-settings-foreground">
                  {tx(t, "settings.enhancements.result.title", "Offline validation")}
                </h3>
                <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-300">
                  {tx(t, "settings.enhancements.modes.offline", "Offline")}
                </span>
              </div>
              <p className="mt-1 text-xs text-settings-muted">
                {tx(t, "settings.enhancements.result.description", "The two questions are separate: did the current code reproduce the recorded path, and did the original run contain an error?")}
              </p>
            </div>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-3">
            <div className={`rounded-xl border p-4 ${replay.all_deterministic
              ? "border-emerald-200 bg-emerald-50/70 dark:border-emerald-900 dark:bg-emerald-950/20"
              : "border-amber-200 bg-amber-50/70 dark:border-amber-900 dark:bg-amber-950/20"}`}>
              <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-settings-muted">
                {replay.all_deterministic ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <AlertTriangle className="h-4 w-4 text-amber-600" />}
                {tx(t, "settings.enhancements.result.replayLabel", "Replay result")}
              </div>
              <div className="mt-2 text-lg font-semibold text-settings-foreground">
                {tx(t, "settings.enhancements.result.replaySummary", "{{matched}}/{{total}} turns replayed consistently", {
                  matched: replay.deterministic_turns,
                  total: replay.total_turns,
                })}
              </div>
              <div className="mt-1 text-xs text-settings-muted">
                {replay.all_deterministic
                  ? tx(t, "settings.enhancements.result.replayConsistentHint", "The current orchestration produced the same observable messages and tool flow.")
                  : tx(t, "settings.enhancements.result.replayDifferenceHint", "Open a turn to inspect the recorded and replayed message differences.")}
              </div>
            </div>
            <div className={`rounded-xl border p-4 ${originalIssueTurns === 0
              ? "border-emerald-200 bg-emerald-50/70 dark:border-emerald-900 dark:bg-emerald-950/20"
              : "border-amber-200 bg-amber-50/70 dark:border-amber-900 dark:bg-amber-950/20"}`}>
              <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-settings-muted">
                {originalIssueTurns === 0 ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <AlertTriangle className="h-4 w-4 text-amber-600" />}
                {tx(t, "settings.enhancements.result.originalLabel", "Original execution")}
              </div>
              <div className="mt-2 text-lg font-semibold text-settings-foreground">
                {originalIssueTurns === 0
                  ? tx(t, "settings.enhancements.result.originalHealthy", "No recorded errors")
                  : tx(t, "settings.enhancements.result.originalIssues", "{{count}} turn(s) originally contained errors", { count: originalIssueTurns })}
              </div>
              <div className="mt-1 text-xs text-settings-muted">
                {tx(t, "settings.enhancements.result.originalBreakdown", "{{tools}} failed tool call(s) · {{models}} failed model request(s) · {{unknown}} uncertain side effect(s)", {
                  tools: replay.original_failed_tool_calls,
                  models: replay.original_provider_errors,
                  unknown: replay.original_unknown_side_effects,
                })}
              </div>
            </div>
            <div className={"rounded-xl border p-4 " + (originalTaskFailures === 0 && replayTaskFailures === 0
              ? "border-emerald-200 bg-emerald-50/70 dark:border-emerald-900 dark:bg-emerald-950/20"
              : "border-amber-200 bg-amber-50/70 dark:border-amber-900 dark:bg-amber-950/20")}>
              <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-settings-muted">
                {originalTaskFailures === 0 && replayTaskFailures === 0
                  ? <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                  : <AlertTriangle className="h-4 w-4 text-amber-600" />}
                {tx(t, "settings.enhancements.result.taskLabel", "Task verification")}
              </div>
              <div className="mt-2 text-lg font-semibold text-settings-foreground">
                {originalTaskFailures === 0
                  ? tx(t, "settings.enhancements.result.taskHealthy", "Original tasks passed")
                  : tx(t, "settings.enhancements.result.taskIssues", "{{count}} original task(s) did not pass", { count: originalTaskFailures })}
              </div>
              <div className="mt-1 text-xs text-settings-muted">
                {tx(t, "settings.enhancements.result.taskReplayBreakdown", "{{count}} replay task check(s) did not pass", { count: replayTaskFailures })}
              </div>
            </div>
          </div>
          {replay.benchmark ? (
            <div className="mt-3 rounded-xl border border-settings-border bg-background/60 p-3">
              <div className="flex flex-wrap items-center gap-2 text-xs font-semibold text-settings-foreground">
                <Gauge className="h-4 w-4 text-settings-muted" />
                {tx(t, "settings.enhancements.result.benchmarkTitle", "Replay benchmark")}
              </div>
              <div className="mt-1 text-xs text-settings-muted">
                {tx(t, "settings.enhancements.result.benchmarkSummary", "{{turns}} turn(s) · {{total}} total · {{average}} average · {{slowest}} slowest", {
                  turns: replay.benchmark.turns,
                  total: formatDuration(replay.benchmark.total_elapsed_ms),
                  average: formatDuration(replay.benchmark.average_elapsed_ms),
                  slowest: formatDuration(replay.benchmark.slowest_elapsed_ms),
                })}
              </div>
            </div>
          ) : null}
          <p className="mt-3 text-xs text-settings-muted">
            {tx(t, "settings.enhancements.result.detailHint", "Open a turn for the readable trace. Use View raw record for the complete JSON.")}
          </p>
          <div className="mt-4 flex flex-col gap-1">
            {replay.results.map((row, index) => {
              const messageDiffs = Array.isArray(row.message_diffs) ? row.message_diffs : row.diffs;
              const traceDiffs = Array.isArray(row.trace_diffs) ? row.trace_diffs : [];
              const traceChecked = row.trace_comparable === true;
              return (
              <div key={row.turn_id} className="rounded-lg border border-settings-border">
                <button
                  type="button"
                  onClick={() => void toggleTurnDetail(row.turn_id)}
                  className="flex w-full items-start gap-3 px-3 py-3 text-left hover:bg-settings-hover"
                >
                  <div className="flex shrink-0 flex-col items-center gap-1 pt-0.5" aria-hidden="true">
                    {row.ok ? (
                      <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                    ) : (
                      <XCircle className="h-4 w-4 text-amber-500" />
                    )}
                    {originalExecutionHasIssue(row.original_execution) ? (
                      <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
                    ) : (
                      <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="whitespace-nowrap text-sm font-medium text-settings-foreground">
                        {tx(t, "settings.enhancements.result.turn", "Turn {{number}}", { number: index + 1 })}
                      </span>
                      <span className="min-w-0 truncate text-xs text-settings-muted">
                        {row.session_name || tx(t, "settings.enhancements.trace.unnamedSession", "Unnamed conversation")}
                      </span>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-2">
                      <span className={`rounded-full border px-2 py-0.5 text-[11px] ${row.ok
                        ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                        : "border-amber-200 bg-amber-50 text-amber-800"}`}>
                        {tx(t, "settings.enhancements.result.replayLabel", "Replay result")} · {row.ok
                          ? tx(t, "settings.enhancements.result.replayConsistent", "Consistent")
                          : tx(t, "settings.enhancements.result.replayDifferencesShort", "{{count}} difference(s)", { count: row.diffs.length })}
                      </span>
                      <span className={`rounded-full border px-2 py-0.5 text-[11px] ${originalExecutionTone(row.original_execution)}`}>
                        {tx(t, "settings.enhancements.result.originalLabel", "Original execution")} · {originalExecutionLabel(row.original_execution, t)}
                      </span>
                      <span className={`rounded-full border px-2 py-0.5 text-[11px] ${outcomeTaskTone(row.original_outcome)}`}>
                        {tx(t, "settings.enhancements.result.taskLabel", "Task verification")} · {outcomeTaskLabel(row.original_outcome, t)}
                      </span>
                      <span className={`rounded-full border px-2 py-0.5 text-[11px] ${outcomeSideEffectTone(row.original_outcome)}`}>
                        {tx(t, "settings.enhancements.result.sideEffectLabel", "External effect")} · {outcomeSideEffectLabel(row.original_outcome, t)}
                      </span>
                      <span className={`rounded-full border px-2 py-0.5 text-[11px] ${traceChecked
                        ? traceDiffs.length > 0
                          ? "border-amber-200 bg-amber-50 text-amber-800"
                          : "border-blue-200 bg-blue-50 text-blue-800"
                        : "border-settings-border bg-background/70 text-settings-muted"}`}>
                        {tx(t, "settings.enhancements.result.traceComparison", "Trace comparison")} · {traceChecked
                          ? traceDiffs.length > 0
                            ? tx(t, "settings.enhancements.result.traceDifferences", "{{count}} trace difference(s)", { count: traceDiffs.length })
                            : tx(t, "settings.enhancements.result.traceConsistent", "Consistent")
                          : tx(t, "settings.enhancements.result.traceUnavailable", "Not in sample")}
                      </span>
                    </div>
                  </div>
                  {detailLoading === row.turn_id ? (
                    <Loader2 className="h-4 w-4 animate-spin text-settings-muted" />
                  ) : expandedTurn === row.turn_id ? (
                    <ChevronDown className="h-4 w-4 text-settings-muted" />
                  ) : (
                    <ChevronRight className="h-4 w-4 text-settings-muted" />
                  )}
                </button>
                {expandedTurn === row.turn_id ? (
                  <div>
                    {row.diffs.length > 0 ? (
                      <div className="border-t border-settings-border bg-settings-hover/50 p-3">
                        <div className="space-y-3">
                          {messageDiffs.length > 0 ? (
                            <div>
                              <div className="mb-2 text-xs font-medium text-settings-muted">
                                {tx(t, "settings.enhancements.result.messageDifferenceTitle", "Message differences")}
                              </div>
                              <div className="flex flex-col gap-2">
                                {messageDiffs.map((diff, diffIndex) => (
                                  <pre key={diffIndex} className="whitespace-pre-wrap break-all font-mono text-xs text-settings-foreground">
                                    {typeof diff === "string" ? diff : JSON.stringify(diff, null, 2)}
                                  </pre>
                                ))}
                              </div>
                            </div>
                          ) : null}
                          {traceDiffs.length > 0 ? (
                            <div>
                              <div className="mb-2 text-xs font-medium text-settings-muted">
                                {tx(t, "settings.enhancements.result.traceDifferenceTitle", "Execution trace differences")}
                              </div>
                              <div className="flex flex-col gap-2">
                                {traceDiffs.map((diff, diffIndex) => (
                                  <pre key={diffIndex} className="whitespace-pre-wrap break-all font-mono text-xs text-settings-foreground">
                                    {typeof diff === "string" ? diff : JSON.stringify(diff, null, 2)}
                                  </pre>
                                ))}
                              </div>
                            </div>
                          ) : null}
                        </div>
                      </div>
                    ) : null}
                    {detailByTurn[row.turn_id] ? (
                      <ReplayTurnSummary
                        detail={detailByTurn[row.turn_id]}
                        replayOk={row.ok}
                        replayDiffCount={row.diffs.length}
                        onFullscreen={() => setFullscreenDetail(detailByTurn[row.turn_id])}
                      />
                    ) : detailLoading === row.turn_id ? (
                      <div className="border-t border-settings-border px-3 py-5 text-center text-xs text-settings-muted">
                        {tx(t, "settings.enhancements.result.loading", "Loading this turn's execution trace…")}
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
              );
            })}
          </div>
        </section>
      ) : null}

      {breakpoint ? (
        <section className="rounded-xl border border-amber-300 bg-amber-50 p-4">
          <div className="flex items-center gap-2 text-sm font-medium text-amber-800">
            <PauseCircle className="h-4 w-4" />
            {tx(t, "settings.enhancements.breakpoint.paused", "Paused at model decision {{number}}", { number: breakpoint.iteration + 1 })}
            {breakpoint.turn_id ? ` · ${breakpoint.turn_id}` : ""}
          </div>
          <div className="mt-2 max-h-72 overflow-auto rounded bg-white/60 p-2">
            {breakpoint.messages.map((message, index) => (
              <pre key={index} className="whitespace-pre-wrap break-all font-mono text-xs text-amber-900">
                {typeof message === "string" ? message : JSON.stringify(message, null, 2)}
              </pre>
            ))}
          </div>
        </section>
      ) : null}

      <Dialog
        open={fullscreenDetail !== null}
        onOpenChange={(open) => {
          if (!open) setFullscreenDetail(null);
        }}
      >
        <DialogContent className="flex h-[calc(100vh-2rem)] max-w-[calc(100vw-2rem)] flex-col gap-0 overflow-hidden p-0">
          <DialogHeader className="shrink-0 border-b border-settings-border px-6 py-4 pr-14 text-left">
              <DialogTitle>{tx(t, "settings.enhancements.raw.dialogTitle", "Turn raw record")}</DialogTitle>
              <DialogDescription>
                {tx(t, "settings.enhancements.raw.dialogDescription", "This is the saved data without rewriting. Close the window to return to the readable execution view.")}
            </DialogDescription>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-hidden">
            {fullscreenDetail ? <RawExecutionViewer detail={fullscreenDetail} /> : null}
          </div>
        </DialogContent>
      </Dialog>

      <section className="rounded-xl border border-settings-border bg-settings-surface p-5">
        <div className="mb-4 flex items-start gap-2">
          <Gauge className="mt-0.5 h-5 w-5 text-settings-foreground" />
          <div>
            <h3 className="text-base font-semibold text-settings-foreground">
              {tx(t, "settings.enhancements.budget.title", "Context budget")}
            </h3>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatCard label={tx(t, "settings.enhancements.budget.estimatedTokens", "Estimated tokens")} value={tokens ? tokens.estimated_tokens.toLocaleString() : "—"} />
          <StatCard label={tx(t, "settings.enhancements.budget.contextWindow", "Context window")} value={tokens ? tokens.context_window_tokens.toLocaleString() : "—"} />
          <StatCard label={tx(t, "settings.enhancements.budget.messages", "Messages")} value={tokens ? String(tokens.message_count) : "—"} />
          <StatCard
            label={tx(t, "settings.enhancements.budget.usage", "Window usage")}
            value={usagePct != null ? `${usagePct}%` : "—"}
            hint={tokens ? tx(t, "settings.enhancements.budget.hint", "{{tools}} tools · {{model}}", {
              tools: tokens.tool_count,
              model: tokens.model ?? "",
            }) : undefined}
          />
        </div>
      </section>
    </div>
  );
}
