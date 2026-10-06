"""Receipt images on disk. The DB stores only the path relative to RECEIPTS_DIR.

Default RECEIPTS_DIR is <repo>/data/receipts, the same folder docker-compose mounts into the
expense-api container, so local runs and the container share the files.
"""
import os
from datetime import datetime
from pathlib import Path

EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/heic": "heic", "image/heif": "heif"}


def root() -> Path:
    default = Path(__file__).resolve().parents[2] / "data" / "receipts"
    return Path(os.environ.get("RECEIPTS_DIR") or default).resolve()


def save(data: bytes, sha256: str, mime_type: str, when: datetime) -> str:
    """Write the image as YYYY/MM/<sha256>.<ext> (atomic rename) and return that relative path."""
    rel = f"{when:%Y}/{when:%m}/{sha256}.{EXT[mime_type]}"
    path = root() / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
    return rel


def load(rel: str) -> bytes:
    """Read an image by its stored path; refuses anything that resolves outside RECEIPTS_DIR."""
    base = root()
    path = (base / rel).resolve()
    if base not in path.parents:
        raise FileNotFoundError(rel)
    return path.read_bytes()
