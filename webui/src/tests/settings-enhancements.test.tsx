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
    provider_errors: [],
    unknown_side_effects: [],
    original_execution: {
      status: "success",
      ok: true,
      failed_tool_count: 0,
      provider_error_count: 0,
      unknown_side_effect_count: 0,
    },
    message: "本轮正常结束",
  },
  files: {
    turns: "turns.jsonl",
    tools: "tools.jsonl",
    cassette: null,
  },
};

const providerErrorDetail: BlackboxDetail = {
  ...detail,
  turn_id: "turn-provider-error",
  turn: {
    ...detail.turn,
    stop_reason: "error",
  },
  events: [{
    kind: "llm",
    turn_id: "turn-provider-error",
    iteration: 0,
    response: {
      content: "upstream overloaded",
      tool_calls: [],
      finish_reason: "error",
      error_status_code: 503,
      error_kind: "http",
      error_type: "server_error",
      error_code: "overloaded",
    },
  }],
  counts: { llm_responses: 1, tool_calls: 0 },
  diagnostics: {
    stop_reason: "error",
    failed_tools: [],
    provider_errors: [{
      status_code: 503,
      kind: "http",
      type: "server_error",
      code: "overloaded",
    }],
    unknown_side_effects: [],
    original_execution: {
      status: "model_error",
      ok: false,
      failed_tool_count: 0,
      provider_error_count: 1,
      unknown_side_effect_count: 0,
    },
    message: "原始执行包含异常，请分别查看回放结果和原始执行状态",
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
          original_issue_turns: 0,
          original_failed_tool_calls: 0,
          original_provider_errors: 0,
          original_unknown_side_effects: 0,
          summary: "1/1 个回合未发现可观察差异",
          results: [{
            turn_id: "turn-1",
            ok: true,
            diffs: [],
            summary: "未发现可观察差异",
            original_execution: {
              status: "success",
              ok: true,
              failed_tool_count: 0,
              provider_error_count: 0,
              unknown_side_effect_count: 0,
            },
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

    await user.click(await screen.findByRole("button", { name: "Replay offline" }));
    const turnButton = await screen.findByRole("button", { name: /Turn 1/ });
    await user.click(turnButton);

    expect(await screen.findByText("Turn execution")).toBeInTheDocument();
    expect(screen.getAllByText("Replay result · Consistent").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Original execution · Completed without recorded errors").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("请检查示例文件")).toBeInTheDocument();
    expect(screen.getAllByText("Model thinking trace")).toHaveLength(2);
    expect(screen.getAllByText("Tool execution").length).toBeGreaterThan(0);
    expect(screen.getByText("示例内容")).toBeInTheDocument();
    expect(screen.queryByText("LLM Response Rail")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "View raw record" }));
    expect(await screen.findByText("Complete raw record")).toBeInTheDocument();
    expect(screen.getByText("Turn envelope")).toBeInTheDocument();
    expect(screen.getByText("Raw execution events")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("turns.jsonl · kind=turn")).toBeInTheDocument());
  });

  it("surfaces provider failures separately from a consistent replay", async () => {
    requestMutationMock.mockImplementation(async (action: string) => {
      if (action === "blackbox.status") {
        return {
          recording: false,
          directory: "",
          model: "demo-model",
          context_window_tokens: 128000,
          tool_count: 0,
        };
      }
      if (action === "blackbox.list") {
        return {
          root: "/tmp/blackbox",
          recordings: [{
            directory: "/tmp/blackbox/provider-error",
            name: "provider-error",
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
          tool_count: 0,
          model: "demo-model",
        };
      }
      if (action === "blackbox.replay") {
        return {
          directory: "/tmp/blackbox/provider-error",
          total_turns: 1,
          deterministic_turns: 1,
          all_deterministic: true,
          original_issue_turns: 1,
          original_failed_tool_calls: 0,
          original_provider_errors: 1,
          original_unknown_side_effects: 0,
          summary: "1/1 个回合回放一致；1 个回合原始执行包含问题",
          results: [{
            turn_id: "turn-provider-error",
            ok: true,
            diffs: [],
            summary: "未发现可观察差异",
            original_execution: {
              status: "model_error",
              ok: false,
              failed_tool_count: 0,
              provider_error_count: 1,
              unknown_side_effect_count: 0,
            },
          }],
        };
      }
      if (action === "blackbox.detail") return providerErrorDetail;
      throw new Error(`Unexpected mutation: ${action}`);
    });

    const user = userEvent.setup();
    render(
      <ClientProvider client={{ requestMutation: requestMutationMock } as never} token="tok">
        <EnhancementsSettings />
      </ClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "Replay offline" }));
    await user.click(await screen.findByRole("button", { name: /Turn 1/ }));

    expect(screen.getAllByText("Replay result · Consistent").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Original execution · Model request failed").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Model request failed")).toBeInTheDocument();
    expect(screen.getByText("HTTP 503 · kind=http · type=server_error · code=overloaded")).toBeInTheDocument();
  });
});
