"""Deterministic, source-traceable localization export for Arikatsu Textmaps.

The primary MultiText table contains masked content when RedirectDbIndex is 1.
Those IDs resolve to multi_text_1sthalf; multi_text_2ndhalf is a duplicate
supplementary table and does not override ordinary primary values.
"""

from __future__ import annotations

import json
import os
import heapq
from collections import Counter
from tempfile import TemporaryDirectory
from pathlib import Path
from typing import Any, Callable

from .core import is_ignored_fs_entry


Emit = Callable[[str, dict[str, Any]], None]
Diagnostic = Callable[[str, str, str, str, dict[str, Any]], None]


def localization_resolution(content: Any, *, present: bool = True,
                            broken_redirect: bool = False) -> str:
    """Classify localization resolution without treating empty text as success."""
    if not present:
        return "missing_key"
    if broken_redirect:
        return "broken_redirect"
    return "resolved_empty" if content is None or content == "" else "resolved_nonempty"


def _rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        rows = json.load(stream)
    if not isinstance(rows, list):
        raise ValueError(f"Expected JSON array in {path}")
    return rows


def build_localization(
    data_root: str | Path,
    out_dir: str | Path,
    emit: Emit | None = None,
    diagnostic: Diagnostic | None = None,
) -> dict[str, dict[str, Any]]:
    """Write one JSONL file per locale and return an English MultiText lookup.

    ``data_root`` can be the repository root or its ``Textmaps`` directory.
    Each output row retains the raw key, locale, content, and exact input file(s).
    ``emit`` receives one ``localization_file`` record per locale, avoiding a
    second in-memory/global copy of millions of translated strings.
    """

    root = Path(data_root)
    textmaps = root / "Textmaps" if (root / "Textmaps").is_dir() else root
    if not textmaps.is_dir():
        raise FileNotFoundError(f"Textmaps directory not found: {textmaps}")
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    english: dict[str, dict[str, Any]] = {}
    source_prefix = "Textmaps/" if textmaps != root else ""

    def source_name(path: Path) -> str:
        return source_prefix + str(path.relative_to(textmaps))

    def report(code: str, severity: str, source: str, raw_path: str, **detail: Any) -> None:
        if diagnostic is not None:
            diagnostic(code, severity, source, raw_path, detail)

    for locale_dir in sorted(path for path in textmaps.iterdir() if path.is_dir()):
        locale = locale_dir.name
        main_path = locale_dir / "multi_text" / "MultiText.json"
        if not main_path.is_file():
            report("missing_localization_table", "error", str(main_path), "", locale=locale)
            continue

        supplements: dict[int, dict[str, tuple[dict[str, Any], int]]] = {}
        supplement_paths: dict[int, str] = {}
        for number, directory in ((1, "multi_text_1sthalf"), (2, "multi_text_2ndhalf")):
            path = locale_dir / directory / "MultiText.json"
            if not path.is_file():
                report("missing_localization_table", "warning", str(path), "", locale=locale)
                continue
            supplement_paths[number] = source_name(path)
            table: dict[str, tuple[dict[str, Any], int]] = {}
            for position, row in enumerate(_rows(path)):
                key = row.get("Id")
                if not isinstance(key, str):
                    report("invalid_localization_key", "error", supplement_paths[number], f"[{position}]", row=row)
                    continue
                if key in table:
                    report("duplicate_localization_key", "error", supplement_paths[number], f"[{position}]", key=key)
                table[key] = (row, position)
            supplements[number] = table

        output_path = destination / f"{locale}.jsonl"
        temporary_path = output_path.with_suffix(".jsonl.tmp")
        main_source = source_name(main_path)
        counts = {"multi_text": 0, "speaker": 0, "subtitle_text": 0}
        seen: set[str] = set()
        with temporary_path.open("w", encoding="utf-8", newline="\n") as target:
            for position, row in enumerate(_rows(main_path)):
                key = row.get("Id")
                if not isinstance(key, str):
                    report("invalid_localization_key", "error", main_source, f"[{position}]", row=row)
                    continue
                if key in seen:
                    report("duplicate_localization_key", "error", main_source, f"[{position}]", key=key)
                seen.add(key)
                redirect = row.get("RedirectDbIndex", 0)
                raw_content = row.get("Content")
                content = raw_content
                resolution = localization_resolution(content)
                sources = [{"file": main_source, "position": position}]
                if redirect:
                    referenced_entry = supplements.get(redirect, {}).get(key)
                    if referenced_entry is None:
                        content = None
                        resolution = "broken_redirect"
                        report("unresolved_localization_redirect", "error", main_source, f"[{position}].RedirectDbIndex", key=key, redirect_db_index=redirect)
                    else:
                        referenced, referenced_position = referenced_entry
                        content = referenced.get("Content")
                        resolution = localization_resolution(content)
                        sources.append({"file": supplement_paths[redirect], "position": referenced_position})
                elif key in supplements.get(2, {}):
                    extra, extra_position = supplements[2][key]
                    sources.append({"file": supplement_paths[2], "position": extra_position})
                    if extra.get("Content") != content:
                        report("conflicting_localization", "warning", main_source, f"[{position}].Content", key=key, other_file=supplement_paths[2])
                record = {
                    "namespace": "multi_text",
                    "key": key,
                    "locale": locale,
                    "content": content,
                    "resolution": resolution,
                    "raw_content": raw_content,
                    "redirect_db_index": redirect,
                    "sources": sources,
                }
                target.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                counts["multi_text"] += 1
                if locale == "en":
                    english[key] = {
                        "content": content,
                        "source": sources[-1]["file"],
                        "sources": sources,
                        "redirect_db_index": redirect,
                        "resolution": resolution,
                    }

            # A newly added supplementary key must remain visible even if a
            # patch has not added its primary redirect row yet.
            for number in sorted(supplements):
                for key, (row, position) in sorted(supplements[number].items()):
                    if key in seen:
                        continue
                    seen.add(key)
                    source = supplement_paths[number]
                    report("orphaned_localization_supplement", "warning", source, f"[{position}]", key=key)
                    record = {
                        "namespace": "multi_text",
                        "key": key,
                        "locale": locale,
                        "content": row.get("Content"),
                        "resolution": localization_resolution(row.get("Content")),
                        "raw_content": row.get("Content"),
                        "redirect_db_index": None,
                        "sources": [{"file": source, "position": position}],
                    }
                    target.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                    counts["multi_text"] += 1
                    if locale == "en":
                        english[key] = {"content": row.get("Content"), "source": source,
                                        "sources": record["sources"], "redirect_db_index": None,
                                        "resolution": record["resolution"]}

            for namespace, relative in (
                ("speaker", "speaker/Speaker.json"),
                ("subtitle_text", "subtitle_text/SubtitleText.json"),
            ):
                path = locale_dir / relative
                if not path.is_file():
                    report("missing_localization_table", "warning", str(path), "", locale=locale)
                    continue
                source = source_name(path)
                numeric_seen: set[int | str] = set()
                for position, row in enumerate(_rows(path)):
                    key = row.get("Id")
                    if key is None:
                        report("invalid_localization_key", "error", source, f"[{position}]", row=row)
                        continue
                    if key in numeric_seen:
                        report("duplicate_localization_key", "error", source, f"[{position}]", key=key)
                    numeric_seen.add(key)
                    record = {
                        "namespace": namespace,
                        "key": key,
                        "locale": locale,
                        "content": row.get("Content"),
                        "resolution": localization_resolution(row.get("Content")),
                        "sources": [{"file": source, "position": position}],
                    }
                    target.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                    counts[namespace] += 1

        os.replace(temporary_path, output_path)
        if emit is not None:
            emit("localization_file", {"locale": locale, "path": str(output_path), "counts": counts})
    return english


