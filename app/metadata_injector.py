import html
import os
import re
from typing import List, Union
from PIL import Image

# ponytail: simple binary JPEG segment splicer; upgrade to full TIFF IFD writer if multi-frame / RAW support is ever needed.

def _clean_keywords(keywords: Union[str, List[str]]) -> List[str]:
    if isinstance(keywords, str):
        # Split by comma or semicolon
        raw_list = re.split(r"[,;\n]+", keywords)
    else:
        raw_list = keywords or []
    
    cleaned = []
    for k in raw_list:
        kw = str(k).strip()
        if kw and kw not in cleaned:
            cleaned.append(kw)
    return cleaned

def _build_xmp_segment(title: str, keywords: List[str], description: str = "") -> bytes:
    desc = description or title
    kw_items = "\n".join(f"     <rdf:li>{html.escape(k)}</rdf:li>" for k in keywords if k)
    title_esc = html.escape(title)
    desc_esc = html.escape(desc)

    xmp_text = f"""<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 7.0-c000 1.000000">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/">
   <dc:title>
    <rdf:Alt>
     <rdf:li xml:lang="x-default">{title_esc}</rdf:li>
    </rdf:Alt>
   </dc:title>
   <dc:description>
    <rdf:Alt>
     <rdf:li xml:lang="x-default">{desc_esc}</rdf:li>
    </rdf:Alt>
   </dc:description>
   <photoshop:Headline>{title_esc}</photoshop:Headline>
   <dc:subject>
    <rdf:Bag>
{kw_items}
    </rdf:Bag>
   </dc:subject>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""
    
    payload = b"http://ns.adobe.com/xap/1.0/\x00" + xmp_text.encode("utf-8")
    seg_len = len(payload) + 2
    return b"\xff\xe1" + seg_len.to_bytes(2, "big") + payload

def _build_iptc_segment(title: str, keywords: List[str], description: str = "") -> bytes:
    iptc_data = bytearray()

    def add_tag(record: int, dataset: int, val_bytes: bytes):
        iptc_data.append(0x1C)
        iptc_data.append(record)
        iptc_data.append(dataset)
        iptc_data.extend(len(val_bytes).to_bytes(2, "big"))
        iptc_data.extend(val_bytes)

    # 1:90 Coded Character Set = UTF-8 (\x1b%G)
    add_tag(1, 90, b"\x1b%G")

    # 2:05 Object Name (Title)
    if title:
        add_tag(2, 5, title.encode("utf-8")[:64])

    # 2:25 Keywords (repeatable, up to 64 bytes each)
    for kw in keywords:
        add_tag(2, 25, kw.encode("utf-8")[:64])

    # 2:120 Caption / Abstract (Description)
    desc = description or title
    if desc:
        add_tag(2, 120, desc.encode("utf-8")[:2000])

    res_len = len(iptc_data).to_bytes(4, "big")
    bim = b"8BIM\x04\x04\x00\x00" + res_len + bytes(iptc_data)
    if len(iptc_data) % 2 != 0:
        bim += b"\x00"  # 16-bit alignment padding

    payload = b"Photoshop 3.0\x00" + bim
    seg_len = len(payload) + 2
    return b"\xff\xed" + seg_len.to_bytes(2, "big") + payload

def _build_exif_segment(title: str, keywords: List[str]) -> bytes:
    exif = Image.Exif()
    if title:
        exif[270] = title
        exif[40091] = title.encode("utf-16le")
        exif[40095] = title.encode("utf-16le")
    if keywords:
        exif[40094] = "; ".join(keywords).encode("utf-16le")

    payload = exif.tobytes()
    seg_len = len(payload) + 2
    return b"\xff\xe1" + seg_len.to_bytes(2, "big") + payload

def inject_jpeg_metadata(
    jpeg_path: str,
    title: str = "",
    keywords: Union[str, List[str]] = "",
    description: str = "",
    auto_expand_keywords: bool = False,
) -> bool:
    """Losslessly injects standard XMP, IPTC, and EXIF metadata into a JPEG file."""
    if not os.path.exists(jpeg_path):
        return False

    clean_kw = _clean_keywords(keywords)
    clean_title = (title or "").strip()
    clean_desc = (description or "").strip()

    if auto_expand_keywords:
        try:
            from app.stock_optimizer import expand_keywords
        except ImportError:
            from stock_optimizer import expand_keywords
        clean_kw = expand_keywords(clean_kw, title=clean_title, concept=clean_desc, target_count=35)

    if not clean_title and not clean_kw:
        return True  # Nothing to inject

    try:
        with open(jpeg_path, "rb") as f:
            raw = f.read()

        if len(raw) < 4 or raw[:2] != b"\xff\xd8":
            return False

        # Parse existing segments and filter out old XMP, IPTC, or Exif metadata to avoid duplication
        pos = 2
        filtered_segments = []
        raw_len = len(raw)

        while pos < raw_len - 1:
            if raw[pos] != 0xFF:
                # Reached entropy image stream (SOS data)
                break

            marker = raw[pos + 1]
            if marker in (0xD8, 0xD9, 0x00) or (0xD0 <= marker <= 0xD7):
                pos += 2
                continue

            if pos + 4 > raw_len:
                break

            seg_len = int.from_bytes(raw[pos + 2 : pos + 4], "big")
            seg_full_len = 2 + seg_len
            seg_data = raw[pos : pos + seg_full_len]

            # Check if this segment is old metadata we should replace
            is_metadata_seg = False
            if marker == 0xE1:
                # APP1: Exif or XMP
                if seg_data[4:].startswith(b"Exif\x00\x00") or seg_data[4:].startswith(b"http://ns.adobe.com/xap/1.0/\x00"):
                    is_metadata_seg = True
            elif marker == 0xED:
                # APP13: IPTC / Photoshop 3.0
                if seg_data[4:].startswith(b"Photoshop 3.0\x00"):
                    is_metadata_seg = True

            if not is_metadata_seg:
                filtered_segments.append(seg_data)

            if marker == 0xDA:  # SOS (Start of Scan) - rest is compressed image data
                pos += seg_full_len
                break

            pos += seg_full_len

        remaining_image_data = raw[pos:]

        # Build new metadata blocks
        new_exif = _build_exif_segment(clean_title, clean_kw)
        new_xmp = _build_xmp_segment(clean_title, clean_kw, clean_desc)
        new_iptc = _build_iptc_segment(clean_title, clean_kw, clean_desc)
        metadata_chunk = new_exif + new_xmp + new_iptc

        # Reassemble: SOI + APP0 (if first) + new metadata + remaining header segments + image data
        reassembled = bytearray(b"\xff\xd8")
        if filtered_segments and filtered_segments[0].startswith(b"\xff\xe0"):
            reassembled.extend(filtered_segments[0])
            reassembled.extend(metadata_chunk)
            for seg in filtered_segments[1:]:
                reassembled.extend(seg)
        else:
            reassembled.extend(metadata_chunk)
            for seg in filtered_segments:
                reassembled.extend(seg)

        reassembled.extend(remaining_image_data)

        # In-place write: Avoids creating temporary (.meta_tmp) files that trigger
        # "temp missing / item not found" error notifications in Windows Explorer.
        with open(jpeg_path, "r+b") as f:
            f.seek(0)
            f.write(reassembled)
            f.truncate()

        return True
    except Exception as e:
        print(f"[METADATA ERROR] Gagal inject metadata {jpeg_path}: {e}")
        return False

def batch_inject_metadata(output_dir: str, items: List[dict], auto_expand_keywords: bool = False) -> dict:
    """Injects metadata into existing image files in output_dir using prompt items."""
    injected = 0
    skipped = 0
    failed = 0

    if not os.path.exists(output_dir):
        return {"injected": 0, "skipped": 0, "failed": 0, "total": 0}

    item_map = {str(item["filename"]): item for item in items if item.get("filename")}

    for fn, item in item_map.items():
        file_path = os.path.join(output_dir, fn)
        if not os.path.exists(file_path):
            continue

        title = item.get("title", "")
        keywords = item.get("keywords", "")
        if not title and not keywords:
            skipped += 1
            continue

        if inject_jpeg_metadata(file_path, title=title, keywords=keywords, auto_expand_keywords=auto_expand_keywords):
            injected += 1
        else:
            failed += 1

    return {"injected": injected, "skipped": skipped, "failed": failed, "total": len(item_map)}
