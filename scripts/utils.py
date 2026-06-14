# utils.py
"""
Common utility functions for PDF extraction, hashing, and text chunking.
"""

import hashlib
import json
import pymupdf
from pathlib import Path


def extract_pdf_text(pdf_path: Path) -> str:
    """Extract raw text from a PDF file."""
    doc = pymupdf.open(str(pdf_path))
    text = "\n\n".join(page.get_text() for page in doc if page.get_text().strip())
    doc.close()
    return text


# Alias for compatibility with paper_breakdown and claim_verify
extract_text = extract_pdf_text


def file_sha256(path: Path) -> str:
    """Calculate the SHA256 checksum of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def chunk_text(text: str, max_chars: int = 10000) -> list[str]:
    """Split text on paragraph boundaries so verification covers long reports."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    current = []
    current_len = 0
    for para in text.split("\n\n"):
        part_len = len(para) + 2
        if current and current_len + part_len > max_chars:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = part_len
        else:
            current.append(para)
            current_len += part_len
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def should_stop_after(current_step: str, stop_after: str | None) -> bool:
    """Check if the pipeline execution should stop after the current step."""
    return bool(stop_after and current_step == stop_after)


def print_stop_after(current_step: str, output_dir: Path):
    """Print the debug pipeline stop message."""
    print(f"\n⏹ --stop-after {current_step}: 已停止。输出目录: {output_dir}", flush=True)

