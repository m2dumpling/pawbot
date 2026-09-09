import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { EnhancementsSettings } from "@/components/settings/EnhancementsSettings";
import type { BlackboxDetail } from "@/lib/api";
import { ClientProvider } from "@/providers/ClientProvider";
import {
  installSettingsViewTestHooks,
  requestMutationMock,
} from "@/tests/settings-test-utils";

const detail: BlackboxDetail = {
  directory: "/tmp/blackbox/demo",
  turn_id: "turn-1",
  turn: {
    kind: "turn",
    session_key: "websocket:test",
    model: "demo-model",
    initial_messages: [{ role: "user", content: "请检查示例文件" }],
    final_messages: [
      { role: "user", content: "请检查示例文件" },
      { role: "assistant", content: "", tool_calls: [{ name: "read_file" }] },
      { role: "tool", name: "read_file", content: "示例内容" },
      { role: "assistant", content: "已完成检查。" },
    ],
    final_content: "已完成检查。",
    stop_reason: "completed",
    tools: [{ name: "read_file", description: "读取示例文件" }],
  },
  events: [
    {
      kind: "llm",
      turn_id: "turn-1",
      iteration: 0,
      response: {
        content: "",
        tool_calls: [{ name: "read_file", arguments: { path: "demo.txt" } }],
        reasoning_content: "先读取样例文件，再根据结果回答。",
        finish_reason: "tool_calls",
      },
    },
    {
      kind: "tool",
      turn_id: "turn-1",
      iteration: 0,
      invocation_index: 0,
      name: "read_file",
      args: { path: "demo.txt" },
      status: "ok",
      result: "示例内容",
    },
    {
      kind: "llm",
      turn_id: "turn-1",
      iteration: 1,
      response: {
        content: "已完成检查。",
        tool_calls: [],
        reasoning_content: "根据读取结果整理最终回答。",
        finish_reason: "stop",
      },
    },
  ],
  counts: { llm_responses: 2, tool_calls: 1 },
  diagnostics: {
    stop_reason: "completed",
    failed_tools: [],
    unknown_side_effects: [],
    message: "本轮正常结束",
  },
  files: {
    turns: "turns.jsonl",
    tools: "tools.jsonl",
    cassette: null,
  },
};

describe("Record & Replay inspection", () => {
  installSettingsViewTestHooks();

  it("shows a readable execution trace and keeps raw JSON in the full-screen view", async () => {
    requestMutationMock.mockImplementation(async (action: string) => {
      if (action === "blackbox.status") {
        return {
          recording: false,
          directory: "",
          model: "demo-model",
          context_window_tokens: 128000,
          tool_count: 1,
        };
      }
      if (action === "blackbox.list") {
        return {
          root: "/tmp/blackbox",
          recordings: [{
            directory: "/tmp/blackbox/demo",
            name: "demo",
            turns: 1,
            status: "ready",
            message: "记录完整",
          }],
        };
      }
      if (action === "blackbox.tokens") {
        return {
          estimated_tokens: 100,
          context_window_tokens: 128000,
          usage_ratio: 0.01,
          message_count: 1,
          tool_count: 1,
          model: "demo-model",
        };
      }
      if (action === "blackbox.replay") {
        return {
          directory: "/tmp/blackbox/demo",
          total_turns: 1,
          deterministic_turns: 1,
          all_deterministic: true,
          summary: "1/1 个回合未发现可观察差异",
          results: [{
            turn_id: "turn-1",
            ok: true,
            diffs: [],
            summary: "未发现可观察差异",
          }],
        };
      }
      if (action === "blackbox.detail") return detail;
      throw new Error(`Unexpected mutation: ${action}`);
    });

    const user = userEvent.setup();
    render(
      <ClientProvider client={{ requestMutation: requestMutationMock } as never} token="tok">
        <EnhancementsSettings />
      </ClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "离线检查" }));
    const turnButton = await screen.findByRole("button", { name: /第 1 回合/ });
    await user.click(turnButton);

    expect(await screen.findByText("本回合执行过程")).toBeInTheDocument();
    expect(screen.getByText("请检查示例文件")).toBeInTheDocument();
    expect(screen.getAllByText("模型思考记录")).toHaveLength(2);
    expect(screen.getAllByText("工具执行").length).toBeGreaterThan(0);
    expect(screen.getByText("示例内容")).toBeInTheDocument();
    expect(screen.queryByText("LLM Response Rail")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "查看原始记录" }));
    expect(await screen.findByText("完整原始记录")).toBeInTheDocument();
    expect(screen.getByText("回合总记录")).toBeInTheDocument();
    expect(screen.getByText("执行过程原始记录")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("turns.jsonl · kind=turn")).toBeInTheDocument());
  });
});
