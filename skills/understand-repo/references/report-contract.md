# 报告契约

## 状态

- `REPORT_PASS`：固定范围内的关键 rubric 全部验证，没有 critical gap，运行证据满足要求。
- `PARTIAL`：已生成有价值的报告，但存在无法访问、无法运行或尚未验证的关键项。
- `BLOCKED`：无法取得足够源码、身份或最小证据，报告不应被当作研究结果使用。

学习者状态 `LEARNER_READY`、`CONTRIBUTOR_READY` 只来自独立的最终考核，不能写入研究状态。

## 必需文件

`docs/ai-generated-wiki/` 必须且至少包含以下契约文件；可以添加图像，但不要新增内容重复的 Markdown 页面。

1. `index.md`：导航、范围、读者画像、状态、推荐阅读顺序。
2. `project-and-domain.md`：项目目的、术语、参与者、领域模型和不变量。
3. `architecture.md`：边界、组件、依赖方向、设计模式、取舍和扩展点。
4. `core-flows.md`：成功、失败/恢复、贡献目标三类端到端流程。
5. `runtime-and-quality.md`：构建、启动、配置、测试、可观测性、安全与实际验证。
6. `contributing.md`：开发环境、代码约定、贡献切面、第一项贡献路径和验收。
7. `learning-path.md`：按用户背景组织的练习、检查题和建议顺序。
8. `evidence.md`：证据索引、命令摘要、源码 permalink、冲突与未知项。
9. `manifest.json`：机器可读范围、状态、rubric 和 gap。

## 页面 frontmatter

每个 Markdown 文件必须以同一组 YAML frontmatter 开头：

```yaml
---
generated_by: understand-repo
source_commit: <完整 SHA>
generated_at: <ISO 8601 时间>
status: REPORT_PASS | PARTIAL | BLOCKED
---
```

## 证据规则

正文用 `[E-001]` 格式引用 `evidence.md` 中的唯一条目。每条证据记录：

- 等级：`Observed`、`Derived`、`Claimed`、`Unknown/Conflict`；
- 支持的结论；
- SHA 固定的 upstream 源码链接，或已脱敏的命令与结果摘要；
- 限制、反例或冲突。

等级含义：

- `Observed`：直接读取固定 SHA 源码，或实际执行得到结果。
- `Derived`：由多个 Observed 事实推导，必须写出推导链。
- `Claimed`：来自 README、ADR、Issue、注释或维护者声明，尚未独立验证。
- `Unknown/Conflict`：证据缺失或彼此冲突。

不得把未执行的命令写成 Observed。不得以浮动分支 URL 支撑源码结论。引用源码片段应短小，优先链接，不复制整个文件。

## `manifest.json`

最小结构：

```json
{
  "schema_version": "1.0",
  "generator": {
    "skill": "understand-repo",
    "skill_version": "0.1.0",
    "engine": {"name": "Understand-Anything", "version": "2.9.x", "commit": ""}
  },
  "repository": {
    "upstream_url": "https://github.com/OWNER/REPO",
    "fork_url": "https://github.com/USER/REPO",
    "default_branch": "main",
    "source_commit": "<完整 SHA>",
    "generated_at": "<ISO 8601 时间>"
  },
  "scope": {
    "budget": "standard",
    "contribution_target": "<目标>",
    "included": [],
    "excluded": [],
    "capability_notes": []
  },
  "status": "PARTIAL",
  "runtime": {"verdict": "unknown", "commands": []},
  "rubric": [
    {
      "id": "architecture",
      "title": "架构边界与依赖方向",
      "critical": true,
      "status": "unknown",
      "evidence_level": "unknown",
      "evidence_ids": []
    }
  ],
  "gaps": [
    {"severity": "critical", "summary": "尚未完成运行验证"}
  ]
}
```

rubric 至少覆盖：项目与领域、仓库地图、架构、核心流程、运行与配置、质量与安全、贡献路径、学习路径。`status` 仅允许 `verified`、`not_applicable`、`unknown`；`evidence_level` 对应四级证据的小写形式：`observed`、`derived`、`claimed`、`unknown_conflict`、`unknown`。

rubric 借鉴 CodeWikiBench 的覆盖性、可追溯性、结构清晰度和教学有效性，但这里只保留能由脚本或读者复核的离散门槛，不计算伪精确总分。

`REPORT_PASS` 的额外门槛：

- 所有 `critical: true` 项为 `verified` 或 `not_applicable`；
- `verified` 项至少关联一条存在的 evidence ID，等级只能是 `observed` 或 `derived`；
- `runtime.verdict` 为 `observed` 或有充分理由的 `not_applicable`；
- `gaps` 中没有 `critical`；
- `evidence.md` 至少包含一个固定到 source SHA 的 upstream 源码链接。

## 内容质量

- 所有图都必须在文字中解释边界和箭头语义；图形语法不应成为阅读前提。
- 设计模式只在源码确实呈现该模式时命名，并先用项目内语言解释作用。
- 明确区分“项目声称的设计”和“当前代码实际实现”。
- 对重复模块记录抽样方法；对未展开范围记录原因和风险。
- 第一项贡献必须足够小、可测试、与仓库约定一致，不虚构“good first issue”。
