from __future__ import annotations

import json
import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from scripts.ci.validate_pr_report import REPORT_FIELDS, main, validate_pr_report

ROOT = Path(__file__).resolve().parents[2]


class ValidatePrReportTest(unittest.TestCase):
    def event(self, body: str | None) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "event.json"
        path.write_text(json.dumps({"pull_request": {"body": body}}), encoding="utf-8")
        return path

    @staticmethod
    def report(overrides: dict[str, str] | None = None) -> str:
        values = {
            "验证环境": "Ubuntu 24.04；Codex CLI",
            "获取的上游 commit": "0123456789abcdef0123456789abcdef01234567",
            "安装目标": "临时项目的 .agents/skills/example",
            "安装步骤": "复制完整技能目录并确认依赖文件",
            "完整性检查": "SKILL.md 与引用文件均存在",
            "使用任务": "运行一个固定示例任务",
            "实际结果": "Agent 发现技能并完成示例",
            "未验证项": "无",
            "已知限制": "仅在 Linux 环境执行",
        }
        values.update(overrides or {})
        lines = ["## 技能验证报告", ""]
        lines.extend(f"- {field}：{values[field]}" for field in REPORT_FIELDS)
        return "\n".join(lines)

    def test_docs_only_pr_is_exempt(self) -> None:
        self.assertEqual(validate_pr_report(self.event(""), ["README.md", "docs/guide.md"]), [])

    def test_entry_or_translation_change_requires_report(self) -> None:
        expected = ["缺少“## 技能验证报告”章节。"]
        self.assertEqual(validate_pr_report(self.event("普通说明"), ["entries/l1/acme/demo.yaml"]), expected)
        self.assertEqual(validate_pr_report(self.event(None), ["translations/acme/demo.md"]), expected)

    def test_complete_report_is_accepted(self) -> None:
        self.assertEqual(validate_pr_report(self.event(self.report()), ["entries/l1/acme/demo.yaml"]), [])

    def test_empty_field_is_rejected_with_stable_error(self) -> None:
        errors = validate_pr_report(self.event(self.report({"实际结果": ""})), ["translations/acme/demo.md"])
        self.assertEqual(errors, ["技能验证报告字段为空：实际结果"])

    def test_renamed_heading_is_not_accepted(self) -> None:
        body = self.report().replace("## 技能验证报告", "## 技能测试报告")
        self.assertEqual(validate_pr_report(self.event(body), ["entries/l1/acme/demo.yaml"]), ["缺少“## 技能验证报告”章节。"])

    def test_multiline_field_values_are_accepted(self) -> None:
        body = self.report({"安装步骤": ""}).replace(
            "- 安装步骤：\n",
            "- 安装步骤：\n  1. 创建临时目录\n  2. 复制完整技能目录\n",
        )
        self.assertEqual(validate_pr_report(self.event(body), ["skills-src/l1/acme/demo/SKILL.md"]), [])

    def test_missing_field_is_rejected(self) -> None:
        body = self.report().replace("- 未验证项：无\n", "")
        self.assertEqual(validate_pr_report(self.event(body), ["entries/l1/acme/demo.yaml"]), ["技能验证报告缺少字段：未验证项"])

    def test_report_hidden_across_fields_by_html_comment_is_rejected(self) -> None:
        body = self.report().replace("- 验证环境：", "<!--\n- 验证环境：").replace("- 已知限制：仅在 Linux 环境执行", "- 已知限制：仅在 Linux 环境执行\n-->")
        self.assertEqual(
            validate_pr_report(self.event(body), ["entries/l1/acme/demo.yaml"]),
            [f"技能验证报告缺少字段：{field}" for field in REPORT_FIELDS],
        )

    def test_unclosed_html_comment_cannot_supply_field_values(self) -> None:
        body = self.report().replace("- 实际结果：Agent 发现技能并完成示例", "- 实际结果：<!-- Agent 发现技能并完成示例")
        errors = validate_pr_report(self.event(body), ["translations/acme/demo.md"])
        self.assertEqual(errors, ["技能验证报告字段为空：实际结果", "技能验证报告缺少字段：未验证项", "技能验证报告缺少字段：已知限制"])

    def test_pull_request_template_contains_the_exact_empty_report(self) -> None:
        template = (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")
        errors = validate_pr_report(self.event(template), ["entries/l1/acme/demo.yaml"])
        self.assertEqual(errors, [f"技能验证报告字段为空：{field}" for field in REPORT_FIELDS])
        for field in REPORT_FIELDS:
            self.assertEqual(template.count(f"- {field}："), 1)

    def test_workflow_passes_event_file_and_changed_path_file(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn('git diff --name-only -z "$BASE_SHA" "$HEAD_SHA"', workflow)
        self.assertIn("validate_pr_report.py --changed-paths-file", workflow)
        self.assertIn("github.event_name == 'pull_request'", workflow)
        self.assertIn("edited", workflow)
        self.assertNotIn("github.event.pull_request.body", workflow)

    def test_cli_reads_non_ascii_nul_delimited_paths_without_git_quoting_bypass(self) -> None:
        event_path = self.event("普通说明")
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        changed_paths_file = Path(directory.name) / "paths"
        changed_paths_file.write_bytes("skills-src/l1/acme/示例.md\0".encode())
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = main(["--event-path", str(event_path), "--changed-paths-file", str(changed_paths_file)])
        self.assertEqual(result, 1)
        self.assertIn("缺少“## 技能验证报告”章节。", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
