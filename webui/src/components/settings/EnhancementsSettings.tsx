import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
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
  PauseCircle,
  Play,
  Radio,
  RefreshCw,
  Square,
  Trash2,
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
  return recording.status === "ready" ? "可回放" : "文件不完整";
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

function RawJsonBlock({
  title,
  value,
  open = false,
}: {
  title: string;
  value: unknown;
  open?: boolean;
}) {
  return (
    <details open={open} className="rounded-lg border border-settings-border bg-background/70">
      <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-settings-foreground">
        {title}
      </summary>
      <pre className="max-h-[28rem] overflow-auto border-t border-settings-border px-3 py-3 font-mono text-[11px] leading-5 text-settings-foreground">
        {formatJson(value)}
      </pre>
    </details>
  );
}

function RailCard({
  title,
  source,
  description,
  value,
  muted = false,
}: {
  title: string;
  source: string;
  description: string;
  value: string;
  muted?: boolean;
}) {
  return (
    <div className={`rounded-lg border px-3 py-3 ${muted ? "border-settings-border bg-settings-hover/30" : "border-settings-border bg-background/70"}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-settings-foreground">{title}</span>
        <span className="text-[11px] text-settings-muted">{value}</span>
      </div>
      <div className="mt-1 text-[11px] leading-4 text-settings-muted">{description}</div>
      <div className="mt-2 font-mono text-[10px] text-settings-muted">{source}</div>
    </div>
  );
}

function ReplayTurnDetail({
  detail,
  onFullscreen,
}: {
  detail: BlackboxDetail;
  onFullscreen?: () => void;
}) {
  const turn = detail.turn;
  const usage = isRecord(turn.usage) ? turn.usage : null;
  return (
    <div className="space-y-3 border-t border-settings-border bg-settings-hover/35 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-semibold text-settings-foreground">回合详情</div>
          <div className="mt-0.5 font-mono text-[11px] text-settings-muted">{detail.turn_id}</div>
        </div>
        {onFullscreen ? (
          <Button type="button" variant="outline" size="sm" onClick={onFullscreen}>
            <Maximize2 className="mr-1.5 h-3.5 w-3.5" />
            全屏查看
          </Button>
        ) : null}
      </div>
      <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-200">
        下面是录制时保存的原始数据。回放不会重新请求模型，而是按这些模型响应和工具结果重建执行。
        内容可能包含 Prompt、文件路径和敏感信息。
      </div>

      <div>
        <div className="mb-2 text-xs font-semibold text-settings-foreground">回放数据轨</div>
        <div className="grid gap-2 sm:grid-cols-2">
          <RailCard
            title="LLM Response Rail"
            source="tools.jsonl · kind=llm"
            value={`${detail.counts.llm_responses} 次响应`}
            description="固定模型响应、finish reason、usage 和模型发出的 Tool Call；回放时由 ReplayProvider 提供。"
          />
          <RailCard
            title="Tool Observation Rail"
            source="tools.jsonl · kind=tool"
            value={`${detail.counts.tool_calls} 次调用`}
            description="固定工具名称、参数、状态和返回值；回放时不会执行真实工具副作用。"
          />
          <RailCard
            title="Turn Envelope"
            source="turns.jsonl · kind=turn"
            value="1 个回合"
            description="保存输入上下文、工具 Schema、最终消息、stop reason、session 和 model。"
          />
          <RailCard
            title="HTTP Audit Cassette"
            source={detail.files.cassette ?? "未生成 YAML cassette"}
            value={detail.files.cassette ? "可查看" : "可选"}
            muted={!detail.files.cassette}
            description="provider HTTP 交互审计记录；不是回放正确性的唯一依据，缺失不影响 JSON rail 回放。"
          />
        </div>
      </div>

      <div className="grid gap-2 text-xs sm:grid-cols-4">
        <div className="rounded-lg border border-settings-border bg-background/70 px-3 py-2">
          <div className="text-settings-muted">会话</div>
          <div className="mt-1 break-all font-mono text-settings-foreground">{String(turn.session_key ?? "—")}</div>
        </div>
        <div className="rounded-lg border border-settings-border bg-background/70 px-3 py-2">
          <div className="text-settings-muted">模型</div>
          <div className="mt-1 break-all font-mono text-settings-foreground">{String(turn.model ?? "—")}</div>
        </div>
        <div className="rounded-lg border border-settings-border bg-background/70 px-3 py-2">
          <div className="text-settings-muted">模型响应</div>
          <div className="mt-1 font-semibold text-settings-foreground">{detail.counts.llm_responses} 次</div>
        </div>
        <div className="rounded-lg border border-settings-border bg-background/70 px-3 py-2">
          <div className="text-settings-muted">工具调用</div>
          <div className="mt-1 font-semibold text-settings-foreground">{detail.counts.tool_calls} 次</div>
        </div>
      </div>

      {usage ? (
        <div className="text-xs text-settings-muted">
          录制 Token：输入 {String(usage.input_tokens ?? usage.prompt_tokens ?? "?")} · 输出 {String(usage.output_tokens ?? usage.completion_tokens ?? "?")} · 总计 {String(usage.total_tokens ?? "?")}
        </div>
      ) : null}

      <RawJsonBlock title="输入上下文（initial_messages）" value={turn.initial_messages} />
      <RawJsonBlock title="工具定义（tools schema）" value={turn.tools} />

      <div>
        <div className="text-xs font-semibold text-settings-foreground">Agent 执行时间线</div>
        <div className="mb-2 mt-1 text-[11px] leading-4 text-settings-muted">
          一个“回合”是一次用户任务；下面的“Agent 循环”是该任务内部的模型决策与工具执行阶段，从 1 开始计数。
        </div>
        <div className="space-y-2">
          {detail.events.length === 0 ? (
            <div className="rounded-lg border border-dashed border-settings-border px-3 py-4 text-xs text-settings-muted">
              没有单独的工具/模型 rail；可能是空回合或旧格式录制。
            </div>
          ) : detail.events.map((event, index) => {
            const eventKind = String(event.kind ?? "unknown");
            const response = isRecord(event.response) ? event.response : null;
            const tool = eventKind === "tool";
            const rawIteration = Number(event.iteration);
            const iteration = Number.isFinite(rawIteration) ? rawIteration + 1 : null;
            const responseNumber = event.response_index == null
              ? null
              : Number(event.response_index) + 1;
            const invocationNumber = event.invocation_index == null
              ? null
              : Number(event.invocation_index) + 1;
            return (
              <div key={`${eventKind}-${index}`} className={`rounded-lg border bg-background/70 p-3 ${tool ? "border-orange-200" : "border-blue-200"}`}>
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className={`rounded-full px-2 py-0.5 font-medium ${tool ? "bg-orange-100 text-orange-800" : "bg-blue-100 text-blue-800"}`}>
                    {tool ? "Tool Observation Rail" : "LLM Response Rail"}
                  </span>
                  <span className="font-medium text-settings-foreground">
                    {iteration == null ? "Agent 循环" : `Agent 循环 ${iteration}`}
                  </span>
                  {tool ? (
                    <span className="font-mono text-settings-foreground">
                      {invocationNumber == null ? "工具调用" : `工具调用 #${invocationNumber}`} · {String(event.name ?? "unknown tool")}
                    </span>
                  ) : (
                    <span className="text-settings-muted">
                      {responseNumber == null ? "模型响应" : `模型响应 #${responseNumber}`}
                    </span>
                  )}
                  <span className="ml-auto text-settings-muted">
                    {tool ? `状态：${String(event.status ?? "unknown")}` : `结束：${String(response?.finish_reason ?? "?")}`}
                  </span>
                </div>

                {tool ? (
                  <div className="mt-3 space-y-2">
                    {event.detail ? <div className="text-xs text-settings-muted">{String(event.detail)}</div> : null}
                    <RawJsonBlock title="Tool arguments（传入参数）" value={event.args} />
                    <RawJsonBlock title="Tool result（工具返回值）" value={event.result} />
                  </div>
                ) : response ? (
                  <div className="mt-3 space-y-2">
                    {typeof response.content === "string" && response.content ? (
                      <div className="rounded-lg bg-settings-hover/60 px-3 py-2 text-xs leading-5 text-settings-foreground">
                        <div className="mb-1 font-medium text-settings-muted">模型可见回答</div>
                        <div className="whitespace-pre-wrap break-words">{response.content}</div>
                      </div>
                    ) : null}
                    <RawJsonBlock title="Tool calls（模型发出的工具调用）" value={response.tool_calls ?? []} />
                    {response.reasoning_content ? (
                      <RawJsonBlock title="Reasoning content（模型内部推理）" value={response.reasoning_content} />
                    ) : null}
                    <RawJsonBlock title="LLM response 原始 JSON" value={response} />
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      </div>

      <RawJsonBlock title="最终消息（final_messages）" value={turn.final_messages} />
      <RawJsonBlock title="Turn Envelope 原始 JSON" value={turn} />
      <div className="text-[11px] text-settings-muted">
        文件：turns.jsonl{detail.files.tools ? " · tools.jsonl" : ""}{detail.files.cassette ? ` · ${detail.files.cassette}` : ""}
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
              {t("settings.enhancements.title", { defaultValue: "Record & Replay" })}
            </h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-settings-foreground">
              {t("settings.enhancements.explainer", {
                defaultValue:
                  "先真实运行一次 Agent，再用同一组模型响应和工具结果离线重跑。它用来判断：你修改 Agent 代码后，工具顺序、上下文处理和最终回答有没有回归。",
              })}
            </p>
            <div className="mt-4 grid gap-3 text-xs text-settings-foreground sm:grid-cols-3">
              <div className="rounded-xl bg-white/70 p-3 dark:bg-black/20">
                <div className="font-semibold">1. 录制真实执行</div>
                <div className="mt-1 text-settings-muted">模型和工具会正常工作。</div>
              </div>
              <div className="rounded-xl bg-white/70 p-3 dark:bg-black/20">
                <div className="font-semibold">2. 修改 Agent 代码</div>
                <div className="mt-1 text-settings-muted">例如修改 Tool、Context 或 Runner。</div>
              </div>
              <div className="rounded-xl bg-white/70 p-3 dark:bg-black/20">
                <div className="font-semibold">3. 离线回放比较</div>
                <div className="mt-1 text-settings-muted">不请求模型，不执行真实工具。</div>
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
                {status?.recording ? "正在录制当前对话" : "录制控制"}
              </h3>
              <p className="text-xs text-settings-muted">
                {status?.recording
                  ? "停止前，所有会话的每个 Agent 回合都会写入当前录制。"
                  : "点击开始后，再执行你想保留为回归样本的任务。"}
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
            当前录制：<span className="font-mono">{status.directory.split(/[\\/]/).pop()}</span>
          </div>
        ) : null}

        {status?.recording ? (
          <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-300">
            录制范围是这次“开始录制 → 停止录制”的时间段，不绑定某一个会话；你可以切换或新开会话，回合会追加到同一份录制。
          </div>
        ) : null}

        <div className="mb-4 flex items-center gap-2 rounded-lg border border-settings-border bg-settings-hover/40 px-3 py-2 text-xs text-settings-muted">
          <Info className="h-4 w-4 shrink-0" />
          回放只使用录制文件中的模型响应和工具结果；回放期间不会产生新的模型请求或真实工具副作用。
        </div>

        <div className="mb-3 flex flex-wrap items-center gap-2">
          <PauseCircle className="h-4 w-4 text-settings-muted" />
          <span className="text-xs text-settings-muted">断点回放（可选）：在第</span>
          <Input
            type="number"
            min={1}
            value={breakAt}
            onChange={(event) => setBreakAt(event.target.value)}
            placeholder="N"
            className="h-7 w-16"
          />
          <span className="text-xs text-settings-muted">轮暂停并查看消息</span>
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
                        {isReady ? `${recording.turns} 个回合可回放` : recording.message}
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
                      {breakAt ? "断点回放" : "开始回放"}
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
            还没有录制。点击“开始录制”，执行一次任务，再回来点击“停止录制”。
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
              <h3 className="text-base font-semibold text-settings-foreground">回放报告</h3>
              <p className="mt-1 text-sm text-settings-foreground">{replay.summary}</p>
              <p className="mt-1 text-xs text-settings-muted">
                这里检查的是 Agent 编排是否复现，不是模型回答质量评分。
              </p>
              <p className="mt-1 text-xs text-settings-muted">
                点击下面任意回合，可查看原始上下文、模型响应、Tool Call 参数、工具返回值和最终消息。
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
                  <span className="text-sm font-medium text-settings-foreground">第 {index + 1} 回合</span>
                  <span className="min-w-0 truncate font-mono text-xs text-settings-muted">{row.turn_id}</span>
                  <span className="ml-auto text-xs text-settings-muted">{row.summary}</span>
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
                        <div className="mb-2 text-xs font-medium text-settings-muted">可观察差异</div>
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
                      <ReplayTurnDetail
                        detail={detailByTurn[row.turn_id]}
                        onFullscreen={() => setFullscreenDetail(detailByTurn[row.turn_id])}
                      />
                    ) : detailLoading === row.turn_id ? (
                      <div className="border-t border-settings-border px-3 py-5 text-center text-xs text-settings-muted">
                        正在读取原始执行数据…
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
            断点命中 · Agent 循环 {breakpoint.iteration + 1}
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
        <DialogContent className="h-[calc(100vh-2rem)] max-w-[calc(100vw-2rem)] gap-0 overflow-hidden p-0">
          <DialogHeader className="shrink-0 border-b border-settings-border px-6 py-4 pr-14 text-left">
            <DialogTitle>回合原始执行详情</DialogTitle>
            <DialogDescription>
              全屏查看输入上下文、LLM Response Rail、Tool Observation Rail 和原始 JSON。
            </DialogDescription>
          </DialogHeader>
          <div className="min-h-0 overflow-y-auto">
            {fullscreenDetail ? <ReplayTurnDetail detail={fullscreenDetail} /> : null}
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