def aggregate_locales(directory: str | Path) -> int:
    """Merge locale JSONL into one key-centered, values-by-locale index.

    Each locale is sorted independently on disk, so only one locale's records
    and one key group are held in memory at a time. The source JSONL files
    remain the complete per-locale evidence.
    """
    folder = Path(directory)
    locale_files = sorted(path for path in folder.glob("*.jsonl")
                          if path.name != "all-locales.jsonl" and not is_ignored_fs_entry(path))
    if not locale_files:
        return 0
    locale_names = [path.stem for path in locale_files]
    with TemporaryDirectory(dir=folder) as temporary:
        sorted_files = []
        identities: set[tuple[str, str]] = set()
        coverage: dict[str, Counter[str]] = {}
        for path in locale_files:
            keyed = []
            with path.open(encoding="utf-8") as source:
                for line in source:
                    row = json.loads(line)
                    identities.add((row["namespace"], str(row["key"])))
                    locale_counts = coverage.setdefault(row["locale"], Counter())
                    locale_counts[row.get("resolution", localization_resolution(row.get("content")))] += 1
                    keyed.append((row["namespace"], str(row["key"]), row["locale"], line))
            keyed.sort(key=lambda item: item[:3])
            sorted_path = Path(temporary) / path.name
            with sorted_path.open("w", encoding="utf-8") as target:
                for namespace, key, locale, line in keyed:
                    target.write(json.dumps([namespace, key, locale, line], ensure_ascii=False) + "\n")
            sorted_files.append(sorted_path)

        streams = [path.open(encoding="utf-8") for path in sorted_files]
        try:
            merged = heapq.merge(*(map(json.loads, stream) for stream in streams),
                                 key=lambda item: item[:3])
            count = 0
            current_key = None
            raw_key = None
            values = {}
            output = folder / "all-locales.jsonl"
            def complete_values(values: dict[str, Any]) -> dict[str, Any]:
                complete = dict(values)
                for locale in locale_names:
                    complete.setdefault(locale, {"content": None, "resolution": "missing_key",
                                                  "sources": None, "redirect_db_index": None})
                return complete
            with output.open("w", encoding="utf-8") as target:
                for namespace, key, locale, line in merged:
                    identity = (namespace, key)
                    if current_key is not None and identity != current_key:
                        target.write(json.dumps({"namespace": current_key[0], "key": raw_key,
                                                 "values_by_locale": complete_values(values)},
                                                ensure_ascii=False, sort_keys=True) + "\n")
                        count += 1
                        values = {}
                    record = json.loads(line)
                    if identity != current_key:
                        raw_key = record["key"]
                    values[locale] = {"content": record.get("content"),
                                      "resolution": record.get("resolution", localization_resolution(record.get("content"))),
                                      "sources": record.get("sources"),
                                      "redirect_db_index": record.get("redirect_db_index")}
                    current_key = identity
                if current_key is not None:
                    target.write(json.dumps({"namespace": current_key[0], "key": raw_key,
                                             "values_by_locale": complete_values(values)},
                                            ensure_ascii=False, sort_keys=True) + "\n")
                    count += 1
        finally:
            for stream in streams:
                stream.close()
    locale_report = {}
    for locale, counts in sorted(coverage.items()):
        present = sum(counts.values())
        locale_report[locale] = {
            "total_identities": len(identities),
            "resolved_nonempty": counts["resolved_nonempty"],
            "resolved_empty": counts["resolved_empty"],
            "missing_key": max(0, len(identities) - present),
            "broken_redirect": counts["broken_redirect"],
        }
    (folder / "coverage.json").write_text(
        json.dumps({"identity_count": len(identities), "locales": locale_report},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return count
