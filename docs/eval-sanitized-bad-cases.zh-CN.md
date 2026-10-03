# 脱敏评测用例

旧 Live Eval catalog 已迁移并收敛为 [`evals/promptfoo/cases.yaml`](../evals/promptfoo/cases.yaml) 的 10 个 Case，完整用法和验收标准见 [Promptfoo README](../evals/promptfoo/README.md)。

其中 8 个由仓库已记录失败模式重建，保留超时重试、审批、MCP 认证、参数修正、最新约束、Provider 错误、预算与评分误判的决策边界；另有 cached 证据不可用/可用的一对对照。标识、工具数据与数据库数值均为可公开的假样本，不包含原始私密对话。

这些是工具取证与诊断任务，不能宣称已经复现全部运行时故障。真实审批、MCP、上下文治理仍由内部 Harness 和对应测试覆盖。后续从真实 Trace 提取样例时，必须先核对 Agent 责任、环境可复现性和任务结果标准，不能把一次 Provider 余额不足或人工取消直接沉淀为模型质量失败。
