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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  blackboxDelete,
  blackboxDetail,
  blackboxList,
  blackboxReplay,
  blackboxStart,
  blackboxStatus,
  blackboxStop,
  blackboxTokens,
  type BlackboxBreakpoint,
  type BlackboxDetail,
  type BlackboxRecording,
  type BlackboxReplayResult,
  type BlackboxStatus,
  type BlackboxTokens,
} from "@/lib/api";
import { useClient } from "@/providers/ClientProvider";

type Translate = TFunction;

function tx(
  t: Translate,
  key: string,
  fallback: string,
  values?: Record<string, unknown>,
): string {
  return t(key, { defaultValue: fallback, ...(values ?? {}) });
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
    ? tx(t, "settings.enhancements.status.ready", "Ready to replay")
    : tx(t, "settings.enhancements.status.incomplete", "Incomplete recording");
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
  const userRequest = lastMessageText(turn.initial_messages, "user");
  const finalAnswer = typeof turn.final_content === "string" && turn.final_content.trim()
    ? turn.final_content
    : lastMessageText(turn.final_messages, "assistant");
  const originalCounts = originalExecutionCounts(originalExecution);
  const hasProblems = originalExecutionHasIssue(originalExecution)
    || failedTools.length > 0
    || providerErrors.length > 0
    || unknownSideEffects.length > 0;
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
      </div>

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
          {detail.events.length > 0 ? detail.events.map((event, index) => (
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
            {tx(t, "settings.enhancements.raw.associatedFiles", "Files: turns.jsonl{{tools}}{{cassette}}", {
              tools: detail.files.tools ? " · tools.jsonl" : "",
              cassette: detail.files.cassette ? ` · ${detail.files.cassette}` : "",
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
  const [tokens, setTokens] = useState<BlackboxTokens | null>(null);
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
      const [nextStatus, nextRecordings, nextTokens] = await Promise.all([
        blackboxStatus(client),
        blackboxList(client),
        blackboxTokens(client, null),
      ]);
      setStatus(nextStatus);
      setRecordings(nextRecordings.recordings);
      setTokens(nextTokens);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [client]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function start() {
    setBusy("start");
    setError(null);
    try {
      await blackboxStart(client, `session-${Date.now()}`);
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
        { name: recording.name },
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

  const usagePct =
    tokens && tokens.context_window_tokens > 0 && tokens.usage_ratio != null
      ? Math.round(tokens.usage_ratio * 1000) / 10
      : null;
  const originalIssueTurns = replay?.original_issue_turns ?? 0;

  return (
    <div className="flex flex-col gap-6 p-6">
      <section className="rounded-2xl border border-violet-200 bg-violet-50/70 p-5 dark:border-violet-900 dark:bg-violet-950/20">
        <div className="flex items-start gap-3">
          <Bug className="mt-0.5 h-5 w-5 shrink-0 text-violet-600" />
          <div>
            <h2 className="text-lg font-semibold text-settings-foreground">
              {tx(t, "settings.enhancements.title", "Record & Replay")}
            </h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-settings-foreground">
              {tx(t, "settings.enhancements.explainer", "Save a real agent run as a test sample. Replay it offline with the recorded model responses and tool results to see whether the current orchestration still follows the same path.")}
            </p>
            <div className="mt-4 grid gap-3 text-xs text-settings-foreground sm:grid-cols-3">
              <div className="rounded-xl bg-white/70 p-3 dark:bg-black/20">
                <div className="font-semibold">{tx(t, "settings.enhancements.steps.recordTitle", "1. Record a real task")}</div>
                <div className="mt-1 text-settings-muted">{tx(t, "settings.enhancements.steps.recordDetail", "The request, model decisions, tool calls, and observations are saved.")}</div>
              </div>
              <div className="rounded-xl bg-white/70 p-3 dark:bg-black/20">
                <div className="font-semibold">{tx(t, "settings.enhancements.steps.changeTitle", "2. Change code or investigate")}</div>
                <div className="mt-1 text-settings-muted">{tx(t, "settings.enhancements.steps.changeDetail", "Useful when checking the agent loop, context handling, or tool flow.")}</div>
              </div>
              <div className="rounded-xl bg-white/70 p-3 dark:bg-black/20">
                <div className="font-semibold">{tx(t, "settings.enhancements.steps.replayTitle", "3. Replay and compare")}</div>
                <div className="mt-1 text-settings-muted">{tx(t, "settings.enhancements.steps.replayDetail", "No token cost, no network calls, and no real tool side effects.")}</div>
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

      <section className="rounded-xl border border-settings-border bg-settings-surface p-5">
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
              <h3 className="text-base font-semibold text-settings-foreground">
                {status?.recording
                  ? tx(t, "settings.enhancements.recording.activeTitle", "Collecting an execution sample")
                  : tx(t, "settings.enhancements.recording.inactiveTitle", "Record a task")}
              </h3>
              <p className="text-xs text-settings-muted">
                {status?.recording
                  ? tx(t, "settings.enhancements.recording.activeHint", "Every task from every session will be added until you stop recording.")
                  : tx(t, "settings.enhancements.recording.inactiveHint", "Start recording, run the task you want to keep, then stop when it is complete.")}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {status?.recording ? (
              <Button variant="secondary" size="sm" onClick={() => void stop()} disabled={busy !== null}>
                {busy === "stop" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Square className="h-4 w-4" />}
                {tx(t, "settings.enhancements.recording.stop", "Stop recording")}
              </Button>
            ) : (
              <Button variant="secondary" size="sm" onClick={() => void start()} disabled={busy !== null}>
                {busy === "start" ? <Loader2 className="h-4 w-4 animate-spin" /> : <CircleDashed className="h-4 w-4" />}
                {tx(t, "settings.enhancements.recording.start", "Start recording")}
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
            {tx(t, "settings.enhancements.recording.currentSample", "Current sample:")} {" "}
            <span className="font-mono">{status.directory.split(/[\\/]/).pop()}</span>
          </div>
        ) : null}

        {status?.recording ? (
          <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-300">
            {tx(t, "settings.enhancements.recording.allSessions", "Every session is included between Start and Stop.")}
          </div>
        ) : null}

        <div className="mb-4 flex items-center gap-2 rounded-lg border border-settings-border bg-settings-hover/40 px-3 py-2 text-xs text-settings-muted">
          <Info className="h-4 w-4 shrink-0" />
          {tx(t, "settings.enhancements.recording.offlineInfo", "Offline replay uses the sample's model responses and tool results. It will not make a new model request or execute a real tool.")}
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
                        <span className="font-mono text-sm text-settings-foreground">{recording.name}</span>
                        <span className={`rounded-full border px-2 py-0.5 text-[11px] ${recordingStatusClass(recording)}`}>
                          {recordingStatusLabel(recording, t)}
                        </span>
                      </div>
                      <div className="mt-1 text-xs text-settings-muted">
                          {isReady
                            ? tx(t, "settings.enhancements.recording.savedTurns", "{{count}} task(s) saved", { count: recording.turns })
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
                        ? tx(t, "settings.enhancements.recording.breakpointReplay", "Replay to breakpoint")
                        : tx(t, "settings.enhancements.recording.replay", "Replay offline")}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void deleteRecording(recording)}
                      disabled={busy !== null}
                      title={tx(t, "settings.enhancements.recording.deleteConfirm", "Delete recording “{{name}}”?", { name: recording.name })}
                      aria-label={tx(t, "settings.enhancements.recording.deleteConfirm", "Delete recording “{{name}}”?", { name: recording.name })}
                    >
                      {deleteBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-settings-border px-4 py-6 text-center text-sm text-settings-muted">
            {tx(t, "settings.enhancements.recording.empty", "No execution samples yet. Start recording, run a task, then come back and stop recording.")}
          </div>
        )}
      </section>

      {replay ? (
        <section className="rounded-xl border border-settings-border bg-settings-surface p-5">
          <div className="flex items-start gap-3">
            <Bug className="mt-0.5 h-5 w-5 shrink-0 text-violet-600" />
            <div className="min-w-0">
              <h3 className="text-base font-semibold text-settings-foreground">
                {tx(t, "settings.enhancements.result.title", "Offline replay result")}
              </h3>
              <p className="mt-1 text-xs text-settings-muted">
                {tx(t, "settings.enhancements.result.description", "Replay consistency and the original run's health are shown separately. Replay does not grade the model's answer quality.")}
              </p>
            </div>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
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
          </div>
          <p className="mt-3 text-xs text-settings-muted">
            {tx(t, "settings.enhancements.result.detailHint", "Open a turn for the readable trace. Use View raw record for the complete JSON.")}
          </p>
          <div className="mt-4 flex flex-col gap-1">
            {replay.results.map((row, index) => (
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
                      <span className="min-w-0 truncate font-mono text-xs text-settings-muted">{row.turn_id}</span>
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
                        <div className="mb-2 text-xs font-medium text-settings-muted">
                          {tx(t, "settings.enhancements.result.differenceTitle", "Differences from the recording")}
                        </div>
                        <div className="flex flex-col gap-2">
                          {row.diffs.map((diff, diffIndex) => (
                            <pre key={diffIndex} className="whitespace-pre-wrap break-all font-mono text-xs text-settings-foreground">
                              {typeof diff === "string" ? diff : JSON.stringify(diff, null, 2)}
                            </pre>
                          ))}
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
            ))}
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
