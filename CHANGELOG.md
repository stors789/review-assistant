# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0] - 2026-06-15

### Added
- **Python Version Check**: Added an explicit runtime check `sys.version_info >= (3, 10)` at the start of all 5 entry scripts (`zotero_read.py`, `claim_verify.py`, `paper_breakdown.py`, `explore_synthesize.py`, `auto_lit.py`) to prevent obscure syntax errors on unsupported older Python versions.
- **Cross-Platform Linked-File Attachment Resolution**: Integrated automatic conversion of Windows backslash `\` directory separators to forward slashes `/` in `zotero_reader.py`'s `_resolve_pdf_path` method.
- **Comprehensive Cross-Platform Path Resolution Testing**: Added a thorough integration and unit test suite `test_resolve_pdf_path_cross_platform` in `tests/test_zotero_reader.py` covering stored files, absolute paths, forward slash and Windows backslash linked files (coupled with `ZOTERO_LINKED_BASE_DIR`), and simulated Windows absolute paths.
- **Explicit Zotero Import Trigger**: Added the `--import-zotero` command-line argument to `auto_lit.py` to make RIS auto-importing on macOS strictly optional, defaulting to non-import to maximize compatibility in CI, server, and headless settings.
- **Flexible Lock File Configuration**: Added `AUTO_LIT_LOCK_DIR` environment variable support for `auto_lit.py` to specify where the Semantic Scholar rate limit lock file should be created. The system now also tests home directory writability before saving the lock file and falls back gracefully to the system temporary folder if the home directory is read-only.
- **Optional Environment Configurations**: Documented `ZOTERO_DIR`, `ZOTERO_LINKED_BASE_DIR`, `AUTO_LIT_LOCK_DIR`, and `PUBMED_API_KEY` / `NCBI_API_KEY` inside `README.md` and `SKILL.md`.

### Changed
- **Unified CLI Entrypoints**: Standardized documentation (README and SKILL) to use `python` instead of `python3`, and introduced support for local installation (`python -m pip install -e .`) followed by directly running console commands (`review-assistant-read`, `review-assistant-breakdown`, etc.) from any directory.
- **llm_client Integration**: Refactored `paper_breakdown.py` to initialize `llm_client` and call `llm_client.call_json`. This provides automatic parameter negotiation (removing parameters like `thinking` and `reasoning_effort` if unsupported by the model provider) and robust JSON regex fallbacks.
- **Expanded Proxy Stripping**: Added HTTP and HTTPS proxy variables (`http_proxy`, `HTTP_PROXY`, `https_proxy`, `HTTPS_PROXY`) to the environment variable cleanup list in `llm_client.py`'s client initialization pool to ensure direct and reliable API communication.
- **Test Suite Alignment**: Refactored `tests/test_paper_breakdown.py` to mock `llm_client.get_client` instead of `OpenAI` client directly.

### Fixed
- Fixed an attribute error in paper breakdown unit tests caused by missing `OpenAI` import inside `paper_breakdown.py`.
- Fixed a bug where Windows-style linked-file paths would fail to resolve on Unix-based OS because directory separators were not translated.
