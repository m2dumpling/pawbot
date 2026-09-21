# pawbot 使用指南

当前文档对应 `v0.6.5`。

这篇文档面向已经安装 Pawbot 的用户，集中说明 CLI、WebUI、Trace、滚动回放、Record & Replay、Task Eval Set 和 Harness 的实际用法。

如果只想开始聊天，先记住两条命令：

```bash
pawbot             # 打开 WebUI
pawbot agent       # 打开 Native TUI
```

`pawbot` 和 `pawbot agent` 使用同一套 Agent Runtime、Session、Provider 和工具配置。

## 1. 入口和第一次使用

### WebUI

```bash
pawbot
```

浏览器打开后：

1. 进入“设置 → 模型”；
2. 选择 Provider，填写 API Key；
3. 保存配置；
4. 选择可用模型或手动填写模型 ID；
5. 新建会话并发送任务。

### Native TUI

```bash
pawbot agent
```

一次性执行并退出：

```bash
pawbot agent --message "检查当前仓库并总结 Agent Loop"
```

查看帮助：

```bash
pawbot --help
pawbot agent --help
```

### Linux 服务器上的 WebUI

服务器默认绑定 `127.0.0.1`，服务器外部无法直接访问。推荐使用 SSH 隧道：

服务器上：

```bash
pawbot webui --no-open
```

本地电脑执行：

```bash
ssh -N -L 8765:127.0.0.1:8765 <user>@<server>
```

然后在本地浏览器打开：

```text
http://127.0.0.1:8765
```

如果需要直接通过服务器 IP 访问：

```bash
pawbot webui --remote --yes --no-open
```

此时需要在服务器防火墙中只开放 WebUI 端口，并通过 HTTPS 或反向代理保护公网访问。Gateway 健康检查端口不应直接暴露到公网。

## 2. WebUI 中查看一次执行

发送任务后，点击对话右上角的“轨迹”图标。

实时 Trace 会按发生顺序显示：

```text
执行阶段
→ 模型请求
→ 模型决定调用的工具
→ 工具参数
→ 工具返回
→ 重试 / 审批 / 恢复
→ 任务验收
→ 最终状态
```

Trace 适合回答：

- 哪个阶段耗时；
- 哪次模型请求失败；
- 哪个工具参数或权限有问题；
- 工具是否真的执行；
- 是否存在未知副作用；
- 为什么 Agent 没有继续或提前结束。

Trace 是轻量诊断记录，不等于完整原始 Prompt 和工具结果。

## 3. 默认滚动回放缓存

Pawbot 默认开启有限容量的滚动 Replay Buffer：

```text
每个会话最近 20 个完整回合
默认保留 24 小时
实例总容量默认 256MB
```

正常回合会自动轮换清理。以下回合会自动成为候选问题：

- Provider 错误；
- Tool 执行失败；
- 任务验收失败；
- 用户取消；
- 预算耗尽；
- 外部副作用状态未知；
- 执行不完整。

这样即使没有提前点击“保存为回归样本”，最近发生的问题仍然有机会被完整回放。

滚动缓存默认保存在 Pawbot 实例运行数据目录，不会因为在项目目录启动就自动生成项目级记录目录。旧版本的 `workspace/blackbox` 样本仍然可以读取。

## 4. 候选问题回合

进入：

```text
设置 → 执行与回归
```

如果系统发现候选问题，会显示：

```text
候选问题回合
原因：Tool 执行失败 / Provider 错误 / 取消 / 未知副作用
```

可以选择：

### 保留为回归样本

适合以后反复 Replay，但不把它作为任务评测标准。

### 加入任务评测

把候选样本加入本机的自定义 Eval Set。之后运行评测时，系统会用当前 Agent 代码离线回放它，并报告：

```text
任务状态
轨迹状态
回放是否一致
```

如果原始回合没有 TaskContract，任务状态可能是 `not_evaluable`，但仍然可以检查轨迹是否退化。

### 忽略

将候选移动到已审核记录，不再出现在待处理列表中。

对应斜杠命令：

```text
/record candidates
/record keep <candidate-id>
/record reject <candidate-id>
```

## 5. 个性化说明与记忆

