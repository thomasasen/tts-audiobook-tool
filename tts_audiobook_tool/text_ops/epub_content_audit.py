from __future__ import annotations

import importlib
import posixpath
import re
import zipfile
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET


_CONTAINER = "META-INF/container.xml"
_MAX_PREVIEW = 180
_MAX_REVIEW_ELEMENTS = 80
_INLINE_FORMAT_TAGS = {"abbr", "b", "code", "em", "i", "mark", "q", "s", "small", "strong", "sub", "sup", "u"}


def _safe_archive_path(base: str, href: str) -> str:
    path = unquote(href.split("#", 1)[0].split("?", 1)[0])
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(base), path))
    if joined.startswith("../") or joined == ".." or joined.startswith("/"):
        raise ValueError("EPUB resource path escapes archive root")
    return joined


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _normal_path(value: str) -> str:
    return posixpath.normpath(unquote(value.split("#", 1)[0].split("?", 1)[0])).lstrip("/")


def _plain(value: str, limit: int = _MAX_PREVIEW) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


class _MarkupAuditParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.counts: dict[str, int] = {}
        self.text_parts: list[str] = []
        self._ignored_depth = 0
        self._ignored_tags = {"script", "style", "head", "svg", "noscript"}
        self.image_alt: list[str] = []
        self.footnotes = 0
        self.footnote_refs = 0
        self.footnote_targets: list[str] = []
        self.scene_breaks = 0
        self.inline_formatting = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        self.counts[tag] = self.counts.get(tag, 0) + 1
        values = {str(key).lower(): value for key, value in attrs}
        if tag == "img":
            alt = (values.get("alt") or "").strip()
            if alt:
                self.image_alt.append(alt[:_MAX_PREVIEW])
            # An explicitly identified decorative scene divider is not spoken,
            # but must be visible in the structural audit.
            image_label = f"{alt} {values.get('src') or ''}".lower().replace("_", "-")
            if re.search(r"(?:scene-break|scenebreak|dinkus|break-ornament)", image_label):
                self.scene_breaks += 1
        role = str(values.get("role") or "").lower().split()
        epub_type = str(values.get("epub:type") or values.get("type") or "").lower().split()
        if "doc-footnote" in role or "footnote" in epub_type:
            self.footnotes += 1
        if "doc-noteref" in role or "noteref" in epub_type:
            self.footnote_refs += 1
            target = str(values.get("href") or "").strip()
            if target:
                self.footnote_targets.append(target)
        if tag == "hr":
            self.scene_breaks += 1
        classes = str(values.get("class") or "").lower().replace("_", "-").split()
        if any(token in {"scene-break", "scenebreak", "ornament", "dinkus"} for token in classes):
            self.scene_breaks += 1
        if tag in _INLINE_FORMAT_TAGS:
            self.inline_formatting += 1
        if tag in self._ignored_tags:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._ignored_tags and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and data.strip():
            self.text_parts.append(data.strip())


def _markup_stats(payload: bytes) -> dict:
    parser = _MarkupAuditParser()
    try:
        parser.feed(payload.decode("utf-8-sig", errors="replace"))
        parser.close()
    except Exception:
        pass
    counts = parser.counts
    text = re.sub(r"\s+", " ", " ".join(parser.text_parts)).strip()
    return {
        "characters": len(text),
        "preview": text[:_MAX_PREVIEW],
        "tables": counts.get("table", 0),
        "lists": counts.get("ul", 0) + counts.get("ol", 0),
        "list_items": counts.get("li", 0),
        "captions": counts.get("figcaption", 0) + counts.get("caption", 0),
        "images": counts.get("img", 0),
        "images_with_alt": len(parser.image_alt),
        "mathml": counts.get("math", 0),
        "footnotes": parser.footnotes,
        "footnote_refs": parser.footnote_refs,
        "footnote_targets": parser.footnote_targets,
        "footnote_markers": sum(
            1 for value in parser.text_parts
            if re.search(r"(?:\[\d+\]|\(\d+\)|\*{1,3})$", value)
        ),
        "scene_breaks": parser.scene_breaks,
        "inline_formatting": parser.inline_formatting,
        "headings": sum(counts.get(f"h{i}", 0) for i in range(1, 7)),
    }


