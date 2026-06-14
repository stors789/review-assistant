#!/usr/bin/env python3
"""
探索总结工作流：给定研究问题 + Zotero 论文集，直接从论文全文提取答案，
生成结构化报告、叙事文章和示意图。

7 步流水线 + 3 项验证：
  Step 1: 逐篇提取发现 + 动态标签（并发）
  Ver 1: 验证发现是否忠于原文，失败重提取
  Step 2: 根据发现摘要生成报告大纲
  Step 3: 逐节缩略匹配 + 含 cite_key 写作（并行）
  Step 4: 整合为结构化 Markdown 报告 + 矛盾识别
  Ver A: 引用正确性验证
  Ver B: 逻辑一致性验证
  Step 5: 验证通过后改写为叙事文章
  Step 6: （如有验证问题）修正报告后重新生成文章
  Step 7: 生成总结表格 + 示意图
"""

import argparse
import itertools
import json
import os
import sys
import threading
from pathlib import Path

# Add script directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from openai import OpenAI
from zotero_reader import ZoteroReader

# Import modular components
STOP_AFTER_CHOICES = ("step1", "ver1", "step2", "step3", "step4")
from llm_client import init_client_pool, get_client
from utils import should_stop_after, print_stop_after
from caching import (
    OUTLINE_CACHE_VERSION,
    SECTIONS_CACHE_VERSION,
    REPORT_CACHE_VERSION,
    outline_cache_matches,
    stable_json_sha256,
    build_step_cache_meta,
    load_cached_sections,
    save_cached_sections,
    load_cached_report,
    save_cached_report,
)
from verification import (
    verify_findings,
    verify_citations,
    verify_claim_map,
    verify_logic,
    verify_references_programmatic,
    step6_fix_report,
)
from pipeline import (
    step1_extract_all,
    load_cached_findings_for_papers,
    step2_generate_outline,
    step3_match_and_write,
    step4_integrate,
    step5_narrative,
    step7_summary,
    _clean_refs,
)


