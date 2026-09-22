import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDashed,
  Gauge,
  Loader2,
  RefreshCw,
} from "lucide-react";

import { ExecutionTraceTimeline } from "@/components/thread/ExecutionTraceTimeline";
import { Button } from "@/components/ui/button";
import {
  traceDetail,
  traceList,
  type TraceDetail,
  type TraceSummary,
} from "@/lib/api";
import { useClient } from "@/providers/ClientProvider";

type TraceFilter = "all" | "issues" | "slow";

function tx(
  t: (key: string, options?: Record<string, unknown>) => string,
  key: string,
  fallback: string,
  values?: Record<string, unknown>,
): string {
  return t(key, { defaultValue: fallback, ...(values ?? {}) });
}

function formatDuration(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "—";
  if (value < 1000) return `${Math.round(value)}ms`;
  if (value < 60_000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)}s`;
  return `${Math.floor(value / 60_000)}m ${Math.round((value % 60_000) / 1000)}s`;
}

/**
 * Read-only local Trace workbench.
 *
 * It intentionally uses the existing `trace.list` / `trace.detail` requests and
 * client-side trace subscription. Moving this view out of regression settings
 * changes navigation only; Trace persistence and replay data are untouched.
 */
export function LiveExecutionSettings() {
  const { t } = useTranslation();
  const { client } = useClient();
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [filter, setFilter] = useState<TraceFilter>("all");
  const [selectedTrace, setSelectedTrace] = useState<TraceDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [traceLoading, setTraceLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const result = await traceList(client, { filter });
      setTraces(result.traces);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [client, filter]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    if (typeof client.onTrace !== "function") return;
    const unsubscribe = client.onTrace((_chatId, event) => {
      const turnId = typeof event.turn_id === "string" ? event.turn_id : null;
      if (turnId) {
        setSelectedTrace((current) => current?.summary.turn_id === turnId
          ? { ...current, events: [...current.events, event] }
          : current);
      }
      if (timer !== null) clearTimeout(timer);
      timer = setTimeout(() => void refresh(), 250);
    });
    return () => {
      unsubscribe();
      if (timer !== null) clearTimeout(timer);
    };
  }, [client, refresh]);

  const openTrace = useCallback(async (trace: TraceSummary) => {
    if (selectedTrace?.summary.id === trace.id) {
      setSelectedTrace(null);
      return;
    }
    setTraceLoading(trace.id);
    try {
      setSelectedTrace(await traceDetail(client, trace.id));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setTraceLoading(null);
    }
  }, [client, selectedTrace?.summary.id]);

  return (
    <div className="flex flex-col gap-6 p-6">
      <section className="rounded-2xl border border-blue-200 bg-blue-50/70 p-5 dark:border-blue-900 dark:bg-blue-950/20">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-start gap-3">
            <Gauge className="mt-0.5 h-5 w-5 shrink-0 text-blue-600" />
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-blue-700 dark:text-blue-300">
                {tx(t, "settings.enhancements.liveExecution.kicker", "Local observability")}
              </div>
              <h2 className="mt-1 text-lg font-semibold text-settings-foreground">
                {tx(t, "settings.enhancements.liveExecution.title", "Live execution records")}
              </h2>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-settings-foreground">
                {tx(t, "settings.enhancements.liveExecution.description", "Inspect local execution records while a task runs or after it completes. Records include stages, model and tool previews, timing, failures, and recovery events.")}
              </p>
            </div>
          </div>
          <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>
            <RefreshCw className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            {tx(t, "settings.enhancements.recording.refresh", "Refresh")}
          </Button>
        </div>
      </section>

      {error ? (
        <div role="alert" className="rounded-xl border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      ) : null}

      <section className="rounded-xl border border-settings-border bg-settings-surface p-5">
        <div className="mb-3 flex flex-wrap gap-2" role="group" aria-label={tx(t, "settings.enhancements.trace.filterLabel", "Trace filter")}>
          {(["all", "issues", "slow"] as const).map((nextFilter) => (
            <Button
              key={nextFilter}
              type="button"
              variant={filter === nextFilter ? "secondary" : "ghost"}
              size="sm"
              onClick={() => setFilter(nextFilter)}
              disabled={loading}
            >
              {nextFilter === "all"
                ? tx(t, "settings.enhancements.trace.filterAll", "All")
                : nextFilter === "issues"
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
              const active = trace.status === "accepted" || trace.status === "running";
              const issueSummary = [
                toolFailures > 0 ? tx(t, "settings.enhancements.trace.toolFailures", "{{count}} tool failures", { count: toolFailures }) : "",
                providerErrors > 0 ? tx(t, "settings.enhancements.trace.providerErrors", "{{count}} model errors", { count: providerErrors }) : "",
                uncertainSideEffects > 0 ? tx(t, "settings.enhancements.trace.unknownSideEffects", "{{count}} uncertain side effects", { count: uncertainSideEffects }) : "",
              ].filter(Boolean).join(" · ");
              return (
                <div key={trace.id} className="rounded-lg border border-settings-border">
                  <button type="button" onClick={() => void openTrace(trace)} className="flex w-full items-center gap-3 px-3 py-3 text-left hover:bg-settings-hover">
                    {hasFailure ? <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" /> : active ? <CircleDashed className="h-4 w-4 shrink-0 animate-spin text-blue-600" /> : <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />}
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-medium text-settings-foreground">{trace.session_name || tx(t, "settings.enhancements.trace.unnamedSession", "Unnamed conversation")}</span>
                      <span className="mt-1 block text-[11px] text-settings-muted">
                        {trace.event_count} {tx(t, "settings.enhancements.trace.events", "events")} · {trace.tool_count} {tx(t, "settings.enhancements.trace.tools", "tools")}{trace.duration_ms != null ? ` · ${formatDuration(trace.duration_ms)}` : ""}{issueSummary ? ` · ${issueSummary}` : ""}
                      </span>
                    </span>
                    <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] ${hasFailure ? "border-amber-200 bg-amber-50 text-amber-800" : active ? "border-blue-200 bg-blue-50 text-blue-800" : "border-emerald-200 bg-emerald-50 text-emerald-800"}`}>
                      {hasFailure ? tx(t, "settings.enhancements.trace.needsAttention", "Needs attention") : active ? tx(t, "settings.enhancements.trace.running", "Running") : tx(t, "settings.enhancements.trace.completed", "Completed")}
                    </span>
                    {traceLoading === trace.id ? <Loader2 className="h-4 w-4 animate-spin text-settings-muted" /> : selectedTrace?.summary.id === trace.id ? <ChevronDown className="h-4 w-4 text-settings-muted" /> : <ChevronRight className="h-4 w-4 text-settings-muted" />}
                  </button>
                  {selectedTrace?.summary.id === trace.id ? <div className="border-t border-settings-border bg-settings-hover/25 p-3"><ExecutionTraceTimeline events={selectedTrace.events} /></div> : null}
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
    </div>
  );
}
