# Pawbot 评测：Promptfoo

真实模型评测已收敛到 [`evals/promptfoo`](../evals/promptfoo/README.md)。该目录包含固定版本依赖、10 个有明确验收条件的脱敏/合成 Case、隔离的 Python Provider、公共确定性断言、可选 LLM Judge 和报告摘要工具。

```powershell
cd evals/promptfoo
npm ci
npm run validate  # 无模型接入冒烟
npm run eval      # 真实模型，默认每个 Case 3 次
npm run view      # 本地报告 http://127.0.0.1:15500
```

先安装 Pawbot Python 依赖，并按[完整说明](../evals/promptfoo/README.md)配置 `PROMPTFOO_PYTHON`。Provider 直接使用 AgentRunner，不依赖 Gateway。它只运行本地假工具；Case 的正确结果不交给被评测模型。

Promptfoo 负责用例编排、重复 Trial、断言结果、HTML/JSON 和 Web 报告；Pawbot 保留运行器、预算、安全执行、TaskContract、Trace 与 Replay。执行错误单独记录，确定性断言判断工具取证与最终事实，Judge 只提供辅助解释质量信号。

旧的自研评测工作台、管理 API、`pawbot eval live`、旧 catalog 和专用导出已经移除。`pawbot harness run`、`pawbot eval run` 仍用于无模型的框架回归。已有本地数据保持可供离线审阅，不自动迁移成高质量任务。

这是个人项目的一套可复现小型评测，不代表生产成功率、行业 benchmark 成绩或已经人工校准的 Judge。Baseline/candidate 必须保持 Case 与评分标准一致；未修改 Agent 时的两份报告只能证明重复运行。报告里保存完整 Transcript 路径，应抽样阅读，发生波动时优先核查原始证据。

## 第一次使用

这是源码仓库中的开发者评测入口，评测目录不随 `pip install pawbot-ai` 安装。
需要 Python 3.11+、`uv` 和 Node.js 22.22.0+；在实际源码目录执行下面的命令。
评测不要求启动 Gateway，也不要求安装 Bun 或下载 Promptfoo 源码。

```powershell
# 仓库根目录
uv sync --dev
cd evals/promptfoo
$env:PROMPTFOO_PYTHON = (Resolve-Path ../../.venv/Scripts/python.exe).Path
npm ci

# 不请求模型、不写入正式评测历史：确认适配器能执行，评分器能工作
npm test
npm run validate

# Pawbot 模型配置可用后，先运行第一个 Case 一次
npm run eval -- --filter-first-n 1 --repeat 1 --no-table

# 运行完整套件：10 个 Case，各 3 次；随后打开报告服务
npm run eval -- --no-table
npm run view
```

macOS/Linux 使用 `export PROMPTFOO_PYTHON="$(pwd)/../../.venv/bin/python"`。
真实运行读取现有 Pawbot 模型配置；可用 `PAWBOT_EVAL_CONFIG` 指定配置文件，
或用 `PAWBOT_EVAL_PRESET` 指定已有模型 preset。配置和 API Key 保留在本机，
不要写入 YAML、报告示例或版本库。新终端需要重新设置上述环境变量。

`npm run eval` 会调用已配置模型并消耗额度；`npm run validate` 仅验证框架链路。
默认 JSON/HTML 报告写入 `results/latest.json`、`results/latest.html`。
指定不同输出名称可以保留历次结果，示例见下方的版本回归流程。

## 浏览器里怎么看结果

启动 `npm run view` 后访问 <http://127.0.0.1:15500/eval>。
Pawbot 对话 WebUI 和 Promptfoo 报告是两个独立页面；当前评测从命令行启动，
报告页用于浏览、筛选和审阅结果。

1. 点击 **Evals** 选择某次报告，核对 ID、时间和 TESTS 数量。
2. 搜索框输入 Case ID，例如 `cached-evidence-unavailable`。默认重复 3 次时，
   同一个 Case 出现 3 行，每行是一条独立 Trial。
3. **Failures** 筛选未满足评分条件的输出；**Errors** 筛选执行错误。
   最新报告没有失败时会显示空列表；此时 `0/0 filtered` 不是全套通过率为零。
4. 点击行内 **View output and test details** 打开详情。

| 详情页 | 查看内容 |
| --- | --- |
| **Prompt & Output** | 本次任务和 Agent 原始回答 |
| **Evaluation** | 各项评分的通过状态、原始分数和失败理由 |
| **Metadata** | 模型、预算、工具调用/结果、停止原因、Trial ID 和 Transcript 路径 |

