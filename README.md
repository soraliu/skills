# skills

基于 [Agent Skills](https://agentskills.io/) 开放规范维护的跨 Agent Harness Skills。每个 Skill 都以 `skills/<name>/SKILL.md` 作为唯一真源，由安装工具映射到各 Harness 的目录。

## Skills

### `discover-open-source`

输入一个主题，先澄清采用场景，再从 GitHub 和官方来源发现、评估开源项目；同时识别曾经优秀但现在已经过时的历史项目，说明过时原因和现代替代方案。

```text
# Codex
$discover-open-source 本地 AI 编码 Agent

# Claude Code / Hermes
/discover-open-source 本地 AI 编码 Agent
```

### `understand-repo`

输入一个公开 GitHub 仓库 URL，先根据学习者背景定制研究路径，再生成带固定源码证据、运行验证、贡献路径和最终交互考核的项目学习报告。

```text
# Codex
$understand-repo https://github.com/owner/repo
$understand-repo assess repo

# Claude Code / Hermes
/understand-repo https://github.com/owner/repo
/understand-repo assess repo
```

## 安装

使用 [skills CLI](https://github.com/vercel-labs/skills) 安装：

```bash
# 交互选择 Harness
npx skills add soraliu/skills --skill discover-open-source

# 安装仓库理解 Skill
npx skills add soraliu/skills --skill understand-repo

# Codex
npx skills add soraliu/skills --skill discover-open-source --agent codex

# Claude Code
npx skills add soraliu/skills --skill discover-open-source --agent claude-code
```

### Codex 全局安装与手动同步

先查看仓库当前提供的 Skill：

```bash
npx skills add soraliu/skills --list
```

安装或刷新仓库中的全部 Skill：

```bash
npx skills add soraliu/skills --skill '*' --agent codex --global --yes
```

每次执行都会重新读取仓库，并安装新 Skill、覆盖已有版本。`'*'` 包含仓库未来新增的 Skill，也可能覆盖同名的全局 Skill，因此更新前应先运行 `--list` 检查。

验证 Codex 的全局安装结果：

```bash
npx skills list --global --agent codex --json
```

Codex 会自动读取本地 Skill 变化。如果当前会话仍显示旧内容，请重启 Codex。

Codex 也可以通过 `$skill-installer` 安装：

```text
$skill-installer install https://github.com/soraliu/skills/tree/main/skills/discover-open-source
$skill-installer install https://github.com/soraliu/skills/tree/main/skills/understand-repo
```

Hermes 可以直接安装单个 Skill，或把仓库注册为 tap：

```bash
hermes skills install soraliu/skills/skills/discover-open-source
hermes skills install soraliu/skills/skills/understand-repo

hermes skills tap add soraliu/skills
hermes skills install soraliu/skills/discover-open-source
```

## 许可证

[Apache License 2.0](LICENSE)
