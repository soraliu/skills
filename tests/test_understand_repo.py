from contextlib import redirect_stdout
import importlib.util
from io import StringIO
import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch


ROOT = Path(__file__).parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


workflow = load("repo_workflow", ROOT / "skills/understand-repo/scripts/repo_workflow.py")
validator = load("validate_report", ROOT / "skills/understand-repo/scripts/validate_report.py")


class WorkflowTest(unittest.TestCase):
    def test_url_and_remote_validation(self):
        self.assertEqual(workflow.parse_github_url("https://github.com/acme/demo.git"), ("acme", "demo"))
        self.assertEqual(workflow.remote_identity("git@github.com:acme/demo.git"), ("acme", "demo"))
        with self.assertRaises(SystemExit):
            workflow.parse_github_url("https://github.com/acme/demo/issues")
        with self.assertRaises(SystemExit):
            workflow.parse_github_url("https://token@github.com/acme/demo")

    def test_prepare_uses_non_main_default_branch(self):
        sha = "b" * 40
        calls = []

        def fake_run(*args, **_kwargs):
            calls.append(args)
            if args[:4] == ("gh", "repo", "view", "acme/demo"):
                return CompletedProcess(args, 0, json.dumps({
                    "isPrivate": False, "defaultBranchRef": {"name": "release"},
                }), "")
            if args[:3] == ("gh", "api", "user"):
                return CompletedProcess(args, 0, "student\n", "")
            if args[:4] == ("gh", "repo", "view", "student/demo"):
                return CompletedProcess(args, 0, json.dumps({
                    "isFork": True, "parent": {"nameWithOwner": "acme/demo"},
                }), "")
            return CompletedProcess(args, 0, "", "")

        with tempfile.TemporaryDirectory() as directory, patch.object(workflow, "run", side_effect=fake_run), \
                patch.object(workflow, "ensure_clean"), patch.object(workflow, "ensure_remote"), \
                patch.object(workflow, "git_output", return_value=sha):
            root = Path(directory)
            with redirect_stdout(StringIO()):
                workflow.command_prepare("https://github.com/acme/demo", root)
            state = json.loads((root / "demo-learning/.understand-repo-state.json").read_text())
            self.assertEqual(state["default_branch"], "release")
            self.assertEqual(state["source_commit"], sha)
            self.assertIn(("gh", "repo", "sync", "student/demo", "--branch", "release"), calls)

    def test_path_collision_and_wrong_remote_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "demo").write_text("碰撞")
            with self.assertRaises(SystemExit):
                workflow.safe_child(root, "../outside")
            def read_only_github(*args, **_kwargs):
                if args[:4] == ("gh", "repo", "view", "acme/demo"):
                    return CompletedProcess(args, 0, json.dumps({
                        "isPrivate": False, "defaultBranchRef": {"name": "release"},
                    }), "")
                if args[:3] == ("gh", "api", "user"):
                    return CompletedProcess(args, 0, "acme\n", "")
                self.fail(f"目录碰撞后不应继续执行：{args}")

            with patch.object(workflow, "run", side_effect=read_only_github):
                with self.assertRaises(SystemExit):
                    workflow.command_prepare("https://github.com/acme/demo", root)
            with patch.object(workflow, "run", return_value=CompletedProcess([], 0, "https://github.com/other/demo.git\n", "")):
                with self.assertRaises(SystemExit):
                    workflow.ensure_remote(root, "origin", ("acme", "demo"))

    def test_tampered_state_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            learning = root / "demo-learning"
            learning.mkdir()
            state = {
                "schema_version": "1.0",
                "upstream_url": "https://github.com/acme/demo",
                "default_branch": "release",
                "source_commit": "a" * 40,
                "clone_dir": "/tmp/not-the-workspace",
                "learning_dir": str(learning),
            }
            (learning / ".understand-repo-state.json").write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaises(SystemExit):
                workflow.load_state("https://github.com/acme/demo", root)


class ReportTest(unittest.TestCase):
    def test_pass_and_unpinned_source(self):
        sha = "a" * 40
        timestamp = "2026-08-21T00:00:00+00:00"
        frontmatter = (
            "---\n"
            "generated_by: understand-repo\n"
            f"source_commit: {sha}\n"
            f"generated_at: {timestamp}\n"
            "status: REPORT_PASS\n"
            "---\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory)
            for name in validator.MARKDOWN_FILES:
                body = "# 页面\n\n[E-001]\n"
                if name == "evidence.md":
                    body = f"# 证据\n\n## E-001 — 源码\n\n- 等级: Observed\n- 来源: https://github.com/acme/demo/blob/{sha}/src/main.py#L1\n"
                (report / name).write_text(frontmatter + body, encoding="utf-8")
            manifest = {
                "schema_version": "1.0",
                "generator": {
                    "skill": "understand-repo",
                    "skill_version": "0.1.0",
                    "engine": {"name": "Understand-Anything", "version": "2.9.x", "commit": ""},
                },
                "repository": {
                    "upstream_url": "https://github.com/acme/demo",
                    "fork_url": "https://github.com/student/demo",
                    "default_branch": "release",
                    "source_commit": sha,
                    "generated_at": timestamp,
                },
                "scope": {
                    "budget": "standard",
                    "contribution_target": "补充回归测试",
                    "included": ["src"],
                    "excluded": [],
                    "capability_notes": [],
                },
                "status": "REPORT_PASS",
                "runtime": {"verdict": "observed"},
                "rubric": [{
                    "id": rubric_id, "critical": True, "status": "verified",
                    "evidence_level": "observed", "evidence_ids": ["E-001"],
                } for rubric_id in validator.REQUIRED_RUBRIC_IDS],
                "gaps": [],
            }
            (report / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(validator.validate(report), [])
            evidence = report / "evidence.md"
            evidence.write_text(evidence.read_text().replace(f"blob/{sha}", "blob/main"), encoding="utf-8")
            self.assertTrue(any("source_commit" in error for error in validator.validate(report)))
            evidence.write_text(evidence.read_text().replace("blob/main", f"blob/{sha}"), encoding="utf-8")
            evidence.write_text(evidence.read_text().replace("Observed", "Claimed"), encoding="utf-8")
            self.assertTrue(any("弱证据" in error for error in validator.validate(report)))
            evidence.write_text(evidence.read_text().replace("Claimed", "Observed"), encoding="utf-8")
            index = report / "index.md"
            index.write_text(index.read_text() + "\ngithub_pat_" + "x" * 24, encoding="utf-8")
            self.assertTrue(any("凭证" in error for error in validator.validate(report)))

    def test_malformed_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory)
            for name in validator.MARKDOWN_FILES:
                (report / name).write_text("---\ngenerated_by: understand-repo\n---\n", encoding="utf-8")
            (report / "manifest.json").write_text("[]", encoding="utf-8")
            errors = validator.validate(report)
            self.assertTrue(any("顶层" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