每行 **3 PASS** 表示 `runtime`、`trajectory`、`outcome` 三项检查通过，
不表示运行了三次。界面的 **Mark test passed/failed** 是人工标记，不会重跑 Agent。
发现失败应结合评分理由、工具观察和原始 Transcript 核对归因。

例如 cached 反例的两次读取都失败，正确回答应为 `unverified`，确认比例与源码行号
均为 `null`；诚实保留未知状态即可通过。正向对照只有读取到了 Fixture 中的源码
公式和精确用量，才确认 `3500 / 10000 = 35%`。这是样本事实，不能当作生产数据库值。

## 开启 Judge 和比较版本

```powershell
# 可选软评分，也会额外消耗模型额度
npm run eval:judge -- --filter-first-n 1 --repeat 1 --no-table

# 修改 Agent 前后，保持用例、工具样本、评分器和模型参数一致
npm run eval -- --no-table --output results/baseline.json results/baseline.html
# 修改 Agent 后再执行下一条
npm run eval -- --no-table --output results/candidate.json results/candidate.html
& $env:PROMPTFOO_PYTHON summarize.py results/baseline.json results/candidate.json --output results/comparison.json
```

Judge 是 `weight: 0` 的辅助信号，不覆盖确定性评分。查看 **Evaluation** 中
Judge 断言的原始 score 和 reason；零权重汇总的 `explanation_quality=0`
不代表裁判认为质量为零。裁判不可用时记为未知，不能凭勾选 Judge 宣称已完成人工校准。

源码未变化的 baseline/candidate 是重复测量；更改任务、Fixture、评分器或模型参数后
不能直接宣称 Agent 改进。摘要会记录这些差异，区分质量失败和执行错误。
API/认证错误应先修复环境，Agent 自身耗尽工具预算仍可能属于质量失败。

## 真实对话里的 Bad Case 怎么加入

先保存 Trace，确认是 Agent 的行为问题，再提取脱敏、边界清晰的最小任务。
余额不足、人工暂停本身不构成 Agent 缺陷；有证据的虚假成功声明、盲目重试等
行为才需要沉淀为用例。一次失败可以作为待验证样本，正式加入表示任务可评测，
不保证当前 Agent 已经通过它。

你不必从空白 YAML 开始。可以让开发 Agent 根据 Trace 和已有 Case 起草完整配置，
自己重点核对任务边界和正确结果。下面是一份可直接给开发 Agent 的请求模板：

> 请根据这份脱敏 Trace，在 `evals/promptfoo/cases.yaml` 起草一个最小回归用例。
> 原任务是：……；实际问题是：……；正确行为应当是：……。
> 请复用公共评分器，给出任务、可观察的工具样本和独立的结果标准；
> 标明哪些事实可确认、哪些必须保留未知，并说明你从哪些证据推导出期望。
> 若责任或正确结果无法确定，请指出缺失证据，暂不加入正式套件。

用例只需按已有结构维护这些参数：

| 字段 | 人工重点核对 |
| --- | --- |
| `task` | 要完成什么、允许哪些行为、需要哪些结果字段 |
| `fixture` | 工具名称、参数 Schema、匹配参数，以及真实可观察的结果或错误样本 |
| `expected.answer` | 正确判断和事实；未知值用 `null`，避免把旧回答当标准答案 |
| `expected.evidence` | 回答需要引用的证据 ID，必须能从工具样本观察到 |
| `expected.required_calls` | 必要取证及参数；失败观察要明确 `status: error` |
| `expected.forbidden_tools` | 不允许尝试的工具 |

确认后加入 `cases.yaml`，先运行 baseline，修复 Agent 后再运行 candidate。
工具样本按参数精确匹配；任务输出的 facts 应与结果标准完整对应。
不需要另写一个专属评分器，也没有自动审批或自动认定业务真值的步骤。
新增用例后，`validate` 只验证原有冒烟配置；新用例仍需在真实套件中运行验证。

## 常见问题

