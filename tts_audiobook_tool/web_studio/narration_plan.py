"""B03: deterministic production manifest over canonical Project-V5/book.v3.

No EPUB parsing, segmentation, TTS inference, or separate German normalization
happens here. The source of spoken units is the persisted PhraseGroup sequence.
Optional B01 provenance is joined by selected logical chapter and output offset.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable

SCHEMA = "narration-plan.v1"
PLAN_VERSION = 3
NEW_NORMALIZER = "de-narration-v3"
MAX_UNITS = 100_000
MAX_SOURCE_CHARS = 20_000_000
REASONS = {"undefined", "w", "isqe", "is", "s", "p", "h", "x", "xx"}
REASON_BOUNDARIES = {
    "undefined": "unspecified", "w": "technical-word-limit",
    "isqe": "quotation", "is": "phrase", "s": "sentence",
    "p": "paragraph", "h": "heading", "x": "space-break",
    "xx": "chapter",
}
AUDIO_CONFIG = {
    "intermediate_format": "wav",
    "sample_rate": "model-native",
    "channels": "model-native",
    "export_codec": "aac",
    "export_bitrate": "96k",
}


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _canonical_sections(book: dict) -> list[dict]:
    if not isinstance(book, dict) or book.get("format") != "book.v3":
        raise ValueError("NarrationPlan benötigt das kanonische book.v3-Format.")
    body = book.get("book")
    sections = body.get("sections") if isinstance(body, dict) else None
    if not isinstance(sections, list) or not sections:
        raise ValueError("NarrationPlan: Kapitelstruktur fehlt.")
    result = []
    total_chars = 0
    for section in sections:
        if not isinstance(section, dict) or not isinstance(section.get("phrase_groups"), list):
            raise ValueError("NarrationPlan: ungültige Kapitel- oder PhraseGroup-Struktur.")
        groups = []
        for group in section["phrase_groups"]:
            if not isinstance(group, dict) or not isinstance(group.get("phrases"), list):
                raise ValueError("NarrationPlan: ungültige PhraseGroup.")
            phrases = []
            for phrase in group["phrases"]:
                if not isinstance(phrase, dict) or not isinstance(phrase.get("text"), str):
                    raise ValueError("NarrationPlan: ungültiger Phrase-Originaltext.")
                reason = phrase.get("reason") or "undefined"
                if reason not in REASONS:
                    raise ValueError("NarrationPlan: unbekannter Phrase-Reason.")
                total_chars += len(phrase["text"])
                if total_chars > MAX_SOURCE_CHARS:
                    raise ValueError("NarrationPlan: Buchtext überschreitet das sichere Zeichenlimit.")
                phrases.append({"text": phrase["text"], "reason": reason})
            groups.append({"phrases": phrases, "is_dialog": bool(group.get("is_dialog", False))})
        title = section.get("title") or ""
        if not isinstance(title, str):
            raise ValueError("NarrationPlan: ungültiger Kapiteltitel.")
        result.append({"title": title, "groups": groups})
    return result


def _selection(project_dir: Path, sections: list[dict],
               source_indices: list[int] | None) -> tuple[list[int], list[str] | None, dict | None]:
    catalogue_path = project_dir / "chapters.json"
    if not catalogue_path.exists():
        if source_indices is not None and len(source_indices) != len(sections):
            raise ValueError("NarrationPlan: Kapitelauswahl weicht von book.v3 ab.")
        return list(range(len(sections))) if source_indices is None else source_indices, None, None
    catalogue = json.loads(catalogue_path.read_text(encoding="utf-8"))
    chapters = catalogue.get("chapters")
    if not isinstance(chapters, list) or source_indices is None or len(source_indices) != len(sections):
        raise ValueError("NarrationPlan: gespeicherte EPUB-Kapitelauswahl ist unvollständig.")
    if len(set(source_indices)) != len(source_indices) or source_indices != sorted(source_indices):
        raise ValueError("NarrationPlan: doppelte oder vertauschte Kapitelreferenzen.")
    if any(not isinstance(i, int) or i < 0 or i >= len(chapters) for i in source_indices):
        raise ValueError("NarrationPlan: ungültiger EPUB-Kapitelindex.")
    raw_path = project_dir / "engine" / "project_text_raw.txt"
    if not raw_path.is_file():
        raise ValueError("NarrationPlan: kanonischer EPUB-Vorlesetext fehlt.")
    raw = raw_path.read_text(encoding="utf-8")
    offset = 0
    texts = []
    for position, index in enumerate(source_indices):
        chapter = chapters[index]
        count = chapter.get("characters") if isinstance(chapter, dict) else None
        if not isinstance(count, int) or count < 0 or offset + count > len(raw):
            raise ValueError("NarrationPlan: gespeicherte EPUB-Textlänge ist ungültig.")
        texts.append(raw[offset:offset + count])
        offset += count
        if position + 1 < len(source_indices):
            if raw[offset:offset + 2] != "\n\n":
                raise ValueError("NarrationPlan: EPUB-Kapitelgrenze stimmt nicht mit Originaltext überein.")
            offset += 2
    if offset != len(raw):
        raise ValueError("NarrationPlan: unzugeordneter Quelltext hinter dem letzten Kapitel.")
    return source_indices, texts, catalogue


def _validated_b01_spans(catalogue: dict | None, source_index: int, text: str) -> list[dict]:
    if catalogue is None:
        return []
    audit = catalogue.get("content_audit")
    mapping = audit.get("source_mapping") if isinstance(audit, dict) else None
    if not isinstance(mapping, list) or not mapping:
        if isinstance(audit, dict) and audit.get("version") == 2 and text.strip():
            raise ValueError("NarrationPlan: B01-Quelltextzuordnung fehlt.")
        # Pre-B01 projects retain canonical book.v3 references, not invented EPUB offsets.
        return []
    spans = []
    for row in mapping:
        if not isinstance(row, dict):
            raise ValueError("NarrationPlan: beschädigte B01-Quelltextzuordnung.")
        if row.get("chapter_index") != source_index:
            continue
        start, end = row.get("output_start"), row.get("output_end")
        s0, s1 = row.get("source_start"), row.get("source_end")
        if (row.get("mapping_status") != "mapped" or not isinstance(start, int)
                or not isinstance(end, int) or not 0 <= start < end <= len(text)
                or not isinstance(s0, int) or not isinstance(s1, int) or not 0 <= s0 < s1
                or not isinstance(row.get("source_href"), str) or not row["source_href"]):
            raise ValueError("NarrationPlan: B01-Quelltextspanne ist nicht zuordenbar.")
        spans.append({
            "source_href": row["source_href"], "source_start": s0, "source_end": s1,
            "output_start": start, "output_end": end,
        })
    spans.sort(key=lambda span: (span["output_start"], span["output_end"]))
    end = 0
    for span in spans:
        start, stop = span["output_start"], span["output_end"]
        if start > end and any(not c.isspace() for c in text[end:start]):
            raise ValueError("NarrationPlan: ausgelassene EPUB-Quelltextspanne.")
        if start < end and any(not c.isspace() for c in text[start:min(stop, end)]):
            raise ValueError("NarrationPlan: doppelte oder überlappende EPUB-Quelltextspanne.")
        end = max(end, stop)
    if any(not c.isspace() for c in text[end:]):
        raise ValueError("NarrationPlan: B01-Abdeckung des ausgewählten Kapitels unvollständig.")
    return spans


def _group_ranges(section: dict, raw: str | None) -> list[tuple[int | None, int | None]]:
    groups = section["groups"]
    if raw is None:
        return [(None, None)] * len(groups)
    # Non-whitespace characters are the canonical coverage domain from B01.
    # Avoid a per-character Python integer position list: multi-million-character
    # technical books otherwise multiply memory by dozens of bytes per char.
    actual = "".join(c for c in raw if not c.isspace())
    from_groups = "".join(
        c for group in groups for phrase in group["phrases"] for c in phrase["text"]
        if not c.isspace()
    )
    if actual != from_groups:
        raise ValueError("NarrationPlan: Auslassung, Dublette oder vertauschter Text zwischen EPUB und book.v3.")
    cursor = 0
    ranges = []
    for group in groups:
        remaining = sum(not c.isspace() for phrase in group["phrases"] for c in phrase["text"])
        if not remaining:
            ranges.append((None, None))
            continue
        while cursor < len(raw) and raw[cursor].isspace():
            cursor += 1
        start = cursor
        last_end = start
        while remaining:
            if cursor >= len(raw):
                raise ValueError("NarrationPlan: Kapitelabdeckung ist unvollständig.")
            if not raw[cursor].isspace():
                remaining -= 1
                last_end = cursor + 1
            cursor += 1
        ranges.append((start, last_end))
    return ranges


def _unit_spans(spans: list[dict], start: int | None, end: int | None) -> list[dict]:
    if start is None or end is None:
        return []
    return [
        {**span, "covered_output_start": max(start, span["output_start"]),
         "covered_output_end": min(end, span["output_end"])}
        for span in spans
        if span["output_start"] < end and start < span["output_end"]
    ]


def _kind(raw: str, reason: str, is_dialog: bool) -> str:
    if reason == "h":
        return "heading"
    if re.match(r"^\s*(?:[•*-]\s+|\d+[.)]\s+)", raw):
        return "list"
    if is_dialog:
        return "dialogue"
    return "prose"


def build_narration_plan(
    project_id: str, project_dir: Path, source_indices: list[int] | None,
    glossary: dict[str, str], voice: str, model_identity: str,
    normalizer_version: str, speech_prompt: Callable[[str, dict[str, str], str], str],
    created_at: str = "",
) -> dict:
    """Produce one immutable-in-meaning plan; timestamps never enter hashes."""
    path = project_dir / "engine" / "project_text.json"
    book = json.loads(path.read_text(encoding="utf-8"))
    sections = _canonical_sections(book)
    selected, raw_texts, catalogue = _selection(project_dir, sections, source_indices)
    units = []
    # Source positions, not duplicate word sequences, identify genuinely
    # duplicated EPUB coverage. Check all *selected* chapters together.
    source_ranges: dict[str, list[tuple[int, int]]] = {}
    for section_index, section in enumerate(sections):
        original_index = selected[section_index]
        chapter_id = f"epub-chapter:{original_index:06d}"
        raw = raw_texts[section_index] if raw_texts is not None else None
        spans = _validated_b01_spans(catalogue, original_index, raw) if raw is not None else []
        for span in spans:
            source_ranges.setdefault(span["source_href"], []).append(
                (span["source_start"], span["source_end"])
            )
        ranges = _group_ranges(section, raw)
        for group_index, (group, (start, end)) in enumerate(zip(section["groups"], ranges)):
            original = "".join(p["text"] for p in group["phrases"])
            spoken_source = original.strip()
            if not spoken_source:
                continue
            if len(units) >= MAX_UNITS:
                raise ValueError("NarrationPlan enthält zu viele Vorleseeinheiten.")
            prompt = speech_prompt(spoken_source, glossary, normalizer_version)
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12000:
                raise ValueError("NarrationPlan enthält einen leeren oder überlangen TTS-Prompt.")
            phrase_ranges = []
            cursor = 0
            for phrase_index, phrase in enumerate(group["phrases"]):
                stop = cursor + len(phrase["text"])
                phrase_ranges.append({
                    "index": phrase_index, "start": cursor, "end": stop,
                    "text": phrase["text"], "reason": phrase["reason"],
                })
                cursor = stop
            reason = group["phrases"][-1]["reason"] if group["phrases"] else "undefined"
            unit_id = f"{chapter_id}:g{group_index:06d}:{hashlib.sha256(original.encode('utf-8')).hexdigest()[:16]}"
            units.append({
                "unit_id": unit_id, "position": len(units),
                "chapter_id": chapter_id, "chapter": section_index,
                "chapter_source_index": original_index, "chapter_position": group_index,
                "title": section["title"] or f"Kapitel {section_index + 1}",
                "text": spoken_source, "original_text": original, "speech_text": prompt,
                "section_type": _kind(original, reason, group["is_dialog"]),
                "boundary_reason": REASON_BOUNDARIES[reason],
                "pause": {"kind": REASON_BOUNDARIES[reason], "duration_ms": None},
                "source": {
                    "format": "book.v3", "section_index": section_index,
                    "group_index": group_index, "phrases": phrase_ranges,
                    "chapter_output_start": start, "chapter_output_end": end,
                    "epub_spans": _unit_spans(spans, start, end),
                    "mapping_status": "mapped" if spans else "book-only",
                },
            })
    if not units:
        raise ValueError("NarrationPlan enthält keinen sprechbaren Inhalt.")
    for ranges in source_ranges.values():
        ranges.sort()
        previous_end = -1
        for start, end in ranges:
            if start < previous_end:
                raise ValueError("NarrationPlan: dieselbe EPUB-Quelltextspanne wurde mehrfach zugeordnet.")
            previous_end = end
    plan = {
        "version": PLAN_VERSION, "schema": SCHEMA, "project_id": project_id,
        "voice": voice, "model_identity": model_identity, "audio": dict(AUDIO_CONFIG),
        "glossary": glossary, "normalizer_version": normalizer_version,
        "book_fingerprint": _digest(sections),
        "segments": units, "created_at": created_at,
    }
    plan["content_fingerprint"] = _digest(units)
    plan["config_fingerprint"] = _digest({
        "voice": voice, "model_identity": model_identity, "audio": plan["audio"],
        "glossary": glossary, "normalizer_version": normalizer_version,
    })
    plan["plan_fingerprint"] = _digest({
        "schema": SCHEMA, "project_id": project_id,
        "book_fingerprint": plan["book_fingerprint"],
        "content_fingerprint": plan["content_fingerprint"],
        "config_fingerprint": plan["config_fingerprint"],
    })
    validate_narration_plan(plan)
    return plan


def validate_narration_plan(plan: dict) -> bool:
    """Return False only for explicitly historical plans; fail closed otherwise."""
    if not isinstance(plan, dict):
        raise ValueError("Ungültiges Produktionsmanifest.")
    version = plan.get("version", 2)
    if version == 2:
        if not isinstance(plan.get("segments"), list):
            raise ValueError("Ungültiger historischer Auftrag.")
        return False
    if version != PLAN_VERSION or plan.get("schema") != SCHEMA:
        raise ValueError("Unbekannte oder inkompatible NarrationPlan-Version.")
    segments = plan.get("segments")
    if not isinstance(segments, list) or not 1 <= len(segments) <= MAX_UNITS:
        raise ValueError("NarrationPlan: ungültige Anzahl Vorleseeinheiten.")
    if not isinstance(plan.get("glossary"), dict) or not isinstance(plan.get("audio"), dict):
        raise ValueError("NarrationPlan: Produktionskonfiguration fehlt.")
    if plan.get("normalizer_version") != NEW_NORMALIZER or plan["audio"] != AUDIO_CONFIG:
        raise ValueError("NarrationPlan: unbekannte Normalisierungs- oder Audio-Konfiguration.")
    seen = set()
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict) or segment.get("position") != index:
            raise ValueError("NarrationPlan: Reihenfolge der Vorleseeinheiten beschädigt.")
        unit_id = segment.get("unit_id")
        if not isinstance(unit_id, str) or unit_id in seen:
            raise ValueError("NarrationPlan: doppelte oder fehlende Einheiten-ID.")
        seen.add(unit_id)
        source = segment.get("source")
        if not isinstance(source, dict) or source.get("format") != "book.v3":
            raise ValueError("NarrationPlan: ungültige Textquelle.")
        phrases = source.get("phrases")
        if not isinstance(phrases, list) or not phrases:
            raise ValueError("NarrationPlan: Quell-Phrases fehlen.")
        offset = 0
        raw = ""
        for phrase_index, phrase in enumerate(phrases):
            if (not isinstance(phrase, dict) or phrase.get("index") != phrase_index
                    or phrase.get("start") != offset or not isinstance(phrase.get("text"), str)
                    or phrase.get("reason") not in REASONS):
                raise ValueError("NarrationPlan: beschädigte Quelltextspanne.")
            raw += phrase["text"]
            offset += len(phrase["text"])
            if phrase.get("end") != offset:
                raise ValueError("NarrationPlan: überlappende oder fehlende Phrase-Spanne.")
        if raw != segment.get("original_text") or raw.strip() != segment.get("text"):
            raise ValueError("NarrationPlan: Originaltext und Einheitenquelle weichen ab.")
        if not isinstance(segment.get("speech_text"), str) or not segment["speech_text"].strip():
            raise ValueError("NarrationPlan: TTS-Prompt fehlt.")
        if segment.get("chapter") != source.get("section_index") or segment.get("chapter_position") != source.get("group_index"):
            raise ValueError("NarrationPlan: ungültige Kapitel- oder Gruppenreferenz.")
        chapter_source_index = segment.get("chapter_source_index")
        if (not isinstance(chapter_source_index, int) or chapter_source_index < 0
                or segment.get("chapter_id") != f"epub-chapter:{chapter_source_index:06d}"):
            raise ValueError("NarrationPlan: Kapitelidentität beschädigt.")
        group_index = source.get("group_index")
        if not isinstance(group_index, int) or group_index < 0:
            raise ValueError("NarrationPlan: PhraseGroup-Identität beschädigt.")
        expected_id = (f"epub-chapter:{chapter_source_index:06d}:g{group_index:06d}:"
                       f"{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}")
        if unit_id != expected_id:
            raise ValueError("NarrationPlan: Einheitenidentität weicht vom Originaltext ab.")
        if segment.get("pause", {}).get("kind") != segment.get("boundary_reason"):
            raise ValueError("NarrationPlan: Segmentgrenze und Pausenplanung weichen ab.")
    expected_config = _digest({
        "voice": plan.get("voice"), "model_identity": plan.get("model_identity"),
        "audio": plan["audio"], "glossary": plan["glossary"],
        "normalizer_version": plan.get("normalizer_version"),
    })
    if (plan.get("content_fingerprint") != _digest(segments)
            or plan.get("config_fingerprint") != expected_config
            or plan.get("plan_fingerprint") != _digest({
                "schema": SCHEMA, "project_id": plan.get("project_id"),
                "book_fingerprint": plan.get("book_fingerprint"),
                "content_fingerprint": plan.get("content_fingerprint"),
                "config_fingerprint": plan.get("config_fingerprint"),
            })):
        raise ValueError("NarrationPlan: Fingerprint stimmt nicht mit den Produktionsdaten überein.")
    return True


def assert_book_unchanged(project_dir: Path, plan: dict) -> None:
    if not validate_narration_plan(plan):
        return
    book = json.loads((project_dir / "engine" / "project_text.json").read_text(encoding="utf-8"))
    if _digest(_canonical_sections(book)) != plan["book_fingerprint"]:
        raise ValueError("Project-V5-Vorlesetext wurde seit Auftragsbeginn verändert.")


def segment_fingerprint(segment: dict, plan: dict) -> str:
    """Called after one whole-plan validation; O(1) per segment on resume."""
    return _digest({
        "version": SCHEMA, "unit_id": segment["unit_id"],
        "position": segment["position"], "chapter_id": segment["chapter_id"],
        "original_text": segment["original_text"],
        "speech_text": segment["speech_text"], "config": plan["config_fingerprint"],
        "boundary_reason": segment["boundary_reason"], "pause": segment["pause"],
    })
