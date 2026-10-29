import glob
import os
import re
from typing import List, Dict, Any, Union, Optional, Tuple
from PIL import Image

# ponytail: 64-bit dHash calculates perceptual similarity in <3ms; upgrade to ResNet embeddings if semantic pose matching is required.

COMMON_STOPWORDS = {
    "with", "from", "into", "over", "under", "about", "above", "after", "again",
    "against", "during", "through", "before", "between", "these", "those", "their",
    "there", "where", "which", "while", "photo", "image", "photograph", "picture",
    "for", "and", "the", "that", "this", "have", "has", "been", "were", "are",
    "was", "is", "its", "your", "you", "can", "will", "some", "all", "any", "not",
    "only", "also", "very", "much", "more", "most", "such", "than", "then"
}

STOCK_BOOST_TAGS = [
    "commercial photo", "copy space", "high resolution", "photorealistic",
    "modern", "professional", "authentic", "contemporary", "clean background",
    "natural light", "creative composition", "lifestyle", "selective focus",
    "aesthetic", "horizontal", "nobody", "indoor", "bright", "vibrant",
    "concept", "detail", "close up", "business", "workplace"
]

def expand_keywords(
    existing_keywords: Union[str, List[str]],
    title: str = "",
    concept: str = "",
    target_count: int = 35,
    max_count: int = 45,
) -> List[str]:
    """Expands sparse keywords up to target_count (30-45) using title context and stock buyer tags."""
    if isinstance(existing_keywords, str):
        raw_list = [k.strip().lower() for k in re.split(r"[,;\n]+", existing_keywords) if k.strip()]
    else:
        raw_list = [str(k).strip().lower() for k in (existing_keywords or []) if str(k).strip()]

    seen = set()
    expanded: List[str] = []

    for k in raw_list:
        clean_k = k.strip().lower()
        if clean_k and clean_k not in seen:
            seen.add(clean_k)
            expanded.append(clean_k)

    # If already has plenty of keywords, truncate at max_count and return
    if len(expanded) >= target_count:
        return expanded[:max_count]

    # 1. Extract contextual terms from Title and Concept
    combined_text = f"{title} {concept}".lower()
    words = re.findall(r"[a-z0-9]{3,}", combined_text)
    for w in words:
        if w not in seen and w not in COMMON_STOPWORDS:
            seen.add(w)
            expanded.append(w)
            if len(expanded) >= target_count:
                break

    # 2. Extract 2-word phrases from title
    phrases = re.findall(r"([a-z]{3,}\s+[a-z]{3,})", title.lower())
    for phrase in phrases:
        p_clean = phrase.strip()
        if p_clean not in seen and len(expanded) < target_count:
            seen.add(p_clean)
            expanded.append(p_clean)

    # 3. Fill remaining slots from high-converting stock search tags
    for tag in STOCK_BOOST_TAGS:
        if tag not in seen and len(expanded) < target_count:
            seen.add(tag)
            expanded.append(tag)

    return expanded[:max_count]

def compute_dhash(image_path: str, hash_size: int = 8) -> Optional[int]:
    """Computes a 64-bit difference hash (dHash) for an image in <3ms using pure PIL."""
    if not os.path.exists(image_path):
        return None
    try:
        with Image.open(image_path) as img:
            gray = img.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.BILINEAR)
            raw = gray.tobytes()

        row_w = hash_size + 1
        hash_val = 0
        for r in range(hash_size):
            r_idx = r * row_w
            for c in range(hash_size):
                bit = 1 if raw[r_idx + c] > raw[r_idx + c + 1] else 0
                hash_val = (hash_val << 1) | bit
        return hash_val
    except Exception:
        return None

def compute_similarity(hash1: Optional[int], hash2: Optional[int], hash_size: int = 8) -> float:
    """Calculates percentage visual similarity from two dHashes using Hamming distance."""
    if hash1 is None or hash2 is None:
        return 0.0
    total_bits = hash_size * hash_size
    diff_bits = bin(hash1 ^ hash2).count("1")
    similarity = (1.0 - (diff_bits / total_bits)) * 100.0
    return round(similarity, 1)

def scan_output_duplicates(
    output_dir: str,
    threshold: float = 85.0,
    max_images: int = 500,
) -> Dict[str, Any]:
    """Scans all output images and flags visual duplicates above similarity threshold."""
    if not os.path.exists(output_dir):
        return {"duplicates": [], "total_scanned": 0, "flagged_count": 0}

    pattern = os.path.join(output_dir, "*.*")
    files = [
        f for f in sorted(glob.glob(pattern))
        if os.path.splitext(f)[1].lower() in (".jpg", ".jpeg", ".png", ".webp")
    ][:max_images]

    hashes: List[Tuple[str, int]] = []
    for f in files:
        h = compute_dhash(f)
        if h is not None:
            hashes.append((os.path.basename(f), h))

    flagged_pairs: List[Dict[str, Any]] = []
    flagged_files: Dict[str, List[Dict[str, Any]]] = {}

    n = len(hashes)
    for i in range(n):
        fn1, h1 = hashes[i]
        for j in range(i + 1, n):
            fn2, h2 = hashes[j]
            sim = compute_similarity(h1, h2)
            if sim >= threshold:
                pair = {
                    "file_a": fn1,
                    "file_b": fn2,
                    "similarity": sim,
                }
                flagged_pairs.append(pair)
                if fn1 not in flagged_files:
                    flagged_files[fn1] = []
                flagged_files[fn1].append({"similar_to": fn2, "similarity": sim})

                if fn2 not in flagged_files:
                    flagged_files[fn2] = []
                flagged_files[fn2].append({"similar_to": fn1, "similarity": sim})

    return {
        "duplicates": flagged_pairs,
        "flagged_files": flagged_files,
        "total_scanned": n,
        "flagged_count": len(flagged_files),
        "threshold": threshold,
    }

