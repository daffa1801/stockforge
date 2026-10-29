"""Inject dan verifikasi metadata XMP/IPTC/EXIF pada JPEG."""

import os
from typing import Any, Dict, List


def inject(path: str, item: Dict[str, Any], expand_keywords: bool = False) -> bool:
    try:
        from app.metadata_injector import inject_jpeg_metadata
    except ImportError:
        from metadata_injector import inject_jpeg_metadata

    return inject_jpeg_metadata(
        path,
        title=item.get("title", ""),
        keywords=item.get("keywords", ""),
        description=item.get("concept", ""),
        auto_expand_keywords=expand_keywords,
    )


def verify(path: str, item: Dict[str, Any]) -> Dict[str, Any]:
    """Baca ulang byte file untuk memastikan metadata benar-benar tertanam."""
    if not os.path.exists(path):
        return {"ok": False, "reason": "file tidak ada", "keywords_found": 0}

    with open(path, "rb") as f:
        head = f.read(200_000)

    has_xmp = b"http://ns.adobe.com/xap/1.0/" in head
    has_iptc = b"Photoshop 3.0" in head
    title = (item.get("title") or "").strip()
    title_ok = bool(title) and title[:40].encode("utf-8", errors="ignore") in head

    keywords: List[str] = [k.strip() for k in (item.get("keywords") or "").split(",") if k.strip()]
    found = sum(1 for k in keywords if k.encode("utf-8", errors="ignore") in head)

    ok = has_xmp and has_iptc and title_ok and found >= max(1, len(keywords) // 2)
    return {
        "ok": ok,
        "xmp": has_xmp,
        "iptc": has_iptc,
        "title": title_ok,
        "keywords_found": found,
        "keywords_total": len(keywords),
    }
