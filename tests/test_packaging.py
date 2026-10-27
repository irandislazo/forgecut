import os, sys, tempfile, tomllib, unittest
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))


class TestPackaging(unittest.TestCase):
    def test_pyproject_ready_for_flet_build(self):
        d = tomllib.load(open(os.path.join(ROOT, "pyproject.toml"), "rb"))
        self.assertIn("flet==0.86.0", d["project"]["dependencies"])
        self.assertEqual(d["tool"]["flet"]["app"]["path"], "src")
        self.assertTrue(os.path.exists(os.path.join(ROOT, "src", "main.py")))
        self.assertIn("android.permission.READ_MEDIA_VIDEO", d["tool"]["flet"]["android"]["permission"])

    def test_requirements_pin_matches_pyproject(self):
        self.assertIn("flet==0.86.0", open(os.path.join(ROOT, "requirements.txt")).read())

    def test_workflow_builds_android_and_windows_and_releases(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML no instalado")
        w = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "build.yml"), encoding="utf-8"))
        jobs = w["jobs"]
        for j in ("tests", "android", "windows", "release"):
            self.assertIn(j, jobs)
        self.assertEqual(jobs["android"]["needs"], "tests")
        steps = " ".join(str(s.get("run", "")) for s in jobs["android"]["steps"])
        self.assertIn("flet build apk", steps)
        self.assertIn("flet build windows", " ".join(str(s.get("run", "")) for s in jobs["windows"]["steps"]))

    def test_ffmpeg_is_found_next_to_the_executable_dir(self):
        from forgecut.media import ffmpeg_backend as fb
        import shutil
        d = tempfile.mkdtemp()
        exe = os.path.join(d, "ffmpeg.exe")
        open(exe, "w").close()
        old_env, old_which = dict(os.environ), shutil.which
        try:
            os.environ["FORGECUT_BIN_DIR"] = d
            os.environ.pop("FORGECUT_FFMPEG", None)
            shutil.which = lambda *a, **k: None
            self.assertEqual(fb._find("ffmpeg"), exe)
        finally:
            shutil.which = old_which
            os.environ.clear(); os.environ.update(old_env)

    def test_app_data_dir_uses_flet_storage_on_mobile(self):
        from forgecut.ui.state import default_data_dir
        d = tempfile.mkdtemp()
        old = os.environ.get("FLET_APP_STORAGE_DATA")
        os.environ["FLET_APP_STORAGE_DATA"] = d
        try:
            self.assertEqual(default_data_dir(), d)
        finally:
            os.environ.pop("FLET_APP_STORAGE_DATA") if old is None else os.environ.__setitem__("FLET_APP_STORAGE_DATA", old)


if __name__ == "__main__":
    unittest.main()