def check_and_purge_duplicate(
    new_file_path: str,
    output_dir: str,
    threshold: float = 98.0,
) -> Dict[str, Any]:
    """Checks if a newly downloaded image is identical/duplicate (>=threshold) to any existing image in output_dir.
    If a duplicate is found, it automatically deletes new_file_path to save hard disk storage."""
    if not os.path.exists(new_file_path) or not os.path.exists(output_dir):
        return {"is_duplicate": False}

    new_hash = compute_dhash(new_file_path)
    if new_hash is None:
        return {"is_duplicate": False}

    new_basename = os.path.basename(new_file_path)
    new_size = os.path.getsize(new_file_path)

    pattern = os.path.join(output_dir, "*.*")
    for f in sorted(glob.glob(pattern)):
        bn = os.path.basename(f)
        if bn == new_basename or os.path.splitext(f)[1].lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            continue

        h = compute_dhash(f)
        if h is None:
            continue

        sim = compute_similarity(new_hash, h)
        if sim >= threshold:
            # Duplicate found! Delete the new duplicate file to save disk space
            try:
                os.remove(new_file_path)
            except Exception as e:
                print(f"[ERROR] Gagal menghapus file duplikat {new_file_path}: {e}")

            return {
                "is_duplicate": True,
                "deleted_file": new_basename,
                "matched_file": bn,
                "similarity": sim,
                "freed_bytes": new_size,
                "freed_kb": round(new_size / 1024, 1),
                "freed_mb": round(new_size / (1024 * 1024), 2),
            }

    return {"is_duplicate": False}

def purge_all_duplicates(
    output_dir: str,
    threshold: float = 98.0,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Scans all output images, identifies duplicate groups (>=threshold), preserves the best/original file,
    and deletes the duplicates to free up hard drive storage."""
    if not os.path.exists(output_dir):
        return {"success": False, "deleted_count": 0, "freed_bytes": 0, "error": "Folder output tidak ditemukan"}

    pattern = os.path.join(output_dir, "*.*")
    files = sorted([
        f for f in glob.glob(pattern)
        if os.path.splitext(f)[1].lower() in (".jpg", ".jpeg", ".png", ".webp")
    ])

    file_entries = []
    for f in files:
        h = compute_dhash(f)
        if h is not None:
            file_entries.append({
                "path": f,
                "filename": os.path.basename(f),
                "hash": h,
                "size": os.path.getsize(f),
                "mtime": os.path.getmtime(f),
            })

    to_delete = []
    kept = []

    for item in file_entries:
        is_dup = False
        for k in kept:
            sim = compute_similarity(item["hash"], k["hash"])
            if sim >= threshold:
                is_dup = True
                # Keep the higher resolution / larger file if one is thumbnail (<500KB) vs full res (>1MB)
                if item["size"] > 1000000 and k["size"] < 500000:
                    # k was a low-res thumbnail, delete k and keep item
                    to_delete.append({
                        "path": k["path"],
                        "filename": k["filename"],
                        "original": item["filename"],
                        "similarity": sim,
                        "size": k["size"],
                    })
                    kept.remove(k)
                    kept.append(item)
                else:
                    # item is duplicate, delete item and keep k
                    to_delete.append({
                        "path": item["path"],
                        "filename": item["filename"],
                        "original": k["filename"],
                        "similarity": sim,
                        "size": item["size"],
                    })
                break
        if not is_dup:
            kept.append(item)

    total_freed_bytes = sum(d["size"] for d in to_delete)
    actually_deleted = []

    if not dry_run:
        for d in to_delete:
            try:
                if os.path.exists(d["path"]):
                    os.remove(d["path"])
                    actually_deleted.append(d)
            except Exception as e:
                print(f"[WARN] Gagal menghapus file {d['filename']}: {e}")
    else:
        actually_deleted = to_delete

    return {
        "success": True,
        "scanned_count": len(files),
        "kept_count": len(kept),
        "deleted_count": len(actually_deleted),
        "freed_bytes": total_freed_bytes,
        "freed_kb": round(total_freed_bytes / 1024, 1),
        "freed_mb": round(total_freed_bytes / (1024 * 1024), 2),
        "deleted_files": actually_deleted,
        "dry_run": dry_run,
        "threshold": threshold,
    }

