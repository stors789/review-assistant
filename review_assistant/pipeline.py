# pipeline.py
"""
Flagship 7-step pipeline stages for literature review synthesis.
"""

import json
import re
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import DEFAULT_FLASH_MODEL
from .utils import file_sha256
from .extraction import prepare_pdf_text, build_extraction_prompt
from .errors import PDFExtractionError, LLMCallError
from .prompts import (
    STEP1_SYSTEM,
    STEP2_PROMPT,
    STEP3_MATCH_PROMPT,
    STEP3_WRITE_PROMPT,
    STEP4_PROMPT,
    STEP5_PROMPT,
    STEP7_TABLE_VIEW_PROMPT,
    STEP7_TABLE_PROMPT,
    STEP7_DIAGRAM_PROMPT,
)
from . import llm_client
from .caching import findings_cache_key, FINDINGS_CACHE_VERSION
from .evidence_pack import (
    normalize_finding_relevance,
    format_finding_metadata,
)


def step1_extract_single(client, pdf_path: Path, meta: dict, question: str, model: str,
                         text_cache_dir: Path, print_lock: threading.Lock,
                         idx: int, total: int, findings_dir: Path = None,
                         force_refresh: bool = False,
                         use_evidence_pack: bool = True,
                         ai_rerank_chunks: bool = False,
                         use_vector_search: bool = False) -> dict:
    """Step 1: Extract findings from a single paper PDF."""
    stem = pdf_path.stem[:60]
    pdf_hash = file_sha256(pdf_path)
    cache_key = pdf_hash[:16]

    text_cache_path = text_cache_dir / f"{cache_key}.txt"
    from_cache = text_cache_path.exists()
    try:
        text = prepare_pdf_text(pdf_path, text_cache_dir)
    except PDFExtractionError as e:
        with print_lock:
            print(f"  [{idx}/{total}] {stem} -> ⚠ PDF提取失败: {e}", flush=True)
        return {"file": pdf_path.name, "pdf_path": str(pdf_path), "relevant": False,
                "findings": [], "error": str(e),
                "ref_title": meta.get("title", ""), "ref_authors": meta.get("authors", ""),
                "ref_year": meta.get("year", ""), "ref_num": meta.get("ref_num", 0)}

    prompt_text, coverage = build_extraction_prompt(
        text, question,
        use_evidence_pack=use_evidence_pack,
        ai_rerank_chunks=ai_rerank_chunks,
        use_vector_search=use_vector_search,
        client=client, model=model,
        pdf_hash=pdf_hash, cache_dir=text_cache_dir,
    )

    fcache_key = findings_cache_key(pdf_path, question, model, use_evidence_pack, ai_rerank_chunks, use_vector_search)
    fcache_path = findings_dir / f"{fcache_key}.json" if findings_dir else None
    if not force_refresh and fcache_path and fcache_path.exists():
        cached_result = normalize_finding_relevance(json.loads(fcache_path.read_text(encoding="utf-8")))
        relevant = cached_result.get("relevant", False)
        findings = cached_result.get("findings", [])
        with print_lock:
            label = "✓ 缓存" if relevant else "✗ 缓存"
            print(f"  [{idx}/{total}] {stem} -> {label}, {len(findings)}条发现", flush=True)
        return cached_result

    try:
        user_prompt = (
            f"论文信息: {meta.get('authors', '')} ({meta.get('year', '')}). {meta.get('title', '')}\n\n"
            f"论文文本输入：\n{prompt_text}\n\n"
            f"请根据上述论文文本，提取与以下研究问题相关的发现：\n研究问题：{question}"
        )
        result = llm_client.call_json(client, STEP1_SYSTEM, user_prompt, model, 16384)
    except LLMCallError as e:
        with print_lock:
            print(f"  [{idx}/{total}] {stem} -> ⚠ API失败 (attempts={e.attempts}): {e}", flush=True)
        return {"file": pdf_path.name, "pdf_path": str(pdf_path), "relevant": False,
                "findings": [], "error": str(e),
                "ref_title": meta.get("title", ""), "ref_authors": meta.get("authors", ""),
                "ref_year": meta.get("year", ""), "ref_num": meta.get("ref_num", 0)}

    result = normalize_finding_relevance(result)
    relevant = result.get("relevant", False)
    findings = result.get("findings", [])
    source = "缓存" if from_cache else "提取"
    charset = len(prompt_text)
    input_mode = "EvidencePack+AI重排" if use_evidence_pack and ai_rerank_chunks else ("EvidencePack" if use_evidence_pack else "全文前缀")

    with print_lock:
        if relevant:
            print(f"  [{idx}/{total}] {stem} -> ✓ 相关, {len(findings)}条发现 ({source}, {input_mode}, {charset}字)", flush=True)
            for j, f in enumerate(findings[:3]):
                print(f"       #{j+1} {f.get('claim_cn', '?')[:80]}", flush=True)
            if len(findings) > 3:
                print(f"       ... 共{len(findings)}条", flush=True)
        else:
            print(f"  [{idx}/{total}] {stem} -> ✗ 不相关 ({source}, {input_mode}, {charset}字)", flush=True)

    result = {"file": pdf_path.name, "pdf_path": str(pdf_path), "relevant": relevant,
            "findings": findings,
            "ref_title": meta.get("title", ""), "ref_authors": meta.get("authors", ""),
            "ref_year": meta.get("year", ""), "ref_num": meta.get("ref_num", 0),
            "cache": {
                "version": FINDINGS_CACHE_VERSION,
                "key": fcache_key,
                "pdf_sha256": pdf_hash,
                "question": question,
                "model": model,
                "input_mode": "evidence_pack" if use_evidence_pack else "full_prefix",
                "ai_rerank_chunks": bool(ai_rerank_chunks and use_evidence_pack),
                "use_vector_search": bool(use_vector_search and use_evidence_pack),
            }}
    if coverage:
        result["evidence_pack"] = coverage

    if findings_dir:
        findings_dir.mkdir(parents=True, exist_ok=True)
        (findings_dir / f"{fcache_key}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    return result


def step1_extract_all(client_factory, papers: list[dict], question: str, model: str,
                       text_cache_dir: Path, workers: int, findings_dir: Path,
                       use_evidence_pack: bool = True,
                       ai_rerank_chunks: bool = False,
                       use_vector_search: bool = False) -> list[dict]:
    """Step 1: Concurrent extraction of all findings across papers."""
    print(f"\n── Step 1: 逐篇提取 ──", flush=True)
    print_lock = threading.Lock()
    total = len(papers)
    results = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for i, paper in enumerate(papers, 1):
            client = client_factory()
            pdf_path = Path(paper["pdf_path"])
            year = paper.get("date", "").split("-")[0] if paper.get("date") else ""
            meta = {
                "title": paper.get("title", ""),
                "authors": paper.get("authors", ""),
                "year": year,
                "ref_num": i,
            }
            f = executor.submit(step1_extract_single, client, pdf_path, meta, question,
                                model, text_cache_dir, print_lock, i, total, findings_dir,
                                use_evidence_pack=use_evidence_pack,
                                ai_rerank_chunks=ai_rerank_chunks,
                                use_vector_search=use_vector_search)
            futures[f] = pdf_path
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda r: [p["pdf_path"] for p in papers].index(r["pdf_path"]))

    relevant_count = sum(1 for r in results if r["relevant"])
    total_findings = sum(len(r.get("findings", [])) for r in results)
    
    direct_count = 0
    indirect_count = 0
    bg_count = 0
    for r in results:
        for f in r.get("findings", []):
            level = str(f.get("relevance_level", "")).lower()
            if level == "direct":
                direct_count += 1
            elif level == "indirect":
                indirect_count += 1
            elif level == "background":
                bg_count += 1

    print(f"\n  结果: {relevant_count}篇相关, {total - relevant_count}篇不相关, 共{total_findings}条发现", flush=True)
    if total_findings > 0:
        print(f"  明细: direct={direct_count}, indirect={indirect_count}, background={bg_count}\n", flush=True)
    else:
        print()
    return results


