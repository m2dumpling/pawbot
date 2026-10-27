# Pawbot 运行状态修复与工作区评测证据

本文记录一次受控的本地验证，配套数字和指纹保存在[机器可读摘要](agent-evaluation-evidence.json)。它不代表生产环境的成功率、用户满意度或安全保证。

Pawbot 在已有开源底座上演进，源码许可证保留原有贡献者信息；本文展示的是项目中的特定改造和验证。Promptfoo 负责评测编排与报告，Pawbot 负责自身运行时、工具、状态和 Trace。

## 子任务状态：同条件修复前后

问题是子任务达到迭代或其他停止条件后，仍可能被通知为“成功完成”。修复位于 [SubagentManager](../../pawbot/agent/subagent.py)，使用已有 TurnOutcome 分类，同步和后台委派都保留停止原因及部分结果；取消继续遵守旧结果失效规则。[子任务生命周期测试](../../tests/agent/test_subagent_lifecycle.py)包含真实 Runner 的迭代上限复现。

| 检查 | 修复前 | 修复后 |
| --- | ---: | ---: |
| 同一组 12 项状态断言 | 0/12 通过 | 12/12 通过 |
| 其中真实 AgentRunner 的迭代上限复现 | 错误地通知成功 | 通知未完成，并保留停止原因 |

基线源码来自摘要记录的 Git 提交；候选是在该提交基础上的本地未提交修改。两侧使用同一份测试文件、同一选择条件和脚本 Provider；测试文件与两版源码的 SHA-256 可在摘要核对。这里的分数表示这些特定行为契约，不是模型任务准确率。

另一项已有的受控证据是[子 Agent 权限继承修复](subagent-policy-regression.zh-CN.md)：固定检查从 3/9 到 9/9。该实验同样使用脚本 Provider，不属于生产事故统计。

父回合与子任务仍各自使用 Runner 预算，当前没有跨委派树共享 Token、时间或成本的总预算。

## 真实模型与本地工作区任务

评测使用所配置的 DeepSeek Flash、真实 AgentLoop/AgentRunner、真实文件工具、固定的公共测试入口和工作区外的独立验收器。每次 Trial 使用新的临时项目、Session、记忆目录和 Trace。模型收到任务要求与项目内容；最终验收器的额外测试输入和对应结果不会注入模型请求。具体可查看[用例](../../evals/promptfoo/workspace_cases.yaml)、[项目与验收器](../../evals/promptfoo/workspace_tasks.py)和[执行适配器](../../evals/promptfoo/workspace_provider.py)。

| 分组 | Case | Trial | 通过 | 失败 | ERROR |
| --- | --- | ---: | ---: | ---: | ---: |
| dev | 超时解析 | 3 | 3 | 0 | 0 |
| dev | cached 比例计算 | 3 | 3 | 0 | 0 |
| dev | 配置键迁移 | 3 | 3 | 0 | 0 |
| dev | 本地日志汇总 | 3 | 3 | 0 | 0 |
| holdout | 只读配置核查 | 3 | 3 | 0 | 0 |
| holdout | CSV 字段去重边界 | 3 | 3 | 0 | 0 |
| 合计 | 六个固定任务 | **18** | **18** | **0** | **0** |

两个 holdout 用例在实验前固定，未用于这次调参；开发者可以看到题目，不是保密基准。任务由已知失败模式及本地需求构造，不是原始生产日志。十八次结果和评分标准均可查阅；样本很小，不能推断大规模成功概率。超时解析任务没有评价带加号的字符串等未明确约定的输入。

这组任务不暴露自由 Shell、网络、MCP 或委派工具。文件操作和测试执行是真实发生的；验收子进程在本机运行候选代码，临时目录不等于操作系统沙箱。

## 本次性能口径

正式实验共 **86 次逻辑模型请求、86 次观测到的物理请求**，没有观测到请求数缺失；输入 **353,616 Token**、输出 **6,663 Token**，合计 **360,279 Token**，均由 Provider 报告。独立预检另有一次 Trial、五次模型请求和 20,994 Token，不计入表中成绩。未配置可信单价，费用为 unknown。

正式实验的平均单 Trial 耗时约 **9.09 秒**，平均模型请求耗时约 **1.81 秒**。本次没有可用的 TTFT，不将缺失值记为零。

| 工具 | 调用数 | 平均耗时 | 中位数 | P95 |
| --- | ---: | ---: | ---: | ---: |
| run_project_tests | 15 | 175.7 ms | 180 ms | 190 ms |
| read_file | 34 | 13.9 ms | 8 ms | 9 ms |
| find_files | 23 | 6.8 ms | 7 ms | 9 ms |
| apply_patch | 6 | 10.0 ms | 10 ms | 16 ms |
| write_file | 9 | 8.3 ms | 10 ms | 12 ms |
| list_dir | 4 | 6.3 ms | 6.5 ms | 7 ms |
| grep | 1 | 8 ms | 8 ms | 8 ms |

run_project_tests 是评测专用的固定测试入口；它在这批调用中平均最慢，不能代表生产 MCP 或网络工具的耗时。P95 只是所列小样本的观测值；并发工具耗时不能直接相加计算墙钟占比。

按每次请求的逻辑输入估算，平均 Prompt 约 **4,157 Token**，其中工具 Schema 估算约 **2,021 Token**，历史工具结果估算约 **210 Token**。估算器使用通用 tokenizer fallback，这些数不等于精确账单，分项也不能直接相加当作计费量。

## 如何复现与查看

按[Promptfoo 接入指南](../../evals/promptfoo/README.md)配置依赖及模型，然后运行：

```powershell
cd evals/promptfoo
npm run validate:workspace
npm run eval:workspace -- --no-table
../../.venv/Scripts/python.exe summarize.py results/workspace-latest.json --output results/workspace-summary.json
npm run view
```

浏览器中打开工作区评测，点击各行查看模型生成的 Original Output、三个断言及 Metadata；Metadata 中的 Trace、Transcript 和文件差异指向本地证据。上述原始文件和临时项目不随仓库提交，公开摘要只包含脱敏统计与指纹。

模型、依赖或环境变化时，需记录新的参数和指纹；自动化检查通过不保证未来每次模型运行都通过。

当前 `agent_sha256` 只覆盖 `pawbot` 下的 Python 源文件，不自动覆盖 Markdown 提示词模板等非 Python 资源。本次模板与记录的基线提交一致，其 Git tree 已单独写入机器摘要。比较两次报告前，应人工核对模板及其他未纳入指纹的运行输入，不能仅凭摘要中的“源码相同”认定全部条件一致。
