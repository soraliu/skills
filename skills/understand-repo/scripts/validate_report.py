#!/usr/bin/env python3
"""校验 understand-repo 报告的结构、证据和 REPORT_PASS 门槛。"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path


MARKDOWN_FILES = (
    "index.md",
    "project-and-domain.md",
    "architecture.md",
    "core-flows.md",
    "runtime-and-quality.md",
    "contributing.md",
    "learning-path.md",
    "evidence.md",
)
STATUSES = {"REPORT_PASS", "PARTIAL", "BLOCKED"}
RUBRIC_STATUSES = {"verified", "not_applicable", "unknown"}
EVIDENCE_LEVELS = {"observed", "derived", "claimed", "unknown_conflict", "unknown"}
REQUIRED_RUBRIC_IDS = {
    "project-domain",
    "repository-map",
    "architecture",
    "core-flows",
    "runtime-config",
    "quality-security",
    "contribution-path",
    "learning-path",
}
SECRET_PATTERNS = (
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def parse_frontmatter(text: str) -> dict[str, str] | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    values: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            return None
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"\'')
    return values


def valid_time(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def validate(report_dir: Path) -> list[str]:
    errors: list[str] = []
    if report_dir.is_symlink():
        return ["报告目录不得是符号链接"]
    report_dir = report_dir.resolve()
    required = [report_dir / name for name in (*MARKDOWN_FILES, "manifest.json")]
    for path in required:
        if not path.is_file() or path.is_symlink():
            errors.append(f"缺少文件或文件不安全：{path.name}")
    if errors:
        return errors
    if any(path.is_symlink() for path in report_dir.rglob("*")):
        errors.append("报告目录不得包含符号链接")

    try:
        manifest = json.loads((report_dir / "manifest.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        return errors + [f"manifest.json 无法解析：{error}"]

    if not isinstance(manifest, dict):
        return errors + ["manifest.json 顶层必须是对象"]
    status = manifest.get("status")
    generator = manifest.get("generator")
    repository = manifest.get("repository")
    scope = manifest.get("scope")
    runtime = manifest.get("runtime")
    gaps = manifest.get("gaps")
    if not isinstance(generator, dict):
        errors.append("generator 必须是对象")
        generator = {}
    if not isinstance(repository, dict):
        errors.append("repository 必须是对象")
        repository = {}
    if not isinstance(scope, dict):
        errors.append("scope 必须是对象")
        scope = {}
    if not isinstance(runtime, dict):
        errors.append("runtime 必须是对象")
        runtime = {}
    if not isinstance(gaps, list):
        errors.append("gaps 必须是数组")
        gaps = []
    source_commit = repository.get("source_commit", "")
    if manifest.get("schema_version") != "1.0":
        errors.append("schema_version 必须为 1.0")
    if generator.get("skill") != "understand-repo":
        errors.append("generator.skill 必须为 understand-repo")
    if status not in STATUSES:
        errors.append("报告 status 不合法")
    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", source_commit):
        errors.append("source_commit 必须是 40 或 64 位小写十六进制 SHA")
    if not valid_time(repository.get("generated_at")):
        errors.append("repository.generated_at 必须是 ISO 8601 时间")
    for key in ("upstream_url", "fork_url", "default_branch"):
        if not isinstance(repository.get(key), str) or not repository[key]:
            errors.append(f"repository.{key} 不能为空")
    for key in ("upstream_url", "fork_url"):
        if repository.get(key) and not re.fullmatch(
            r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository[key]
        ):
            errors.append(f"repository.{key} 必须是规范 GitHub 仓库 URL")
    if not isinstance(scope.get("contribution_target"), str) or not scope.get("contribution_target"):
        errors.append("scope.contribution_target 不能为空")
    for key in ("included", "excluded", "capability_notes"):
        if not isinstance(scope.get(key), list):
            errors.append(f"scope.{key} 必须是数组")

    texts: dict[str, str] = {}
    for name in MARKDOWN_FILES:
        text = (report_dir / name).read_text(encoding="utf-8")
        texts[name] = text
        frontmatter = parse_frontmatter(text)
        if frontmatter is None:
            errors.append(f"{name} 缺少合法 frontmatter")
            continue
        expected = {
            "generated_by": "understand-repo",
            "source_commit": source_commit,
            "status": status,
        }
        for key, value in expected.items():
            if frontmatter.get(key) != value:
                errors.append(f"{name} 的 {key} 与 manifest 不一致")
        if not valid_time(frontmatter.get("generated_at")):
            errors.append(f"{name} 的 generated_at 不是 ISO 8601 时间")
        elif frontmatter.get("generated_at") != repository.get("generated_at"):
            errors.append(f"{name} 的 generated_at 与 manifest 不一致")

    all_text = "\n".join(texts.values()) + json.dumps(manifest, ensure_ascii=False)
    if any(pattern.search(all_text) for pattern in SECRET_PATTERNS):
        errors.append("报告疑似包含凭证或私钥")

    evidence_text = texts.get("evidence.md", "")
    evidence_sections = re.findall(
        r"^## (E-\d{3})\b(.*?)(?=^## E-\d{3}\b|\Z)",
        evidence_text,
        re.MULTILINE | re.DOTALL,
    )
    evidence_headings = {evidence_id for evidence_id, _ in evidence_sections}
    evidence_levels = {
        evidence_id: match.group(1).lower().replace("/", "_")
        for evidence_id, body in evidence_sections
        if (match := re.search(r"^- 等级:\s*(Observed|Derived|Claimed|Unknown/Conflict)\s*$", body, re.MULTILINE))
    }
    referenced = set(re.findall(r"\[(E-\d{3})\]", "\n".join(texts.values())))
    for evidence_id in sorted(referenced - evidence_headings):
        errors.append(f"引用了不存在的证据：{evidence_id}")
    if len(evidence_headings) != len(re.findall(r"^## E-\d{3}\b", evidence_text, re.MULTILINE)):
        errors.append("evidence.md 存在重复 evidence ID")
    for evidence_id in evidence_headings - evidence_levels.keys():
        errors.append(f"证据缺少合法等级：{evidence_id}")

    github_sources = re.findall(r"https://github\.com/[^\s)>]+/(?:blob|tree)/([^/\s)>]+)/[^\s)>]+", all_text)
    for revision in github_sources:
        if revision != source_commit:
            errors.append(f"源码链接未固定到 source_commit：{revision}")

    for name, text in texts.items():
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            local = (report_dir / target.split("#", 1)[0]).resolve()
            if report_dir not in local.parents and local != report_dir:
                errors.append(f"{name} 的链接越出报告目录：{target}")
            elif not local.exists():
                errors.append(f"{name} 的本地链接不存在：{target}")

    rubric = manifest.get("rubric")
    if not isinstance(rubric, list) or not rubric:
        errors.append("rubric 必须是非空数组")
        rubric = []
    rubric_ids = {item.get("id") for item in rubric if isinstance(item, dict)}
    if len(rubric_ids) != len(rubric):
        errors.append("rubric id 必须唯一")
    missing_rubric = REQUIRED_RUBRIC_IDS - rubric_ids
    if missing_rubric:
        errors.append(f"缺少 rubric：{', '.join(sorted(missing_rubric))}")
    for item in rubric:
        if not isinstance(item, dict):
            errors.append("rubric 项必须是对象")
            continue
        item_status = item.get("status")
        level = item.get("evidence_level")
        ids = item.get("evidence_ids")
        if item_status not in RUBRIC_STATUSES:
            errors.append(f"rubric {item.get('id', '?')} 的 status 不合法")
        if level not in EVIDENCE_LEVELS:
            errors.append(f"rubric {item.get('id', '?')} 的 evidence_level 不合法")
        if not isinstance(ids, list) or any(evidence_id not in evidence_headings for evidence_id in ids):
            errors.append(f"rubric {item.get('id', '?')} 引用了无效证据")
        elif item_status == "verified" and any(
            evidence_levels.get(evidence_id) not in {"observed", "derived"} for evidence_id in ids
        ):
            errors.append(f"verified rubric 引用了弱证据：{item.get('id', '?')}")
        if item.get("id") in REQUIRED_RUBRIC_IDS and item.get("critical") is not True:
            errors.append(f"必需 rubric 必须标记为 critical：{item.get('id')}")

    if status == "REPORT_PASS":
        for item in rubric:
            if isinstance(item, dict) and item.get("critical") and item.get("status") not in {"verified", "not_applicable"}:
                errors.append(f"关键 rubric 未通过：{item.get('id', '?')}")
            if isinstance(item, dict) and item.get("status") == "verified":
                if item.get("evidence_level") not in {"observed", "derived"} or not item.get("evidence_ids"):
                    errors.append(f"verified rubric 缺少强证据：{item.get('id', '?')}")
        if runtime.get("verdict") not in {"observed", "not_applicable"}:
            errors.append("REPORT_PASS 需要运行证据或明确 not_applicable")
        if any(gap.get("severity") == "critical" for gap in gaps if isinstance(gap, dict)):
            errors.append("REPORT_PASS 不得包含 critical gap")
        upstream_url = repository.get("upstream_url", "").removesuffix("/")
        if not upstream_url or not re.search(
            rf"{re.escape(upstream_url)}/(?:blob|tree)/{re.escape(source_commit)}/", all_text
        ):
            errors.append("REPORT_PASS 至少需要一个固定到 source_commit 的 GitHub 源码链接")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report_dir", type=Path)
    args = parser.parse_args()
    errors = validate(args.report_dir)
    print(json.dumps({"ok": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    raise SystemExit(bool(errors))


if __name__ == "__main__":
    main()
