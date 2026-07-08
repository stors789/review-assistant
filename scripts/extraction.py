"""
Shared PDF text preparation and extraction prompt building.
Provides functions used by both pipeline.py and verification.py
without introducing circular dependencies.
"""

from pathlib import Path
from utils import extract_pdf_text, file_sha256
from errors import PDFExtractionError
from evidence_pack import build_evidence_pack


def prepare_pdf_text(pdf_path: Path, text_cache_dir: Path) -> str:
    """
    Extract text from a PDF, caching it on disk.

    Returns the plain text. Raises PDFExtractionError if extraction fails.
    """
    pdf_hash = file_sha256(pdf_path)
    cache_key = pdf_hash[:16]

    text_cache_path = text_cache_dir / f"{cache_key}.txt"
    if text_cache_path.exists():
        return text_cache_path.read_text(encoding="utf-8")

    text = extract_pdf_text(pdf_path)
    text_cache_dir.mkdir(parents=True, exist_ok=True)
    text_cache_path.write_text(text, encoding="utf-8")
    return text


def build_extraction_prompt(
    text: str,
    question: str,
    use_evidence_pack: bool = True,
    ai_rerank_chunks: bool = False,
    use_vector_search: bool = False,
    client=None,
    model: str = "",
    pdf_hash: str = "",
    cache_dir: Path | None = None,
    max_chars: int = 80000,
) -> tuple[str, dict | None]:
    """
    Build the input prompt text for Step 1 extraction.

    When use_evidence_pack is True, delegates to build_evidence_pack.
    Otherwise returns the first max_chars characters of raw text.

    Returns (prompt_text, coverage) where coverage is evidence pack
    metadata or None.
    """
    if use_evidence_pack:
        return build_evidence_pack(
            text,
            question,
            max_chars=max_chars,
            ai_rerank=ai_rerank_chunks,
            rerank_client=client,
            rerank_model=model,
            use_vector_search=use_vector_search,
            pdf_hash=pdf_hash,
            cache_dir=cache_dir,
        )

    prompt_text = text[:max_chars] if len(text) > max_chars else text
    return prompt_text, None