def load_cached_findings_for_papers(papers: list[dict], question: str, model: str,
                                    findings_dir: Path, use_evidence_pack: bool = True,
                                    ai_rerank_chunks: bool = False,
                                    use_vector_search: bool = False) -> list[dict]:
    """Load cached findings JSON if they match parameters and file hashes."""
    results = []
    missing = []
    for i, paper in enumerate(papers, 1):
        pdf_path = Path(paper["pdf_path"])
        key = findings_cache_key(pdf_path, question, model, use_evidence_pack, ai_rerank_chunks, use_vector_search)
        path = findings_dir / f"{key}.json"
        if not path.exists():
            missing.append(str(pdf_path))
            continue
        data = normalize_finding_relevance(json.loads(path.read_text(encoding="utf-8")))
        cache_meta = data.get("cache", {})
        if (
            cache_meta.get("version") != FINDINGS_CACHE_VERSION
            or cache_meta.get("question") != question
            or cache_meta.get("model") != model
            or cache_meta.get("pdf_sha256") != file_sha256(pdf_path)
            or cache_meta.get("input_mode") != ("evidence_pack" if use_evidence_pack else "full_prefix")
            or bool(cache_meta.get("ai_rerank_chunks")) != bool(ai_rerank_chunks and use_evidence_pack)
            or bool(cache_meta.get("use_vector_search")) != bool(use_vector_search and use_evidence_pack)
        ):
            missing.append(str(pdf_path))
            continue
        data["ref_num"] = i
        data["pdf_path"] = str(pdf_path)
        results.append(data)

    if missing:
        preview = ", ".join(missing[:5])
        more = f" 等 {len(missing)} 篇" if len(missing) > 5 else ""
        raise RuntimeError(f"缺少当前问题/模型对应的 findings 缓存: {preview}{more}")
    return results


