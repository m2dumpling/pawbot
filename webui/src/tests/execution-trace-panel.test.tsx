import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ExecutionTracePanel } from "@/components/thread/ExecutionTracePanel";
import type { ExecutionTraceEvent } from "@/lib/types";
import { setAppLanguage } from "@/i18n";
import { ClientProvider } from "@/providers/ClientProvider";

describe("ExecutionTracePanel", () => {
  const handlers = new Set<(chatId: string, event: ExecutionTraceEvent) => void>();
  const requestMutation = vi.fn();
  const summary = {
    id: "websocket_chat-1/turn-1.jsonl",
    trace_id: "trace:turn-1",
    session_key: "websocket:chat-1",
    session_name: "Los Angeles weather",
    turn_id: "turn-1",
    channel: "websocket",
    chat_id: "chat-1",
    model: "demo-model",
    provider: "demo-provider",
    status: "completed",
    stop_reason: "completed",
    duration_ms: 420,
    event_count: 3,
    tool_count: 1,
    failure_count: 0,
  };
  const events: ExecutionTraceEvent[] = [
    {
      event: "stage.completed",
      trace_id: "trace:turn-1",
      session_key: "websocket:chat-1",
      turn_id: "turn-1",
      sequence: 1,
      stage: "build",
      status: "completed",
      duration_ms: 20,
      initial_message_count: 4,
      history_message_count: 3,
    },
    {
      event: "llm.response",
      trace_id: "trace:turn-1",
      session_key: "websocket:chat-1",
      turn_id: "turn-1",
      sequence: 2,
      iteration: 0,
      status: "received",
      content_preview: "The answer is ready.",
      content_chars: 20,
    },
    {
      event: "turn.completed",
      trace_id: "trace:turn-1",
      session_key: "websocket:chat-1",
      turn_id: "turn-1",
      sequence: 3,
      status: "completed",
      stop_reason: "completed",
      duration_ms: 420,
    },
  ];

  beforeEach(async () => {
    await setAppLanguage("en");
    handlers.clear();
    requestMutation.mockReset().mockImplementation(async (action: string) => {
      if (action === "trace.list") return { traces: [summary], root: "/tmp/traces" };
      if (action === "trace.detail") return { summary, events };
      throw new Error(`Unexpected mutation: ${action}`);
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("loads the latest run and exposes useful event details", async () => {
    const user = userEvent.setup();
    const client = {
      requestMutation,
      onTrace: (handler: (chatId: string, event: ExecutionTraceEvent) => void) => {
        handlers.add(handler);
        return () => handlers.delete(handler);
      },
    };

    render(
      <ClientProvider client={client as never} token="tok">
        <ExecutionTracePanel chatId="chat-1" sessionKey="websocket:chat-1" title="Los Angeles weather" />
      </ClientProvider>,
    );

    await user.click(screen.getByRole("button", { name: "View execution trace" }));
    expect(await screen.findByText("Run 1")).toBeInTheDocument();
    expect(await screen.findByText(/Stage · build context/)).toBeInTheDocument();
    expect(requestMutation).toHaveBeenCalledWith(
      "trace.list",
      expect.objectContaining({ session_key: "websocket:chat-1", chat_id: "chat-1" }),
      expect.any(Number),
    );

    await user.click(screen.getByText(/Stage · build context/));
    expect((await screen.findAllByText("Event type")).length).toBeGreaterThan(0);
    expect(screen.getByText("Initial messages")).toBeInTheDocument();
    expect(screen.getByText("build")).toBeInTheDocument();

    await act(async () => {
      for (const handler of handlers) {
        handler("chat-1", {
          event: "tool.finished",
          trace_id: "trace:turn-1",
          session_key: "websocket:chat-1",
          turn_id: "turn-1",
          sequence: 4,
          iteration: 0,
          status: "succeeded",
          tool_name: "read_file",
          result_preview: "sample content",
        });
      }
    });
    await waitFor(() => expect(screen.getByText(/Tool result · read_file/)).toBeInTheDocument());
    expect(screen.getByText("sample content")).toBeInTheDocument();
  });
});
