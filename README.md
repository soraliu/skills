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

## 安装

使用 [skills CLI](https://github.com/vercel-labs/skills) 安装：

```bash
# 交互选择 Harness
npx skills add soraliu/skills --skill discover-open-source

# Codex
npx skills add soraliu/skills --skill discover-open-source --agent codex

# Claude Code
npx skills add soraliu/skills --skill discover-open-source --agent claude-code
```

Codex 也可以通过 `$skill-installer` 安装：

```text
$skill-installer install https://github.com/soraliu/skills/tree/main/skills/discover-open-source
```

Hermes 可以直接安装单个 Skill，或把仓库注册为 tap：

```bash
hermes skills install soraliu/skills/skills/discover-open-source

hermes skills tap add soraliu/skills
hermes skills install soraliu/skills/discover-open-source
```

## 许可证

[Apache License 2.0](LICENSE)