def _print_outline_tree(sections: list, indent: str):
    """Print report outline structure as a tree visualization in logs."""
    for i, sec in enumerate(sections):
        is_last = i == len(sections) - 1
        prefix = "└─" if is_last else "├─"
        print(f"  {indent}{prefix} {sec.get('heading', '?')}", flush=True)
        subs = sec.get("subsections", [])
        for j, sub in enumerate(subs):
            sub_last = j == len(subs) - 1
            sp = "   " if is_last else "│  "
            sp2 = "└─" if sub_last else "├─"
            print(f"  {indent}{sp}{sp2} {sub.get('heading', '?')}", flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="探索总结工作流：给定研究问题和Zotero论文集，生成报告+文章"
    )
    parser.add_argument("collection", nargs="+", help="Zotero 论文集路径（如 '主题 > 子集'，可多个）")
    parser.add_argument("--question", "-q", required=True, help="研究问题")
    parser.add_argument("--output", "-o", default="synthesize_output", help="输出目录")
    parser.add_argument("--model", "-m", default="deepseek-v4-pro", help="模型名")
    parser.add_argument("--workers", "-w", type=int, default=5, help="并发数")
    parser.add_argument("--cache-dir", help="文本缓存目录（默认 output/cache）")
    parser.add_argument("--max-papers", type=int, default=0, help="最大处理论文数（0=无限制）")
    parser.add_argument("--skip-step1", action="store_true", help="跳过Step1，从已有 findings 继续")
    parser.add_argument("--skip-verify", action="store_true", help="跳过所有验证")
    parser.add_argument("--full-prefix", action="store_true",
                        help="Step1 使用旧模式：发送 PDF 文本前 80000 字符，而不是 EvidencePack")
    parser.add_argument("--ai-rerank-chunks", action="store_true",
                        help="Step1 可选：用 AI 对 EvidencePack 候选文本块重排（默认关闭，--full-prefix 时无效）")
    parser.add_argument("--max-fix-passes", type=int, default=2, help="修正报告的最大轮数（默认2）")
    parser.add_argument("--stop-after", choices=STOP_AFTER_CHOICES,
                        help="调试模式：在指定步骤完成后停止（step1/ver1/step2/step3/step4）")
    args = parser.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit("请设置 OPENAI_API_KEY 或 DEEPSEEK_API_KEY")

    api_keys = [api_key]
    for i in range(2, 20):
        k = os.environ.get(f"DEEPSEEK_API_KEY_{i}")
        if k:
            api_keys.append(k)
        else:
            break

    base_url = "https://api.deepseek.com"
    output_dir = Path(args.output).resolve()
    cache_dir = Path(args.cache_dir) if args.cache_dir else output_dir / "cache"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Setup client pool
    init_client_pool(base_url)

    _key_cycle = itertools.cycle(api_keys)
    _key_lock = threading.Lock()

    def _client_factory():
        with _key_lock:
            key = next(_key_cycle)
        return OpenAI(api_key=key, base_url=base_url)

    client_factory = _client_factory
    client = client_factory()

    # ── 获取论文集 ──
    seen_titles = set()
    all_papers = []
    for col in args.collection:
        print(f"📚 Zotero「{col}」", flush=True)
        with ZoteroReader() as reader:
            papers = reader.get_papers(col)
        n_new = 0
        for p in papers:
            if p["title"] not in seen_titles:
                seen_titles.add(p["title"])
                all_papers.append(p)
                n_new += 1
        print(f"   {len(papers)} 篇 PDF, {n_new} 篇新收录", flush=True)

    papers = all_papers
    if not papers:
        sys.exit("论文集没有可用的 PDF")

    if args.max_papers and args.max_papers < len(papers):
        papers = papers[:args.max_papers]
    print(f"   {len(papers)} 篇 PDF\n", flush=True)

    # ── Step 1 ──
    findings_dir = output_dir / "findings"
    if args.skip_step1:
        print(f"\n── Step 1: 从缓存加载 ──", flush=True)
        if not findings_dir.exists():
            sys.exit("无缓存 findings，请先完整跑一次")
        try:
            all_results = load_cached_findings_for_papers(
                papers,
                args.question,
                args.model,
                findings_dir,
                use_evidence_pack=not args.full_prefix,
                ai_rerank_chunks=args.ai_rerank_chunks and not args.full_prefix,
            )
        except RuntimeError as e:
            sys.exit(str(e))
        relevant_loaded = sum(1 for r in all_results if r.get("relevant"))
        print(f"   加载 {len(all_results)} 篇 findings，其中 {relevant_loaded} 篇相关", flush=True)
    else:
        findings_dir.mkdir(parents=True, exist_ok=True)
        all_results = step1_extract_all(client_factory, papers, args.question,
                                        args.model, cache_dir, args.workers, findings_dir,
                                        use_evidence_pack=not args.full_prefix,
                                        ai_rerank_chunks=args.ai_rerank_chunks and not args.full_prefix)

    relevant_papers = [r for r in all_results if r["relevant"]]
    if not relevant_papers:
        print("论文集无相关论文。退出。", flush=True)
        return
    if should_stop_after("step1", args.stop_after):
        print_stop_after("step1", output_dir)
        return

    # ── Ver 1: 验证发现 ──
    if not args.skip_verify:
        all_results = verify_findings(all_results, papers, client_factory,
                                       args.model,
                                       cache_dir, findings_dir,
                                       args.question,
                                       use_evidence_pack=not args.full_prefix,
                                       ai_rerank_chunks=args.ai_rerank_chunks and not args.full_prefix)
                                       
    # ── 导出 EvidencePack 覆盖率报告 ──
    coverage_report = []
    for r in all_results:
        if r.get("relevant") and "evidence_pack" in r:
            cov = r["evidence_pack"]
            coverage_report.append({
                "file": r["file"],
                "ref_num": r.get("ref_num"),
                "total_chars": cov.get("full_text_chars"),
                "pack_chars": cov.get("sent_chars"),
                "coverage_ratio": cov.get("coverage_ratio"),
                "chunks_used": cov.get("selected_chunks"),
                "ai_reranked": cov.get("ai_rerank", {}).get("used", False)
            })
    if coverage_report:
        cov_path = output_dir / "evidence_coverage.json"
        cov_path.write_text(json.dumps(coverage_report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  📄 EvidencePack 覆盖率报告已保存: {cov_path}", flush=True)

    if should_stop_after("ver1", args.stop_after):
        print_stop_after("ver1", output_dir)
        return

    # ── Step 2 ──
    outline_path = output_dir / "outline.json"
    outline_meta_path = output_dir / "outline.meta.json"
    if outline_path.exists() and outline_cache_matches(outline_meta_path, args.question, args.model):
        print(f"── Step 2: 加载缓存大纲 ──", flush=True)
        outline = json.loads(outline_path.read_text(encoding="utf-8"))
        print(f"  📋 {outline.get('title', '报告')}", flush=True)
        _print_outline_tree(outline.get("sections", []), indent="    ")
        print(flush=True)
    else:
        outline = step2_generate_outline(client, all_results, args.question, args.model)
        outline_path.write_text(json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8")
        outline_meta_path.write_text(json.dumps({
            "version": OUTLINE_CACHE_VERSION,
            "question": args.question,
            "model": args.model,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    if should_stop_after("step2", args.stop_after):
        print_stop_after("step2", output_dir)
        return

    # ── Step 3 ──
    sections_debug_path = output_dir / "sections.json"
    sections_meta_path = output_dir / "sections.meta.json"
    outline_hash = stable_json_sha256(outline)
    findings_hash = stable_json_sha256(all_results)
    sections_meta = build_step_cache_meta(
        SECTIONS_CACHE_VERSION,
        args.question,
        args.model,
        outline_sha256=outline_hash,
        findings_sha256=findings_hash,
    )
    cached_sections = load_cached_sections(sections_debug_path, sections_meta_path, sections_meta)
    if cached_sections:
        print(f"── Step 3: 加载缓存分节草稿 ──", flush=True)
        sections, paper_refs = cached_sections
        print(f"  📄 分节草稿已加载: {sections_debug_path} ({len(sections)} 节)", flush=True)
    else:
        sections, paper_refs = step3_match_and_write(client_factory, outline, all_results, args.question, args.model, args.workers)
        save_cached_sections(sections_debug_path, sections_meta_path, sections, paper_refs, sections_meta)
        print(f"  📄 分节草稿已保存: {sections_debug_path}", flush=True)

    if not sections:
        print("撰写失败，无内容产出。退出。", flush=True)
        return
    if should_stop_after("step3", args.stop_after):
        print_stop_after("step3", output_dir)
        return

    # ── Step 4 ──
    report_path = output_dir / "report.md"
    report_meta_path = output_dir / "report.meta.json"
    sections_hash = stable_json_sha256(sections)
    paper_refs_hash = stable_json_sha256(paper_refs)
    report_meta = build_step_cache_meta(
        REPORT_CACHE_VERSION,
        args.question,
        args.model,
        outline_sha256=outline_hash,
        sections_sha256=sections_hash,
        paper_refs_sha256=paper_refs_hash,
    )
    report = load_cached_report(report_path, report_meta_path, report_meta)
    if report is not None:
        print(f"── Step 4: 加载缓存报告 ──", flush=True)
        print(f"  📄 报告已加载: {report_path}", flush=True)
    else:
        report = step4_integrate(client, outline, sections, args.question, paper_refs, args.model)
        save_cached_report(report_path, report_meta_path, report, report_meta)
        print(f"  📄 报告已保存: {report_path}", flush=True)
    if should_stop_after("step4", args.stop_after):
        print_stop_after("step4", output_dir)
        return

    # ── Ver A/B: 引用+逻辑验证 ──
    has_issues = False
    verification_report = ""
    if not args.skip_verify:
        vp = verify_references_programmatic(report, paper_refs)
        va = verify_citations(report, all_results, client, args.model)
        vcm = verify_claim_map(report, client, args.model)
        vb = verify_logic(report, client, args.model)
        if vp or va or vcm or vb:
            has_issues = True
            verification_report = "\n\n---\n\n# 验证报告\n\n" + vp + va + vcm + vb
            verify_path = output_dir / "verification.md"
            verify_path.write_text(verification_report, encoding="utf-8")
            print(f"  📄 验证报告已保存: {verify_path}", flush=True)
        else:
            print(f"  ✅ 验证通过，无问题\n", flush=True)

    # ── Step 6: 修正报告（如验证发现问题）──
    if has_issues and args.max_fix_passes > 0:
        print(flush=True)
        for fix_pass in range(1, args.max_fix_passes + 1):
            report = step6_fix_report(client, report, verification_report, all_results, args.model, pass_num=fix_pass, total_passes=args.max_fix_passes)
            report = _clean_refs(report, paper_refs)
            report_path = output_dir / "report.md"
            save_cached_report(report_path, report_meta_path, report, report_meta)
            print(f"  📄 修正后报告已保存: {report_path}", flush=True)
            if not args.skip_verify:
                print(f"\n── Ver A/B: 修正后二次验证 (第 {fix_pass}/{args.max_fix_passes} 轮) ──", flush=True)
                vp2 = verify_references_programmatic(report, paper_refs)
                va2 = verify_citations(report, all_results, client, args.model)
                vcm2 = verify_claim_map(report, client, args.model)
                vb2 = verify_logic(report, client, args.model)
                if vp2 or va2 or vcm2 or vb2:
                    verification_report = f"\n\n---\n\n# 修正后二次验证报告 (第 {fix_pass} 轮)\n\n" + vp2 + va2 + vcm2 + vb2
                    second_path = output_dir / "verification_after_fix.md"
                    second_path.write_text(verification_report, encoding="utf-8")
                    print(f"  📄 修正后二次验证报告已保存: {second_path}", flush=True)
                    if fix_pass == args.max_fix_passes:
                        print(f"  ⚠ 经过 {args.max_fix_passes} 轮修正，仍有遗留问题，已追加到报告末尾。", flush=True)
                        report += "\n\n## 遗留问题与局限性\n\n"
                        report += "以下为自动验证步骤中发现且未能自动修复的逻辑或引用问题，供读者参考：\n\n"
                        report += (vp2 + va2 + vcm2 + vb2)
                        save_cached_report(report_path, report_meta_path, report, report_meta)
                else:
                    print(f"  ✅ 修正后二次验证通过，无问题\n", flush=True)
                    second_path = output_dir / "verification_after_fix.md"
                    if second_path.exists():
                        second_path.unlink()
                    break

    # ── Step 5: 叙事文章（基于最终报告）──
    article = step5_narrative(client, report, args.model)

    article_path = output_dir / "article.md"
    article_path.write_text(article, encoding="utf-8")
    print(f"  📄 文章已保存: {article_path}", flush=True)

    # ── Step 7: 总结图表 ──
    summary = step7_summary(client_factory, report)
    if summary["table"]:
        table_path = output_dir / "table.md"
        table_path.write_text(summary["table"], encoding="utf-8")
        print(f"  📄 总结表格已保存: {table_path}", flush=True)
    if summary["diagram"]:
        diagram_path = output_dir / "diagram.md"
        diagram_path.write_text(summary["diagram"], encoding="utf-8")
        print(f"  📄 示意图已保存: {diagram_path}", flush=True)

    (output_dir / "outline.json").write_text(
        json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "outline.meta.json").write_text(json.dumps({
        "version": OUTLINE_CACHE_VERSION,
        "question": args.question,
        "model": args.model,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"全部完成！")
    print(f"  结构化报告: {report_path}")
    print(f"  叙事文章:   {article_path}")
    print(f"  示意图:     {output_dir / 'diagram.md'}")
    print(f"  中间产物:   {findings_dir}/")
    print(f"  大纲:       {output_dir / 'outline.json'}")
    print(f"{'='*60}")



# Module-level redirection for testing backwards compatibility (e.g. test_explore_synthesize.py)
import types
import utils
import caching
import llm_client

class ExploreSynthesizeModule(types.ModuleType):
    def __getattr__(self, name):
        if name == 'chunk_text':
            return utils.chunk_text
        if name == 'findings_cache_key':
            return caching.findings_cache_key
        if name == 'stable_json_sha256':
            return caching.stable_json_sha256
        if name == 'build_step_cache_meta':
            return caching.build_step_cache_meta
        if name == 'SECTIONS_CACHE_VERSION':
            return caching.SECTIONS_CACHE_VERSION
        if name == 'REPORT_CACHE_VERSION':
            return caching.REPORT_CACHE_VERSION
        if name == 'save_cached_sections':
            return caching.save_cached_sections
        if name == 'load_cached_sections':
            return caching.load_cached_sections
        if name == 'save_cached_report':
            return caching.save_cached_report
        if name == 'load_cached_report':
            return caching.load_cached_report
        if name in ('call_json', 'call_text', 'call_json_light'):
            return getattr(llm_client, name)
        raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

    def __setattr__(self, name, value):
        if name in ('call_json', 'call_text', 'call_json_light'):
            setattr(llm_client, name, value)
        super().__setattr__(name, value)

def _get_current_module():
    m = sys.modules.get(__name__)
    if m and m.__dict__ is globals():
        return m
    frame = sys._getframe()
    while frame:
        for val in list(frame.f_locals.values()):
            if type(val).__name__ == 'module' and getattr(val, '__dict__', None) is globals():
                return val
        frame = frame.f_back
    return None

mod = _get_current_module()
if mod:
    mod.__class__ = ExploreSynthesizeModule


if __name__ == "__main__":
    main()

