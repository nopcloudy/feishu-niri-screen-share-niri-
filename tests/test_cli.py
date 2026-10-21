import tempfile
import unittest
from pathlib import Path

from feishu_niri_share.cli import portal_with_wlr, restore, sha256, snapshot, wlr_configuration


class PortalConfigurationTests(unittest.TestCase):
    def test_preserves_other_portals_and_replaces_only_screencast(self):
        original = "[preferred]\ndefault=gnome;gtk;\norg.freedesktop.impl.portal.ScreenCast=gnome;\norg.freedesktop.impl.portal.Settings=gtk;\n"
        updated = portal_with_wlr(original)
        self.assertIn("default=gnome;gtk;", updated)
        self.assertIn("org.freedesktop.impl.portal.Settings=gtk;", updated)
        self.assertEqual(updated.count("org.freedesktop.impl.portal.ScreenCast=wlr;"), 1)
        self.assertEqual(portal_with_wlr(updated), updated)

    def test_rejects_unsafe_monitor_name(self):
        with self.assertRaises(ValueError):
            wlr_configuration("eDP-1\nexec_after=bad", 30)


class RestoreTests(unittest.TestCase):
    def test_restores_originals_without_overwriting_later_user_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            keys = ("portal_config", "wlr_config", "binary", "dbus_service", "portal_descriptor")
            paths = {key: root / key for key in keys}
            paths["state"] = root / "state"
            paths["portal_config"].write_text("original")
            state = snapshot(paths)
            paths["portal_config"].write_text("installed")
            paths["wlr_config"].write_text("installed")
            for key in keys:
                state["files"][key]["installed_sha256"] = sha256(paths[key])
            paths["wlr_config"].write_text("user edit")
            skipped = restore(paths, state, check_hash=True)
            self.assertEqual(paths["portal_config"].read_text(), "original")
            self.assertEqual(paths["wlr_config"].read_text(), "user edit")
            self.assertEqual(skipped, [str(paths["wlr_config"])])


if __name__ == "__main__":
    unittest.main()
