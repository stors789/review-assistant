"""Custom exception hierarchy for the review-assistant pipeline."""


class ReviewAssistantError(Exception):
    """Base exception for all review-assistant errors."""


class PDFExtractionError(ReviewAssistantError):
    """Raised when PDF text extraction fails (e.g. encrypted, scanned, or corrupt file)."""


class LLMCallError(ReviewAssistantError):
    """Raised when an LLM API call fails after exhausting all retries."""

    def __init__(self, message: str = "", original: Exception | None = None, attempts: int = 0):
        super().__init__(message)
        self.original = original
        self.attempts = attempts


class CacheError(ReviewAssistantError):
    """Raised when a cache read/write operation fails (warning-level, non-fatal)."""