def step2_generate_outline(client, all_results: list[dict], question: str,
                           model: str) -> dict:
    """Step 2: Generate a structured report outline based on extracted findings."""
    print(f"── Step 2: 根据发现生成大纲 ──", flush=True)

    idx_lines = []
    fidx = 0
    for paper in all_results:
        if not paper.get("relevant"):
            continue
        for f in paper.get("findings", []):
            idx_lines.append(
                f"[{fidx}] ({f.get('relevance_level', 'direct')}) {f.get('claim_cn', '')} | {format_finding_metadata(f)}"
            )
            fidx += 1
    findings_index = "\n".join(idx_lines)

    if not findings_index:
        print("  ⚠ 无有效发现用于生成大纲", flush=True)
        return {"title": "文献综述报告", "sections": [{"heading": "背景与综述", "subsections": []}]}

    try:
        outline = llm_client.call_json(client, "", STEP2_PROMPT.format(question=question, findings=findings_index), model, 32768)
        title = outline.get("title", "文献综述报告")
        sections = outline.get("sections", [])
        print(f"  ✅ 生成大纲标题: {title}，共 {len(sections)} 个主章节\n", flush=True)
        return outline
    except LLMCallError as e:
        print(f"  ⚠ 大纲生成失败 (attempts={e.attempts}): {e}，回退使用通用结构", flush=True)

    # Generic Fallback Outline Generation
    try:
        from .prompts import STEP2_MODEL_FALLBACK
        print(f"  🔄 尝试使用 {STEP2_MODEL_FALLBACK} 生成大纲...", flush=True)
        from .llm_client import get_client
        pro_client = get_client()
        outline = llm_client.call_json(pro_client, "", STEP2_PROMPT.format(question=question, findings=findings_index), STEP2_MODEL_FALLBACK, 32768)
        return outline
    except LLMCallError as e2:
        print(f"  ⚠ Fallback 大纲生成仍失败 (attempts={e2.attempts}): {e2}，生成极简大纲", flush=True)

    # Programmatic Ultimate Fallback
    dim_counts = {}
    topic_tags_count = {}
    direct_findings = []
    for paper in all_results:
        if not paper.get("relevant"):
            continue
        for f in paper.get("findings", []):
            if f.get("relevance_level") == "direct":
                direct_findings.append(f)
            relation = f.get("relation") or {}
            for k in ("subject", "object", "direction"):
                if relation.get(k) and relation.get(k) != "not_applicable":
                    dim_counts[(k, relation[k])] = dim_counts.get((k, relation[k]), 0) + 1
            context = f.get("context") or {}
            for k in ("study_type", "sample_or_system", "condition", "method"):
                if context.get(k):
                    dim_counts[(k, context[k])] = dim_counts.get((k, context[k]), 0) + 1
            for v in f.get("variables", []):
                if isinstance(v, dict) and v.get("name"):
                    dim_counts[("variable", v["name"])] = dim_counts.get(("variable", v["name"]), 0) + 1
            tags = f.get("topic_tags") or f.get("tags") or {}
            for k, v in tags.items():
                if v:
                    topic_tags_count[(k, v)] = topic_tags_count.get((k, v), 0) + 1

    best_dim = None
    best_val_count = 0
    for dim, count in sorted(dim_counts.items(), key=lambda x: x[1], reverse=True):
        if count >= 3:
            best_dim = dim[0]
            best_val_count = count
            break
    if not best_dim:
        for dim, count in sorted(topic_tags_count.items(), key=lambda x: x[1], reverse=True):
            if count >= 2:
                best_dim = dim[0]
                best_val_count = count
                break

    sections = []
    labels = {
        "subject": "按研究对象划分",
        "object": "按关联指标/机制划分",
        "direction": "按作用关系/趋势划分",
        "study_type": "按研究/证据类型划分",
        "sample_or_system": "按样本与实验系统划分",
        "condition": "按实验条件与环境划分",
        "method": "按实验与测量方法划分",
        "variable": "按核心关联变量划分",
    }
    if not best_dim:
        best_dim = "default"
        labels["default"] = "核心发现"

    dim = best_dim
    values = []
    seen = set()
    for (d, val), count in sorted(dim_counts.items() if dim != "default" else topic_tags_count.items(), key=lambda x: x[1], reverse=True):
        if d == dim and val not in seen:
            values.append(val)
            seen.add(val)
            if len(values) >= 4:
                break

    for value in values:
        subsections = []
        criteria = {}
        if dim in ("subject", "predicate", "object", "direction"):
            criteria["relation"] = {dim: value}
        elif dim in ("study_type", "sample_or_system", "condition", "method"):
            criteria["context"] = {dim: value}
        elif dim == "variable":
            criteria["variables"] = [value]
        else:
            criteria["topic_tags"] = {dim: value}

        subsections.append({"heading": value[:60], "match_criteria": criteria})
        sections.append({"heading": labels[dim], "subsections": subsections})
        break

    if not sections:
        sections = [{"heading": "所有发现", "subsections": []}]

    return {"title": "报告", "sections": sections}


