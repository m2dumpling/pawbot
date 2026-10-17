# 子 Agent 工具策略继承：可复现的修复案例

本案例来自源码审查后经受控执行确认的缺陷，**不是生产事故记录**。脚本 Provider
驱动真实 `AgentLoop → SpawnTool → SubagentManager → AgentRunner → CallExecutor`
链路，文件操作只发生在临时目录，不需要模型账号或网络请求。

## 用户场景与根因

受限工作区中的用户批准委派任务，仍应逐项决定子任务的高风险操作；配置拒绝的
能力也应对委派生效。原实现向子任务传递了模型、工作区和来源会话，却没有向其
独立 `AgentRunSpec` 传递能力拒绝、审批及恢复确认配置。

固定输入复现了两种失败：

- 禁止 `read` 后，主任务直接读文件会被拒绝，改为同步或后台子任务则能读到临时
  canary。这里使用 `read` 是因为禁止 `write` 已会拦住 `spawn` 本身，不能用后一种
  场景证明子工具执行绕过。
- 受限模式批准 `spawn` 后，子任务能直接写文件，没有发出应有的写入审批请求。
  即使审批回调计划拒绝写入，回调也未被调用。

修复在 Runner 入口从实际 `AgentRunSpec` 生成不可变 `ToolAccessPolicy`，通过已绑定
的请求上下文和委派参数传递给
每个子任务。子 Runner 使用相同的能力拒绝、审批范围及回调，并携带原 channel、
chat 和 session 路由。Manager 自身的拒绝能力与继承限制取并集，不能放宽父策略。
策略不作为共享 Manager 的可变会话状态，也不把回调写入 checkpoint。

## 固定条件下的前后结果

基线为 `a8c6a9864d69e7077e9c238a38c0445691ea9b5e`。候选为基于该版本的本地
修复；未提交时用补丁和文件哈希标识。两次运行使用同一 Python 环境、同一测试
文件及断言、相同脚本响应和临时文件内容。报告不比较耗时，操作系统调度不是固定变量。

| 固定检查 | 基线 | 修复后 |
| --- | --- | --- |
| 主 Agent 直接读取被拒绝的文件 | 拒绝，通过 | 拒绝，通过 |
| 同步/后台子 Agent 读取被拒绝的文件 | 两条路径均缺少拒绝，2 项失败 | 两条路径均拒绝，2 项通过 |
| 拒绝子写入时的实际文件写入 | 两条路径均写入，2 项失败 | 两条路径均未写入，2 项通过 |
| 允许子写入时仍需产生审批请求 | 两条路径均缺少请求，2 项失败 | 两条路径各产生请求并完成，2 项通过 |
| 完全访问下正常委派写入 | 2 项通过 | 2 项通过 |
| **总计** | **3/9 通过，6/9 失败** | **9/9 通过** |

失败必须是行为断言失败；导入错误、缺少依赖、测试路径错误均不能算复现。
原始报告保存在本地 `.test-tmp/reliability/`，包括 `policy-baseline.xml`、
`policy-candidate.xml`、对应日志、环境记录及比较命令。审阅后的结果摘要见
[`subagent-policy-regression.json`](subagent-policy-regression.json)。

## 补充的生命周期回归

逐项验收还确认了停止后的旧结果问题：子任务已完成并将结果排入消息总线后，
原实现的停止操作只取消运行中的任务，排队结果仍可能启动新的模型回合，或被
注入后续用户回合。修复为每条待处理结果保留进程内的会话归属凭据；停止时失效，
分发、取得会话锁后及回合注入时校验，实际消费时移除。有效结果只消费一次，
停止另一个会话不会将其丢弃。凭据不写入 checkpoint，也不创建跨重启投递保证。

| 固定补充检查 | 同一基线 | 修复后 |
| --- | --- | --- |
| 停止后旧结果到达新回合：独立分发 / pending queue 注入 | 0/2 通过，旧结果仍可调用模型或污染上下文 | 2/2 通过 |
| MCP 旧版 HTTP 会话空闲过期后再次调用工具 | 0/1 通过，返回 `Session not found` | 1/1 通过，建立新会话并返回预期工具结果 |

MCP 检查使用当前 SDK 启动真实本地 HTTP 服务，夹具显式拒绝现代 discovery，并
关闭可选 GET 事件流，以覆盖会话会空闲过期的旧版握手路径。SDK v2 保留服务端的
`-32600 / Session not found` 错误体；原实现只识别旧错误文本。修复精确识别该
错误码及消息，普通工具错误或参数错误不会因此触发重连。原夹具的启动错误不再
转为跳过，服务无法启动会直接失败并附诊断日志；另有真实重连中关闭的回归。

补充结果分别记录于 JSON 的 `additional_regressions`，不并入主案例的九项分数。