def _review_elements(payload: bytes, href: str, overrides: dict[str, str], *, retained: bool) -> list[dict]:
    try:
        BeautifulSoup = getattr(importlib.import_module("bs4"), "BeautifulSoup")
        soup = BeautifulSoup(payload.decode("utf-8-sig", errors="replace"), "html.parser")
    except Exception:
        return []

    counters: dict[str, int] = {}
    result: list[dict] = []
    for element in soup.find_all(["table", "math", "figure", "img"]):
        tag = str(element.name or "").lower()
        index = counters.get(tag, 0)
        counters[tag] = index + 1
        key = f"{tag}:{index}"
        if tag == "img" and element.find_parent("figure") is not None:
            continue
        text = _plain(element.get_text(" ", strip=True), 500)
        alt = _plain(str(element.get("alt") or ""), 300) if tag == "img" else ""
        caption_node = element.find(["caption", "figcaption"])
        caption = _plain(caption_node.get_text(" ", strip=True), 300) if caption_node else ""
        preview = caption or alt or text or "Kein beschreibender Text im Quell-EPUB."
        if tag == "table":
            handling = "Zellinhalte werden derzeit in Lesereihenfolge abgeflacht."
            kind = "Tabelle"
        elif tag == "math":
            handling = "MathML-Inhalt wird derzeit aus seinen Textknoten abgeflacht."
            kind = "Formel"
        elif tag == "figure":
            handling = "Bilddateien werden nicht gesprochen; vorhandene Bildunterschriften bleiben erhalten."
            kind = "Abbildung"
        else:
            marker = f"{alt} {element.get('src') or ''}".lower().replace("_", "-")
            if re.search(r"(?:scene-break|scenebreak|dinkus|break-ornament)", marker):
                handling = "Dekorativer Szenentrenner: Abschnittsabstand bleibt erhalten, das Bild wird nicht gesprochen."
                kind = "Szenentrenner"
            else:
                handling = "Das Bild selbst wird nicht gesprochen; Alternativtext wird nur als Vorschau angezeigt."
                kind = "Bild"
        result.append({
            "source_href": href,
            "element_key": key,
            "kind": kind,
            "preview": preview,
            "source_text": text,
            "alt_text": alt,
            "caption": caption,
            "handling": handling,
            "can_edit": retained,
            "speaker_text": overrides.get(key, ""),
            "approved": bool(overrides.get(key, "").strip()),
        })
        if len(result) >= _MAX_REVIEW_ELEMENTS:
            break
    return result


def _mapping_for_chapters(chapters) -> tuple[list[dict], dict[str, dict]]:
    mapping: list[dict] = []
    coverage: dict[str, dict] = {}
    for chapter_index, chapter in enumerate(chapters):
        text = getattr(chapter, "text", "") or ""
        total_non_whitespace = sum(not char.isspace() for char in text)
        intervals: list[tuple[int, int]] = []
        for span in getattr(chapter, "source_spans", []) or []:
            start = getattr(span, "output_start", None)
            end = getattr(span, "output_end", None)
            source_start = getattr(span, "source_start", None)
            source_end = getattr(span, "source_end", None)
            status = getattr(span, "mapping_status", "unresolved")
            if (
                status == "mapped" and isinstance(start, int) and isinstance(end, int)
                and isinstance(source_start, int) and isinstance(source_end, int)
                and 0 <= start <= end <= len(text)
            ):
                intervals.append((start, end))
            mapping.append({
                "chapter_index": chapter_index,
                "chapter_title": getattr(chapter, "title", ""),
                "source_href": getattr(span, "source_href", ""),
                "source_start": source_start,
                "source_end": source_end,
                "output_start": start,
                "output_end": end,
                "navigation_title": getattr(span, "navigation_title", ""),
                "mapping_status": status,
            })
        intervals.sort()
        merged: list[tuple[int, int]] = []
        for start, end in intervals:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        mapped_non_whitespace = sum(
            not char.isspace()
            for start, end in merged
            for char in text[start:end]
        )
        coverage[str(chapter_index)] = {
            "total_non_whitespace_characters": total_non_whitespace,
            "mapped_non_whitespace_characters": mapped_non_whitespace,
            "unmapped_non_whitespace_characters": max(0, total_non_whitespace - mapped_non_whitespace),
            "coverage_percent": round(
                100.0 if total_non_whitespace == 0 else
                100.0 * mapped_non_whitespace / total_non_whitespace, 2
            ),
        }
    return mapping, coverage


