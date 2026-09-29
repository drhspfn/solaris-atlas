"""Focused fixtures for deterministic Textmaps localization export."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wuwa_story_worker.compiler.localization import aggregate_locales, build_localization, localization_resolution


LOCALES = ("de", "en", "es", "fr", "id", "ja", "ko", "pt", "ru", "th", "vi", "zh-Hans", "zh-Hant")


def write_json(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


class LocalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "upstream"
        self.diagnostics: list[tuple] = []
        self.emitted: list[tuple] = []
        for locale in LOCALES:
            base = self.root / "Textmaps" / locale
            write_json(
                base / "multi_text" / "MultiText.json",
                [
                    {"Id": "ordinary", "Content": f"ordinary-{locale}", "RedirectDbIndex": 0},
                    {"Id": "redirected", "Content": "********", "RedirectDbIndex": 1},
                    {"Id": "duplicated", "Content": f"same-{locale}", "RedirectDbIndex": 0},
                ],
            )
            write_json(base / "multi_text_1sthalf" / "MultiText.json", [{"Id": "redirected", "Content": f"actual-{locale}"}])
            write_json(base / "multi_text_2ndhalf" / "MultiText.json", [{"Id": "duplicated", "Content": f"same-{locale}"}])
            write_json(base / "speaker" / "Speaker.json", [{"Id": 42, "Content": f"speaker-{locale}"}])
            write_json(base / "subtitle_text" / "SubtitleText.json", [{"Id": 17, "Content": f"subtitle-{locale}"}])

    def export(self, destination: str = "dist") -> dict:
        return build_localization(
            self.root,
            Path(self.temporary.name) / destination,
            lambda kind, record: self.emitted.append((kind, record)),
            lambda *args: self.diagnostics.append(args),
        )

    def test_redirect_numeric_namespaces_and_all_locales(self) -> None:
        english = self.export()
        self.assertEqual(english["redirected"]["content"], "actual-en")
        self.assertEqual(english["redirected"]["source"], "Textmaps/en/multi_text_1sthalf/MultiText.json")
        self.assertEqual(len(self.emitted), len(LOCALES))
        self.assertFalse(self.diagnostics)
        for locale in LOCALES:
            output = Path(self.temporary.name) / "dist" / f"{locale}.jsonl"
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 5)
            redirect = next(row for row in rows if row["key"] == "redirected")
            self.assertEqual(redirect["content"], f"actual-{locale}")
            self.assertEqual(redirect["raw_content"], "********")
            self.assertEqual(redirect["sources"][-1]["position"], 0)
            self.assertEqual({row["namespace"] for row in rows}, {"multi_text", "speaker", "subtitle_text"})

    def test_missing_redirect_and_conflicting_duplicate_are_reported(self) -> None:
        base = self.root / "Textmaps" / "en"
        write_json(base / "multi_text_1sthalf" / "MultiText.json", [])
        write_json(base / "multi_text_2ndhalf" / "MultiText.json", [{"Id": "duplicated", "Content": "changed"}])
        english = self.export()
        self.assertIsNone(english["redirected"]["content"])
        self.assertEqual(english["duplicated"]["content"], "same-en")
        codes = {entry[0] for entry in self.diagnostics}
        self.assertIn("unresolved_localization_redirect", codes)
        self.assertIn("conflicting_localization", codes)

    def test_orphaned_supplement_is_preserved(self) -> None:
        base = self.root / "Textmaps" / "en"
        write_json(base / "multi_text_2ndhalf" / "MultiText.json", [{"Id": "new-key", "Content": "new text"}])
        english = self.export()
        self.assertEqual(english["new-key"]["content"], "new text")
        self.assertIn("orphaned_localization_supplement", {entry[0] for entry in self.diagnostics})

    def test_export_is_deterministic(self) -> None:
        first = self.export("first")
        second = self.export("second")
        self.assertEqual(first, second)
        for locale in LOCALES:
            a = (Path(self.temporary.name) / "first" / f"{locale}.jsonl").read_bytes()
            b = (Path(self.temporary.name) / "second" / f"{locale}.jsonl").read_bytes()
            self.assertEqual(a, b)

    def test_aggregated_values_by_locale(self) -> None:
        self.export()
        output = Path(self.temporary.name) / "dist"
        (output / "._en.jsonl").write_text("AppleDouble noise")
        self.assertEqual(aggregate_locales(output), 5)
        records = [json.loads(line) for line in (output / "all-locales.jsonl").read_text().splitlines()]
        redirected = next(row for row in records if row["key"] == "redirected")
        numeric = next(row for row in records if row["namespace"] == "subtitle_text")
        self.assertEqual(numeric["key"], 17)
        self.assertEqual(len(redirected["values_by_locale"]), 13)
        self.assertEqual(redirected["values_by_locale"]["en"]["content"], "actual-en")
        first = (output / "all-locales.jsonl").read_bytes()
        self.assertEqual(aggregate_locales(output), 5)
        self.assertEqual((output / "all-locales.jsonl").read_bytes(), first)

    def test_aggregate_ignores_appledouble_jsonl(self) -> None:
        self.export()
        output = Path(self.temporary.name) / "dist"
        (output / "._dialogue.jsonl").write_text("not json")
        self.assertEqual(aggregate_locales(output), 5)

    def test_localization_resolution_states(self) -> None:
        self.assertEqual(localization_resolution("text"), "resolved_nonempty")
        self.assertEqual(localization_resolution(""), "resolved_empty")
        self.assertEqual(localization_resolution(None), "resolved_empty")
        self.assertEqual(localization_resolution(None, present=False), "missing_key")
        self.assertEqual(localization_resolution(None, broken_redirect=True), "broken_redirect")

    def test_empty_content_and_broken_redirect_are_distinguished(self) -> None:
        base = self.root / "Textmaps" / "en" / "multi_text" / "MultiText.json"
        rows = json.loads(base.read_text())
        rows.extend([
            {"Id": "empty", "Content": "", "RedirectDbIndex": 0},
            {"Id": "broken", "Content": "********", "RedirectDbIndex": 1},
        ])
        write_json(base, rows)
        english = self.export()
        self.assertEqual(english["empty"]["resolution"], "resolved_empty")
        self.assertEqual(english["broken"]["resolution"], "broken_redirect")
        en_rows = [json.loads(line) for line in
                   (Path(self.temporary.name) / "dist/en.jsonl").read_text().splitlines()]
        self.assertEqual(next(r for r in en_rows if r["key"] == "empty")["resolution"], "resolved_empty")
        self.assertEqual(next(r for r in en_rows if r["key"] == "broken")["resolution"], "broken_redirect")

    def test_locale_coverage_counts_missing_and_empty(self) -> None:
        root = self.root / "Textmaps"
        for locale in LOCALES:
            path = root / locale / "multi_text" / "MultiText.json"
            rows = json.loads(path.read_text())
            rows.append({"Id": "only-somewhere", "Content": "", "RedirectDbIndex": 0})
            if locale == "ru":
                rows.pop()
            write_json(path, rows)
        output = Path(self.temporary.name) / "coverage-dist"
        self.export("coverage-dist")
        aggregate_locales(output)
        coverage = json.loads((output / "coverage.json").read_text())
        self.assertEqual(coverage["locales"]["en"]["resolved_empty"], 1)
        self.assertEqual(coverage["locales"]["ru"]["missing_key"], 1)
        identity = next(row for row in
                        (json.loads(line) for line in (output / "all-locales.jsonl").read_text().splitlines())
                        if row["key"] == "only-somewhere")
        self.assertEqual(identity["values_by_locale"]["ru"]["resolution"], "missing_key")


if __name__ == "__main__":
    unittest.main()
