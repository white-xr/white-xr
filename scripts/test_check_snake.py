import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import check_snake as checker


class SnakeChecks(unittest.TestCase):
    def setUp(self):
        self.source = {
            "owner_matches": True, "read_user_scope": True, "repo_scope": False,
            "total": 8, "restricted": 0, "active_days": 1,
            "days": [
                {"date": "2026-10-08", "x": 0, "y": 4, "count": 8, "level": 4},
                {"date": "2026-10-09", "x": 0, "y": 5, "count": 0, "level": 0},
            ],
        }
        self.baseline = copy.deepcopy(self.source)
        self.baseline["total"] = 2
        self.baseline["days"][0]["count"] = 2

    def svg(self, filename):
        snake, colors = checker.PALETTES[filename]
        palette = f"--cs:{snake};" + "".join(f"--c{i}:{c};" for i, c in enumerate(colors))
        return (f'<svg xmlns="http://www.w3.org/2000/svg"><style>:root{{{palette}}}'
                '.c.c0{fill:var(--c4);animation-name:c0}@keyframes c0{100%{fill:var(--ce)}}'
                '</style><rect class="c c0" x="2" y="66"/>'
                '<rect class="c" x="2" y="82"/></svg>')

    def test_private_coverage_does_not_require_restricted_count(self):
        self.assertEqual(checker.check_visibility(self.source, self.baseline), 6)

    def test_reject_wrong_owner_missing_scope_or_public_only(self):
        for field in ("owner_matches", "read_user_scope"):
            source = dict(self.source, **{field: False})
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                checker.check_visibility(source, self.baseline)
        with self.assertRaisesRegex(RuntimeError, "No private-contribution"):
            checker.check_visibility(self.source, self.source)

    def test_reject_window_change_or_total_regression(self):
        baseline = copy.deepcopy(self.baseline)
        baseline["days"][0]["date"] = "2026-10-07"
        with self.assertRaisesRegex(RuntimeError, "date ranges"):
            checker.check_visibility(self.source, baseline)
        baseline["days"][0]["date"] = "2026-10-08"
        baseline["total"] = 9
        with self.assertRaisesRegex(RuntimeError, "total is below"):
            checker.check_visibility(self.source, baseline)

    def test_baseline_is_not_assumed_to_be_a_daily_subset(self):
        self.baseline["days"][0]["count"] = 0
        self.baseline["days"][1]["count"] = 2
        self.assertEqual(checker.check_visibility(self.source, self.baseline), 6)

    def test_verify_both_themes_and_reject_missing_or_wrong_cell(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            for name in checker.PALETTES:
                (folder / name).write_text(self.svg(name), encoding="utf-8")
            snapshot = {"source": self.source, "baseline": self.baseline}
            report = checker.verify(snapshot, folder)
            self.assertEqual(report["additional_contributions_with_owner_token"], 6)
            self.assertEqual(report["verified_cells_per_svg"], 2)
            self.assertEqual(len(report["svg_sha256"]), 2)
            self.assertNotIn("days", report)
            for replacement in ['<rect class="c c0" x="2" y="66"/>', 'var(--c4)']:
                name = next(iter(checker.PALETTES))
                broken = self.svg(name).replace(replacement, '' if replacement.startswith('<') else 'var(--c1)')
                (folder / name).write_text(broken, encoding="utf-8")
                with self.subTest(replacement=replacement), self.assertRaises(RuntimeError):
                    checker.verify(snapshot, folder)

    def test_reject_palette_change(self):
        name = next(iter(checker.PALETTES))
        with self.assertRaisesRegex(RuntimeError, "palette"):
            checker.svg_grid(self.svg(name).replace("#111111", "#FF0000"), name)

    def test_api_errors_cannot_disclose_response_body(self):
        with patch.object(checker.urllib.request, "urlopen") as request:
            response = request.return_value.__enter__.return_value
            response.headers.get.return_value = "read:user"
            response.read.return_value = json.dumps({"errors": [{"message": "SENSITIVE_FIXTURE"}]}).encode()
            with self.assertRaises(RuntimeError) as error:
                checker.calendar("FAKE_TOKEN", "example")
            self.assertNotIn("SENSITIVE_FIXTURE", str(error.exception))
            self.assertNotIn("FAKE_TOKEN", str(error.exception))


if __name__ == "__main__":
    unittest.main()
