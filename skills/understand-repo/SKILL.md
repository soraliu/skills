---
name: understand-repo
description: 将公开 GitHub 仓库研究成有证据、可复核、因人而异的 contributor 学习报告，并在报告完成后通过一次交互考核验证理解程度。Use when the user provides a GitHub repository URL and wants to understand its architecture, domain model, runtime behavior, core flows, contribution path, or requests an assessment based on a previously generated report.
---

# Understand Repository

把“读过 README”升级成“能解释设计、追踪业务路径、运行验证并完成第一项贡献”。研究对象始终固定到一个 commit SHA；报告只声明证据真正覆盖的范围。

## 路由

- `$understand-repo <github-url>`：执行完整研究并向用户 Fork 提交报告 PR。
- `$understand-repo assess <repo-name>`：读取 `<workspace-root>/<repo-name>-learning/` 中的档案和已合并报告，执行一次最终交互考核。
- URL 缺失或不是公开 GitHub 仓库时，停止并请用户提供有效 URL。

开始前必须完整读取：

- 研究或生成报告：`references/report-contract.md`、`references/execution-policy.md`。
- 用户画像、教学组织或考核：`references/learning-assessment.md`。
- 创建报告文件：以 `assets/report-template.md` 为结构模板，不要原样复制占位符。

## “已经理解整个项目”的定义

只有同时满足以下条件，报告才可标记 `REPORT_PASS`：

1. 固定并记录 upstream URL、默认分支和完整 commit SHA。
2. 顶层目录、公开入口、可部署单元、外部系统、数据存储和关键配置均已分类。
3. 每条架构关键路径都能从入口追到核心逻辑、状态变化、外部副作用、错误处理和测试；重复模块可抽样，但必须说明抽样规则和未逐文件阅读的范围。
4. 架构图、领域模型、核心流程和运行时结论都可追溯到 SHA 固定的源码或实际命令证据。
5. 已选择一项真实贡献目标，并追踪它会改哪些代码、测试、文档和验证路径。
6. 所有关键 rubric 项均为 `verified` 或有理由的 `not_applicable`，且没有 critical gap。

这不等于逐行背诵。跨仓库依赖、私有服务或生产系统不自动纳入“整个项目”；若它们决定核心行为，先列为边界，再取得用户确认后扩展范围。

## 研究流程

### 1. 建立安全工作区

先向用户说明即将创建或同步 GitHub Fork、写入本地工作区，随后运行：

```bash
python3 <skill-dir>/scripts/repo_workflow.py <github-url> prepare
python3 <skill-dir>/scripts/repo_workflow.py <github-url> analysis
```

默认工作区是 `~/Github/os-wrapper`，可用 `--workspace-root` 覆盖。脚本会：

- 验证 URL 和公开仓库状态；
- 创建或同步当前 GitHub 用户的 Fork；
- 克隆到 `<workspace-root>/<repo-name>`；
- 建立独立分析副本 `<workspace-root>/<repo-name>-learning/analysis-<sha>`；
- 记录 `.understand-repo-state.json`，但不执行目标仓库代码。

遇到目录碰撞、错误 remote、脏工作树或 Fork 无法安全快进时停止，不强制覆盖。

### 2. 快扫后了解用户

先用目录、清单文件、README 和公开入口做只读快扫，再询问用户，避免问仓库已经能回答的问题。宿主有 `grill-me` Skill 时优先调用；否则执行同等访谈。一次只问一个问题；已有答案不要重问。至少确认：

- 语言和框架基础；
- 领域知识；
- 希望贡献的方向；
- 可投入时间；
- 能否运行容器、依赖服务或测试；
- 期望的解释深度。

将画像写入 `<repo-name>-learning/learner-profile.md`。按 `references/learning-assessment.md` 选择报告组织方式：新手先概念和最小心智模型，熟练者先设计取舍和扩展点。

### 3. 生成结构地图

优先调用宿主已安装的 Understand-Anything `understand` / `understand-domain` Skill，固定使用用户批准的 2.9.x 版本并执行 full scan。若未安装，说明所需版本与官方来源，取得确认后再安装；禁止静默安装或执行 `curl | sh`。

Understand-Anything 只提供结构候选，不是最终证据；CodeWiki 只作为报告组织方法的交叉参考，不安装或运行第二套索引服务。全量扫描后仍需直接核实：

- 构建、依赖、启动和部署入口；
- API、CLI、事件、任务或 UI 入口；
- 数据模型、状态机和外部集成；
- 核心调用路径及其测试；
- ADR、Issue、Release 或维护者文档中的设计声明。

不要依赖增量结果判断完整性。将分析产物保留在 learning 目录，不提交目标仓库生成的缓存。

### 4. 深挖并验证

按“入口 → 编排 → 领域逻辑 → 持久化/外部副作用 → 错误与恢复 → 测试”追踪至少三类代表性流程。默认使用 `standard` 预算；时间不足不会降低 `REPORT_PASS` 门槛，只会留下明确 gap 并返回 `PARTIAL`。

- 主成功路径；
- 关键失败或恢复路径；
- 一项用户选择的贡献路径。

遵循 `references/execution-policy.md` 判断命令。安全只读命令可直接执行；依赖安装、生命周期脚本、容器、云端、数据库、凭证或外部写操作必须先确认。无法运行时使用静态替代证据并降级报告，禁止把“应该能运行”写成“已验证”。

每条结论只使用四种证据等级：`Observed`、`Derived`、`Claimed`、`Unknown/Conflict`。代码本身也可能过期或未接线；通过调用关系、测试和运行结果交叉验证。

### 5. 生成并校验报告

创建报告分支 worktree：

```bash
python3 <skill-dir>/scripts/repo_workflow.py <github-url> report-worktree
```

只在该 worktree 的 `docs/ai-generated-wiki/` 写入 `references/report-contract.md` 规定的九个文件。所有源码链接固定到 upstream commit SHA。报告应能独立教学，但不要重复粘贴大段源码。

校验：

```bash
python3 <skill-dir>/scripts/validate_report.py <report-worktree>/docs/ai-generated-wiki
```

修复全部错误后再提交。不得为了通过校验虚构证据或把 `unknown` 改成 `verified`。执行仓库自带 hook 或构建命令前仍适用执行政策，禁止 `--no-verify`。

### 6. 创建 PR，不合并

在报告 worktree 中审阅 diff、提交，然后运行：

```bash
python3 <skill-dir>/scripts/repo_workflow.py <github-url> publish \
  --title "docs: add contributor learning guide" \
  --body-file <pr-body-file>
```

脚本只 push 分支并创建 Fork 内的 PR，不会 merge。PR 创建后运行 `cleanup` 删除报告 worktree，再向用户提供 PR、source SHA、报告状态、关键未知项和被拒绝/未执行的验证。用户明确批准 squash merge 后，才可合并。

## 最终考核

报告交付不等于学习者已掌握。仅当用户调用 `assess` 时，按 `references/learning-assessment.md` 进行一次自适应考核，要求其解释架构、追踪陌生路径、诊断失败并设计贡献。结果记录到 learning 目录：

- `LEARNER_READY`：能独立导航、解释和验证项目。
- `CONTRIBUTOR_READY`：还能提出范围合理、测试闭环完整的真实贡献方案。

考核失败只给出薄弱点和下一轮练习，不修改研究报告的 `REPORT_PASS` 状态。
