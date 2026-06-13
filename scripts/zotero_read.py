#!/usr/bin/env python3
"""简单查询 Zotero 论文集的 PDF 路径（CLI 工具）。"""
import argparse
from zotero_reader import ZoteroReader


def main():
    parser = argparse.ArgumentParser(description="查询 Zotero 论文集的 PDF 存储路径")
    parser.add_argument("collection", nargs="?", help="论文集名称（不指定则列出全部）")
    parser.add_argument("--list", "-l", action="store_true", help="列出所有论文集概览")
    parser.add_argument("--pdf-only", action="store_true", help="只显示有 PDF 的文献")
    args = parser.parse_args()

    with ZoteroReader() as reader:
        if args.list or not args.collection:
            cols = reader.list_collections()
            print(f"{'论文集':<20} {'总数':>5} {'有PDF':>5} {'缺PDF':>5}")
            print("-" * 40)
            for c in cols:
                print(f"{c['name']:<20} {c['total']:>5} {c['has_attachment']:>5} {c['missing']:>5}")
            if args.collection is None:
                return
            print()

        if args.collection:
            items = reader.list_items(args.collection)
            if args.pdf_only:
                items = [it for it in items if it["pdf_available"]]
            have = sum(1 for it in items if it["pdf_available"])
            print(f"「{args.collection}」: {len(items)} 篇, {have} 有PDF\n")
            for it in items:
                icon = "[有]" if it["pdf_available"] else "[缺]"
                print(f"{icon} {it['title'][:80]}")
                if it["authors"]:
                    print(f"    {it['authors'][:80]}")
                if it["journal"]:
                    print(f"    {it['journal']} | {it['date']}")
                if it["doi"]:
                    print(f"    DOI: {it['doi']}")
                if it["pdf_available"]:
                    print(f"    {it['pdf_path']}")
                print()


if __name__ == "__main__":
    main()
