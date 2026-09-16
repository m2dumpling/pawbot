import * as React from "react";
import { Check, ShieldAlert, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import type { ToolApprovalRequest } from "@/lib/types";

interface ToolApprovalNoticeProps {
  requests: readonly ToolApprovalRequest[];
  onResolve: (requestId: string, decision: "approved" | "denied") => Promise<void>;
}

export function ToolApprovalNotice({ requests, onResolve }: ToolApprovalNoticeProps) {
  const { t } = useTranslation();
  if (requests.length === 0) return null;

  return (
    <div
      role="alert"
      aria-live="assertive"
      className="mx-auto mb-2 w-full max-w-[49.5rem] rounded-control border border-amber-300/80 bg-amber-50/80 px-3 py-3 text-sm dark:border-amber-500/40 dark:bg-amber-950/20"
    >
      {requests.map((request) => (
        <ApprovalRow key={request.request_id} request={request} onResolve={onResolve} />
      ))}
      <p className="mt-2 text-xs text-muted-foreground">
        {t("toolApproval.sideEffectNote", {
          defaultValue: "The Tool has not run yet. Approve only if this action and its parameters are expected.",
        })}
      </p>
    </div>
  );
}

function ApprovalRow({
  request,
  onResolve,
}: {
  request: ToolApprovalRequest;
  onResolve: ToolApprovalNoticeProps["onResolve"];
}) {
  const { t } = useTranslation();
  const [pending, setPending] = React.useState<"approved" | "denied" | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const parameters = JSON.stringify(request.arguments, null, 2);

  const resolve = (decision: "approved" | "denied") => {
    setPending(decision);
    setError(null);
    void onResolve(request.request_id, decision)
      .catch(() => {
        setError(t("toolApproval.actionFailed", { defaultValue: "Could not submit this decision. Try again." }));
      })
      .finally(() => setPending(null));
  };

  return (
    <div className="flex flex-col gap-2 border-b border-amber-200/80 py-1.5 last:border-b-0 dark:border-amber-500/20">
      <div className="flex items-start gap-2">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="font-medium">
            {t("toolApproval.title", { defaultValue: "Approval needed before the Agent runs a Tool" })}
          </p>
          <p className="mt-0.5 break-words text-xs text-muted-foreground">
            <span className="font-mono text-foreground">{request.name}</span>
            {request.capabilities.length > 0 ? ` · ${request.capabilities.join(", ")}` : ""}
          </p>
          {request.recovery_required ? (
            <p className="mt-1 rounded-md border border-amber-300/70 bg-amber-100/55 px-2 py-1 text-xs text-amber-900 dark:border-amber-500/30 dark:bg-amber-950/25 dark:text-amber-200">
              {t("toolApproval.recoveryWarning", {
                defaultValue: "This operation was interrupted. It may already have taken effect; confirm before retrying.",
              })}
              {request.recovery_reason ? request.recovery_reason : ""}
            </p>
          ) : null}
          <pre className="mt-2 max-h-32 overflow-auto rounded-md bg-background/80 p-2 font-mono text-[11px] leading-4 text-foreground">
            {parameters}
          </pre>
          {error ? <p className="mt-1 text-xs text-destructive">{error}</p> : null}
        </div>
        <div className="flex shrink-0 gap-1.5">
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={pending !== null}
            onClick={() => resolve("denied")}
          >
            <X className="mr-1 h-3.5 w-3.5" aria-hidden />
            {t("toolApproval.deny", { defaultValue: "Deny" })}
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={pending !== null}
            onClick={() => resolve("approved")}
          >
            <Check className="mr-1 h-3.5 w-3.5" aria-hidden />
            {t("toolApproval.approve", { defaultValue: "Approve" })}
          </Button>
        </div>
      </div>
    </div>
  );
}
