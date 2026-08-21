#!/usr/bin/env python3
"""为 understand-repo 管理安全、可恢复的 GitHub 报告工作区。"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlsplit


NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def fail(message: str) -> NoReturn:
    raise SystemExit(message)


def parse_github_url(value: str) -> tuple[str, str]:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.query
        or parsed.fragment
    ):
        fail("只接受不含凭证、query 或 fragment 的 https://github.com/OWNER/REPO URL")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        fail("GitHub URL 必须准确指向仓库根路径")
    owner, repo = parts
    repo = repo.removesuffix(".git")
    if owner in {".", ".."} or repo in {".", ".."} or not all(
        NAME_RE.fullmatch(part) for part in (owner, repo)
    ):
        fail("GitHub owner 或仓库名不合法")
    return owner, repo


def remote_identity(value: str) -> tuple[str, str] | None:
    value = value.strip().removesuffix(".git")
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+)/([^/]+)", value)
    return (match.group(1), match.group(2)) if match else None


def run(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"退出码 {result.returncode}"
        fail(f"命令失败：{' '.join(args)}\n{detail}")
    return result


def safe_child(root: Path, name: str) -> Path:
    root = root.expanduser().resolve()
    candidate = root / name
    if candidate.is_symlink():
        fail(f"拒绝使用符号链接路径：{candidate}")
    target = candidate.resolve()
    if target.parent != root:
        fail(f"路径越界：{target}")
    return target


def state_paths(url: str, workspace_root: Path) -> tuple[Path, Path, Path]:
    _, repo = parse_github_url(url)
    clone_dir = safe_child(workspace_root, repo)
    learning_dir = safe_child(workspace_root, f"{repo}-learning")
    return clone_dir, learning_dir, learning_dir / ".understand-repo-state.json"


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        fail(f"拒绝写入符号链接目录：{path.parent}")
    temporary = path.with_suffix(".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as file:
            file.write(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    except FileExistsError:
        fail(f"临时状态文件已存在，拒绝覆盖：{temporary}")
    temporary.replace(path)


def load_state(url: str, workspace_root: Path) -> tuple[dict, Path]:
    clone_dir, learning_dir, state_path = state_paths(url, workspace_root)
    if not state_path.is_file() or state_path.is_symlink():
        fail(f"状态文件不存在或不安全，请先运行 prepare：{state_path}")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        fail(f"状态文件无法解析：{error}")
    if not isinstance(state, dict) or state.get("schema_version") != "1.0":
        fail("状态文件结构不合法")
    if state.get("upstream_url") != canonical_url(url):
        fail("状态文件与当前 URL 不匹配")
    if Path(state.get("clone_dir", "")).resolve() != clone_dir or Path(
        state.get("learning_dir", "")
    ).resolve() != learning_dir:
        fail("状态文件包含工作区外路径")
    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", state.get("source_commit", "")):
        fail("状态文件 source_commit 不合法")
    branch = state.get("default_branch", "")
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch) or ".." in branch or branch.startswith("-"):
        fail("状态文件 default_branch 不合法")
    return state, state_path


def canonical_url(url: str) -> str:
    owner, repo = parse_github_url(url)
    return f"https://github.com/{owner}/{repo}"


def git_output(repo: Path, *args: str) -> str:
    return run("git", "-C", str(repo), *args).stdout.strip()


def ensure_clean(repo: Path) -> None:
    if git_output(repo, "status", "--porcelain"):
        fail(f"工作树不干净，拒绝继续：{repo}")


def ensure_tracked_clean(repo: Path) -> None:
    if run("git", "-C", str(repo), "diff", "--quiet").returncode or run(
        "git", "-C", str(repo), "diff", "--cached", "--quiet"
    ).returncode:
        fail(f"分析目录包含已跟踪文件改动，拒绝继续：{repo}")


def ensure_remote(repo: Path, name: str, expected: tuple[str, str], url: str | None = None) -> None:
    result = run("git", "-C", str(repo), "remote", "get-url", name, check=False)
    if result.returncode:
        if not url:
            fail(f"缺少 remote：{name}")
        run("git", "-C", str(repo), "remote", "add", name, url)
        return
    identity = remote_identity(result.stdout)
    if not identity or tuple(part.lower() for part in identity) != tuple(part.lower() for part in expected):
        fail(f"remote {name} 指向意外仓库：{result.stdout.strip()}")


def command_prepare(url: str, workspace_root: Path) -> None:
    upstream_owner, repo_name = parse_github_url(url)
    upstream = f"{upstream_owner}/{repo_name}"
    clone_dir, learning_dir, state_path = state_paths(url, workspace_root)
    if workspace_root.expanduser().exists() and not workspace_root.expanduser().is_dir():
        fail(f"workspace root 不是目录：{workspace_root}")
    workspace_root.expanduser().mkdir(parents=True, exist_ok=True)

    metadata = json.loads(run("gh", "repo", "view", upstream, "--json", "nameWithOwner,defaultBranchRef,isPrivate").stdout)
    if metadata.get("isPrivate"):
        fail("只研究公开 GitHub 仓库")
    default_branch_ref = metadata.get("defaultBranchRef")
    default_branch = default_branch_ref.get("name") if isinstance(default_branch_ref, dict) else None
    if not default_branch:
        fail("无法确定默认分支")

    login = run("gh", "api", "user", "--jq", ".login").stdout.strip()
    if not NAME_RE.fullmatch(login):
        fail("无法确定当前 GitHub 用户")
    fork = f"{login}/{repo_name}"

    if learning_dir.exists() and (learning_dir.is_symlink() or not learning_dir.is_dir()):
        fail(f"学习目录碰撞：{learning_dir}")
    if clone_dir.exists():
        if clone_dir.is_symlink() or not (clone_dir / ".git").exists():
            fail(f"克隆目录碰撞：{clone_dir}")
        ensure_clean(clone_dir)
        ensure_remote(clone_dir, "origin", (login, repo_name))
        upstream_result = run("git", "-C", str(clone_dir), "remote", "get-url", "upstream", check=False)
        if upstream_result.returncode == 0:
            identity = remote_identity(upstream_result.stdout)
            if not identity or tuple(part.lower() for part in identity) != (
                upstream_owner.lower(), repo_name.lower()
            ):
                fail(f"remote upstream 指向意外仓库：{upstream_result.stdout.strip()}")

    if fork.lower() != upstream.lower():
        fork_result = run("gh", "repo", "view", fork, "--json", "isFork,parent", check=False)
        if fork_result.returncode:
            run("gh", "repo", "fork", upstream, "--clone=false")
        else:
            fork_metadata = json.loads(fork_result.stdout)
            parent = fork_metadata.get("parent") or {}
            if not fork_metadata.get("isFork") or parent.get("nameWithOwner", "").lower() != upstream.lower():
                fail(f"{fork} 已存在，但不是 {upstream} 的 Fork")
        run("gh", "repo", "sync", fork, "--branch", default_branch)

    if not clone_dir.exists():
        run("gh", "repo", "clone", fork, str(clone_dir))

    ensure_clean(clone_dir)
    ensure_remote(clone_dir, "origin", (login, repo_name))
    ensure_remote(clone_dir, "upstream", (upstream_owner, repo_name), f"https://github.com/{upstream}.git")
    run("git", "-C", str(clone_dir), "fetch", "origin", "--prune")
    run("git", "-C", str(clone_dir), "fetch", "upstream", "--prune")
    source_commit = git_output(clone_dir, "rev-parse", f"refs/remotes/upstream/{default_branch}")
    state = {
        "schema_version": "1.0",
        "upstream_url": f"https://github.com/{upstream}",
        "fork_url": f"https://github.com/{fork}",
        "default_branch": default_branch,
        "source_commit": source_commit,
        "clone_dir": str(clone_dir),
        "learning_dir": str(learning_dir),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_state(state_path, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))


def command_analysis(url: str, workspace_root: Path) -> None:
    state, state_path = load_state(url, workspace_root)
    clone_dir = Path(state["clone_dir"])
    analysis_dir = Path(state["learning_dir"]) / f"analysis-{state['source_commit'][:12]}"
    if analysis_dir.exists():
        if analysis_dir.is_symlink() or not (analysis_dir / ".git").exists():
            fail(f"分析目录碰撞：{analysis_dir}")
        if git_output(analysis_dir, "rev-parse", "HEAD") != state["source_commit"]:
            fail(f"现有分析目录 SHA 不匹配：{analysis_dir}")
        # Understand-Anything 会生成未跟踪缓存；只拒绝源文件被修改。
        ensure_tracked_clean(analysis_dir)
    else:
        run("git", "clone", "--local", "--no-hardlinks", str(clone_dir), str(analysis_dir))
        run("git", "-C", str(analysis_dir), "checkout", "--detach", state["source_commit"])
    state["analysis_dir"] = str(analysis_dir)
    save_state(state_path, state)
    print(analysis_dir)


def command_report_worktree(url: str, workspace_root: Path, branch: str | None) -> None:
    state, state_path = load_state(url, workspace_root)
    clone_dir = Path(state["clone_dir"])
    ensure_clean(clone_dir)
    short_sha = state["source_commit"][:12]
    branch = branch or f"docs/ai-generated-wiki-{short_sha}"
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch) or ".." in branch or branch.startswith("-"):
        fail("报告分支名不合法")
    worktree = Path(state["learning_dir"]) / "worktrees" / f"report-{short_sha}"
    worktree_parent = worktree.parent
    if worktree_parent.is_symlink():
        fail(f"拒绝使用符号链接目录：{worktree_parent}")
    recorded_worktree = state.get("report_worktree")
    if recorded_worktree == str(worktree) and worktree.is_dir():
        if git_output(worktree, "branch", "--show-current") != branch:
            fail(f"现有报告 worktree 分支不匹配：{worktree}")
        print(worktree)
        return
    if worktree.exists():
        fail(f"报告 worktree 已存在：{worktree}")
    if run("git", "-C", str(clone_dir), "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode == 0:
        fail(f"本地分支已存在：{branch}")
    if run("git", "-C", str(clone_dir), "ls-remote", "--exit-code", "--heads", "origin", branch, check=False).returncode == 0:
        fail(f"远端分支已存在：{branch}")
    worktree_parent.mkdir(parents=True, exist_ok=True)
    run("git", "-C", str(clone_dir), "worktree", "add", "-b", branch, str(worktree), state["source_commit"])
    state.update({"report_branch": branch, "report_worktree": str(worktree)})
    save_state(state_path, state)
    print(worktree)


def command_publish(url: str, workspace_root: Path, title: str, body_file: Path) -> None:
    state, state_path = load_state(url, workspace_root)
    worktree = Path(state.get("report_worktree", ""))
    branch = state.get("report_branch")
    expected_worktree = Path(state["learning_dir"]) / "worktrees" / f"report-{state['source_commit'][:12]}"
    if worktree.is_symlink() or worktree.resolve() != expected_worktree.resolve():
        fail("状态文件包含意外的报告 worktree 路径")
    if not branch or not re.fullmatch(r"[A-Za-z0-9._/-]+", branch) or ".." in branch or branch.startswith("-"):
        fail("状态文件包含非法报告分支")
    if not worktree.is_dir():
        fail("缺少报告 worktree，请先运行 report-worktree")
    ensure_clean(worktree)
    if not (worktree / "docs" / "ai-generated-wiki" / "manifest.json").is_file():
        fail("报告 manifest 不存在")
    if not body_file.is_file() or body_file.is_symlink():
        fail(f"PR body 文件不存在或不安全：{body_file}")
    if int(git_output(worktree, "rev-list", "--count", f"{state['source_commit']}..HEAD")) < 1:
        fail("报告分支没有提交")
    run("git", "-C", str(worktree), "push", "-u", "origin", branch)
    existing = run(
        "gh", "pr", "list", "--repo", remote_slug(state["fork_url"]),
        "--head", branch, "--state", "open", "--json", "url", "--jq", ".[0].url",
        check=False,
    )
    if existing.returncode == 0 and existing.stdout.strip():
        pr_url = existing.stdout.strip()
    else:
        pr_url = run(
            "gh", "pr", "create", "--repo", remote_slug(state["fork_url"]),
            "--base", state["default_branch"], "--head", branch,
            "--title", title, "--body-file", str(body_file),
        ).stdout.strip()
    state["pr_url"] = pr_url
    save_state(state_path, state)
    print(pr_url)


def remote_slug(url: str) -> str:
    owner, repo = parse_github_url(url)
    return f"{owner}/{repo}"


def command_cleanup(url: str, workspace_root: Path) -> None:
    state, state_path = load_state(url, workspace_root)
    worktree_value = state.get("report_worktree")
    if not state.get("pr_url") or not worktree_value:
        fail("只有已创建 PR 的报告 worktree 才能清理")
    worktree = Path(worktree_value)
    expected_worktree = Path(state["learning_dir"]) / "worktrees" / f"report-{state['source_commit'][:12]}"
    if worktree.is_symlink() or worktree.resolve() != expected_worktree.resolve():
        fail("状态文件包含意外的报告 worktree 路径")
    if worktree.exists():
        ensure_clean(worktree)
        run("git", "-C", state["clone_dir"], "worktree", "remove", str(worktree))
    state["report_worktree_removed"] = True
    save_state(state_path, state)
    print(worktree)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="公开 GitHub 仓库 URL")
    parser.add_argument("--workspace-root", type=Path, default=Path.home() / "Github" / "os-wrapper")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("prepare")
    subparsers.add_parser("analysis")
    report = subparsers.add_parser("report-worktree")
    report.add_argument("--branch")
    publish = subparsers.add_parser("publish")
    publish.add_argument("--title", required=True)
    publish.add_argument("--body-file", type=Path, required=True)
    subparsers.add_parser("cleanup")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    commands = {
        "prepare": lambda: command_prepare(args.url, args.workspace_root),
        "analysis": lambda: command_analysis(args.url, args.workspace_root),
        "report-worktree": lambda: command_report_worktree(args.url, args.workspace_root, args.branch),
        "publish": lambda: command_publish(args.url, args.workspace_root, args.title, args.body_file),
        "cleanup": lambda: command_cleanup(args.url, args.workspace_root),
    }
    commands[args.command]()


if __name__ == "__main__":
    main()