def _collect_leaves(sections: list) -> list[dict]:
    leaves = []
    for sec in sections:
        subs = sec.get("subsections", [])
        if subs:
            leaves.extend(_collect_leaves(subs))
        else:
            leaves.append(sec)
    return leaves


def step3_match_and_write(client_factory, outline: dict, all_results: list[dict],
                           question: str, model: str, workers: int) -> tuple[list[dict], dict]:
    """Step 3: Parallel section matching and writing with local citation checks."""
    print(f"── Step 3: 逐节匹配 + 并行写作 ──", flush=True)

    all_findings = []
    fidx = 0
    paper_refs = {}
    for paper in all_results:
        ref_num = paper.get("ref_num", 0)
        if ref_num and ref_num not in paper_refs and paper.get("relevant"):
            paper_refs[ref_num] = {
                "title": paper.get("ref_title", ""),
                "authors": paper.get("ref_authors", ""),
                "year": paper.get("ref_year", ""),
            }
        if not paper["relevant"]:
            continue
        for f in paper.get("findings", []):
            level = f.get("relevance_level", "direct")
            all_findings.append({
                "index": fidx, "file": paper["file"], "ref_num": ref_num,
                "cite_key": f.get("cite_key", ""),
                "claim_cn": f.get("claim_cn", ""), "quote": f.get("quote", ""),
                "relevance_level": level,
                "include_in_main_report": f.get("include_in_main_report", level == "direct"),
                "relation": f.get("relation", {}),
                "context": f.get("context", {}),
                "variables": f.get("variables", []),
                "constraints": f.get("constraints", []),
                "topic_tags": f.get("topic_tags", f.get("tags", {})),
                "tags": f.get("topic_tags", f.get("tags", {})),
            })
            fidx += 1

    if not all_findings:
        print("  ⚠ 无 finding 可合成", flush=True)
        return [], {}

    leaves = _collect_leaves(outline.get("sections", []))
    total_leaves = len(leaves)
    if total_leaves == 0:
        print("  ⚠ 大纲无子章节", flush=True)
        return [], {}

    main_findings = [f for f in all_findings if f.get("include_in_main_report")]
    if not main_findings:
        main_findings = [f for f in all_findings if f.get("relevance_level") in {"indirect", "background"}]
        if main_findings:
            print("  ⚠ 无 direct findings，Step 3 降级使用 indirect/background", flush=True)
    candidate_indices = {f["index"] for f in main_findings}

    abbreviated = "\n".join(
        f"[{f['index']}] ({f.get('relevance_level','direct')}) {f['claim_cn']} | {format_finding_metadata(f)}"
        for f in main_findings
    )

    def _normalize_indices(indices) -> list[int]:
        seen = set()
        clean = []
        for raw in indices or []:
            try:
                idx = int(raw)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(all_findings) and idx in candidate_indices and idx not in seen:
                clean.append(idx)
                seen.add(idx)
            if len(clean) >= 8:
                break
        return clean

    # ── Phase A: 逐节并行匹配 ──
    print(f"    匹配: {total_leaves} 节 × {len(main_findings)}条缩略（并行）", flush=True)
    leaf_matched = {}

    def _match_leaf(pos: int, leaf: dict) -> tuple[int, str, list[int]]:
        client = client_factory()
        heading = leaf["heading"]
        match_criteria = leaf.get("match_criteria") or leaf.get("search_tags", {})
        try:
            result = llm_client.call_json(client, "",
                                          STEP3_MATCH_PROMPT.format(
                                              heading=heading,
                                              match_criteria=json.dumps(match_criteria, ensure_ascii=False),
                                              findings=abbreviated),
                                          model, 8192)
            indices = result.get("matched_indices", [])
        except LLMCallError as e:
            print(f"    ⚠ {heading} 匹配失败 (attempts={e.attempts}): {e}，取前8条", flush=True)
            indices = [f["index"] for f in main_findings[:8]]
        indices = _normalize_indices(indices)
        print(f"    {heading}: {len(indices)}条", flush=True)
        return pos, heading, indices

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_match_leaf, pos, leaf): leaf["heading"] for pos, leaf in enumerate(leaves)}
        for future in as_completed(futures):
            pos, heading, indices = future.result()
            leaf_matched[pos] = indices

    # Coverage calculations
    matched_set = set()
    for indices in leaf_matched.values():
        matched_set.update(indices)
    coverage_ratio = len(matched_set) / len(candidate_indices) * 100 if candidate_indices else 100
    dropped = len(candidate_indices) - len(matched_set)
    if dropped > 0:
        print(f"    覆盖率: {len(matched_set)}/{len(candidate_indices)} ({coverage_ratio:.0f}%), {dropped}条候选发现未使用", flush=True)
    else:
        print(f"    覆盖率: {len(matched_set)}/{len(candidate_indices)} ({coverage_ratio:.0f}%)", flush=True)

    # ── Phase B: 逐节并行写作 ──
    print(f"    写作: {total_leaves} 节并行", flush=True)

    def _write_leaf(pos: int, leaf: dict) -> tuple[int, dict]:
        client = client_factory()
        heading = leaf["heading"]
        indices = leaf_matched.get(pos, [])
        matched = [all_findings[i] for i in indices if i < len(all_findings)]
        if not matched:
            print(f"    {heading} -> 无发现", flush=True)
            return pos, {"heading": heading, "content": "_该节未匹配到相关发现。_"}

        findings_text = "\n\n".join(
            f"**[ref:{f['ref_num']}] {f['cite_key']}**\n摘要: {f['claim_cn']}\n证据结构: {format_finding_metadata(f)}\n原文: \"{f['quote']}\""
            for j, f in enumerate(matched)
        )
        try:
            content = llm_client.call_text(client,
                                           STEP3_WRITE_PROMPT.format(heading=heading, question=question, findings=findings_text),
                                           model, 32768)
        except LLMCallError as e:
            content = f"撰写失败 (attempts={e.attempts}): {e}"

        # --- Citation Sanitizer ---
        allowed_refs = {f['ref_num'] for f in matched}
        def _sanitize_refs_block(m):
            inner = m.group(1)
            valid_nums = []
            for part in re.split(r',', inner):
                part = part.strip()
                if '-' in part:
                    try:
                        start, end = map(int, part.split('-'))
                        for n in range(start, end + 1):
                            if n in allowed_refs:
                                valid_nums.append(str(n))
                    except ValueError:
                        pass
                else:
                    try:
                        n = int(part)
                        if n in allowed_refs:
                            valid_nums.append(str(n))
                    except ValueError:
                        pass
            
            unique_valid = []
            for n in valid_nums:
                if n not in unique_valid:
                    unique_valid.append(n)
                    
            if unique_valid:
                return "[" + ", ".join(unique_valid) + "]"
            return ""

        content = re.sub(r'\[([\d\s,\-]+)\]', _sanitize_refs_block, content)
        # --------------------------

        print(f"    {heading} -> 完成 ({len(matched)}条)", flush=True)
        return pos, {"heading": heading, "content": content}

    sections_output = [None] * total_leaves
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_write_leaf, pos, leaf): leaf["heading"] for pos, leaf in enumerate(leaves)}
        for future in as_completed(futures):
            pos, section = future.result()
            sections_output[pos] = section

    print(flush=True)
    return [s for s in sections_output if s is not None], paper_refs


