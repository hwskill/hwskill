import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import yaml

from hwskill.profiles import (
    ProfileError,
    list_profiles,
    parse_csv,
    read_profile_ids,
    resolve_profiles,
    set_profiles,
    unset_profiles,
)
from hwskill.scopes import lock_path, profile_path, project_scope, user_scope


ROOT = Path(__file__).parents[2]


class ScopedProfilesTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.environment = patch.dict(
            os.environ,
            {
                "HOME": str(self.base / "home"),
                "XDG_CONFIG_HOME": str(self.base / "config"),
                "XDG_STATE_HOME": str(self.base / "state"),
            },
            clear=True,
        )
        self.environment.start()
        self.user = user_scope()
        self.project_target = project_scope(self.project)

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def test_parse_csv_trims_deduplicates_and_rejects_empty_members(self):
        self.assertEqual(
            parse_csv("superpowers, codex-demo,superpowers", "profile"),
            ("superpowers", "codex-demo"),
        )
        for value in ("", "superpowers,,codex-demo", "superpowers, "):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ProfileError, "empty profile selector"):
                    parse_csv(value, "profile")

    def test_list_profiles_returns_registry_definitions(self):
        definitions = list_profiles(ROOT)

        self.assertEqual(
            [definition.profile_id for definition in definitions],
            ["codex-demo", "personal-baseline", "superpowers"],
        )
        self.assertTrue(definitions[0].description)
        self.assertIn("superpowers/systematic-debugging", definitions[0].skill_ids)

    def test_project_profiles_override_user_and_absence_falls_back(self):
        set_profiles(self.user, ROOT, ("personal-baseline", "superpowers"))

        fallback = resolve_profiles(self.project, ROOT, user_target=self.user)

        self.assertEqual(fallback.profile_ids, ("personal-baseline", "superpowers"))
        self.assertEqual(fallback.effective_scope, "user")
        self.assertEqual(fallback.profile_source, profile_path(self.user))

        set_profiles(self.project_target, ROOT, ("codex-demo",))
        overridden = resolve_profiles(self.project, ROOT, user_target=self.user)

        self.assertEqual(overridden.profile_ids, ("codex-demo",))
        self.assertEqual(overridden.effective_scope, "project")
        self.assertEqual(overridden.profile_source, profile_path(self.project_target))

    def test_explicit_empty_project_does_not_fall_back_and_unset_restores_fallback(self):
        set_profiles(self.user, ROOT, ("personal-baseline",))
        set_profiles(self.project_target, ROOT, ())

        disabled = resolve_profiles(self.project, ROOT, user_target=self.user)

        self.assertEqual(disabled.profile_ids, ())
        self.assertEqual(disabled.skill_ids, ())
        self.assertEqual(disabled.effective_scope, "project")
        self.assertEqual(read_profile_ids(self.project_target), ())

        self.assertTrue(unset_profiles(self.project_target))
        self.assertFalse(profile_path(self.project_target).exists())
        self.assertFalse(lock_path(self.project_target).exists())
        self.assertFalse(unset_profiles(self.project_target))
        self.assertEqual(
            resolve_profiles(self.project, ROOT, user_target=self.user).profile_ids,
            ("personal-baseline",),
        )

    def test_set_rejects_unknown_profile_without_writing_files(self):
        with self.assertRaisesRegex(ProfileError, "unknown profile"):
            set_profiles(self.user, ROOT, ("missing",))

        self.assertFalse(profile_path(self.user).exists())
        self.assertFalse(lock_path(self.user).exists())

    def test_mismatched_effective_lock_fails_closed(self):
        set_profiles(self.user, ROOT, ("personal-baseline",))
        lock = yaml.safe_load(lock_path(self.user).read_text(encoding="utf-8"))
        lock["catalog_digest"] = "sha256:stale"
        lock_path(self.user).write_text(
            yaml.safe_dump(lock, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ProfileError, "lock does not match"):
            resolve_profiles(self.project, ROOT, user_target=self.user)


if __name__ == "__main__":
    unittest.main()
