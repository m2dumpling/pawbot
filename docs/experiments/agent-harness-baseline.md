# Agent Harness 任务评测基线

## 结论

这份基线用于回答一个具体问题：Agent 代码被修改后，执行路径是否仍然遵守契约，
以及在可以评测的任务中，最终结果是否达到最低要求。

它不是模型排行榜，也不是对自然语言回答质量的通用评分。测试使用脚本 Provider 和
内存 Tool 驱动真实 `AgentRunner`，网络关闭，不读取或写入用户 Workspace。

## 为什么要有任务 Fixture

只检查“模型调用顺序没有变化”不够。一个 Agent 可能按原来的轨迹运行，却没有写对
内容、没有完成校验，或者总结了错误对象。因此基线把两种证据分开：

- `trajectory`：模型请求、Tool 顺序、Tool 状态、stop reason 等编排行为；
- `task`：最终内容、必要的成功 Tool 和正常完成状态。

异常边界仍然有价值，但 Provider 错误、超时、取消和预算阻断没有可评价的最终任务，
会明确记为 `task: not_evaluable`，而不是伪造一个任务分数。

## 当前 Fixture

| Fixture | 模拟的工作流 | 关键断言 |
| --- | --- | --- |
| `basic-tool-call` | 使用一个工具完成简单请求 | Tool 成功，最终回答正确 |
| `tool-failure-recovery` | 首个查询失败后切换备用工具 | 错误回填，备用工具成功 |
| `workspace-change-and-verify` | 写入任务工作区后回读校验 | 写入和回读都成功，回答包含校验结论 |
| `investigate-and-summarize` | 检索事件、查看详情、总结结论 | 两个调查工具成功，回答包含事件和状态 |
| `approval-gated-tool` | 高风险写入等待用户批准 | 批准前不执行，批准后完成任务 |
| `provider-error` | 模型服务返回结构化错误 | 错误类型可识别，任务不评测 |
| `llm-timeout` | 模型请求超过时间边界 | 受控超时，任务不评测 |
| `cancelled-turn` | 用户取消进行中的回合 | 回合为 cancelled，任务不评测 |
| `turn-budget` | Tool 数量超过回合预算 | 下一次操作被阻断，任务不评测 |

两个任务 Fixture 的“工作区”和“事件记录”都是内存中的确定性数据。它们验证 Agent
的多步编排和任务契约，不代表真实文件系统或生产事件平台已经被端到端验证。

## 如何复现

运行完整报告：

```bash
uv run --no-sync pawbot harness run --json
```

运行一个任务 Fixture：

```bash
uv run --no-sync pawbot harness run --case workspace-change-and-verify --json
uv run --no-sync pawbot harness run --case investigate-and-summarize --json
```

AI 修改后执行和 CI 相同的门禁：

```bash
uv run --no-sync python scripts/quality_gate.py
```

## 结果口径

基线报告应至少记录：

- `trajectory_passed / total`；
- `task_passed / task_evaluable`；
- `task_failed` 和 `task_not_evaluable`；
- 模型请求数、Tool 尝试数和 Tool 失败数；
- pawbot 版本、Git revision、平台、实验 ID 和工作树状态。

当前 9 个内置场景的预期结构是：9/9 条轨迹通过，5 个任务可评测且通过，4 个异常
边界不评测任务结果。性能耗时只用于观察本机回归，不作为跨机器的绝对指标。

## 局限与后续

这套基线能证明代码在固定行为契约下没有明显回归，不能证明：

- 真实模型回答质量普遍提高；
- 任意 Provider、MCP server 或 Tool 组合都能工作；
- 真实外部系统的副作用一定成功或 exactly-once；
- 领域任务的事实性、偏好或业务收益已经达标。

下一步如果有真实业务数据，应在脱敏后增加领域 Fixture 和独立评估器，并保留同样的
`trajectory`/`task` 双层结果，不用一个总分掩盖错误发生在哪一层。
