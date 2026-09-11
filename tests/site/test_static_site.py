from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).parents[2]
SITE = ROOT / "site"


class StaticSiteContractTests(unittest.TestCase):
    def test_site_declares_locked_supported_toolchain(self) -> None:
        package = json.loads((SITE / "package.json").read_text(encoding="utf-8"))
        self.assertRegex(package["engines"]["node"], r"22\.19\.0")
        self.assertRegex(package["dependencies"]["astro"], r"^7\.")
        self.assertRegex(package["devDependencies"]["pagefind"], r"^1\.5\.")
        self.assertNotIn("react", package.get("dependencies", {}))
        self.assertTrue(package["scripts"]["build"].endswith("astro build"))
        self.assertIn("pagefind", package["scripts"]["index"])

    def test_pages_only_consume_normalized_generated_json(self) -> None:
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (SITE / "src").rglob("*")
            if path.suffix in {".astro", ".ts", ".js", ".mjs"}
        )
        self.assertNotRegex(sources, r"(?:entries|recommendations|curation)/.+\.ya?ml")
        self.assertNotIn("fetch(\"http", sources)
        self.assertIn("catalog.json", sources)
        self.assertIn("recommendations.json", sources)

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
        for metadata in ("layer", "agent", "source", "verification"):
            with self.subTest(metadata=metadata):
                self.assertRegex(detail, rf'data-pagefind-filter=["\'][^"\']*{metadata}')
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

    def test_verification_stages_have_explicit_lifecycle_order(self) -> None:
        matrix = (SITE / "src/components/VerificationMatrix.astro").read_text(encoding="utf-8")
        positions = [matrix.index(f'"{stage}"') for stage in ("metadata", "acquisition", "installation", "behavior")]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("Object.entries(summary)", matrix)

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
        self.assertRegex(recommendation, r"status\s*===?\s*[\"']ready[\"'][^\n]+SkillCard")
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
        self.assertIn('identity.startsWith("web:")', data)
        self.assertIn("topicAliases", data)
        self.assertRegex(data, r"Object\.entries\(curation\.synonyms")
        self.assertRegex(data, r"group\.some\([^\n]+aliases\.has")
        self.assertRegex(detail, r"entry\.source\.kind\s*===\s*[\"']hosted[\"'][^\n]+随本目录版本")
        self.assertIn("外部版本未固定", detail)
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
        self.assertIn('id="verification-filter"', search)
        self.assertRegex(search, r"verification:\s*verification\.value")

    def test_build_output_contract_is_checked_by_script(self) -> None:
        checker = (SITE / "scripts/check-build.mjs").read_text(encoding="utf-8")
        for route in ("skills", "recommendations", "topics", "contribute"):
            self.assertIn(route, checker)
        for resource in ("data/catalog.json", "schemas/entry.schema.json", "contribute/agent.md"):
            self.assertIn(resource, checker)
        self.assertTrue(re.search(r"pagefind", checker, re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