def _clean_refs(report: str, paper_refs: dict) -> str:
    """Keep only references cited in the report body, removing orphan references."""
    cited_nums = set(int(m) for m in re.findall(r'\[(\d+)\]', report))
    ref_marker = "\n## 参考文献\n"
    ref_idx = report.rfind(ref_marker)
    if ref_idx < 0:
        ref_marker = "\n## 参考文献"
        ref_idx = report.rfind(ref_marker)
    if ref_idx < 0:
        return report
    body = report[:ref_idx]
    ref_list_lines = [ref_marker]
    for num in sorted(paper_refs.keys()):
        if num not in cited_nums:
            continue
        info = paper_refs[num]
        ref_list_lines.append(f"[{num}] {info['authors']}. *{info['title']}*. {info['year']}.")
    return body + "\n".join(ref_list_lines)


def step4_integrate(client, outline: dict, sections: list[dict],
                    question: str, paper_refs: dict, model: str) -> str:
    """Step 4: Integrate sections into a unified report and construct reference list."""
    print(f"── Step 4: 整合报告 ──", flush=True)

    sects_text = []
    for s in sections:
        sects_text.append(f"### {s['heading']}\n\n{s['content']}\n")
    sections_input = "\n\n".join(sects_text)

    try:
        report = llm_client.call_text(client,
                                      STEP4_PROMPT.format(sections=sections_input, question=question),
                                      model, 65536)
    except LLMCallError as e:
        print(f"  ⚠ 整合失败 (attempts={e.attempts}): {e}，直接拼接", flush=True)
        report = f"# {outline.get('title', '文献综述报告')}\n\n" + sections_input

    # Add Reference List
    ref_lines = ["\n## 参考文献\n"]
    for num in sorted(paper_refs.keys()):
        info = paper_refs[num]
        ref_lines.append(f"[{num}] {info['authors']}. *{info['title']}*. {info['year']}.")
    report += "\n".join(ref_lines)

    report = _clean_refs(report, paper_refs)
    print(f"  ✅ 整合完成，字数: {len(report)}\n", flush=True)
    return report