## 复现与验证

按贡献指南准备开发依赖后，先在修复版本的仓库根目录执行：

```powershell
uv run --no-sync pytest -q tests/agent/test_subagent_policy.py
```

仅运行上述固定 9 项：

```powershell
uv run --no-sync pytest -q tests/agent/test_subagent_policy.py -k "denied_read_cannot_be_delegated or child_write_requires_its_own_approval or full_access_keeps_delegated_writes_usable"
```

验证补充场景及真实客户端链路：

```powershell
uv run --no-sync pytest -q tests/agent/test_subagent_policy.py -k "cancel_drops_already_queued_child_result_before_new_turn"
uv run --no-sync pytest -q tests/agent/test_mcp_reconnect_crash.py tests/agent/test_mcp_transient_retry.py
# 需要 Bun；WebUI CI 安装 Bun 并要求执行这两项集成测试。
uv run --no-sync pytest -q tests/webui/test_gateway_policy_clients.py
```

最后一条命令启动独立 Gateway、临时工作区与本地脚本模型 API，直接加载 WebUI
和 TUI 的实际客户端代码。每个客户端分别走完同步/后台委派的批准、拒绝、停止，
以及中断恢复的继续、放弃八条流程，检查批准前零写入、错误会话无法批准、停止后
迟到批准无效及恢复不隐式重写。它验证实际客户端与 Gateway 的事件和动作贯通，
不代表人工视觉验收或终端按键验收。

在基线复现相同用例的 PowerShell 步骤如下。请使用尚不存在的隔离目录；这里不
切换或重置当前工作区，Python 依赖复用当前已准备好的环境。

```powershell
$repoPath = (Get-Location).Path
$baselinePath = Join-Path $repoPath '.test-tmp/subagent-policy-baseline'
$repoPython = Join-Path $repoPath '.venv/Scripts/python.exe'
git worktree add --detach $baselinePath a8c6a9864d69e7077e9c238a38c0445691ea9b5e
if ($LASTEXITCODE -ne 0) { throw 'Cannot create baseline worktree' }
Copy-Item -LiteralPath 'tests/agent/test_subagent_policy.py' -Destination (Join-Path $baselinePath 'tests/agent/test_subagent_policy.py')
Push-Location $baselinePath
try {
    & $repoPython -c "import pawbot; print(pawbot.__file__)"
    # 确认上一行路径位于 baselinePath，再运行；预期退出码为 1，6 项行为断言失败。
    & $repoPython -m pytest -q tests/agent/test_subagent_policy.py -k "denied_read_cannot_be_delegated or child_write_requires_its_own_approval or full_access_keeps_delegated_writes_usable"
} finally {
    Pop-Location
}
```

## 相关回归与边界

专项测试还检查真实审批管理器的会话匹配、批准前零写入、超时/取消后迟到批准无效、
排队会话之间的受限/完全访问隔离、批准后工作区边界、审批范围定制、SDK 与独立
Manager/Runner 的能力拒绝，以及完全访问下未知副作用重试仍需确认。两个不同
项目同时委派时，还检查各自身份文件、子写入审批和输出目录不会串用。

取消新增待处理子结果的接收校验，其他取消与重启恢复继续复用既有实现及故障
注入测试。未知副作用回归覆盖同一子任务中的重试；重启恢复仍以父回合既有
checkpoint 为边界，子任务尚未新增独立对话持久化或重启续跑能力。
既有 Harness、Task Eval 和 Replay 检查用于相关回归，不把当前未完整
录制的子任务链路宣称为 Replay 已覆盖。

这些结果证明固定条件下的运行时契约。没有调用真实模型，没有独立的生产样本或
统计 holdout，因此不推导模型任务准确率、部署成功率、成本下降或 Token 节省。
审批和路径检查也不等同于操作系统沙箱；停止任务不能撤销已经发生的外部写入。

本机 Windows / Python 3.11 验证：委派专项 29 项通过；Python 全量 7,049 项
通过、52 项跳过，行覆盖率 83.06%；严格类型及 10 项质量门禁通过。真实 Gateway
与 WebUI/TUI 客户端集成 2 项通过，每客户端 8 条流程；相同测试在隔离的
`uv sync --extra dev` 环境通过，CI 已加入必跑步骤。真实 MCP 生命周期 2 项通过。
WebUI 1,134 项、TUI 164 项、Promptfoo 离线断言 9 项与 scripted smoke 1 项重新通过。
这些集合有重叠，不相加。

跳过项涉及 Windows/POSIX、权限和可选依赖等环境条件，详细原因保存在本地
`polish-full.xml`；MCP 真实生命周期已实际通过。本次未运行远端 CI，也未请求
真实模型；合并前仍须通过仓库 CI 的各平台矩阵。