打开 **设置 → 个性化**，可以填写跨会话生效的个性化说明，例如回复语言、回答
风格以及修改代码时的工作偏好。只对某个项目生效的规则请写在项目根目录的
`AGENTS.md` 中。

同一个页面可以控制是否使用已确认记忆，以及是否允许后台生成记忆候选。使用
`/memories` 可以查看或覆盖当前会话的设置：

```text
/memories show
/memories use on|off|default
/memories generate on|off|default
```

### 保存明确的用户偏好

明确要求记住的偏好不需要等待 Compact 或 Dream，会立即保存到 Pawbot
运行时目录，并在之后的回合中按作用域注入。

```text
/remember global reply_language=zh-CN
/remember global response_style=concise
/remember workspace primary_provider=deepseek
/memory list
/memory candidates
/memory confirm <candidate-id>
/memory reject <candidate-id>
/forget <memory-id>
```

`global` 表示所有工作区生效，`workspace` 只对当前项目生效。WebUI 中也可以在
**设置 → 个性化 → 记忆**里新增、查看和删除记忆。系统会拒绝明显的 API Key、
Token 和密码，并保留删除事件用于审计；“删除已保存记忆”会清空已确认记忆和候选，
但保留审计删除记录。

Dream 仍然负责自动整理低置信度记忆，但不能覆盖用户明确确认过的偏好。

Dream 发现可能值得保留的偏好或项目事实时，会先保存为带置信度和证据的候选，
不会直接生效。可以在 **设置 → 个性化 → 记忆 → 待确认建议** 中保存或忽略，
也可以使用 `/memory confirm <id>` 和 `/memory reject <id>`。

## 6. 手动保存完整回归样本

滚动缓存用于防止遗漏，手动录制用于保存明确想长期保留的完整样本。

WebUI 中点击：

```text
保存为回归样本
```

然后执行一个或多个任务，最后点击：

```text
停止保存
```

CLI 方式：

```bash
pawbot record start --name demo
pawbot record status
pawbot record stop
pawbot record list
```

也可以只为一次终端任务录制：

```bash
pawbot agent \
  --message "检查仓库并总结 Agent Loop" \
  --record .pawbot/blackbox/demo
```

手动完整样本可能包含 Prompt、文件内容、工具参数和返回值。分享前必须检查敏感信息。

## 7. 离线回放

在 WebUI 的样本列表中点击“离线验证”。

或者使用 CLI：

```bash
pawbot replay .pawbot/blackbox/demo
```

回放不会：

- 再次请求真实模型；
- 消耗新的 Token；
- 执行真实工具；
- 修改真实工作区；
- 访问真实外部系统。

回放结果必须和原执行结果分开理解：

```text
回放结果：一致 / 有差异
原执行结果：成功 / 工具失败 / 模型失败 / 取消 / 未知副作用
任务验收：通过 / 未通过 / 无法评估
```

例如：

```text
回放结果：一致
原执行结果：工具执行失败
任务验收：未通过
```

意思是当前代码成功重现了原来的失败，不是任务成功。

指定回合暂停：

```bash
pawbot replay .pawbot/blackbox/demo --break-at 2
```

暂停后可以查看当时重建的消息序列。

## 8. TaskContract 怎么用

TaskContract 是针对有明确完成条件的任务的验收规则，不是普通聊天必须填写的工具调用计划。

CLI 示例：

```json
{
  "id": "release-note",
  "final_content_contains": ["verified"],
  "required_files": ["CHANGELOG.md"],
  "file_contains": [["CHANGELOG.md", "verified"]]
}
```

```bash
pawbot agent \
  --message "创建并验证发布说明" \
  --task-contract contract.json
```

可检查：

- 最终文本；
- 必须存在的文件；
- 文件内容；
- 工具结果；
- 必须成功的工具（只有业务确实要求时才设置）。

如果验收失败，Agent 会收到失败条件并继续修正；如果没有足够条件判断，则标记为 `not_evaluable`。

普通 WebUI 自由对话目前不会自动生成 TaskContract，但可以显示已有样本中的任务验收结果。

### `must / must_not / ordered`

需要表达执行边界时，可以增加断言：