def step5_narrative(client, report_text: str, model: str) -> str:
    """Step 5: Rewrite the structured report into a narrative review article."""
    print(f"── Step 5: 改写叙事文章 ──", flush=True)

    try:
        article = llm_client.call_text(client,
                                       STEP5_PROMPT.format(report=report_text),
                                       model, 32768, temperature=0.3)
    except LLMCallError as e:
        print(f"  ⚠ 文章生成失败 (attempts={e.attempts}): {e}，回退使用原报告", flush=True)
        article = report_text

    print(f"  ✅ 文章生成完成\n", flush=True)
    return article


def normalize_table_views(raw) -> list[dict]:
    """Normalize proposed Step 7 table views."""
    views = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
    normalized = []
    for i, view in enumerate(views):
        if not isinstance(view, dict):
            continue
        title = str(view.get("title", "")).strip() or f"总结表 {i + 1}"
        row_dimension = str(view.get("row_dimension", "")).strip()
        column_dimension = str(view.get("column_dimension", "")).strip()
        cell_schema = str(view.get("cell_schema", "")).strip()
        if not row_dimension or not column_dimension:
            continue
        if not cell_schema:
            cell_schema = "主要发现；证据强度；引用"
        try:
            coverage = float(view.get("estimated_direct_evidence_coverage", 0))
        except (TypeError, ValueError):
            coverage = 0
        coverage = max(0.0, min(1.0, coverage))
        normalized.append({
            "title": title[:120],
            "row_dimension": row_dimension[:120],
            "column_dimension": column_dimension[:120],
            "cell_schema": cell_schema[:200],
            "coverage_rationale": str(view.get("coverage_rationale", view.get("why", ""))).strip()[:500],
            "estimated_direct_evidence_coverage": coverage,
        })
    return normalized


