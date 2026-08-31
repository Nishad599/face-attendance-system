"""The pre-commit secret backstop (deploy/check_staged.py).

A credential pushed to GitHub stays in the history even after the file is
deleted, so this check failing open is unrecoverable. These tests exist
because the first implementation used lstrip("./"), which ate the leading dot
of ".env" and let exactly the most important case through.
"""

import importlib.util
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "check_staged", os.path.join(ROOT, "deploy", "check_staged.py")
)
check_staged = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_staged)

blocked = lambda p: check_staged.reason(p) is not None


class TestBlocksSecrets:

    @pytest.mark.parametrize("path", [
        ".env",
        "./.env",
        ".env.production",
        ".env.local",
        ".env.prod.backup",
    ])
    def test_env_files(self, path):
        assert blocked(path), f"{path} holds SMTP/DB credentials"

    @pytest.mark.parametrize("path", [
        "key.pem", "cert.pem", "server.key", "store.p12", "a.pfx",
        "id_rsa", "id_ed25519", "credentials.json",
    ])
    def test_keys_and_certs(self, path):
        assert blocked(path)

    @pytest.mark.parametrize("path", [
        "attendance.db",
        "backups/attendance.db.migrate5-20260831",
        "data/thing.sqlite3",
        "x.sqlite",
    ])
    def test_databases_and_backups(self, path):
        assert blocked(path)

    @pytest.mark.parametrize("path", [
        "student_photos/000_Nishad/1.jpg",
        "student_photos/x.png",
        "./student_photos/deep/nested/y.jpg",
    ])
    def test_biometric_photos(self, path):
        assert blocked(path)

    def test_windows_separators_are_handled(self):
        """git reports forward slashes, but a hand-passed path may not."""
        assert blocked(r"student_photos\000_x\1.jpg")
        assert blocked(r"backups\a.db")


class TestAllowsRealWork:
    """False positives are not harmless: they would block ordinary commits
    until someone disables the check entirely."""

    @pytest.mark.parametrize("path", [
        ".env.example",        # tracked on purpose
        ".env.sample",
        ".env.template",
        ".env.dist",
    ])
    def test_env_templates_are_committable(self, path):
        assert not blocked(path), f"{path} is a tracked template, not a secret"

    @pytest.mark.parametrize("path", [
        "reports.py",
        "push.bat",
        "migrate_phase5.py",
        "templates/_navbar.html",
        "static/js/navbar.js",
        "static/images/icon-192.png",
        "static/manifest.webmanifest",
        "deploy/DEPLOYMENT.md",
        "deploy/attendance.service",
        "deploy/check_staged.py",
        "tests/test_leave_and_alerts.py",
        ".github/workflows/deploy.yml",
        "README.md",
    ])
    def test_ordinary_files(self, path):
        assert not blocked(path)

    def test_a_filename_merely_containing_env_is_fine(self):
        assert not blocked("environment_setup.py")
        assert not blocked("static/js/env_helper.js")

    def test_empty_and_blank(self):
        assert not blocked("")
        assert not blocked("   ")


class TestEveryTrackedFilePasses:
    """The strongest guard: nothing already committed may trip the check,
    or the next `push.bat` would refuse to commit for no reason."""

    def test_no_tracked_file_is_flagged(self):
        import subprocess
        out = subprocess.run(["git", "ls-files"], cwd=ROOT,
                             capture_output=True, text=True)
        if out.returncode != 0:
            pytest.skip("not a git repository")
        flagged = [p for p in out.stdout.splitlines()
                   if p.strip() and blocked(p)]
        assert not flagged, (
            "these already-tracked files would be blocked by the scanner: "
            f"{flagged}"
        )