```json
{
  "id": "research-summary",
  "must": [
    {"id": "search", "kind": "tool_called", "tool": "web_search"},
    {"id": "sources", "kind": "evidence_sources", "min_count": 3}
  ],
  "must_not": [
    {"id": "no_write", "kind": "tool_called", "tool": "write_file"}
  ],
  "ordered": [
    {"before": "search", "after": "sources"}
  ]
}
```

`must` 表示必须满足，`must_not` 表示禁止行为，`ordered` 只约束部分顺序，允许并行搜索
和重试。系统逐条返回证据和原因。未知的语义断言返回 `not_evaluable`，不会交给 Agent
自己宣布通过。

## 9. Agent Task Eval Set

任务评测集用一批固定任务比较当前 Agent 编排是否退化。它不请求真实 Provider，也不执行真实工具。

CLI：

```bash
pawbot eval list
pawbot eval run
pawbot eval run --json
```

WebUI：

```text
设置 → 执行与回归 → Agent 任务评测集 → 运行评测
```

报告分别展示：

```text
任务通过数
轨迹通过数
not_evaluable 数量
工具失败
模型请求数
工具调用数
耗时
```

Task Eval Set 和 Harness 的区别：

```text
Harness：验证 Runtime 如何处理错误、取消、预算和审批。
Task Eval Set：验证固定任务的结果和执行轨迹。
```

## 10. Harness 是什么

Harness 是开发者质量检查用的固定故障演练场。它使用脚本化模型和内存工具，驱动真实 AgentRunner，但不访问真实 Provider 和工作区。

运行：

```bash
pawbot harness list
pawbot harness run
pawbot harness run --json
```

当前场景包括：

- 正常工具调用；
- 工具失败恢复；
- 文件修改后验证；
- 调查并总结；
- 人工审批；
- Provider 错误；
- 模型超时；
- 用户取消；
- 预算边界。

Harness 通过不代表模型质量是 100 分，只代表声明的工程边界没有被破坏。

质量门禁：

```bash
python scripts/quality_gate.py
```

质量门禁会组合静态检查、契约测试、Harness、Task Eval Set、Replay Fixture 和其他关键测试。

## 11. Gateway 事件恢复与 Doctor

WebUI 会自动协商 Gateway protocol v1。事件会带有 `event_id`、`stream_id` 和 `seq`；客户端
发现缺口时会发送 `resume`，Gateway 从本地 Event Journal 补发。WebUI mutation 在协议 v1
下使用 request_id 幂等账本，状态未知时不会自动重复提交。

部署前可以运行：

```bash
pawbot doctor
pawbot doctor --json
```

Doctor 只读检查 Python、配置、workspace、Gateway、事件序列、操作账本和 WebUI 构建产物，
默认不向真实 Provider 发请求。

## 12. 常用斜杠命令

```text
/trace
/trace errors
/trace slow
/trace <trace-id>

/record status
/record start <name>
/record stop
/record candidates
/record keep <candidate-id>
/record reject <candidate-id>

/eval list
/eval run

/remember global reply_language=zh-CN
/remember workspace response_style=concise
/memory list
/forget <memory-id>

/model
/models
/effort
/stop
/help
```

## 13. 数据和隐私

默认数据都留在本机：

```text
配置：实例配置目录/config.json
Session：运行数据目录/sessions
轻量 Trace：运行数据目录/traces
滚动回放：运行数据目录/blackbox/rolling
候选问题：运行数据目录/blackbox/candidates
永久样本：运行数据目录/blackbox/samples
```

录制文件可能包含敏感 Prompt、工具参数、文件内容和本地路径。不要把以下内容提交到 Git：

- `config.json`；
- `.env` 和 API Key；
- `sessions/`；
- `traces/`；
- `blackbox/`；
- 个人 Prompt 和工具返回值。

## 14. 快速排查

```text
看不到 Trace：检查 Trace 是否启用，并确认当前会话已发送任务。
没有完整回放：检查样本是否 ready；普通 Trace 不等于完整 Replay 样本。
任务是 not_evaluable：没有设置 TaskContract，或原执行没有足够的确定性证据。
回放有差异：先看消息差异，再看 Tool 顺序、结果回填、预算和上下文治理。
Linux WebUI 打不开：使用 SSH 隧道，或显式执行 pawbot webui --remote --yes --no-open。
```
