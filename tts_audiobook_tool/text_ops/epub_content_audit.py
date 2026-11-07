from __future__ import annotations

import posixpath
import re
import zipfile
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import unquote
from xml.etree import ElementTree as ET


_OPF_NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
_CONTAINER = "META-INF/container.xml"
_MAX_PREVIEW = 180


def _safe_archive_path(base: str, href: str) -> str:
    path = unquote(href.split("#", 1)[0].split("?", 1)[0])
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(base), path))
    if joined.startswith("../") or joined == ".." or joined.startswith("/"):
        raise ValueError("EPUB resource path escapes archive root")
    return joined


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


class _MarkupAuditParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.counts: dict[str, int] = {}
        self.text_parts: list[str] = []
        self._ignored_depth = 0
        self._ignored_tags = {"script", "style", "head", "svg", "noscript"}
        self._capture_depth = 0
        self.image_alt: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        self.counts[tag] = self.counts.get(tag, 0) + 1
        values = dict(attrs)
        if tag == "img":
            alt = (values.get("alt") or "").strip()
            if alt:
                self.image_alt.append(alt[:_MAX_PREVIEW])
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
        "footnote_markers": sum(
            1 for value in parser.text_parts
            if re.search(r"(?:\[\d+\]|\(\d+\)|\*{1,3})$", value)
        ),
        "headings": sum(counts.get(f"h{i}", 0) for i in range(1, 7)),
    }


def audit_epub_content(epub_path: str, extracted_chapters, warnings: list[str] | None = None) -> dict:
    """Build a read-only inventory of EPUB spine resources and retained prose.

    This audit never changes the EPUB or the canonical extraction result. It is
    deliberately conservative: source markup counts are evidence for review,
    not a claim that every image/table/formula must be narrated.
    """
    warnings = list(warnings or [])
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
            retained_paths = set()
            for chapter in extracted_chapters:
                href = getattr(chapter, "href", "")
                if not href:
                    continue
                # EbookLib item.file_name and navigation hrefs are archive-root
                # paths; manifest hrefs below are relative to the OPF location.
                candidate = unquote(href.split("#", 1)[0].split("?", 1)[0])
                if candidate in names:
                    retained_paths.add(candidate)
                else:
                    retained_paths.add(_safe_archive_path(rootfile, href))
            resources = []
            skipped = []
            aggregate = {
                "spine_items": len(spine_ids), "retained_spine_items": 0,
                "skipped_spine_items": 0, "missing_spine_items": 0,
                "source_characters": 0, "source_tables": 0, "source_lists": 0,
                "source_images": 0, "source_captions": 0, "source_mathml": 0,
            }
            for index, item_id in enumerate(spine_ids):
                item = manifest.get(item_id)
                if item is None:
                    entry = {"index": index, "id": item_id, "status": "missing",
                             "reason": "Spine-Eintrag verweist auf keine Manifest-Ressource."}
                    skipped.append(entry)
                    aggregate["missing_spine_items"] += 1
                    continue
                path = item["path"]
                if path not in names:
                    entry = {"index": index, "id": item_id, "href": path,
                             "status": "missing", "reason": "Manifest-Ressource fehlt im ZIP-Archiv."}
                    skipped.append(entry)
                    aggregate["missing_spine_items"] += 1
                    continue
                media_type = item["media_type"]
                if not (media_type.endswith("xhtml+xml") or media_type.endswith("html")):
                    resources.append({"index": index, "href": path, "media_type": media_type,
                                      "status": "non-text", "characters": 0})
                    continue
                stats = _markup_stats(archive.read(path))
                retained = path in retained_paths
                entry = {
                    "index": index, "href": path, "media_type": media_type,
                    "status": "retained" if retained else ("empty" if not stats["characters"] else "not-retained"),
                    **stats,
                }
                resources.append(entry)
                if retained:
                    aggregate["retained_spine_items"] += 1
                else:
                    aggregate["skipped_spine_items"] += 1
                    reason = "Kein extrahierter Kapitelabschnitt mit dieser Quellressource verknüpft."
                    matching_warnings = [
                        re.sub(r"\x1b\[[0-9;]*m", "", warning)[:500]
                        for warning in warnings
                        if path.lower() in warning.lower() or posixpath.basename(path).lower() in warning.lower()
                    ]
                    if matching_warnings:
                        reason = "Parser-Hinweis: " + matching_warnings[0]
                    elif not stats["characters"]:
                        reason = "Quellressource enthält nach Markup-Analyse keinen sichtbaren Text."
                    skipped.append({**entry, "reason": reason})
                for key in ("characters", "tables", "lists", "images", "captions", "mathml"):
                    aggregate_key = "source_" + key
                    if aggregate_key in aggregate:
                        aggregate[aggregate_key] += stats[key]
            warnings_limited = [
                re.sub(r"\x1b\[[0-9;]*m", "", warning)[:500]
                for warning in warnings[:100]
            ]
            return {
                "version": 1,
                "status": "review-required" if skipped or warnings_limited else "inventory-only",
                "summary": aggregate,
                "resources": resources[:500],
                "skipped": skipped[:200],
                "warnings": warnings_limited,
                "review_notes": [
                    "Der Audit ist eine Quelleninventur, keine automatische redaktionelle Freigabe.",
                    "Tabellen, Formeln, Abbildungen und Fußnoten müssen anhand der Vorschau geprüft werden.",
                    "Eine nicht zugeordnete Quellressource bedeutet nicht automatisch, dass ihr Inhalt fehlt: Navigationsabschnitte können in andere Kapitel zusammengeführt sein.",
                ],
            }
    except (OSError, zipfile.BadZipFile, ET.ParseError, KeyError, ValueError) as exc:
        return {
            "version": 1,
            "status": "audit-error",
            "summary": {},
            "resources": [],
            "skipped": [],
            "warnings": [f"EPUB-Content-Audit konnte nicht vollständig ausgeführt werden: {str(exc)[:400]}"],
            "review_notes": ["Der Import darf nicht als vollständig geprüft gelten."],
        }