def audit_epub_content(
        epub_path: str,
        extracted_chapters,
        warnings: list[str] | None = None,
        *,
        skipped_sources: list[dict] | None = None,
        speaker_overrides: dict[str, dict[str, str]] | None = None,
        included_source_hrefs: set[str] | None = None,
) -> dict:
    """Inventory source XHTML and report explicit source-to-output text mappings.

    Mapping offsets refer to normalized extracted text and output chapter text,
    never to ZIP byte offsets. Complex markup is inventoried for human review;
    speaker text is used only when explicitly supplied by the user.
    """
    warnings = list(warnings or [])
    skipped_sources = list(skipped_sources or [])
    speaker_overrides = speaker_overrides or {}
    mapping, chapter_coverage = _mapping_for_chapters(extracted_chapters)
    try:
        with zipfile.ZipFile(epub_path) as archive:
            names = set(archive.namelist())
            if _CONTAINER not in names:
                raise ValueError("container.xml missing")
            container = ET.fromstring(archive.read(_CONTAINER))
            rootfile = next(
                (node.attrib.get("full-path") for node in container.iter()
                 if _local_name(node.tag) == "rootfile"),
                None,
            )
            if not rootfile or rootfile not in names:
                raise ValueError("OPF package document missing")
            manual_include_paths = set()
            for href in (included_source_hrefs or set()):
                candidate = _normal_path(href)
                if candidate not in names:
                    candidate = _safe_archive_path(rootfile, href)
                manual_include_paths.add(candidate)
            package = ET.fromstring(archive.read(rootfile))
            manifest: dict[str, dict] = {}
            for item in package.iter():
                if _local_name(item.tag) == "item":
                    item_id = item.attrib.get("id", "")
                    href = item.attrib.get("href", "")
                    if item_id and href:
                        path = _safe_archive_path(rootfile, href)
                        manifest[item_id] = {
                            "path": path,
                            "media_type": item.attrib.get("media-type", ""),
                            "properties": item.attrib.get("properties", "").split(),
                        }
            spine = next((node for node in package.iter() if _local_name(node.tag) == "spine"), None)
            if spine is None:
                raise ValueError("OPF spine missing")
            spine_ids = [
                node.attrib.get("idref", "")
                for node in spine
                if _local_name(node.tag) == "itemref"
            ]
            spine_paths = {
                manifest[item_id]["path"]
                for item_id in spine_ids if item_id in manifest
            }

            mapped_by_path: dict[str, list[dict]] = {}
            for item in mapping:
                source_href = item.get("source_href") or ""
                if not source_href:
                    continue
                candidate = _normal_path(source_href)
                if candidate not in names:
                    candidate = _safe_archive_path(rootfile, source_href)
                mapped_by_path.setdefault(candidate, []).append(item)

            skip_by_path: dict[str, dict] = {}
            for item in skipped_sources:
                href = item.get("href")
                if not href:
                    continue
                candidate = _normal_path(href)
                if candidate not in names:
                    candidate = _safe_archive_path(rootfile, href)
                skip_by_path[candidate] = item
            override_paths: dict[str, dict[str, str]] = {}
            for href, values in speaker_overrides.items():
                if not isinstance(values, dict):
                    continue
                candidate = _normal_path(href)
                if candidate not in names:
                    candidate = _safe_archive_path(rootfile, href)
                override_paths[candidate] = values
            resources = []
            skipped = []
            review_elements = []
            aggregate = {
                "spine_items": len(spine_ids), "retained_spine_items": 0,
                "skipped_spine_items": 0, "missing_spine_items": 0,
                "source_characters": 0, "source_tables": 0, "source_lists": 0,
                "source_images": 0, "source_captions": 0, "source_mathml": 0,
                "source_footnotes": 0, "source_footnote_refs": 0,
                "source_scene_breaks": 0, "source_inline_formatting": 0,
                "linked_non_spine_footnote_targets": 0,
                "mapped_output_non_whitespace_characters": 0,
                "total_output_non_whitespace_characters": 0,
            }
            for chapter_coverage_item in chapter_coverage.values():
                aggregate["mapped_output_non_whitespace_characters"] += chapter_coverage_item["mapped_non_whitespace_characters"]
                aggregate["total_output_non_whitespace_characters"] += chapter_coverage_item["total_non_whitespace_characters"]

            for index, item_id in enumerate(spine_ids):
                item = manifest.get(item_id)
                if item is None:
                    skipped.append({
                        "index": index, "id": item_id, "status": "missing",
                        "kind": "missing-spine-item",
                        "reason": "Spine-Eintrag verweist auf keine Manifest-Ressource.",
                        "preview": "", "can_restore": False, "overridden": False,
                    })
                    aggregate["missing_spine_items"] += 1
                    continue
                path = item["path"]
                if path not in names:
                    skipped.append({
                        "index": index, "id": item_id, "href": path, "status": "missing",
                        "kind": "missing-resource",
                        "reason": "Manifest-Ressource fehlt im ZIP-Archiv.",
                        "preview": "", "can_restore": False, "overridden": False,
                    })
                    aggregate["missing_spine_items"] += 1
                    continue
                media_type = item["media_type"]
                if not (media_type.endswith("xhtml+xml") or media_type.endswith("html")):
                    resources.append({
                        "index": index, "href": path, "media_type": media_type,
                        "status": "non-text", "characters": 0,
                    })
                    continue
                payload = archive.read(path)
                stats = _markup_stats(payload)
                mapped_items = mapped_by_path.get(path, [])
                skip_record = skip_by_path.get(path, {})
                retained = bool(mapped_items)
                overridden = bool(skip_record.get("overridden")) or path in manual_include_paths
                source_status = (
                    "retained-by-user" if retained and overridden
                    else "retained" if retained
                    else skip_record.get("status", "not-retained")
                )
                resource_overrides = override_paths.get(path, {})
                can_edit_elements = retained or skip_record.get("kind") == "non-vocalizable"
                elements = _review_elements(payload, path, resource_overrides, retained=can_edit_elements)
                review_elements.extend(elements)
                resource = {
                    "index": index, "href": path, "media_type": media_type,
                    "status": source_status,
                    "included_by_user": overridden,
                    "override_reason": skip_record.get("reason", "") if overridden else "",
                    "mapped_segments": len(mapped_items),
                    "mapped_output_characters": sum(
                        max(0, (item.get("output_end") or 0) - (item.get("output_start") or 0))
                        for item in mapped_items
                        if item.get("mapping_status") == "mapped"
                    ),
                    **stats,
                }
                resources.append(resource)
                if retained:
                    aggregate["retained_spine_items"] += 1
                else:
                    aggregate["skipped_spine_items"] += 1
                    reason = skip_record.get("reason") or (
                        "Quellressource enthält keinen sichtbaren Text."
                        if not stats["characters"]
                        else "Kein extrahierter Vorlesetext ist dieser Quellressource zugeordnet."
                    )
                    skipped.append({
                        "index": index, "id": item_id, "href": path,
                        "status": skip_record.get("status", "not-retained"),
                        "kind": skip_record.get("kind", "unmapped-source"),
                        "reason": reason,
                        "preview": skip_record.get("preview") or stats["preview"],
                        "can_restore": bool(skip_record.get("can_restore")),
                        "overridden": False,
                    })
                for key in (
                    "characters", "tables", "lists", "images", "captions", "mathml",
                    "footnotes", "footnote_refs", "scene_breaks", "inline_formatting",
                ):
                    aggregate_key = "source_" + key
                    if aggregate_key in aggregate:
                        aggregate[aggregate_key] += stats[key]
                for raw_target in stats.get("footnote_targets", []):
                    parsed_target = urlsplit(raw_target)
                    if parsed_target.scheme or parsed_target.netloc:
                        continue
                    if not parsed_target.path:
                        target_path = path
                    else:
                        target_path = posixpath.normpath(
                            posixpath.join(posixpath.dirname(path), unquote(parsed_target.path))
                        )
                    if target_path.startswith("../") or target_path == ".." or target_path.startswith("/"):
                        continue
                    if target_path == path or target_path in spine_paths:
                        continue
                    if target_path in names:
                        target_stats = _markup_stats(archive.read(target_path))
                        reason = (
                            "Fußnotenverweis zeigt auf eine XHTML-Ressource außerhalb des EPUB-Spine. "
                            "Der kanonische Vorleseimport übernimmt diese Ressource nicht automatisch."
                        )
                        target_status = "linked-outside-spine"
                        preview = target_stats["preview"]
                    else:
                        reason = "Fußnotenverweis zeigt auf eine nicht vorhandene EPUB-Ressource."
                        target_status = "missing"
                        preview = ""
                    if any(
                        item.get("href") == target_path
                        and item.get("kind") == "non-spine-footnote-target"
                        for item in skipped
                    ):
                        continue
                    aggregate["linked_non_spine_footnote_targets"] += 1
                    skipped.append({
                        "index": len(spine_ids) + aggregate["linked_non_spine_footnote_targets"],
                        "href": target_path,
                        "status": target_status,
                        "kind": "non-spine-footnote-target",
                        "reason": reason,
                        "preview": preview,
                        "can_restore": False,
                        "overridden": False,
                    })

            total_output = aggregate["total_output_non_whitespace_characters"]
            mapped_output = aggregate["mapped_output_non_whitespace_characters"]
            coverage_percent = round(100.0 if total_output == 0 else 100.0 * mapped_output / total_output, 2)
            aggregate["unmapped_output_non_whitespace_characters"] = max(0, total_output - mapped_output)
            aggregate["mapping_coverage_percent"] = coverage_percent
            aggregate["review_elements"] = len(review_elements)
            clean_warnings = [_plain(re.sub(r"\x1b\[[0-9;]*m", "", warning), 500) for warning in warnings[:100]]
            restorable = sorted(
                {
                    path for path, item in skip_by_path.items()
                    if item.get("can_restore")
                } | manual_include_paths
            )
            return {
                "version": 2,
                "status": "review-required" if skipped or clean_warnings or coverage_percent < 100 else "inventory-only",
                "summary": aggregate,
                "resources": resources[:500],
                "skipped": skipped[:200],
                "source_mapping": mapping[:5000],
                "chapter_coverage": chapter_coverage,
                "restorable_source_hrefs": restorable,
                "review_elements": review_elements[:_MAX_REVIEW_ELEMENTS],
                "warnings": clean_warnings,
                "review_notes": [
                    "Die Mapping-Abdeckung bezieht sich auf Nicht-Leerzeichen im normalisierten extrahierten Vorlesetext; Quell-Offsets sind keine EPUB-Bytepositionen.",
                    "Tabellen, Formeln und Abbildungen werden inventarisiert. Eine Sprecherfassung wird nur nach ausdrücklicher Eingabe und Speicherung durch den Nutzer eingesetzt.",
                    "Fußnotenverweise auf XHTML außerhalb des EPUB-Spine werden gemeldet, aber nicht automatisch in den Vorlesetext übernommen.",
                    "Nicht zugeordnete Ressourcen können Navigation oder redaktionell entbehrliche Seiten sein. Vor einer Vertonung müssen sie geprüft werden.",
                ],
            }
    except (OSError, zipfile.BadZipFile, ET.ParseError, KeyError, ValueError) as exc:
        return {
            "version": 2,
            "status": "audit-error",
            "summary": {},
            "resources": [],
            "skipped": [],
            "source_mapping": mapping[:5000],
            "chapter_coverage": chapter_coverage,
            "restorable_source_hrefs": [],
            "review_elements": [],
            "warnings": [f"EPUB-Content-Audit konnte nicht vollständig ausgeführt werden: {str(exc)[:400]}"],
            "review_notes": ["Der Import darf nicht als vollständig geprüft gelten."],
        }
