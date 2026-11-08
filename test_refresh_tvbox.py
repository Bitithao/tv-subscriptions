import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import refresh_tvbox


class TVBoxRefreshTest(unittest.TestCase):
    def test_probes_only_approved_routes_and_preserves_usable_backup(self):
        jade = "TVB翡翠台"
        rows = [
            {"name": jade, "url": "https://one.example/jade.m3u8", "logo": ""},
            {"name": jade, "url": "https://two.example/jade.m3u8", "logo": ""},
            {"name": jade, "url": "https://excluded.example/jade.m3u8", "logo": ""},
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / refresh_tvbox.CANDIDATES).write_text(json.dumps(rows), encoding="utf-8")
            (root / refresh_tvbox.ORIGINS).write_text(json.dumps({jade: ["https://one.example:443", "https://two.example:443"]}), encoding="utf-8")
            with patch.object(refresh_tvbox, "probe", side_effect=lambda row: (row, None)) as checked:
                report = refresh_tvbox.refresh_tvbox(root)
                self.assertEqual(checked.call_count, 2)
            self.assertTrue(report["published"])
            self.assertIn(rows[0]["url"], (root / refresh_tvbox.MAIN).read_text(encoding="utf-8"))
            self.assertIn(rows[1]["url"], (root / refresh_tvbox.BACKUP).read_text(encoding="utf-8"))
            with patch.object(refresh_tvbox, "probe", return_value=(None, "timeout")):
                report = refresh_tvbox.refresh_tvbox(root)
            self.assertTrue(report["retained_previous"])
            self.assertIn(rows[0]["url"], (root / refresh_tvbox.MAIN).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
