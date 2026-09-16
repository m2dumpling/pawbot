# Agent Harness Benchmark

Pawbot 的 Agent Harness 是一组不依赖真实 Provider 的确定性回归场景。
它不模拟一套新的 Agent，而是用脚本化的模型响应和内存 Tool 驱动真实的
`AgentRunner`，检查 Agent 在成功、失败和边界条件下是否仍然遵守既定行为。
其中包含两个模拟真实工作流的任务级 Fixture：写入后回读校验，以及调查记录后总结。

## 为什么需要 Harness

静态检查只能说明代码能够通过语法、类型和风格检查；单次手工对话也很难稳定复现
失败。Harness 把“模型给出什么响应、Agent 应该怎样继续、最后应该得到什么结果”
固定下来，因此 AI 修改 Agent Loop 后可以在本地和 CI 重复验证。

它与 Record & Replay 的分工不同：

| 能力 | 解决的问题 |
| --- | --- |
| Trace | 线上或本地某一次执行卡在哪里、耗时多少、哪一步失败 |
| Record & Replay | 保存真实执行现场，修改代码后离线比较编排轨迹 |
| Agent Harness | 用固定场景批量检查 Agent 的成功、恢复、错误、任务结果和预算边界 |

Harness 同时输出两类结果：

- `trajectory`：模型请求次数、Tool 顺序、Tool 状态、stop reason 等执行契约；
- `task`：最终回答是否存在、内容断言是否满足、要求的成功 Tool 是否真的完成。

因此，“轨迹一致”不会再被误读成“任务成功”。Provider 错误、超时、取消等场景会
显示为 `task: not_evaluable`；执行完成但结果断言不满足时会显示为 `task: failed`。
这为后续接入领域评测器保留了清晰边界。

## 运行时任务验收

Harness 使用的 TaskContract 已经是 AgentRunner 的公共运行时能力。SDK 调用方可以
把完成条件随一次运行传入；Runner 会在模型准备结束时检查最终文本、成功 Tool、
Tool 返回内容、Workspace 文件或自定义同步校验器。验收失败会回填具体失败项，
让模型在剩余预算内继续修正：

~~~python
from pawbot import TaskContract

result = await bot.run(
    "写入并验证结果",
    task_contract=TaskContract(
        id="write-and-verify",
        required_tools=("write_file", "read_file"),
        required_files=("result.txt",),
        file_contains=(("result.txt", "ready"),),
    ),
)
assert result.task_evaluation is not None
~~~

任务结果只会有三种含义：passed 表示条件全部满足，failed 表示执行完成但条件
没有满足，not_evaluable 表示执行被取消、模型/运行时失败，或校验器本身无法运行。
它与轨迹是否复现、原执行是否有工具错误是三条独立信息。

## 运行

列出场景：

```bash
uv run --no-sync pawbot harness list
```

运行完整基准：

```bash
uv run --no-sync pawbot harness run
```

运行单个场景或多个场景：

```bash
uv run --no-sync pawbot harness run --case tool-failure-recovery
uv run --no-sync pawbot harness run --case provider-error --case llm-timeout
```

在 CI 或脚本中使用 JSON：

```bash
uv run --no-sync pawbot harness run --json
```

## 当前场景

| 场景 | 检查内容 |
| --- | --- |
| `basic-tool-call` | 模型选择 Tool，Tool 返回后 Agent 生成最终回答 |
| `tool-failure-recovery` | 第一个 Tool 失败，错误回到上下文，Agent 使用备用 Tool 恢复 |
| `provider-error` | Provider 返回结构化错误，回合以可识别的错误结束 |
| `llm-timeout` | 慢 Provider 被请求超时边界停止，不伪装成成功 |
| `cancelled-turn` | 用户取消进行中的请求，回合状态为 cancelled |
| `turn-budget` | Tool 数量超过预算时，在真实 Tool 执行前阻断 |
| `workspace-change-and-verify` | 模拟写入文件后回读并校验内容的完整任务链 |
| `investigate-and-summarize` | 模拟检索事件、查看详情并形成结论的完整任务链 |
| `approval-gated-tool` | 写入型 Tool 在人工批准前不会开始执行 |

每个结果都会给出：

- 结果是否满足场景契约；
- 实际执行状态和 stop reason；
- 模型请求次数；
- Tool 尝试次数、失败次数、名称和状态；
- 总耗时和可读的失败原因。

报告还会给出汇总指标：轨迹通过数、可评测任务数、任务通过率、模型请求数、Tool
尝试数和 Tool 失败数。任务通过率只在 `task` 可评测的场景中计算；Provider 错误、
超时、取消和预算阻断显示为 `not_evaluable`，不会被误算成失败或成功。

## 人工确认

`AgentRunSpec` 支持注入 `tool_approval_callback`。当 Tool 声明为非只读，且拥有
`write`、`execute` 或 `network` 能力时，Runner 会在真正执行前发出
`ToolApprovalRequest`，等待 `ToolApprovalResult.approve()` 或
`ToolApprovalResult.deny()`：

```text
模型计划 Tool
→ 参数校验和能力判断
→ 等待人工确认
→ 批准：执行 Tool
→ 拒绝/超时：返回模型可见错误，side_effect=not_started
```

`ToolApprovalManager` 是一个与 WebUI、CLI 或其他客户端无关的协调器。客户端负责
展示请求并用 request ID 回传决定；未知 ID、通知失败、超时都会拒绝执行。Trace 会
记录请求和决定，但只记录参数名，不复制原始参数值。

在 Gateway 的 WebUI 工作区访问模式下，受限工作区会显示批准卡片；完全访问模式仍
表示用户已经明确授予本地工具权限，不额外拦截。原生 TUI 使用同一条协议，按 `a`
批准、`d` 或 `Esc` 拒绝，`Ctrl+C` 仍然可以取消整个回合。

Tool 还可以通过 execution_policy 声明副作用类型、幂等性、是否可撤销、恢复策略
和执行凭证读取方式。只读或明确幂等的操作可以自动重试；取消、超时或异常返回后，
未知或不可逆操作会保留操作指纹并在再次执行相同操作前要求人工确认。系统不会把
进程取消当成外部系统已回滚。

## 实验元数据

每份 Harness JSON 报告包含实验 ID、pawbot 版本、Git revision、工作区是否有已跟踪
改动、Python/平台/架构、Benchmark 版本，以及“脚本 Provider、禁用网络、不读写
Workspace”的运行声明。真实 Record & Replay 的 `meta.json` 也保存同类版本和实验
信息，便于解释一次回放差异来自代码、环境还是样本。

## AI 修改后的质量门禁

开发者可以用一条命令执行和 CI 相同的快速门禁：

```bash
uv run --no-sync python scripts/quality_gate.py
```

门禁依次检查工作区空白错误、Ruff、basedpyright、Agent Harness、Agent/评测/审批
契约测试和公开 Record & Replay Fixture。任一检查失败都会返回非零退出码。需要在本地
连同完整 Python 测试一起检查时使用：

```bash
uv run --no-sync python scripts/quality_gate.py --full
```

这套门禁回答的是“这次代码修改还能不能进入主线”，不是“模型回答得好不好”。当前
Harness 已把简单任务契约和两个任务 Fixture 纳入门禁；更复杂的领域质量评测仍需要
单独建设评测集和评估器。
