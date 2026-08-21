# 报告模板

在 `docs/ai-generated-wiki/` 创建九个契约文件。每个 Markdown 文件先使用同一 frontmatter：

```yaml
---
generated_by: understand-repo
source_commit: "{{SOURCE_COMMIT}}"
generated_at: "{{GENERATED_AT}}"
status: "{{STATUS}}"
---
```

建议最小标题：

```text
index.md
  # 项目学习地图
  ## 研究范围与状态
  ## 你的阅读路径
  ## 全局心智模型
  ## 未知项

project-and-domain.md
  # 项目与业务领域
  ## 项目解决什么问题
  ## 参与者与术语
  ## 领域模型与不变量
  ## 范围边界

architecture.md
  # 架构设计
  ## 系统上下文
  ## 组件与依赖方向
  ## 运行时拓扑
  ## 设计模式与取舍
  ## 扩展点

core-flows.md
  # 核心业务流程
  ## 主成功路径
  ## 失败与恢复路径
  ## 贡献目标路径

runtime-and-quality.md
  # 运行时与质量
  ## 构建、启动与配置
  ## 测试与验证
  ## 可观测性
  ## 安全与供应链

contributing.md
  # 贡献指南
  ## 开发环境
  ## 代码与测试约定
  ## 第一项贡献
  ## PR 验收清单

learning-path.md
  # 个性化学习路径
  ## 学习目标
  ## 分阶段练习
  ## 自检问题
  ## 下一步

evidence.md
  # 证据索引
  ## E-001 — <标题>
  - 等级: Observed
  - 结论: <此证据支持什么>
  - 来源: <固定 SHA 的 permalink 或脱敏命令摘要>
  - 限制: <限制或冲突>
```

`manifest.json` 使用 `references/report-contract.md` 的结构。先填证据，再写结论；最后更新所有页面和 manifest 的统一状态。
