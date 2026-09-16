import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  Clock3,
  RefreshCw,
  Route,
  Wrench,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { ExecutionTraceTimeline } from "@/components/thread/ExecutionTraceTimeline";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { traceDetail, traceList, type TraceDetail, type TraceSummary } from "@/lib/api";
import type { ExecutionTraceEvent } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

interface ExecutionTracePanelProps {
  chatId: string;
  sessionKey?: string | null;
  title: string;
}

type TraceMap = Record<string, ExecutionTraceEvent[]>;

const TERMINAL_EVENTS = new Set([
  "turn.completed",
  "turn.failed",
  "turn.cancelled",
  "turn.incomplete",
]);

function traceKey(value: { trace_id?: string | null; turn_id?: string | null; id?: string | null }): string {
  return value.trace_id || value.turn_id || value.id || "current";
}

function appendTraceEvent(previous: ExecutionTraceEvent[], event: ExecutionTraceEvent): ExecutionTraceEvent[] {
  const sequence = Number(event.sequence);
  if (Number.isFinite(sequence) && previous.some((item) => Number(item.sequence) === sequence)) {
    return previous;
  }
  return [...previous, event].sort((left, right) => Number(left.sequence ?? 0) - Number(right.sequence ?? 0));
}

function eventCallKey(event: ExecutionTraceEvent, prefix: string): string {
  const callId = typeof event.call_id === "string" && event.call_id ? event.call_id : null;
  if (callId) return `${prefix}:call:${callId}`;
  return `${prefix}:fallback:${event.iteration ?? "?"}:${event.tool_name ?? "?"}`;
}

function countUniqueEvents(events: ExecutionTraceEvent[], names: Set<string>, prefix: string): number {
  const keys = new Set<string>();
  for (const event of events) {
    if (names.has(String(event.event ?? ""))) {
      keys.add(eventCallKey(event, prefix));
    }
  }
  return keys.size;
}

function modelRequestCount(events: ExecutionTraceEvent[]): number {
  const started = countUniqueEvents(
    events,
    new Set(["llm.request_started"]),
    "model",
  );
  if (started > 0) return started;
  return new Set(
    events
      .filter((event) => ["llm.response", "llm.request_failed"].includes(String(event.event ?? "")))
      .map((event) => `${event.iteration ?? "?"}:${event.event ?? ""}`),
  ).size;
}

function summaryFromEvents(
  events: ExecutionTraceEvent[],
  previous?: TraceSummary,
): TraceSummary | null {
  const first = events[0];
  const last = events[events.length - 1];
  if (!first || !last) return previous ?? null;
  const failures = events.filter((event) => ["error", "failed", "blocked", "denied", "unknown_side_effect", "cancelled", "incomplete"].includes(String(event.status ?? "").toLowerCase())).length;
  const tools = countUniqueEvents(
    events,
    new Set(["tool.planned", "tool.started", "tool.finished", "tool.cancelled"]),
    "tool",
  );
  const toolFailures = events.filter((event) => (
    String(event.event ?? "") === "tool.finished"
    && ["failed", "unknown_side_effect"].includes(String(event.status ?? "").toLowerCase())
  )).length;
  const providerErrors = events.filter((event) => (
    ["llm.request_failed", "provider_tool.error"].includes(String(event.event ?? ""))
    || (String(event.event ?? "") === "llm.response" && String(event.status ?? "").toLowerCase() === "error")
  )).length;
  const unknownSideEffects = events.filter((event) => ["unknown_side_effect", "may_have_occurred"].includes(String(event.status ?? "").toLowerCase())).length;
  const verification = [...events].reverse().find((event) => String(event.event ?? "") === "task.verification");
  const terminal = TERMINAL_EVENTS.has(String(last.event ?? ""));
  const duration = terminal && typeof last.duration_ms === "number"
    ? last.duration_ms
    : typeof first.timestamp_ms === "number" && typeof last.timestamp_ms === "number"
      ? Math.max(0, last.timestamp_ms - first.timestamp_ms)
      : typeof last.duration_ms === "number" ? last.duration_ms : null;
  return {
    id: previous?.id || `live:${traceKey(first)}`,
    trace_id: String(first.trace_id || previous?.trace_id || traceKey(first)),
    session_key: typeof first.session_key === "string" ? first.session_key : previous?.session_key ?? null,
    session_name: previous?.session_name,
    turn_id: String(first.turn_id || previous?.turn_id || "current"),
    channel: String(first.channel || previous?.channel || "websocket"),
    chat_id: String(first.chat_id || previous?.chat_id || ""),
    model: typeof last.model === "string" ? last.model : previous?.model ?? null,
    provider: typeof last.provider === "string" ? last.provider : previous?.provider ?? null,
    owner_pid: previous?.owner_pid ?? null,
    status: terminal ? String(last.status || previous?.status || "completed") : "running",
    stop_reason: terminal && typeof last.stop_reason === "string" ? last.stop_reason : previous?.stop_reason ?? null,
    duration_ms: duration,
    event_count: events.length,
    tool_count: tools,
    failure_count: failures,
    tool_failure_count: toolFailures,
    provider_error_count: providerErrors,
    unknown_side_effect_count: unknownSideEffects,
    verification_status: verification && ["passed", "failed", "not_evaluable"].includes(String(verification.verification_status ?? ""))
      ? String(verification.verification_status) as TraceSummary["verification_status"]
      : previous?.verification_status ?? null,
    verification_completed: typeof verification?.verification_completed === "boolean"
      ? verification.verification_completed
      : previous?.verification_completed ?? null,
    max_step_duration_ms: previous?.max_step_duration_ms ?? 0,
    timestamp_ms: typeof first.timestamp_ms === "number" ? first.timestamp_ms : previous?.timestamp_ms ?? null,
  };
}

