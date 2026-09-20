import { useCallback, useEffect, useState } from "react";
import { BookOpen, RefreshCw, RotateCcw, Save, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  memoryClear,
  personalizationClear,
  personalizationGet,
  personalizationUpdate,
} from "@/lib/api";
import type { PersonalizationPayload } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";
import { MemorySettings } from "@/components/settings/MemorySettings";

export function PersonalizationSettings() {
  const { t } = useTranslation();
  const { client } = useClient();
  const [state, setState] = useState<PersonalizationPayload | null>(null);
  const [instructions, setInstructions] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const next = await personalizationGet(client);
      setState(next);
      setInstructions(next.instructions);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, [client]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function saveInstructions() {
    setBusy("instructions");
    setError(null);
    try {
      setState(await personalizationUpdate(client, { instructions }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function updateFlag(
    key: "enabled" | "use_memories" | "generate_memories",
    value: boolean,
  ) {
    setBusy(key);
    setError(null);
    try {
      setState(await personalizationUpdate(client, { [key]: value }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function clearInstructions() {
    if (!window.confirm(t("settings.personalization.clearInstructionsConfirm", {
      defaultValue: "Clear your personal instructions?",
    }))) return;
    setBusy("clear-instructions");
    setError(null);
    try {
      const next = await personalizationClear(client);
      setState(next);
      setInstructions(next.instructions);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function clearMemories() {
    if (!window.confirm(t("settings.personalization.clearMemoriesConfirm", {
      defaultValue: "Delete all saved memories? This cannot be undone from the UI.",
    }))) return;
    setBusy("clear-memories");
    setError(null);
    try {
      await memoryClear(client);
      window.dispatchEvent(new CustomEvent("pawbot:memory-cleared"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  const maxChars = state?.max_instructions_chars ?? 16_000;
  const disabled = loading || busy !== null;

  return (
    <div className="flex flex-col gap-6 p-6">
      <section className="rounded-2xl border border-settings-border bg-settings-surface p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-settings-foreground">
              <BookOpen className="h-5 w-5 text-violet-600" />
              <h2 className="text-lg font-semibold">
                {t("settings.personalization.title", { defaultValue: "Personalization" })}
              </h2>
            </div>
            <p className="mt-1 max-w-3xl text-sm text-settings-muted">
              {t("settings.personalization.description", {
                defaultValue: "Tell Pawbot how you prefer to communicate and work. These instructions apply across chats.",
              })}
            </p>
          </div>
          <Button type="button" variant="ghost" size="sm" onClick={() => void refresh()} disabled={disabled}>
            <RefreshCw className="mr-2 h-4 w-4" />
            {t("settings.personalization.refresh", { defaultValue: "Refresh" })}
          </Button>
        </div>

        <Textarea
          className="mt-5 min-h-48 resize-y text-sm leading-6"
          value={instructions}
          onChange={(event) => setInstructions(event.target.value)}
          placeholder={t("settings.personalization.placeholder", {
            defaultValue: "Example: Always respond in Simplified Chinese. Keep answers concise and explain code changes before editing.",
          })}
          maxLength={maxChars}
          aria-label={t("settings.personalization.instructions", {
            defaultValue: "Personal instructions",
          })}
          disabled={loading || busy !== null}
        />
        <div className="mt-2 flex items-center justify-between gap-3 text-xs text-settings-muted">
          <span>
            {t("settings.personalization.instructionsHint", {
              defaultValue: "Use this for stable preferences, not API keys or project-only rules.",
            })}
          </span>
          <span>{instructions.length}/{maxChars}</span>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button type="button" onClick={() => void saveInstructions()} disabled={disabled}>
            <Save className="mr-2 h-4 w-4" />
            {t("settings.personalization.save", { defaultValue: "Save instructions" })}
          </Button>
          <Button type="button" variant="ghost" onClick={() => void clearInstructions()} disabled={disabled}>
            <RotateCcw className="mr-2 h-4 w-4" />
            {t("settings.personalization.clearInstructions", { defaultValue: "Clear instructions" })}
          </Button>
        </div>
      </section>

      <section className="rounded-2xl border border-settings-border bg-settings-surface p-5">
        <h3 className="text-base font-semibold text-settings-foreground">
          {t("settings.personalization.memoryTitle", { defaultValue: "Memory" })}
        </h3>
        <p className="mt-1 text-sm text-settings-muted">
          {t("settings.personalization.memoryDescription", {
            defaultValue: "Memory is separate from your personal instructions. Review suggestions before they become durable preferences.",
          })}
        </p>
        <div className="mt-4 divide-y divide-settings-border rounded-xl border border-settings-border">
          {([
            ["enabled", "settings.personalization.enable", "Enable personalization and saved memory", state?.enabled ?? true],
            ["use_memories", "settings.personalization.useMemories", "Use confirmed memories in new turns", state?.use_memories ?? true],
            ["generate_memories", "settings.personalization.generateMemories", "Allow background memory candidates", state?.generate_memories ?? true],
          ] as const).map(([key, labelKey, fallback, checked]) => (
            <label key={key} className="flex cursor-pointer items-center justify-between gap-4 p-4 text-sm text-settings-foreground">
              <span>{t(labelKey, { defaultValue: fallback })}</span>
              <input
                type="checkbox"
                checked={checked}
                onChange={(event) => void updateFlag(key, event.target.checked)}
                disabled={disabled}
                className="h-4 w-4 accent-violet-600"
              />
            </label>
          ))}
        </div>
        <div className="mt-4 flex items-center justify-between gap-3 rounded-xl border border-red-200 bg-red-50/60 p-4 dark:border-red-900 dark:bg-red-950/20">
          <div>
            <div className="text-sm font-medium text-settings-foreground">
              {t("settings.personalization.deleteMemories", { defaultValue: "Delete saved memories" })}
            </div>
            <div className="mt-1 text-xs text-settings-muted">
              {t("settings.personalization.deleteMemoriesHint", { defaultValue: "This removes confirmed records and candidates while keeping an audit tombstone." })}
            </div>
          </div>
          <Button type="button" variant="ghost" onClick={() => void clearMemories()} disabled={disabled}>
            <Trash2 className="mr-2 h-4 w-4" />
            {t("settings.personalization.delete", { defaultValue: "Delete" })}
          </Button>
        </div>
      </section>

      {error ? <p className="text-sm text-red-600">{error}</p> : null}

      <MemorySettings />
    </div>
  );
}
