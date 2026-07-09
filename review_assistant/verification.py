# verification.py
"""
Verification modules: Ver 1 (findings), Ver A (citations), Ver B0 (claim-map), Ver B (logic).
Also includes programmatic citation checks.
"""

import json
import re
import threading
from pathlib import Path
from .utils import file_sha256, chunk_text
from .errors import LLMCallError, PDFExtractionError
from . import llm_client
from .prompts import (
    STEP1_SYSTEM,
    VERIFY_CITATION_PROMPT,
    VERIFY_LOGIC_PROMPT,
    CLAIM_MAP_EXTRACT_PROMPT,
    CLAIM_MAP_CHECK_PROMPT,
    STEP6_FIX_PROMPT
)

from .extraction import prepare_pdf_text, build_extraction_prompt
from .evidence_pack import normalize_finding_relevance
from .caching import findings_cache_key, FINDINGS_CACHE_VERSION


def verify_findings(all_results: list[dict], papers: list[dict],
                    client_factory, extraction_model: str,
                    text_cache_dir: Path, findings_dir: Path,
                    question: str = "", print_lock: threading.Lock = None,
                    use_evidence_pack: bool = True,
                    ai_rerank_chunks: bool = False,
                    use_vector_search: bool = False) -> list[dict]:
    """Ver 1: Directly search original text with finding quote. Re-extract on failure (max 2 rounds)."""
    print(f"\n── Ver 1: 验证发现（字符串检索）──", flush=True)
    if print_lock is None:
        print_lock = threading.Lock()

    file_to_pdf = {}
    for p in papers:
        path_str = p.get("pdf_path", "")
        if path_str:
            file_to_pdf[path_str] = Path(path_str)

    total_findings = 0
    total_failed = 0

    retry_files = set()
    for paper in all_results:
        if not paper.get("relevant") or not paper.get("findings"):
            continue
        result_pdf_path = paper.get("pdf_path", "")
        pdf_path = file_to_pdf.get(result_pdf_path)
        if not pdf_path:
            continue

        cache_key = file_sha256(pdf_path)[:16]
        text_path = text_cache_dir / f"{cache_key}.txt"
        if not text_path.exists():
            continue
        full_text = text_path.read_text(encoding="utf-8")

        has_failure = False
        for f in paper["findings"]:
            total_findings += 1
            quote = f.get("quote", "")
            if not quote:
                continue

            def _normalize(s: str) -> str:
                # Fix ligatures, convert to lowercase, and strip non-alphanumeric chars
                s = s.replace('ﬁ', 'fi').replace('ﬂ', 'fl').replace('ﬀ', 'ff').replace('ﬃ', 'ffi').replace('ﬄ', 'ffl').lower()
                return re.sub(r'\W+', '', s)

            norm_text = _normalize(full_text)
            norm_q60 = _normalize(quote[:60])
            norm_q120 = _normalize(quote[:120])
            norm_qfull = _normalize(quote)

            if norm_q60 and norm_q60 in norm_text:
                continue
            if norm_q120 and norm_q120 in norm_text:
                continue
            if norm_qfull and norm_qfull in norm_text:
                continue

            has_failure = True
            total_failed += 1
            print(f"  ❌ {pdf_path.name[:40]}... → quote未在原文找到", flush=True)

        if has_failure:
            retry_files.add(str(pdf_path))

    pass_rate = (total_findings - total_failed) / total_findings * 100 if total_findings else 100
    print(f"  {total_findings - total_failed}/{total_findings} 通过 ({pass_rate:.0f}%)", flush=True)

    if not retry_files:
        print(f"  ✅ 全部通过", flush=True)
        return all_results

    total = len(papers)
    print(f"  🔄 {len(retry_files)} 篇失败，开始重提取（最多2轮）", flush=True)
    for attempt in range(2):
        still_bad = set()
        for i, paper in enumerate(all_results):
            result_pdf_path = paper.get("pdf_path", "")
            if result_pdf_path not in retry_files:
                continue
            pdf_path = file_to_pdf.get(result_pdf_path)
            if not pdf_path:
                continue
            meta = {"title": paper.get("ref_title", ""), "authors": paper.get("ref_authors", ""),
                    "year": paper.get("ref_year", ""), "ref_num": paper.get("ref_num", 0)}
            client = client_factory()

            # Re-extraction using shared extraction functions + direct LLM call
            try:
                text = prepare_pdf_text(pdf_path, text_cache_dir)
            except PDFExtractionError as e:
                with print_lock:
                    print(f"  [{i+1}/{total}] {pdf_path.name[:40]}... -> ⚠ PDF提取失败: {e}", flush=True)
                still_bad.add(result_pdf_path)
                continue

            pdf_hash = file_sha256(pdf_path)
            prompt_text, coverage = build_extraction_prompt(
                text, question,
                use_evidence_pack=use_evidence_pack,
                ai_rerank_chunks=ai_rerank_chunks,
                use_vector_search=use_vector_search,
                client=client, model=extraction_model,
                pdf_hash=pdf_hash, cache_dir=text_cache_dir,
            )

            user_prompt = (
                f"论文信息: {meta.get('authors', '')} ({meta.get('year', '')}). {meta.get('title', '')}\n\n"
                f"论文文本输入：\n{prompt_text}\n\n"
                f"请根据上述论文文本，提取与以下研究问题相关的发现：\n研究问题：{question}"
            )
            try:
                raw_result = llm_client.call_json(client, STEP1_SYSTEM, user_prompt, extraction_model, 16384)
            except LLMCallError as e:
                with print_lock:
                    print(f"  [{i+1}/{total}] {pdf_path.name[:40]}... -> ⚠ API失败: {e}", flush=True)
                still_bad.add(result_pdf_path)
                continue

            raw_result = normalize_finding_relevance(raw_result)
            relevant = raw_result.get("relevant", False)
            findings = raw_result.get("findings", [])

            fcache_key = findings_cache_key(pdf_path, question, extraction_model, use_evidence_pack, ai_rerank_chunks, use_vector_search)
            new = {
                "file": pdf_path.name, "pdf_path": str(pdf_path),
                "relevant": relevant, "findings": findings,
                "ref_title": meta.get("title", ""), "ref_authors": meta.get("authors", ""),
                "ref_year": meta.get("year", ""), "ref_num": meta.get("ref_num", 0),
                "cache": {
                    "version": FINDINGS_CACHE_VERSION,
                    "key": fcache_key,
                    "pdf_sha256": pdf_hash,
                    "question": question,
                    "model": extraction_model,
                    "input_mode": "evidence_pack" if use_evidence_pack else "full_prefix",
                    "ai_rerank_chunks": bool(ai_rerank_chunks and use_evidence_pack),
                    "use_vector_search": bool(use_vector_search and use_evidence_pack),
                },
            }
            if coverage:
                new["evidence_pack"] = coverage

            if findings_dir:
                findings_dir.mkdir(parents=True, exist_ok=True)
                (findings_dir / f"{fcache_key}.json").write_text(
                    json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")

            if new.get("relevant") and new.get("findings"):
                all_results[i] = new
                print(f"  ✅ 重提取成功: {pdf_path.name[:40]}...", flush=True)
            else:
                still_bad.add(result_pdf_path)
                print(f"  ⚠ 重提取仍失败: {pdf_path.name[:40]}...", flush=True)
        if not still_bad:
            break
        retry_files = still_bad

    print(f"  ✅ 验证完毕", flush=True)
    return all_results


def _split_report_refs(report: str) -> tuple[str, str]:
    """Helper to split report body and reference list."""
    ref_match = re.search(r"\n##\s*参考文献\b", report)
    if not ref_match:
        return report, ""
    return report[:ref_match.start()], report[ref_match.start():]


def verify_references_programmatic(report: str, paper_refs: dict) -> str:
    """Deterministically check numeric citations and generated reference list."""
    body, refs = _split_report_refs(report)
    body_cites = [int(m) for m in re.findall(r"\[(\d+)\]", body)]
    ref_nums = [int(m) for m in re.findall(r"^\[(\d+)\]\s+", refs, flags=re.MULTILINE)]
    body_set = set(body_cites)
    ref_set = set(ref_nums)
    known_set = set(paper_refs.keys())

    issues = []
    if body_set and not refs:
        issues.append(("error", "正文存在数字引用，本报告缺少参考文献列表。"))

    missing_refs = sorted(body_set - ref_set)
    if missing_refs:
        issues.append(("error", f"正文引用缺少参考文献条目: {missing_refs}"))

    orphan_refs = sorted(ref_set - body_set)
    if orphan_refs:
        issues.append(("warning", f"参考文献列表存在正文未引用条目: {orphan_refs}"))

    unknown_body = sorted(body_set - known_set)
    if unknown_body:
        issues.append(("error", f"正文引用编号不在 paper_refs 中: {unknown_body}"))

    unknown_refs = sorted(ref_set - known_set)
    if unknown_refs:
        issues.append(("error", f"参考文献编号不在 paper_refs 中: {unknown_refs}"))

    duplicate_refs = sorted(n for n in ref_set if ref_nums.count(n) > 1)
    if duplicate_refs:
        issues.append(("error", f"参考文献列表重复编号: {duplicate_refs}"))

    for num in sorted(ref_set & known_set):
        info = paper_refs[num]
        pattern = re.compile(rf"^\[{num}\]\s+(.+)$", flags=re.MULTILINE)
        m = pattern.search(refs)
        if not m:
            continue
        line = m.group(1)
        year = str(info.get("year", "")).strip()
        title = str(info.get("title", "")).strip()
        if year and year not in line:
            issues.append(("warning", f"参考文献[{num}]缺少年份 {year}。"))
        if title and title[:30] not in line:
            issues.append(("warning", f"参考文献[{num}]标题可能与 Zotero 元数据不一致。"))

    if not issues:
        return ""

    md = "## 程序化引用检查\n\n"
    for severity, issue in issues:
        icon = "❌" if severity == "error" else "⚠️"
        md += f"- {icon} {issue}\n"
    md += "\n"
    print(f"  程序化引用检查: {len(issues)} 处问题", flush=True)
    return md


def verify_citations(report_text: str, all_results: list[dict], client, model: str) -> str:
    """Ver A: Verify citation correctness against original claims."""
    print(f"\n── Ver A: 引用正确性 ──", flush=True)

    idx_lines = []
    for paper in all_results:
        if not paper.get("relevant"):
            continue
        for f in paper.get("findings", []):
            idx_lines.append(
                f"[ref:{paper.get('ref_num',0)}] {f.get('cite_key','')} | {f.get('claim_cn','')}"
            )
    findings_index = "\n".join(idx_lines)

    issues = []
    refs_split = report_text.split("## 参考文献")
    references_text = "## 参考文献" + refs_split[-1] if len(refs_split) > 1 else ""

    for chunk_no, chunk in enumerate(chunk_text(report_text, 10000), 1):
        try:
            prompt_report = f"【报告片段 {chunk_no}】\n{chunk}"
            if references_text and "## 参考文献" not in chunk:
                prompt_report += f"\n\n【全局参考文献列表】\n{references_text}"
            
            result = llm_client.call_json(client, "", VERIFY_CITATION_PROMPT.format(
                report=prompt_report, findings_index=findings_index), model, 65536)
        except LLMCallError as e:
            return f"⚠ 引用验证失败 (attempts={e.attempts}): {e}"
        chunk_issues = result if isinstance(result, list) else [result]
        for iss in chunk_issues:
            if isinstance(iss, dict):
                iss.setdefault("chunk", chunk_no)
                issues.append(iss)
    if not issues:
        print(f"  ✅ 引用无问题", flush=True)
        return ""

    md = "## 引用正确性检查\n\n"
    for iss in issues:
        icon = "❌" if iss.get("severity") == "error" else "⚠️"
        md += f"- {icon} **[{iss.get('ref_num','?')}]** {iss.get('location','')[:40]}... → {iss.get('issue','')}\n"
    md += "\n"
    print(f"  {len(issues)} 处问题", flush=True)
    return md


def _coerce_json_list(result) -> list:
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        return [result]
    return []


def verify_claim_map(report_text: str, client, model: str) -> str:
    """Ver B0: Extract core claim map and verify logic consistency across claims."""
    print(f"\n── Ver B0: Claim-map 逻辑检查 ──", flush=True)

    try:
        extracted = llm_client.call_json(
            client,
            "",
            CLAIM_MAP_EXTRACT_PROMPT.format(report=report_text[:30000]),
            model,
            65536,
        )
    except LLMCallError as e:
        print(f"  ⚠ claim-map 抽取失败 (attempts={e.attempts})，跳过: {e}", flush=True)
        return f"## Claim-map 逻辑检查\n\n- ⚠️ claim-map 抽取失败，已降级使用常规逻辑验证: {e}\n\n"

    claims = _coerce_json_list(extracted)
    if not claims:
        print(f"  ✅ 未抽取到可检查的核心论断", flush=True)
        return ""

    normalized_claims = []
    for i, claim in enumerate(claims):
        refs = claim.get("evidence_refs", [])
        if not isinstance(refs, list):
            refs = []
        clean_refs = []
        for ref in refs:
            try:
                clean_refs.append(int(ref))
            except (TypeError, ValueError):
                continue
        normalized_claims.append({
            "index": i,
            "claim": str(claim.get("claim", ""))[:1000],
            "scope": str(claim.get("scope", ""))[:500],
            "evidence_refs": clean_refs,
            "certainty": str(claim.get("certainty", "unclear")),
            "location": str(claim.get("location", ""))[:300],
        })

    try:
        checked = llm_client.call_json(
            client,
            "",
            CLAIM_MAP_CHECK_PROMPT.format(
                claim_map=json.dumps(normalized_claims, ensure_ascii=False, indent=2)
            ),
            model,
            65536,
        )
    except LLMCallError as e:
        print(f"  ⚠ claim-map 检查失败 (attempts={e.attempts})，跳过: {e}", flush=True)
        return f"## Claim-map 逻辑检查\n\n- ⚠️ claim-map 检查失败，已降级使用常规逻辑验证: {e}\n\n"

    issues = _coerce_json_list(checked)
    if not issues:
        print(f"  ✅ claim-map 无问题 ({len(normalized_claims)} 条论断)", flush=True)
        return ""

    md = "## Claim-map 逻辑检查\n\n"
    for iss in issues:
        icon = "❌" if iss.get("severity") == "error" else "⚠️"
        rel = iss.get("relationship", "issue")
        claim_ids = iss.get("claim_indices", [])
        md += (
            f"- {icon} **{rel}** claims={claim_ids} "
            f"{str(iss.get('location', ''))[:40]}... → {iss.get('issue', '')}\n"
        )
    md += "\n"
    print(f"  {len(issues)} 处 claim-map 问题", flush=True)
    return md


def verify_logic(report_text: str, client, model: str) -> str:
    """Ver B: Verify global logical consistency of report chunks."""
    print(f"\n── Ver B: 逻辑一致性 ──", flush=True)

    issues = []
    for chunk_no, chunk in enumerate(chunk_text(report_text, 10000), 1):
        try:
            result = llm_client.call_json(client, "", VERIFY_LOGIC_PROMPT.format(
                report=f"【报告片段 {chunk_no}】\n{chunk}"), model, 65536)
        except LLMCallError as e:
            return f"⚠ 逻辑验证失败 (attempts={e.attempts}): {e}"
        chunk_issues = result if isinstance(result, list) else [result]
        for iss in chunk_issues:
            if isinstance(iss, dict):
                iss.setdefault("chunk", chunk_no)
                issues.append(iss)
    if not issues:
        print(f"  ✅ 逻辑无问题", flush=True)
        return ""

    md = "## 逻辑一致性检查\n\n"
    for iss in issues:
        icon = "❌" if iss.get("severity") == "error" else "⚠️"
        md += f"- {icon} **{iss.get('section','')}** {iss.get('location','')[:40]}... → {iss.get('issue','')}\n"
    md += "\n"
    print(f"  {len(issues)} 处问题", flush=True)
    return md


def step6_fix_report(client, report: str, verification_feedback: str,
                     all_results: list[dict], model: str, pass_num: int = 1, total_passes: int = 1) -> str:
    """Step 6: Revise report based on verification issues."""
    print(f"\n── Step 6: 修正报告 (第 {pass_num}/{total_passes} 轮) ──", flush=True)

    idx_lines = []
    for paper in all_results:
        if not paper.get("relevant"):
            continue
        for f in paper.get("findings", []):
            idx_lines.append(
                f"[ref:{paper.get('ref_num',0)}] {f.get('cite_key','')} | claim: {f.get('claim_cn','')} | quote: {f.get('quote','')[:120]}"
            )
    findings_index = "\n".join(idx_lines)

    max_report = 30000
    report_input = report[:max_report] if len(report) > max_report else report

    try:
        fixed = llm_client.call_text(client,
                                     STEP6_FIX_PROMPT.format(
                                         report=report_input,
                                         verification_feedback=verification_feedback,
                                         findings_index=findings_index),
                                     model, 65536)
    except LLMCallError as e:
        print(f"  ⚠ 修正失败 (attempts={e.attempts}): {e}，保留原报告", flush=True)
        return report

    print(f"  ✅ 修正完成\n", flush=True)
    return fixed