def choose_table_view(views: list[dict]) -> dict:
    """Choose the best table view with maximum coverage score."""
    if not views:
        return {
            "title": "直接证据总结表",
            "row_dimension": "核心对象或变量",
            "column_dimension": "样本、条件或证据类型",
            "cell_schema": "主要发现；证据强度；引用",
            "coverage_rationale": "fallback view",
            "estimated_direct_evidence_coverage": 0,
        }
    return max(
        views,
        key=lambda v: (
            v.get("estimated_direct_evidence_coverage", 0),
            bool(v.get("coverage_rationale")),
            len(v.get("row_dimension", "")) + len(v.get("column_dimension", "")),
        ),
    )


def step7_summary(client_factory, report_text: str, step7_model: str = DEFAULT_FLASH_MODEL) -> dict:
    """Step 7: Concurrent generation of Markdown summary table and Mermaid diagram."""
    print(f"── Step 7: 生成总结图表 ──", flush=True)
    result = {"table": "", "diagram": ""}

    def _gen_table():
        c = client_factory()
        # Propose views
        try:
            raw_proposal = llm_client.call_json(c, "", STEP7_TABLE_VIEW_PROMPT.format(report=report_text), step7_model, 8192)
            views = normalize_table_views(raw_proposal)
            selected_view = choose_table_view(views)
            print(
                f"  📊 Step 7 Table: 选择视图 '{selected_view['title']}' "
                f"(估计覆盖率={selected_view['estimated_direct_evidence_coverage']})",
                flush=True,
            )
            resp = c.chat.completions.create(
                model=step7_model,
                messages=[{"role": "user", "content": STEP7_TABLE_PROMPT.format(
                    report=report_text,
                    table_view=json.dumps(selected_view, ensure_ascii=False, indent=2),
                )}],
                temperature=0,
                max_tokens=4096,
                timeout=60,
            )
            t = resp.choices[0].message.content
            if t and "|" in t:
                return t
        except LLMCallError as e:
            print(f"  ⚠ 表格生成失败 (attempts={e.attempts}): {e}", flush=True)
        return ""

    def _gen_diagram():
        c = client_factory()
        try:
            resp = c.chat.completions.create(
                model=step7_model,
                messages=[{"role": "user", "content": STEP7_DIAGRAM_PROMPT.format(report=report_text)}],
                temperature=0,
                max_tokens=8192,
                timeout=60,
            )
            d = resp.choices[0].message.content
            if d and "```mermaid" in d:
                return d
        except LLMCallError as e:
            print(f"  ⚠ 示意图生成失败 (attempts={e.attempts}): {e}",
                  file=sys.stderr, flush=True)
        except Exception as e:
            print(f"  ⚠ 示意图生成失败（非API错误）: {e}",
                  file=sys.stderr, flush=True)
        return ""

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_table = ex.submit(_gen_table)
        f_diag = ex.submit(_gen_diagram)
        table = f_table.result(timeout=90)
        diagram = f_diag.result(timeout=90)

    if table:
        result["table"] = table
        print(f"  ✅ 表格生成完成", flush=True)
    else:
        print(f"  ⚠ 表格生成失败", flush=True)
    if diagram:
        result["diagram"] = diagram
        print(f"  ✅ 示意图生成完成", flush=True)

    print(flush=True)
    return result
