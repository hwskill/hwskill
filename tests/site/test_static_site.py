from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest
from urllib.parse import urlsplit


ROOT = Path(__file__).parents[2]
SITE = ROOT / "site"


class StaticSiteContractTests(unittest.TestCase):
    def test_contribution_page_offers_skill_and_recommendation_prompts_that_end_in_a_pr(self) -> None:
        contribute = (SITE / "src/pages/contribute/index.astro").read_text(encoding="utf-8")
        prompts = (SITE / "src/lib/contribution-prompts.ts").read_text(encoding="utf-8")
        copy_component = (SITE / "src/components/CopyPrompt.astro").read_text(encoding="utf-8")
        contribution_sources = contribute + prompts
        for prompt_id in ("skill-contribution-prompt", "recommendation-contribution-prompt"):
            self.assertIn(prompt_id, contribute)
        for resource in (
            "schemas/entry.schema.json",
            "schemas/recommendation-source.schema.json",
            "templates/entries/external.yaml",
            "templates/recommendations/recommendation.md",
            "data/catalog.json",
        ):
            self.assertIn(resource, contribution_sources)
        self.assertIn("未收录", contribution_sources)
        self.assertIn("同一个 Pull Request", contribution_sources)
        self.assertGreaterEqual(prompts.count("本提示词授权你"), 2)
        self.assertIn("不授权合并", prompts)
        self.assertRegex(prompts, r"最终[^`\n]*(?:Pull Request|PR)[^`\n]*URL")
        self.assertIn("querySelectorAll", copy_component)

    def test_repository_has_a_pull_request_template_for_agent_contributions(self) -> None:
        template = ROOT / ".github/PULL_REQUEST_TEMPLATE.md"
        self.assertTrue(template.is_file())
        text = template.read_text(encoding="utf-8")
        for section in ("变更内容", "来源与版本", "验证", "验证边界"):
            self.assertIn(section, text)

    def test_install_prompt_asks_agent_to_inspect_install_and_report_actual_results(self) -> None:
        prompt = (SITE / "src/components/InstallPrompt.astro").read_text(encoding="utf-8")
        for phrase in ("声明的来源", "安装", "尝试使用", "实际结果"):
            self.assertIn(phrase, prompt)
        self.assertNotRegex(prompt, r"未验证|验证状态|核验来源")

    def test_external_source_page_displays_optional_declared_ref_without_verification_language(self) -> None:
        detail = (SITE / "src/pages/skills/[namespace]/[name].astro").read_text(encoding="utf-8")
        card = (SITE / "src/components/SkillCard.astro").read_text(encoding="utf-8")
        self.assertIn("entry.source.locator.ref", detail)
        self.assertIn("声明引用", detail)
        self.assertNotRegex(detail + card, r"待核验|固定来源|版本未指定")

    def test_copied_agent_prompts_use_complete_site_urls(self) -> None:
        install = (SITE / "src/components/InstallPrompt.astro").read_text(encoding="utf-8")
        contribute = (SITE / "src/pages/contribute/index.astro").read_text(encoding="utf-8")
        prompts = (SITE / "src/lib/contribution-prompts.ts").read_text(encoding="utf-8")
        self.assertIn("new URL(sitePath(", install)
        self.assertIn("Astro.site", install)
        self.assertIn("new URL(sitePath(", prompts)
        self.assertIn("Astro.site", contribute)

    def test_ci_lockfile_downloads_packages_from_the_public_npm_registry(self) -> None:
        lockfile = json.loads((SITE / "package-lock.json").read_text(encoding="utf-8"))
        hosts = {
            urlsplit(package["resolved"]).hostname
            for package in lockfile["packages"].values()
            if package.get("resolved", "").startswith("https://")
        }
        self.assertEqual(hosts, {"registry.npmjs.org"})

    @unittest.skipUnless(shutil.which("node"), "Node.js is required to load the Astro configuration")
    def test_organization_site_build_uses_the_root_url(self) -> None:
        env = dict(os.environ)
        env.pop("SITE_BASE", None)
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", 'import config from "./astro.config.mjs"; console.log(JSON.stringify({site: config.site, base: config.base}));'],
            cwd=SITE,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), {"site": "https://hwskill.github.io", "base": "/"})

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node.js and npm are required for the development-server integration check")
    def test_dev_startup_makes_the_complete_search_index_public(self) -> None:
        """A fresh dev server must serve every Pagefind asset, not only its entry module."""
        completed = subprocess.run(
            ["npm", "--prefix", str(SITE), "run", "predev"],
            cwd=ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        built = SITE / "dist/pagefind"
        public = SITE / "public/pagefind"
        built_files = {path.relative_to(built) for path in built.rglob("*") if path.is_file()}
        self.assertIn(Path("pagefind.js"), built_files)
        self.assertGreater(len(built_files), 1)
        self.assertEqual(
            {path.relative_to(public) for path in public.rglob("*") if path.is_file()},
            built_files,
        )
        for relative in built_files:
            self.assertEqual((public / relative).read_bytes(), (built / relative).read_bytes())
        page = (SITE / "dist/skills/index.html").read_text(encoding="utf-8")
        self.assertIn('id="search-results" class="card-grid', page)
        for item in json.loads((SITE / ".generated/directory/catalog.json").read_text(encoding="utf-8"))["entries"]:
            self.assertIn(f'data-skill-id="{item["entry"]["id"]}"', page)

    def test_site_declares_locked_supported_toolchain(self) -> None:
        package = json.loads((SITE / "package.json").read_text(encoding="utf-8"))
        self.assertRegex(package["engines"]["node"], r"22\.19\.0")
        self.assertRegex(package["dependencies"]["astro"], r"^7\.")
        self.assertRegex(package["devDependencies"]["pagefind"], r"^1\.5\.")
        self.assertNotIn("react", package.get("dependencies", {}))
        self.assertTrue(package["scripts"]["build"].endswith("astro build"))
        self.assertIn("pagefind", package["scripts"]["index"])

    def test_pages_only_consume_normalized_generated_json(self) -> None:
        contribution_page = SITE / "src/pages/contribute/index.astro"
        contribution_prompts = SITE / "src/lib/contribution-prompts.ts"
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (SITE / "src").rglob("*")
            if path.suffix in {".astro", ".ts", ".js", ".mjs"}
            and path not in {contribution_page, contribution_prompts}
        )
        self.assertNotRegex(sources, r"(?:entries|recommendations|curation)/.+\.ya?ml")
        self.assertNotIn("fetch(\"http", sources)
        self.assertIn("catalog.json", sources)
        self.assertIn("recommendations.json", sources)
        contribution = contribution_prompts.read_text(encoding="utf-8")
        self.assertIn("templates/entries/external.yaml", contribution)
        self.assertIn("templates/recommendations/recommendation.md", contribution)

    def test_internal_links_and_pagefind_follow_configured_base(self) -> None:
        config = (SITE / "astro.config.mjs").read_text(encoding="utf-8")
        urls = (SITE / "src/lib/urls.ts").read_text(encoding="utf-8")
        sources = "\n".join(path.read_text(encoding="utf-8") for path in (SITE / "src").rglob("*.astro"))
        self.assertIn("SITE_BASE", config)
        self.assertIn("import.meta.env.BASE_URL", urls)
        self.assertNotRegex(sources, r'href=["\']/')
        self.assertIn("data-pagefind-base", sources)

    def test_required_routes_and_chinese_document_language_exist(self) -> None:
        expected = [
            "src/pages/index.astro",
            "src/pages/skills/index.astro",
            "src/pages/skills/[namespace]/[name].astro",
            "src/pages/recommendations/[id].astro",
            "src/pages/topics/[slug].astro",
            "src/pages/contribute/index.astro",
        ]
        for relative in expected:
            with self.subTest(relative=relative):
                self.assertTrue((SITE / relative).is_file())
        layout = (SITE / "src/layouts/BaseLayout.astro").read_text(encoding="utf-8")
        self.assertRegex(layout, r'<html[^>]+lang="zh-CN"')

    def test_skill_detail_exposes_pagefind_filters_and_copy_fallback(self) -> None:
        detail = (SITE / "src/pages/skills/[namespace]/[name].astro").read_text(encoding="utf-8")
        layout = (SITE / "src/layouts/BaseLayout.astro").read_text(encoding="utf-8")
        for metadata in ("layer", "agent", "source"):
            with self.subTest(metadata=metadata):
                self.assertRegex(detail, rf'data-pagefind-filter=["\'][^"\']*{metadata}')
        self.assertIn('data-pagefind-meta="lifecycle"', detail)
        card = (SITE / "src/components/SkillCard.astro").read_text(encoding="utf-8")
        self.assertIn("data-lifecycle", card)
        prompt = (SITE / "src/components/InstallPrompt.astro").read_text(encoding="utf-8")
        self.assertIn("navigator.clipboard", prompt)
        self.assertRegex(prompt, r"select\(\)|setSelectionRange")
        self.assertRegex(detail, r"<BaseLayout[^>]+searchable")
        self.assertRegex(layout, r"data-pagefind-body=\{searchable")

    def test_search_only_commits_latest_async_request(self) -> None:
        search = (SITE / "src/components/SearchFilters.astro").read_text(encoding="utf-8")
        self.assertRegex(search, r"let\s+latestRequest\s*=\s*0")
        self.assertRegex(search, r"const\s+requestId\s*=\s*\+\+latestRequest")
        self.assertGreaterEqual(search.count("requestId !== latestRequest"), 2)

    def test_pagefind_dynamic_import_uses_an_absolute_url_in_dev(self) -> None:
        """Vite adds ?import to relative dynamic imports from public/, causing a 500."""
        search = (SITE / "src/components/SearchFilters.astro").read_text(encoding="utf-8")
        self.assertRegex(search, r"new URL\(pagefindPath, window\.location\.origin\)\.href")
        self.assertRegex(search, r"import\(/\* @vite-ignore \*/ pagefindUrl\)")

    def test_verification_ui_is_removed(self) -> None:
        self.assertFalse((SITE / "src/components/VerificationMatrix.astro").exists())
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (SITE / "src").rglob("*")
            if path.suffix in {".astro", ".ts", ".js", ".mjs"}
        )
        self.assertNotRegex(sources, r"verificationLabel|verificationState|verification_summary|verification-filter|验证矩阵")
        public_copy = sources + "\n" + "\n".join(
            path.read_text(encoding="utf-8") for path in (ROOT / "recommendations").glob("*.md")
        )
        self.assertNotRegex(public_copy, r"验证状态|已验证|未验证|核验状态|安装或行为验证|安装与行为验证")

    def test_lifecycle_pages_are_retained_but_not_featured_or_installable_when_withdrawn(self) -> None:
        data = (SITE / "src/lib/data.ts").read_text(encoding="utf-8")
        detail = (SITE / "src/pages/skills/[namespace]/[name].astro").read_text(encoding="utf-8")
        home = (SITE / "src/pages/index.astro").read_text(encoding="utf-8")
        prompt = (SITE / "src/components/InstallPrompt.astro").read_text(encoding="utf-8")

        self.assertNotRegex(detail, r"entries\.filter\([^\n]+lifecycle[^\n]+active")
        self.assertIn("lifecycle_reason", detail)
        self.assertIn("replacement_id", detail)
        self.assertRegex(home, r"entries\.filter\([^\n]+lifecycle\s*===\s*[\"']active[\"']")
        self.assertRegex(prompt, r"installCapability\s*===\s*[\"']disabled[\"']")
        self.assertRegex(prompt, r"停止新安装|已撤回")

    def test_drafts_and_badges_follow_fail_safe_contract(self) -> None:
        recommendation = (SITE / "src/pages/recommendations/[id].astro").read_text(encoding="utf-8")
        data = (SITE / "src/lib/data.ts").read_text(encoding="utf-8")
        self.assertRegex(recommendation, r"status\s*!==?\s*[\"']draft[\"']")
        self.assertIn("withdrawal_reason", recommendation)
        self.assertRegex(recommendation, r"status\s*===?\s*[\"']withdrawn[\"']")
        self.assertRegex(recommendation, r"status\s*===?\s*[\"']ready[\"'][^\n]+skill-link-groups")
        self.assertIn("recommendationData.recommendations.filter", data)
        self.assertRegex(data, r"status\s*===?\s*[\"']ready[\"']")
        card = (SITE / "src/components/SkillCard.astro").read_text(encoding="utf-8")
        self.assertIn("source", card)
        self.assertRegex(card, r"badge|stars")
        self.assertRegex(card, r"onerror|onError")

    def test_topic_alias_groups_and_web_sources_preserve_discovery_links(self) -> None:
        data = (SITE / "src/lib/data.ts").read_text(encoding="utf-8")
        detail = (SITE / "src/pages/skills/[namespace]/[name].astro").read_text(encoding="utf-8")
        card = (SITE / "src/components/SkillCard.astro").read_text(encoding="utf-8")
        self.assertIn('source.locator.type === "git"', data)
        self.assertIn("topicAliases", data)
        self.assertRegex(data, r"Object\.entries\(curation\.synonyms")
        self.assertRegex(data, r"group\.some\([^\n]+aliases\.has")
        self.assertRegex(detail, r"entry\.source\.kind\s*===\s*[\"']hosted[\"']")
        self.assertIn("随本目录版本", detail)
        self.assertIn("声明引用", detail)
        self.assertIn("外部来源", card)

    def test_query_evaluation_covers_at_least_thirty_cases(self) -> None:
        cases = json.loads((SITE / "search-cases.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(cases), 30)
        kinds = {case["kind"] for case in cases}
        self.assertTrue({"zh", "en", "mixed", "synonym", "filter", "zero"}.issubset(kinds))
        for case in cases:
            self.assertIn("query", case)
            self.assertIn("expected_ids", case)
            if case["query"]:
                self.assertLessEqual(len(case["expected_ids"]), 5)
        evaluator = (SITE / "scripts/evaluate-search.mjs").read_text(encoding="utf-8")
        self.assertIn("non_skill_results", evaluator)
        self.assertIn("unexpected", evaluator)
        search = (SITE / "src/components/SearchFilters.astro").read_text(encoding="utf-8")
        self.assertNotIn('id="verification-filter"', search)
        self.assertNotRegex(search, r"verification:\s*verification\.value")

    def test_build_output_contract_is_checked_by_script(self) -> None:
        checker = (SITE / "scripts/check-build.mjs").read_text(encoding="utf-8")
        for route in ("skills", "recommendations", "topics", "contribute"):
            self.assertIn(route, checker)
        for resource in (
            "data/catalog.json",
            "schemas/entry.schema.json",
            "schemas/recommendation.schema.json",
            "schemas/recommendation-source.schema.json",
            "templates/entries/external.yaml",
            "templates/recommendations/recommendation.md",
            "contribute/agent.md",
        ):
            self.assertIn(resource, checker)
        self.assertTrue(re.search(r"pagefind", checker, re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
