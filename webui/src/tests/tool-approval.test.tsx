import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ToolApprovalNotice } from "@/components/thread/ToolApprovalNotice";
import type { ToolApprovalRequest } from "@/lib/types";

const REQUEST: ToolApprovalRequest = {
  request_id: "approval-1",
  call_id: "call-1",
  name: "write_file",
  arguments: { path: "notes.txt", content: "hello" },
  capabilities: ["write"],
  session_key: "websocket:chat",
  iteration: 0,
  created_at_ms: 1_700,
  channel: "websocket",
  chat_id: "chat",
};

describe("ToolApprovalNotice", () => {
  it("explains the pending side effect and submits an approval", async () => {
    const onResolve = vi.fn().mockResolvedValue(undefined);

    render(<ToolApprovalNotice requests={[REQUEST]} onResolve={onResolve} />);

    expect(screen.getByRole("alert")).toHaveTextContent("write_file");
    expect(screen.getByRole("alert")).toHaveTextContent("The Tool has not run yet");
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(onResolve).toHaveBeenCalledWith("approval-1", "approved"));
  });

  it("offers a separate deny action", async () => {
    const onResolve = vi.fn().mockResolvedValue(undefined);

    render(<ToolApprovalNotice requests={[REQUEST]} onResolve={onResolve} />);
    fireEvent.click(screen.getByRole("button", { name: "Deny" }));

    await waitFor(() => expect(onResolve).toHaveBeenCalledWith("approval-1", "denied"));
  });
});
