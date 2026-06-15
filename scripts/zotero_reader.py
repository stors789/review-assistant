#!/usr/bin/env python3
"""Zotero 数据库读取器，支持按论文集查询文献元数据与 PDF 路径。"""
import os
import re
import sqlite3
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile


class ZoteroReader:
    def __init__(self, zotero_dir: str | None = None):
        if zotero_dir is None:
            zotero_dir = os.environ.get("ZOTERO_DIR") or os.path.expanduser("~/Zotero")
        self.zotero_dir = Path(zotero_dir)
        self.db_path = self.zotero_dir / "zotero.sqlite"
        self._tmp_path: str | None = None
        self._conn: sqlite3.Connection | None = None

    def __enter__(self):
        db_str = str(self.db_path)
        # Try to connect directly in read-only mode first
        try:
            self._conn = sqlite3.connect(f"file:{db_str}?mode=ro", uri=True)
            self._conn.execute("SELECT 1 FROM items LIMIT 1").fetchall()
            self._tmp_path = None
            return self
        except sqlite3.Error:
            if self._conn:
                self._conn.close()
                self._conn = None

        # Fallback: copy the database to a temporary file
        with NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
            self._tmp_path = f.name
        shutil.copyfile(db_str, self._tmp_path)

        wal_path = str(self.db_path) + "-wal"
        shm_path = str(self.db_path) + "-shm"
        if os.path.exists(wal_path):
            shutil.copyfile(wal_path, self._tmp_path + "-wal")
        if os.path.exists(shm_path):
            shutil.copyfile(shm_path, self._tmp_path + "-shm")

        self._conn = sqlite3.connect(self._tmp_path)
        return self

    def __exit__(self, *args):
        if self._conn:
            self._conn.close()
            self._conn = None
        if self._tmp_path:
            try:
                os.unlink(self._tmp_path)
                for ext in ("-wal", "-shm"):
                    p = self._tmp_path + ext
                    if os.path.exists(p):
                        os.unlink(p)
            except OSError:
                pass
            self._tmp_path = None

    def _resolve_pdf_path(self, pdf_key: str, path: str) -> Path | None:
        """Resolve the actual physical file path of a Zotero PDF attachment."""
        if not path:
            return None

        # Normalize Windows backslashes to forward slashes
        path = path.replace("\\", "/")


        # 1. Stored File (storage:filename.pdf)
        if path.startswith("storage:"):
            filename = path.replace("storage:", "", 1)
            full = self.zotero_dir / "storage" / pdf_key / filename
            if full.exists():
                return full
            return None

        # 2. Linked File (attachments:relative/path.pdf)
        if path.startswith("attachments:"):
            filename = path.replace("attachments:", "", 1)
            base_dir = os.environ.get("ZOTERO_LINKED_BASE_DIR")
            if base_dir:
                full = Path(base_dir) / filename
                if full.exists():
                    return full
            return None

        # 3. Absolute path (e.g. /path/to/file.pdf or C:\path\to\file.pdf)
        try:
            full = Path(path)
            if full.is_absolute() and full.exists():
                return full
        except Exception:
            pass

        return None

    def _query(self, sql: str, params=None):
        return self._conn.execute(sql, params or []).fetchall()

    def _parent_item_ids(self, collection_name: str) -> list[int]:
        """获取论文集中作为「父条目」（非附件/笔记）的 itemID 列表。
        collection_name 支持完整路径（如 '电波 > AMPK'）或末级名称。"""
        candidate_sql = """
        SELECT c.collectionID
        FROM collections c
        WHERE c.collectionName = ?
        """
        leaf = collection_name.split(" > ")[-1]
        target_ids: set[int] = set()

        for (cid,) in self._query(candidate_sql, (leaf,)):
            if " > " in collection_name:
                if self._resolve_path(cid) == collection_name:
                    target_ids.add(cid)
            else:
                target_ids.add(cid)

        if not target_ids:
            return []

        placeholders = ",".join("?" * len(target_ids))
        sql = f"""
        SELECT DISTINCT i.itemID
        FROM collectionItems ci
        JOIN items i ON ci.itemID = i.itemID
        WHERE ci.collectionID IN ({placeholders})
          AND i.itemID NOT IN (
            SELECT ia.itemID FROM itemAttachments ia WHERE ia.parentItemID IS NOT NULL
          )
          AND i.itemID NOT IN (
            SELECT n.itemID FROM itemNotes n WHERE n.parentItemID IS NOT NULL
          )
        ORDER BY i.dateAdded
        """
        return [row[0] for row in self._query(sql, list(target_ids))]

    def _resolve_path(self, collection_id: int) -> str:
        """根据 collectionID 反查完整层级路径。"""
        rows = self._query(
            """WITH RECURSIVE tree AS (
               SELECT collectionID, collectionName, parentCollectionID, 0 AS depth
               FROM collections WHERE collectionID=?
               UNION ALL
               SELECT c.collectionID, c.collectionName, c.parentCollectionID, tree.depth + 1
               FROM collections c JOIN tree ON c.collectionID = tree.parentCollectionID
            ) SELECT collectionName FROM tree ORDER BY depth DESC""",
            (collection_id,),
        )
        return " > ".join(r[0] for r in rows)

    def list_collections(self) -> list[dict]:
        """列出所有论文集的文献数及 PDF 覆盖情况（含完整层级路径，校验文件实际存在）。"""
        # 1. 获取层级树和每个集合的 itemID 列表
        tree_sql = """
        WITH RECURSIVE tree AS (
            SELECT collectionID, collectionName, parentCollectionID,
                   CAST(collectionName AS TEXT) AS path
            FROM collections WHERE parentCollectionID IS NULL
            UNION ALL
            SELECT c.collectionID, c.collectionName, c.parentCollectionID,
                   t.path || ' > ' || c.collectionName
            FROM collections c JOIN tree t ON c.parentCollectionID = t.collectionID
        )
        SELECT tree.path, tree.collectionID
        FROM tree
        ORDER BY tree.path
        """
        tree_rows = self._query(tree_sql)
        if not tree_rows:
            return []

        # 2. 收集所有父条目 ID → 集合映射
        all_item_ids: set[int] = set()
        col_items: dict[int, list[int]] = {}  # collectionID -> [itemIDs]

        for _, col_id in tree_rows:
            item_ids = self._parent_item_ids_by_id(col_id)
            if item_ids:
                col_items[col_id] = item_ids
                all_item_ids.update(item_ids)

        if not all_item_ids:
            return []

        # 3. 批量查询所有 PDF 附件，校验文件存在
        placeholders = ",".join("?" * len(all_item_ids))
        pdf_rows = self._query(
            f"""
            SELECT ia.parentItemID, att.key, ia.path
            FROM itemAttachments ia
            JOIN items att ON ia.itemID = att.itemID
            WHERE ia.parentItemID IN ({placeholders})
              AND ia.contentType = 'application/pdf'
            """,
            list(all_item_ids),
        )

        exists: set[int] = set()
        for parent_id, pdf_key, path in pdf_rows:
            full = self._resolve_pdf_path(pdf_key, path)
            if full:
                exists.add(parent_id)

        # 4. 统计每个集合
        results = []
        for path, col_id in tree_rows:
            item_ids = col_items.get(col_id, [])
            total = len(item_ids)
            if total == 0:
                continue
            has = sum(1 for iid in item_ids if iid in exists)
            results.append({
                "name": path,
                "total": total,
                "has_attachment": has,
                "missing": total - has,
            })
        return results

    def _parent_item_ids_by_id(self, collection_id: int) -> list[int]:
        """直接按 collectionID 获取父条目 ID。"""
        return [row[0] for row in self._query(
            """SELECT DISTINCT i.itemID
            FROM collectionItems ci
            JOIN items i ON ci.itemID = i.itemID
            WHERE ci.collectionID = ?
              AND i.itemID NOT IN (
                SELECT ia.itemID FROM itemAttachments ia WHERE ia.parentItemID IS NOT NULL
              )
              AND i.itemID NOT IN (
                SELECT n.itemID FROM itemNotes n WHERE n.parentItemID IS NOT NULL
              )
            ORDER BY i.dateAdded""",
            (collection_id,),
        )]

    def list_items(self, collection_name: str) -> list[dict]:
        """列出指定论文集的全部文献及元数据、PDF 状态。"""
        return self._fetch_items(collection_name)

    def get_papers(self, collection_name: str) -> list[dict]:
        """仅返回有 PDF 的文献。"""
        return [it for it in self._fetch_items(collection_name) if it["pdf_available"]]

    def _fetch_items(self, collection_name: str) -> list[dict]:
        item_ids = self._parent_item_ids(collection_name)
        if not item_ids:
            return []

        placeholders = ",".join("?" * len(item_ids))

        field_rows = self._query(
            f"""
            SELECT id.itemID, f.fieldName, idv.value
            FROM itemData id
            JOIN itemDataValues idv ON id.valueID = idv.valueID
            JOIN fields f ON id.fieldID = f.fieldID
            WHERE id.itemID IN ({placeholders})
              AND f.fieldName IN ('title', 'date', 'publicationTitle', 'DOI')
            """,
            item_ids,
        )

        creator_rows = self._query(
            f"""
            SELECT ic.itemID, c.lastName, c.firstName, ic.orderIndex
            FROM itemCreators ic
            JOIN creators c ON ic.creatorID = c.creatorID
            WHERE ic.itemID IN ({placeholders})
            ORDER BY ic.itemID, ic.orderIndex
            """,
            item_ids,
        )

        pdf_rows = self._query(
            f"""
            SELECT ia.parentItemID, att.key, ia.path
            FROM itemAttachments ia
            JOIN items att ON ia.itemID = att.itemID
            WHERE ia.parentItemID IN ({placeholders})
              AND ia.contentType = 'application/pdf'
            """,
            item_ids,
        )

        fields_map: dict[int, dict[str, str]] = {}
        for item_id, fname, fval in field_rows:
            fields_map.setdefault(item_id, {})[fname] = fval

        creators_map: dict[int, list[str]] = {}
        for item_id, last, first, idx in creator_rows:
            name = f"{last}, {first}" if first else last
            creators_map.setdefault(item_id, []).append((idx, name))

        pdf_map: dict[int, str] = {}
        for parent_id, pdf_key, path in pdf_rows:
            full = self._resolve_pdf_path(pdf_key, path)
            if full:
                pdf_map[parent_id] = str(full)

        def strip_html(text: str) -> str:
            return re.sub(r"<[^>]+>", "", text).strip() if text else ""

        results = []
        for item_id in item_ids:
            fields = fields_map.get(item_id, {})
            authors_list = sorted(creators_map.get(item_id, []), key=lambda x: x[0])
            pdf_path = pdf_map.get(item_id, "")
            results.append({
                "title": strip_html(fields.get("title", "")),
                "authors": "; ".join(name for _, name in authors_list),
                "journal": strip_html(fields.get("publicationTitle", "")),
                "date": strip_html(fields.get("date", "")),
                "doi": strip_html(fields.get("DOI", "")),
                "pdf_available": bool(pdf_path),
                "pdf_path": pdf_path,
            })
        return results