| 现象 | 处理方式 |
| --- | --- |
| Python 找不到依赖或 Pawbot | 在源码根目录运行 `uv sync --dev`，把 `PROMPTFOO_PYTHON` 指向该仓库虚拟环境 |
| 模型配置、认证、余额或连接报错 | 核对本机 Pawbot 配置和模型可用性，再重跑；保留 ERROR 记录 |
| `Required observation missing` | 对照 Metadata 检查工具名、参数、Fixture 匹配和预期状态 |
| `Outcome field ... expected ... received ...` | 对照任务与可见证据判断是 Agent 出错还是期望不成立，必要时阅读 Transcript |
| Judge 分数看起来为 0 | 看 Evaluation 原始软分；不要使用零权重汇总分作为质量判断 |
| 报告里的成本显示 0 | 检查 `cost_source`；未配置单价时真实估算是未知，不代表免费 |
| 浏览器没有看到刚运行的报告 | 核对 CLI 是否完成、是否用了 `--no-write`，再回到 Evals 选择新报告 |

## 接入时的验收记录（2026-10-03）

使用配置中的 `deepseek-flash`，固定工具 Fixture、关闭缓存，每个 Case 运行 3 次：

| 报告 | Trial 通过 | 执行错误 | 总 Token | 平均 Trial 耗时 |
| --- | --- | --- | --- | --- |
| `baseline-local.json` | 30 / 30 | 0 | 49,130 | 1,895.8 ms |
| `candidate-local.json` | 30 / 30 | 0 | 49,100 | 2,007.2 ms |

两份报告的 Case、评分配置、源码和模型参数一致，`comparison-local.json` 明确给出 `agent_changed=false`、通过率差值 0。这是接入与重复运行的验证，不是 Agent 优化实验。10 个完整 Case 组中，至少一次成功和三次全部成功的观察比例均为 100%；样本规模有限，不能外推至开放任务。单价未配置，成本记录为未知。这两份历史报告使用接入时的评分器；收尾时已收紧额外事实字段检查，新的报告不能直接与旧报告比较。

已抽查 cached 证据不可用、cached 证据可用及写入超时三类 Transcript：工具错误后保留未知状态；可读样本按 3500 / 10000 计算 35%；缺少回执的写入保持提交状态未知，不授权盲目重试。故意伪造完成、错误参数、缺少取证、越权工具尝试和编造证据 ID 的断言测试都会失败。报告文件保存在本地 `evals/promptfoo/results/`，不纳入版本库。

确定性硬检查只覆盖结构化结果与取证行为，不校验自由文本 `reason` 的全部语义。可选 Judge 用于发现解释与事实的矛盾，摘要单独统计原始软分和不可用次数；零权重的 `namedScores` 不是 Judge 原始评分。尚无独立人工标签校准，也未证明真实 AgentLoop 的全任务成功率。

## 收尾复核（2026-10-03）

收紧额外事实字段检查后，完整真实模型套件重新运行：`release-local.json` 为
10 Case × 3 Trial，30/30 通过，执行错误 0，50,404 Token，平均 Trial 耗时
2,154.5 ms，30 份 Transcript 全部存在。该报告使用新评分器，不能与上面的
历史报告直接计算改进幅度。它验证的是当前诊断套件的可执行性及固定条件下的行为。

cached 两个对照另各运行 3 次，6/6 通过。证据不可用用例开启 Judge 单独运行
1 次，三项硬断言通过，原始 Judge 软分为 1；Agent 消耗 1,696 Token，Judge
消耗 538 Token，总计 2,234。单次正向判断不是 Judge 准确率或人工校准结果。
`summarize.py` 会分别统计两类 Token，并将金额估算标为仅包含 Agent。

这些报告保存在本地 `evals/promptfoo/results/`，证据路径可能包含本机目录，
不直接提交到公共仓库。CI 运行无需 API Key 的适配和评分冒烟，真实模型套件
手动运行。WebUI 安装包与设置接口的验收属于发布检查，不混入 Agent 通过率。

本地发布检查同步完成：Python 全量 7,011 通过、54 跳过，覆盖率 83.03%；
WebUI 76 个测试文件、1,134 项测试通过；TUI 164 项测试通过。
全新环境安装 wheel 后，三项真实 Gateway 冒烟全部通过，覆盖构建资源加载、
认证、聊天恢复，以及模型配置、时区和网络设置的读写。Ruff、basedpyright、
WebUI 生产构建和框架质量门均通过。Python 测试保留一条 Discord 代理认证
使用 `aiohttp.BasicAuth` 的弃用警告。这些是本地验收结果，推送后仍需等待
本次变更的远端 CI，不能据此宣称跨平台 CI 已全绿。
