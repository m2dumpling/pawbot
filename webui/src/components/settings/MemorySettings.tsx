import { useCallback, useEffect, useState } from "react";
import { BookmarkPlus, RefreshCw, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  memoryForget,
  memoryList,
  memoryPromote,
  memoryReject,
  memoryRemember,
} from "@/lib/api";
import type {
  ExplicitMemoryRecord,
  ExplicitMemoryScope,
} from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

export function MemorySettings() {
  const { t } = useTranslation();
  const { client } = useClient();
  const [memories, setMemories] = useState<ExplicitMemoryRecord[]>([]);
  const [view, setView] = useState<"confirmed" | "candidate">("confirmed");
  const [scope, setScope] = useState<ExplicitMemoryScope>("global");
  const [key, setKey] = useState("reply_language");
  const [value, setValue] = useState("zh-CN");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setMemories((await memoryList(client, { status: view })).memories);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [client, view]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function saveMemory() {
    if (!key.trim() || !value.trim()) return;
    setBusy("save");
    setError(null);
    try {
      await memoryRemember(client, {
        scope,
        kind: "preference",
        key: key.trim(),
        value: value.trim(),
      });
      setValue("");
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function removeMemory(record: ExplicitMemoryRecord) {
    if (!window.confirm(t("settings.memory.forgetConfirm", {
      defaultValue: "Forget {{key}}?",
      key: record.key,
    }))) return;
    setBusy(`forget:${record.memory_id}`);
    setError(null);
    try {
      await memoryForget(client, record.memory_id);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function updateCandidate(record: ExplicitMemoryRecord, promote: boolean) {
    setBusy(`candidate:${record.memory_id}`);
    setError(null);
    try {
      if (promote) await memoryPromote(client, record.memory_id);
      else await memoryReject(client, record.memory_id);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="rounded-2xl border border-settings-border bg-settings-surface p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-settings-foreground">
            <BookmarkPlus className="h-5 w-5 text-violet-600" />
            <h2 className="text-lg font-semibold">
              {t("settings.memory.title", { defaultValue: "Confirmed memory" })}
            </h2>
          </div>
          <p className="mt-1 text-sm text-settings-muted">
            {t("settings.memory.description", {
              defaultValue: "Save explicit preferences immediately. These records do not depend on Dream or conversation compaction.",
            })}
          </p>
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={() => void refresh()} disabled={busy !== null}>
          <RefreshCw className="mr-2 h-4 w-4" />
          {t("settings.memory.refresh", { defaultValue: "Refresh" })}
        </Button>
      </div>

      <div className="mt-4 flex gap-2 border-b border-settings-border">
        <Button
          type="button"
          variant={view === "confirmed" ? "secondary" : "ghost"}
          size="sm"
          onClick={() => setView("confirmed")}
        >
          {t("settings.memory.confirmed", { defaultValue: "Confirmed" })}
        </Button>
        <Button
          type="button"
          variant={view === "candidate" ? "secondary" : "ghost"}
          size="sm"
          onClick={() => setView("candidate")}
        >
          {t("settings.memory.candidates", { defaultValue: "Suggestions" })}
        </Button>
      </div>

      <div className="mt-4 grid gap-2 md:grid-cols-[150px_180px_1fr_auto]">
        <select
          value={scope}
          onChange={(event) => setScope(event.target.value as ExplicitMemoryScope)}
          className="h-10 rounded-md border border-settings-border bg-settings-background px-3 text-sm text-settings-foreground"
          aria-label={t("settings.memory.scope", { defaultValue: "Memory scope" })}
        >
          <option value="global">{t("settings.memory.global", { defaultValue: "Global" })}</option>
          <option value="workspace">{t("settings.memory.workspace", { defaultValue: "This workspace" })}</option>
        </select>
        <Input
          value={key}
          onChange={(event) => setKey(event.target.value)}
          placeholder={t("settings.memory.key", { defaultValue: "Memory key" })}
          aria-label={t("settings.memory.key", { defaultValue: "Memory key" })}
        />
        <Input
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder={t("settings.memory.value", { defaultValue: "Memory value" })}
          aria-label={t("settings.memory.value", { defaultValue: "Memory value" })}
        />
        <Button type="button" onClick={() => void saveMemory()} disabled={busy !== null || !key.trim() || !value.trim()}>
          {t("settings.memory.save", { defaultValue: "Save" })}
        </Button>
      </div>

      {error ? <p className="mt-3 text-sm text-red-600">{error}</p> : null}

      <div className="mt-4 space-y-2">
        {memories.length === 0 ? (
          <p className="rounded-lg border border-dashed border-settings-border p-4 text-sm text-settings-muted">
            {t("settings.memory.empty", { defaultValue: "No confirmed memories yet." })}
          </p>
        ) : memories.map((record) => (
          <div key={record.memory_id} className="flex items-center justify-between gap-3 rounded-lg border border-settings-border p-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium text-settings-foreground">
                {record.key} = {String(record.value)}
              </div>
              <div className="text-xs text-settings-muted">
                {record.scope} · {record.source} · {record.memory_id}
              </div>
              {view === "candidate" ? (
                <div className="mt-1 text-xs text-settings-muted">
                  {record.confidence != null ? `confidence ${record.confidence.toFixed(2)}` : ""}
                  {record.evidence ? ` · ${record.evidence}` : ""}
                </div>
              ) : null}
            </div>
            {view === "candidate" ? (
              <div className="flex gap-1">
                <Button type="button" size="sm" onClick={() => void updateCandidate(record, true)} disabled={busy !== null}>
                  {t("settings.memory.confirm", { defaultValue: "Save" })}
                </Button>
                <Button type="button" variant="ghost" size="sm" onClick={() => void updateCandidate(record, false)} disabled={busy !== null}>
                  {t("settings.memory.ignore", { defaultValue: "Ignore" })}
                </Button>
              </div>
            ) : (
              <Button type="button" variant="ghost" size="sm" onClick={() => void removeMemory(record)} disabled={busy !== null}>
                <Trash2 className="h-4 w-4" />
                <span className="sr-only">{t("settings.memory.forget", { defaultValue: "Forget" })}</span>
              </Button>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
