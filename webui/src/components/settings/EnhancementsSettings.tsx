import { useCallback, useEffect, useState, type ReactNode } from "react";
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

function recordingStatusLabel(recording: BlackboxRecording): string {
  return recording.status === "ready" ? "记录完整" : "记录不完整";
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

function compactText(value: string, maxLength = 320): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (!normalized) return "未记录";
  return normalized.length > maxLength
    ? `${normalized.slice(0, maxLength).trimEnd()}…`
    : normalized;
}

function humanizeStopReason(value: unknown): string {
  const reason = String(value ?? "").toLowerCase();
  switch (reason) {
    case "completed":
    case "stop":
      return "正常完成";
    case "tool_calls":
      return "等待工具结果";
    case "max_iterations":
      return "达到执行上限";
    case "cancelled":
    case "canceled":
      return "已取消";
    case "error":
      return "执行出错";
    default:
      return reason && reason !== "unknown" ? reason : "未说明";
  }
}

function summarizeTools(events: Array<Record<string, unknown>>): string {
  const counts = new Map<string, number>();
  for (const event of events) {
    if (event.kind !== "tool") continue;
    const name = String(event.name ?? "未命名工具");
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }
  if (counts.size === 0) return "未调用工具";
  const entries = [...counts.entries()];
  const visible = entries.slice(0, 4).map(([name, count]) => `${name} × ${count}`);
  if (entries.length > visible.length) visible.push(`另有 ${entries.length - visible.length} 种`);
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

function humanizeToolStatus(value: unknown): string {
  switch (String(value ?? "").toLowerCase()) {
    case "ok":
      return "成功";
    case "error":
      return "失败";
    case "blocked":
      return "已拦截";
    case "cancelled":
    case "canceled":
      return "已取消";
    case "unknown":
      return "状态不确定";
    default:
      return String(value ?? "未说明");
  }
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
  const fullText = displayValue(value);
  const preview = compactText(fullText, maxLength);
  const canExpand = fullText.trim().length > maxLength;
  return (
    <div className="min-w-0 rounded-lg border border-settings-border bg-background/70 px-3 py-2">
      <div className="text-[11px] font-medium text-settings-muted">{label}</div>
      <div className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-5 text-settings-foreground">
        {preview}
      </div>
      {canExpand ? (
        <details className="mt-2">
          <summary className="cursor-pointer text-[11px] text-settings-muted">展开完整内容</summary>
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
  const isTool = event.kind === "tool";
  const response = isRecord(event.response) ? event.response : null;
  const iterationNumber = Number(event.iteration);
  const iteration = Number.isFinite(iterationNumber) ? iterationNumber + 1 : null;
  const thinking = response ? responseThinking(response) : "";
  const toolNames = response ? responseToolNames(response) : [];
  const content = response && typeof response.content === "string" ? response.content : "";
  const toolStatus = humanizeToolStatus(event.status);
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
          {isTool ? "工具执行" : "模型决策"}
        </span>
        <span className="text-settings-muted">步骤 {index + 1}</span>
        {isTool ? (
          <span className="font-mono text-settings-foreground">{String(event.name ?? "未命名工具")}</span>
        ) : (
          <span className="text-settings-muted">
            {iteration == null ? "未记录决策轮次" : `第 ${iteration} 次决策`}
          </span>
        )}
        <span className={`ml-auto rounded-full px-2 py-0.5 text-[11px] ${isTool
          ? event.status === "ok"
            ? "bg-emerald-100 text-emerald-800"
            : "bg-orange-100 text-orange-800"
          : "bg-blue-100 text-blue-800"}`}>
          {isTool ? toolStatus : humanizeStopReason(response?.finish_reason)}
        </span>
      </div>

      {isTool ? (
        <div className="mt-3 grid gap-2 lg:grid-cols-2">
          <InlineRecordPreview label="传入参数" value={event.args} />
          <InlineRecordPreview label="工具返回" value={event.result} maxLength={480} />
          {event.detail ? (
            <div className="lg:col-span-2 text-xs leading-5 text-settings-muted">{String(event.detail)}</div>
          ) : null}
        </div>
      ) : (
        <div className="mt-3 space-y-2">
          {thinking ? (
            <div className="rounded-lg border border-violet-200 bg-violet-50/80 px-3 py-2 dark:border-violet-900 dark:bg-violet-950/20">
              <div className="flex items-center gap-1.5 text-[11px] font-medium text-violet-800 dark:text-violet-200">
                <MessageSquare className="h-3.5 w-3.5" />
                模型思考记录
              </div>
              <div className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-words text-xs leading-5 text-settings-foreground">
                {compactText(thinking, 720)}
              </div>
              {thinking.trim().length > 720 ? (
                <details className="mt-2">
                  <summary className="cursor-pointer text-[11px] text-violet-800 dark:text-violet-200">展开完整思考记录</summary>
                  <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded bg-white/70 p-2 font-mono text-[11px] leading-5 text-settings-foreground dark:bg-black/20">
                    {thinking}
                  </pre>
                </details>
              ) : null}
            </div>
          ) : null}
          {toolNames.length > 0 ? (
            <div className="rounded-lg border border-settings-border bg-background/70 px-3 py-2 text-xs text-settings-foreground">
              <span className="font-medium">模型决定调用：</span>
              <span className="ml-1 font-mono">{toolNames.join(" · ")}</span>
            </div>
          ) : null}
          {content ? (
            <div className="rounded-lg border border-settings-border bg-background/70 px-3 py-2">
              <div className="text-[11px] font-medium text-settings-muted">模型输出</div>
              <div className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words text-xs leading-5 text-settings-foreground">
                {compactText(content, 720)}
              </div>
              {content.trim().length > 720 ? (
                <details className="mt-2">
                  <summary className="cursor-pointer text-[11px] text-settings-muted">展开完整模型输出</summary>
                  <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded bg-settings-hover/50 p-2 text-xs leading-5 text-settings-foreground">
                    {content}
                  </pre>
                </details>
              ) : null}
            </div>
          ) : null}
          {!thinking && toolNames.length === 0 && !content ? (
            <div className="text-xs text-settings-muted">模型没有返回可展示文本，执行器继续处理下一步。</div>
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
  onFullscreen,
}: {
  detail: BlackboxDetail;
  onFullscreen?: () => void;
}) {
  const turn = detail.turn;
  const diagnostics = isRecord(detail.diagnostics) ? detail.diagnostics : {};
  const failedTools = Array.isArray(diagnostics.failed_tools) ? diagnostics.failed_tools : [];
  const unknownSideEffects = Array.isArray(diagnostics.unknown_side_effects)
    ? diagnostics.unknown_side_effects
    : [];
  const userRequest = lastMessageText(turn.initial_messages, "user");
  const finalAnswer = typeof turn.final_content === "string" && turn.final_content.trim()
    ? turn.final_content
    : lastMessageText(turn.final_messages, "assistant");
  const hasProblems = failedTools.length > 0 || unknownSideEffects.length > 0;
  const statusLabel = hasProblems
    ? "需要关注"
    : humanizeStopReason(diagnostics.stop_reason ?? turn.stop_reason);
  const statusDescription = hasProblems
    ? `${failedTools.length ? `${failedTools.length} 个工具调用失败` : ""}${failedTools.length && unknownSideEffects.length ? "；" : ""}${unknownSideEffects.length ? `${unknownSideEffects.length} 个工具的执行结果不确定` : ""}。请查看原始记录。`
    : detail.counts.tool_calls > 0
      ? `这次执行包含 ${summarizeTools(detail.events)}。`
      : "这次任务没有调用工具，模型直接生成了最终回复。";
  return (
    <div className="space-y-4 border-t border-settings-border bg-settings-hover/25 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-semibold text-settings-foreground">本回合执行过程</div>
          <div className="mt-0.5 font-mono text-[11px] text-settings-muted">{detail.turn_id}</div>
        </div>
        {onFullscreen ? (
          <Button type="button" variant="outline" size="sm" onClick={onFullscreen}>
            <Maximize2 className="mr-1.5 h-3.5 w-3.5" />
            查看原始记录
          </Button>
        ) : null}
      </div>
      <div className="grid items-start gap-3 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
        <ConversationCard
          icon="user"
          label="用户消息"
          value={compactText(userRequest, 360)}
          detail="这次任务开始时发送给 Agent 的内容"
        />
        <ConversationCard
          icon="assistant"
          label="最终回答"
          value={compactText(finalAnswer, 720)}
          detail="Agent 最后展示给用户的内容"
        >
          {finalAnswer.trim().length > 720 ? (
            <details className="mt-2">
              <summary className="cursor-pointer text-[11px] text-settings-muted">展开完整回答</summary>
              <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-words rounded bg-settings-hover/50 p-2 text-xs leading-5 text-settings-foreground">
                {finalAnswer}
              </pre>
            </details>
          ) : null}
        </ConversationCard>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded-full border border-settings-border bg-background/70 px-3 py-1 font-mono text-settings-foreground">
          模型 · {String(turn.model ?? "未记录")}
        </span>
        <span className="rounded-full border border-settings-border bg-background/70 px-3 py-1 text-settings-foreground">
          模型决策 · {detail.counts.llm_responses} 次
        </span>
        <span className="rounded-full border border-settings-border bg-background/70 px-3 py-1 text-settings-foreground">
          工具执行 · {detail.counts.tool_calls} 次
        </span>
        <span className={`rounded-full border px-3 py-1 ${hasProblems
          ? "border-amber-200 bg-amber-50 text-amber-800"
          : "border-emerald-200 bg-emerald-50 text-emerald-800"}`}>
          {statusLabel}
        </span>
      </div>

      <div className="rounded-xl border border-settings-border bg-background/55 p-3">
        <div className="flex items-start gap-2">
          <MessageSquare className="mt-0.5 h-4 w-4 shrink-0 text-settings-muted" />
          <div>
            <div className="text-sm font-semibold text-settings-foreground">Agent 实际经历的过程</div>
            <div className="mt-1 text-xs leading-5 text-settings-muted">
              下面按发生顺序展示模型思考记录、模型决策、工具调用和工具返回。长参数和返回值可以在事件内展开；完整原始 JSON 请点击上方按钮。
            </div>
          </div>
        </div>
        <div className="mt-3 space-y-2">
          {detail.events.length > 0 ? detail.events.map((event, index) => (
            <ReplayTimelineEvent key={`${String(event.kind ?? "event")}-${index}`} event={event} index={index} />
          )) : (
            <div className="rounded-lg border border-dashed border-settings-border px-3 py-4 text-xs text-settings-muted">
              这个回合没有单独的模型或工具事件记录，可能是旧格式样本。
            </div>
          )}
        </div>
      </div>

      <div className={`rounded-xl border px-3 py-3 text-xs leading-5 ${hasProblems
        ? "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-200"
        : "border-settings-border bg-background/70 text-settings-muted"}`}>
        <div className="font-medium text-settings-foreground">本回合结果</div>
        <div className="mt-1">{statusDescription} 回放只使用录制中的模型响应和工具结果，不会重新请求模型或执行真实工具。</div>
      </div>
    </div>
  );
}

function RawExecutionViewer({ detail }: { detail: BlackboxDetail }) {
  const rawEvents = detail.events.map((event) => JSON.stringify(event)).join("\n");
  return (
    <div className="flex h-full min-h-0 flex-col bg-settings-surface">
      <div className="shrink-0 border-b border-settings-border px-6 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3 pr-12">
          <div>
            <div className="text-base font-semibold text-settings-foreground">完整原始记录</div>
            <div className="mt-1 break-all font-mono text-xs text-settings-muted">{detail.turn_id}</div>
          </div>
          <div className="rounded-full bg-settings-hover px-3 py-1 text-xs text-settings-muted">
            不做摘要 · 不改写内容
          </div>
        </div>
        <p className="mt-3 max-w-4xl text-xs leading-5 text-settings-muted">
          这里展示这个回合保存下来的全部 JSON 数据：回合总记录，以及按执行顺序排列的模型响应和工具调用。内容可能包含 Prompt、文件路径和其他敏感信息。
        </p>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        <div className="mx-auto max-w-[1600px] space-y-5">
          <section>
            <div className="mb-2 flex items-center justify-between gap-2">
              <h4 className="text-sm font-semibold text-settings-foreground">回合总记录</h4>
              <span className="font-mono text-[11px] text-settings-muted">turns.jsonl · kind=turn</span>
            </div>
            <pre className="overflow-auto rounded-xl border border-settings-border bg-slate-950 p-4 font-mono text-[11px] leading-5 text-slate-100">
              {formatJson(detail.turn)}
            </pre>
          </section>

          <section>
            <div className="mb-2 flex items-center justify-between gap-2">
              <h4 className="text-sm font-semibold text-settings-foreground">执行过程原始记录</h4>
              <span className="font-mono text-[11px] text-settings-muted">tools.jsonl · {detail.events.length} 条记录</span>
            </div>
            <pre className="min-h-32 overflow-auto whitespace-pre-wrap break-all rounded-xl border border-settings-border bg-slate-950 p-4 font-mono text-[11px] leading-5 text-slate-100">
              {rawEvents || "这个回合没有单独的模型或工具事件记录。"}
            </pre>
          </section>

          <div className="text-xs text-settings-muted">
            关联文件：turns.jsonl{detail.files.tools ? " · tools.jsonl" : ""}{detail.files.cassette ? ` · ${detail.files.cassette}` : ""}
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
      `删除录制“${recording.name}”？这会删除其中的 Prompt、模型响应和工具结果，无法恢复。`,
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

  return (
    <div className="flex flex-col gap-6 p-6">
      <section className="rounded-2xl border border-violet-200 bg-violet-50/70 p-5 dark:border-violet-900 dark:bg-violet-950/20">
        <div className="flex items-start gap-3">
          <Bug className="mt-0.5 h-5 w-5 shrink-0 text-violet-600" />
          <div>
            <h2 className="text-lg font-semibold text-settings-foreground">
              {t("settings.enhancements.title", { defaultValue: "录制与回放" })}
            </h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-settings-foreground">
              {t("settings.enhancements.explainer", {
                defaultValue:
                  "先把一次真实任务保存成可重走的执行样本。之后可以不请求模型、不执行真实工具，离线检查当前 Agent 的执行流程有没有变化。",
              })}
            </p>
            <div className="mt-4 grid gap-3 text-xs text-settings-foreground sm:grid-cols-3">
              <div className="rounded-xl bg-white/70 p-3 dark:bg-black/20">
                <div className="font-semibold">1. 保存一次真实任务</div>
                <div className="mt-1 text-settings-muted">输入、模型回答、工具调用和结果都会保留。</div>
              </div>
              <div className="rounded-xl bg-white/70 p-3 dark:bg-black/20">
                <div className="font-semibold">2. 修改代码或排查问题</div>
                <div className="mt-1 text-settings-muted">适合检查模型循环、上下文和工具处理。</div>
              </div>
              <div className="rounded-xl bg-white/70 p-3 dark:bg-black/20">
                <div className="font-semibold">3. 离线重走并比较</div>
                <div className="mt-1 text-settings-muted">不花 Token、不联网，也不会产生工具副作用。</div>
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
                {status?.recording ? "正在收集执行样本" : "录制一次任务"}
              </h3>
              <p className="text-xs text-settings-muted">
                {status?.recording
                  ? "停止前，所有会话中的任务都会继续写入这份样本。"
                  : "点击开始，执行你想保存下来、以后重复检查的任务。"}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {status?.recording ? (
              <Button variant="secondary" size="sm" onClick={() => void stop()} disabled={busy !== null}>
                {busy === "stop" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Square className="h-4 w-4" />}
                停止录制
              </Button>
            ) : (
              <Button variant="secondary" size="sm" onClick={() => void start()} disabled={busy !== null}>
                {busy === "start" ? <Loader2 className="h-4 w-4 animate-spin" /> : <CircleDashed className="h-4 w-4" />}
                开始录制
              </Button>
            )}
            <Button variant="ghost" size="sm" onClick={() => void refresh()} disabled={busy !== null}>
              <RefreshCw className="h-4 w-4" />
              刷新
            </Button>
          </div>
        </div>

        {status?.recording && status.directory ? (
          <div className="mb-4 rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:bg-emerald-950/20 dark:text-emerald-300">
            当前样本：<span className="font-mono">{status.directory.split(/[\\/]/).pop()}</span>
          </div>
        ) : null}

        {status?.recording ? (
          <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-300">
            从“开始”到“停止”期间，不论你在哪个会话中执行任务，所有回合都会追加到同一个样本。
          </div>
        ) : null}

        <div className="mb-4 flex items-center gap-2 rounded-lg border border-settings-border bg-settings-hover/40 px-3 py-2 text-xs text-settings-muted">
          <Info className="h-4 w-4 shrink-0" />
          离线检查只使用样本里的模型响应和工具结果；不会产生新的模型请求，也不会真的执行工具。
        </div>

        <div className="mb-3 flex flex-wrap items-center gap-2">
          <PauseCircle className="h-4 w-4 text-settings-muted" />
          <span className="text-xs text-settings-muted">暂停检查（可选）：在第</span>
          <Input
            type="number"
            min={1}
            value={breakAt}
            onChange={(event) => setBreakAt(event.target.value)}
            placeholder="N"
            className="h-7 w-16"
          />
          <span className="text-xs text-settings-muted">个模型决策处暂停</span>
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
                          {recordingStatusLabel(recording)}
                        </span>
                      </div>
                      <div className="mt-1 text-xs text-settings-muted">
                        {isReady ? `${recording.turns} 个任务已保存` : recording.message}
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
                      {breakAt ? "暂停检查" : "离线检查"}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void deleteRecording(recording)}
                      disabled={busy !== null}
                      title="删除录制"
                      aria-label={`删除录制 ${recording.name}`}
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
            还没有执行样本。点击“开始”，执行一次任务，再回来点击“停止”。
          </div>
        )}
      </section>

      {replay ? (
        <section className="rounded-xl border border-settings-border bg-settings-surface p-5">
          <div className="flex items-start gap-3">
            {replay.all_deterministic ? (
              <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-500" />
            ) : (
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />
            )}
            <div>
              <h3 className="text-base font-semibold text-settings-foreground">离线检查结果</h3>
              <p className="mt-1 text-sm text-settings-foreground">
                {replay.deterministic_turns}/{replay.total_turns} 个回合未发现可观察差异
              </p>
              <p className="mt-1 text-xs text-settings-muted">
                这里检查的是当前代码能否重走原来的执行流程，不是模型回答质量评分。
              </p>
              <p className="mt-1 text-xs text-settings-muted">
                点开一个回合查看完整的可读执行过程；需要完整 JSON 时，再点击“查看原始记录”。
              </p>
            </div>
          </div>
          <div className="mt-4 flex flex-col gap-1">
            {replay.results.map((row, index) => (
              <div key={row.turn_id} className="rounded-lg border border-settings-border">
                <button
                  type="button"
                  onClick={() => void toggleTurnDetail(row.turn_id)}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-settings-hover"
                >
                  {row.ok ? (
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
                  ) : (
                    <XCircle className="h-4 w-4 shrink-0 text-amber-500" />
                  )}
                  <span className="whitespace-nowrap text-sm font-medium text-settings-foreground">第 {index + 1} 回合</span>
                  <span className="min-w-0 truncate font-mono text-xs text-settings-muted">{row.turn_id}</span>
                  <span className={`ml-auto whitespace-nowrap text-xs ${row.ok ? "text-emerald-700" : "text-amber-700"}`}>
                    {row.ok ? "未发现可观察差异" : `发现 ${row.diffs.length} 处差异`}
                  </span>
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
                        <div className="mb-2 text-xs font-medium text-settings-muted">当前结果与原样本的不同之处</div>
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
                        onFullscreen={() => setFullscreenDetail(detailByTurn[row.turn_id])}
                      />
                    ) : detailLoading === row.turn_id ? (
                      <div className="border-t border-settings-border px-3 py-5 text-center text-xs text-settings-muted">
                        正在读取这个回合的执行过程…
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
            已在第 {breakpoint.iteration + 1} 个模型决策处暂停
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
            <DialogTitle>回合原始记录</DialogTitle>
            <DialogDescription>
              这是保存下来的完整数据，不做改写；关闭窗口后返回可读执行过程。
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
            <h3 className="text-base font-semibold text-settings-foreground">上下文预算</h3>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatCard label="估算 Token" value={tokens ? tokens.estimated_tokens.toLocaleString() : "—"} />
          <StatCard label="上下文窗口" value={tokens ? tokens.context_window_tokens.toLocaleString() : "—"} />
          <StatCard label="消息数" value={tokens ? String(tokens.message_count) : "—"} />
          <StatCard
            label="窗口使用率"
            value={usagePct != null ? `${usagePct}%` : "—"}
            hint={tokens ? `${tokens.tool_count} 个工具 · ${tokens.model ?? ""}` : undefined}
          />
        </div>
      </section>
    </div>
  );
}