function isActiveTrace(summary: TraceSummary | null): boolean {
  return !!summary
    && !summary.stop_reason
    && !["completed", "error", "cancelled", "incomplete"].includes(summary.status.toLowerCase());
}

function formatTime(timestamp: number | null | undefined): string {
  if (typeof timestamp !== "number" || !Number.isFinite(timestamp)) return "";
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(timestamp));
}

function formatDuration(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return value < 1000 ? `${Math.round(value)}ms` : `${(value / 1000).toFixed(1)}s`;
}

export function ExecutionTracePanel({ chatId, sessionKey = null, title }: ExecutionTracePanelProps) {
  const { t } = useTranslation();
  const { client } = useClient();
  const [open, setOpen] = useState(false);
  const [summaries, setSummaries] = useState<TraceSummary[]>([]);
  const [eventsByTrace, setEventsByTrace] = useState<TraceMap>({});
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<TraceDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const eventsByTraceRef = useRef<TraceMap>({});
  const timelineEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    eventsByTraceRef.current = {};
    setEventsByTrace({});
    setSummaries([]);
    setSelectedKey(null);
    setSelectedDetail(null);
  }, [chatId, sessionKey]);

  const refresh = useCallback(async () => {
    if (!sessionKey) return;
    setLoading(true);
    try {
      const payload = await traceList(client, { sessionKey, chatId, limit: 50 });
      setSummaries(payload.traces);
      setSelectedKey((current) => current || (payload.traces[0] ? traceKey(payload.traces[0]) : null));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [client, sessionKey]);

  useEffect(() => {
    if (typeof client.onTrace !== "function") return;
    const unsubscribe = client.onTrace((eventChatId, event) => {
      if (eventChatId !== chatId) return;
      const key = traceKey(event);
      const nextEvents = appendTraceEvent(eventsByTraceRef.current[key] ?? [], event);
      eventsByTraceRef.current[key] = nextEvents;
      setEventsByTrace({ ...eventsByTraceRef.current });
      setSummaries((current) => {
        const existing = current.find((item) => traceKey(item) === key);
        const next = summaryFromEvents(
          nextEvents,
          existing,
        );
        if (!next) return current;
        const merged = current.filter((item) => traceKey(item) !== key);
        return [next, ...merged].slice(0, 50);
      });
      setSelectedKey((current) => current || key);
      if (open) {
        window.setTimeout(() => timelineEndRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }), 0);
      }
    });
    return unsubscribe;
  }, [chatId, client, open]);

  useEffect(() => {
    if (open) void refresh();
  }, [open, refresh]);

  const selectedSummary = useMemo(
    () => summaries.find((summary) => traceKey(summary) === selectedKey) ?? null,
    [selectedKey, summaries],
  );
  const selectedEvents = selectedKey ? eventsByTrace[selectedKey] ?? [] : [];
  const detailEvents = selectedDetail?.events as ExecutionTraceEvent[] | undefined;
  const visibleEvents = selectedEvents.length > 0 ? selectedEvents : detailEvents ?? [];
  const active = isActiveTrace(selectedSummary);
  const issueCount = selectedSummary?.failure_count ?? summaries.reduce((total, summary) => total + (summary.failure_count || 0), 0);

  const loadDetail = useCallback(async (summary: TraceSummary) => {
    const key = traceKey(summary);
    if (!summary.id || summary.id.startsWith("live:")) return;
    setLoading(true);
    try {
      const detail = await traceDetail(client, summary.id);
      const events = detail.events as ExecutionTraceEvent[];
      eventsByTraceRef.current[key] = events;
      setEventsByTrace({ ...eventsByTraceRef.current });
      setSelectedDetail(detail);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [client]);

  useEffect(() => {
    if (!open || !selectedSummary || visibleEvents.length > 0) return;
    void loadDetail(selectedSummary);
  }, [loadDetail, open, selectedSummary, visibleEvents.length]);

  async function selectTrace(summary: TraceSummary) {
    const key = traceKey(summary);
    setSelectedKey(key);
    setSelectedDetail(null);
    const cached = eventsByTrace[key];
    if (cached && cached.length > 0) return;
    await loadDetail(summary);
  }

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={t("thread.trace.open", { defaultValue: "查看执行轨迹" })}
          aria-pressed={open}
          data-testid="execution-trace-trigger"
          className={cn(
            "host-no-drag relative h-8 w-8 rounded-full text-muted-foreground/85",
            "hover:bg-accent/40 hover:text-foreground",
            open && "bg-primary/10 text-primary",
          )}
          title={t("thread.trace.open", { defaultValue: "查看执行轨迹" })}
        >
          <Route className="h-4 w-4 stroke-[1.8]" />
          {active ? <span className="absolute right-1 top-1 h-1.5 w-1.5 animate-pulse rounded-full bg-blue-500" /> : null}
          {!active && issueCount > 0 ? <span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-amber-500" /> : null}
        </Button>
      </SheetTrigger>
      <SheetContent
        side="right"
        className="w-[min(44rem,100vw)] gap-0 overflow-hidden border-l border-border/70 p-0 sm:max-w-[44rem]"
      >
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="shrink-0 border-b border-border/70 px-5 py-5 pr-14">
            <div className="flex items-start gap-3">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                <Activity className="h-5 w-5" />
              </div>
              <div className="min-w-0">
                <SheetTitle className="text-base">{t("thread.trace.title", { defaultValue: "执行轨迹" })}</SheetTitle>
                <SheetDescription className="mt-1 truncate text-xs">
                  {title || t("thread.trace.untitled", { defaultValue: "当前会话" })}
                </SheetDescription>
              </div>
            </div>
            <div className="mt-4 flex flex-wrap items-center gap-2 text-[11px]">
              <span className={cn(
                "rounded-full border px-2.5 py-1",
                active
                  ? "border-blue-200 bg-blue-50 text-blue-800 dark:border-blue-900 dark:bg-blue-950/25 dark:text-blue-200"
                  : issueCount > 0
                    ? "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/25 dark:text-amber-200"
                    : "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/25 dark:text-emerald-200",
              )}>
                {active
                  ? t("thread.trace.running", { defaultValue: "正在执行" })
                  : issueCount > 0
                    ? t("thread.trace.hasIssues", { count: issueCount, defaultValue: "发现 {{count}} 个问题" })
                    : t("thread.trace.idle", { defaultValue: "执行已完成" })}
              </span>
              <span className="text-muted-foreground">
                {t("thread.trace.hint", { defaultValue: "按发生顺序查看模型、工具和恢复事件" })}
              </span>
            </div>
            {selectedSummary?.verification_status ? (
              <div className={cn(
                "mt-2 inline-flex rounded-full border px-2.5 py-1 text-[11px]",
                selectedSummary.verification_status === "passed"
                  ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/25 dark:text-emerald-200"
                  : "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/25 dark:text-amber-200",
              )}>
                {t("thread.trace.taskVerification", { defaultValue: "任务验收" })}: {selectedSummary.verification_status === "passed"
                  ? t("thread.trace.taskPassed", { defaultValue: "通过" })
                  : selectedSummary.verification_status === "failed"
                    ? t("thread.trace.taskFailed", { defaultValue: "未通过" })
                    : t("thread.trace.taskNotEvaluable", { defaultValue: "无法判断" })}
              </div>
            ) : null}
          </div>

          {error ? (
            <div className="mx-5 mt-4 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-900 dark:bg-red-950/25 dark:text-red-200">
              {error}
            </div>
          ) : null}

          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
            <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
              <TraceMetric icon={<Clock3 className="h-3.5 w-3.5" />} label={t("thread.trace.events", { defaultValue: "事件" })} value={String(selectedSummary?.event_count ?? visibleEvents.length)} />
              <TraceMetric icon={<Bot className="h-3.5 w-3.5" />} label={t("thread.trace.modelCalls", { defaultValue: "模型请求" })} value={String(modelRequestCount(visibleEvents))} />
              <TraceMetric icon={<Wrench className="h-3.5 w-3.5" />} label={t("thread.trace.toolCalls", { defaultValue: "工具调用" })} value={String(selectedSummary?.tool_count ?? countUniqueEvents(visibleEvents, new Set(["tool.planned", "tool.started", "tool.finished", "tool.cancelled"]), "tool"))} />
              <TraceMetric icon={<AlertTriangle className="h-3.5 w-3.5" />} label={t("thread.trace.issues", { defaultValue: "问题" })} value={String(issueCount)} />
            </div>

            {selectedSummary ? (
              <div className="mb-5 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-border/65 bg-muted/20 px-3 py-2 text-[11px] text-muted-foreground">
                <span>{t("thread.trace.modelLabel", { defaultValue: "模型" })}: <strong className="font-medium text-foreground">{selectedSummary.model || "—"}</strong></span>
                <span>{t("thread.trace.providerLabel", { defaultValue: "提供商" })}: <strong className="font-medium text-foreground">{selectedSummary.provider || "—"}</strong></span>
                <span>{t("thread.trace.durationLabel", { defaultValue: "耗时" })}: <strong className="font-medium text-foreground">{formatDuration(selectedSummary.duration_ms)}</strong></span>
              </div>
            ) : null}

            {summaries.length > 0 ? (
              <section className="mb-5">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <h3 className="text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                    {t("thread.trace.recentRuns", { defaultValue: "本会话执行记录" })}
                  </h3>
                  <Button type="button" variant="ghost" size="icon" onClick={() => void refresh()} disabled={loading} className="h-7 w-7" aria-label={t("thread.trace.refresh", { defaultValue: "刷新" })}>
                    <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
                  </Button>
                </div>
                <div className="space-y-1.5">
                  {summaries.map((summary, index) => {
                    const key = traceKey(summary);
                    const selected = key === selectedKey;
                    const hasIssues = summary.failure_count > 0 || ["error", "cancelled", "incomplete"].includes(summary.status);
                    return (
                      <button
                        key={key}
                        type="button"
                        onClick={() => void selectTrace(summary)}
                        className={cn(
                          "flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition-colors",
                          selected ? "border-primary/40 bg-primary/[0.06]" : "border-border/65 hover:bg-muted/45",
                        )}
                      >
                        {hasIssues ? <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" /> : selected && active ? <CircleDashed className="h-4 w-4 shrink-0 animate-spin text-blue-600" /> : <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />}
                        <span className="min-w-0 flex-1">
                          <span className="block text-xs font-medium text-foreground">
                            {t("thread.trace.runNumber", { number: summaries.length - index, defaultValue: "第 {{number}} 次执行" })}
                          </span>
                          <span className="mt-0.5 block text-[11px] text-muted-foreground">
                            {formatTime(summary.timestamp_ms)} · {summary.event_count} {t("thread.trace.eventsUnit", { defaultValue: "个事件" })} · {summary.tool_count} {t("thread.trace.toolsUnit", { defaultValue: "个工具" })} · {formatDuration(summary.duration_ms)}
                          </span>
                          {summary.verification_status ? (
                            <span className={cn(
                              "mt-1 inline-block text-[10px]",
                              summary.verification_status === "passed" ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300",
                            )}>
                              {t("thread.trace.taskVerification", { defaultValue: "任务验收" })}: {summary.verification_status === "passed"
                                ? t("thread.trace.taskPassed", { defaultValue: "通过" })
                                : summary.verification_status === "failed"
                                  ? t("thread.trace.taskFailed", { defaultValue: "未通过" })
                                  : t("thread.trace.taskNotEvaluable", { defaultValue: "无法判断" })}
                            </span>
                          ) : null}
                        </span>
                        <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      </button>
                    );
                  })}
                </div>
              </section>
            ) : null}

            <section>
              <div className="mb-2 flex items-center justify-between gap-2">
                <div>
                  <h3 className="text-sm font-semibold text-foreground">
                    {selectedSummary
                      ? t("thread.trace.currentRun", { defaultValue: "这次执行发生了什么" })
                      : t("thread.trace.waiting", { defaultValue: "等待下一次执行" })}
                  </h3>
                  <p className="mt-1 text-[11px] leading-5 text-muted-foreground">
                    {t("thread.trace.detailHint", { defaultValue: "展开任意事件，可查看阶段、耗时、模型响应摘要、工具参数摘要、返回结果和错误。" })}
                  </p>
                </div>
              </div>
              <ExecutionTraceTimeline
                events={visibleEvents}
                emptyLabel={t("thread.trace.empty", { defaultValue: "当前会话还没有执行事件。发送一个任务后，这里会实时更新。" })}
              />
              <div ref={timelineEndRef} />
            </section>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function TraceMetric({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="rounded-xl border border-border/65 bg-muted/25 px-3 py-2">
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-muted-foreground">{icon}{label}</div>
      <div className="mt-1 text-lg font-semibold text-foreground">{value}</div>
    </div>
  );
}
