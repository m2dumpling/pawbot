# 下一版更新草案：Promptfoo 评测接入

本页是尚未发布的更新说明。当前版本号保持不变，远端 CI 和正式发布结果将在完成后补充。

## 对用户有什么变化

- 真实模型评测统一使用固定版本 Promptfoo，支持重复运行、公共确定性断言、
  可选 LLM Judge，以及 JSON、HTML 和本地浏览器报告。
- 初始套件收敛为 10 个脱敏诊断用例，每个默认运行 3 次；工具样本和结果标准明确，
  每次运行的会话、工具注册表、临时目录和 Trace 独立。
- 自研评测工作台、管理 API、Live Eval CLI 和旧 catalog 已移除。
  TaskContract、工具安全边界、Trace、Record & Replay、Harness 和 Task Eval 保留。
- WebUI 构建资源纳入正常提交，并增加安装包资源完整性和真实设置接口检查，
  降低更新后设置页面或按钮因资源、接口不一致而出错的风险。

## 原有使用方式如何迁移

从源码仓库进入 `evals/promptfoo`，安装锁定的 npm 依赖，设置虚拟环境 Python 路径。
使用 `npm run eval` 运行、`npm run view` 查看报告；不再使用 `pawbot eval live`
或 Pawbot WebUI 中的旧评测工作台。评测 Provider 直接驱动 Python AgentRunner，
无需先启动 HTTP API 或 Gateway。

本地已有的录制、回放和旧报告不删除；它们是诊断材料，不会自动变成新套件的正确答案。
新增 Bad Case 时先确认责任和结果标准，再维护 YAML；可以由开发 Agent 起草，人工核对。

完整操作见[使用指南](../agent-evaluation-promptfoo.zh-CN.md)和[接入 README](../../evals/promptfoo/README.md)。

## 验证和边界

本地 Python 全量 7,011 通过、54 跳过，覆盖率 83.03%；WebUI 1,134 通过，
TUI 164 通过；全新安装 wheel 后的三项 Gateway 冒烟通过。
真实模型诊断套件 30/30 Trial 通过，执行错误为 0。

该套件检查固定样本下的取证、诊断和证据诚实性，不代表完整 AgentLoop 端到端成功率、
生产准确率或行业 Benchmark 成绩。Judge 仅提供辅助软分，尚无独立人工校准。
CI 使用无需 API Key 的适配冒烟；真实模型套件需自行配置模型并手动运行。

## 发布流程

推送后必须等待本次变更的 CI 全绿再合并。正式发布按精确 tag 手动执行：
确认版本及仓库既有许可/源码提供义务，备齐五个平台同版本 TUI 及校验文件，
再发布 Python 包。具体步骤见[贡献与发布流程](../../CONTRIBUTING.md#release-packaging-contract)。
