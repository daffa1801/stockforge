import json
import os
import time
from typing import List, Dict, Any

# Magic bytes signatures
JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
WEBP_MAGIC = b"RIFF"

def is_file_valid(file_path: str, min_size: int = 10240) -> bool:
    """Checks if a file exists, meets minimum size, and has valid image header."""
    if not os.path.exists(file_path):
        return False
        
    try:
        size = os.path.getsize(file_path)
        if size < min_size:
            return False
            
        with open(file_path, "rb") as f:
            header = f.read(16)
            
        # Check against common image magic bytes
        if header.startswith(JPEG_MAGIC):
            return True
        if header.startswith(PNG_MAGIC):
            return True
        if header.startswith(WEBP_MAGIC) and b"WEBP" in header:
            return True
            
        # Reject if header does not match known image types
        return False
    except Exception:
        return False

def get_output_path(output_dir: str, filename: str) -> str:
    """Returns absolute normalized destination path for a given filename."""
    clean_filename = os.path.basename(filename.strip())
    # Ensure has extension
    if not os.path.splitext(clean_filename)[1]:
        clean_filename += ".jpg"
    return os.path.normpath(os.path.join(output_dir, clean_filename))

def get_pending_items(items: List[Dict[str, Any]], output_dir: str, min_size: int = 10240) -> List[Dict[str, Any]]:
    """Filters out items whose output file is already validly downloaded."""
    os.makedirs(output_dir, exist_ok=True)
    pending = []
    
    for item in items:
        target_path = get_output_path(output_dir, item["filename"])
        if is_file_valid(target_path, min_size=min_size):
            continue
        pending.append(item)
        
    return pending

def save_progress_state(state_file: str, item: Dict[str, Any]) -> None:
    """Appends item execution record to a JSON tracking log."""
    os.makedirs(os.path.dirname(os.path.abspath(state_file)), exist_ok=True)
    
    records = []
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                records = json.load(f)
                if not isinstance(records, list):
                    records = []
        except Exception:
            records = []
            
    record_entry = {
        **item,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    records.append(record_entry)
    
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
