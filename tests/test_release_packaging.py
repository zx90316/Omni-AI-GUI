import json
import re
import unittest
from pathlib import Path

import launch
from omni_version import __version__


ROOT = Path(__file__).resolve().parents[1]


class ReleasePackagingTests(unittest.TestCase):
    def test_source_version_is_semantic(self):
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
        frontend_package = json.loads(
            (ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
        )
        self.assertEqual(frontend_package["version"], __version__)

    def test_release_manifest_sources_are_present(self):
        manifest = json.loads(
            (ROOT / "packaging" / "release-manifest.json").read_text(encoding="utf-8")
        )
        for relative in manifest["directories"] + manifest["files"]:
            with self.subTest(relative=relative):
                self.assertTrue((ROOT / relative).exists())

    def test_source_tree_passes_runtime_integrity_check(self):
        self.assertEqual(launch.project_integrity_errors(ROOT), [])

    def test_build_is_nuitka_standalone_without_executable_packer(self):
        script = (ROOT / "scripts" / "build_nuitka.ps1").read_text(encoding="utf-8")
        self.assertIn('"--mode=standalone"', script)
        self.assertNotIn("--mode=onefile", script)
        self.assertIn('"--nofollow-import-to=backend"', script)
        self.assertIn('"--nofollow-import-to=manager.model_downloader"', script)
        self.assertIn('"--no-deployment-flag=excluded-module-usage"', script)
        self.assertIn("contains an installed Omni-AI-GUI project", script)
        self.assertNotIn('"--include-package=backend"', script)
        self.assertIsNone(re.search(r"--(?:enable-)?plugin=upx", script, re.IGNORECASE))

    def test_release_workflow_exists(self):
        workflow = ROOT / ".github" / "workflows" / "release.yml"
        self.assertTrue(workflow.is_file())
        text = workflow.read_text(encoding="utf-8")
        self.assertIn("scripts/build_nuitka.ps1", text)
        self.assertIn("actions/attest@", text)
        self.assertIn("$buildParameters = @{", text)
        self.assertIn("@buildParameters", text)

    def test_release_does_not_copy_backend_or_frontend(self):
        manifest = json.loads(
            (ROOT / "packaging" / "release-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["directories"], [])
        self.assertIn("backend", manifest["excluded_names"])
        self.assertIn("frontend", manifest["excluded_names"])


if __name__ == "__main__":
    unittest.main()
