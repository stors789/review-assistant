import sys
import os
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

# Add review_assistant package root to sys.path to import zotero_reader
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Clear any mock that other test modules may have set
sys.modules.pop("review_assistant.zotero_reader", None)
from review_assistant.zotero_reader import ZoteroReader

class ZoteroReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.zotero_path = Path(self.temp_dir.name)
        self.db_path = self.zotero_path / "zotero.sqlite"

        # Create database and tables
        self.conn = sqlite3.connect(self.db_path)
        self.create_schema()
        self.conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_schema(self):
        cursor = self.conn.cursor()
        cursor.executescript("""
        CREATE TABLE collections (
            collectionID INTEGER PRIMARY KEY,
            collectionName TEXT,
            parentCollectionID INTEGER
        );
        CREATE TABLE items (
            itemID INTEGER PRIMARY KEY,
            key TEXT,
            dateAdded TEXT
        );
        CREATE TABLE collectionItems (
            itemID INTEGER,
            collectionID INTEGER,
            PRIMARY KEY (itemID, collectionID)
        );
        CREATE TABLE itemAttachments (
            itemID INTEGER PRIMARY KEY,
            parentItemID INTEGER,
            path TEXT,
            contentType TEXT
        );
        CREATE TABLE itemNotes (
            itemID INTEGER PRIMARY KEY,
            parentItemID INTEGER
        );
        CREATE TABLE fields (
            fieldID INTEGER PRIMARY KEY,
            fieldName TEXT
        );
        CREATE TABLE itemData (
            itemID INTEGER,
            fieldID INTEGER,
            valueID INTEGER,
            PRIMARY KEY (itemID, fieldID)
        );
        CREATE TABLE itemDataValues (
            valueID INTEGER PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE itemCreators (
            itemID INTEGER,
            creatorID INTEGER,
            orderIndex INTEGER,
            PRIMARY KEY (itemID, creatorID)
        );
        CREATE TABLE creators (
            creatorID INTEGER PRIMARY KEY,
            lastName TEXT,
            firstName TEXT
        );
        """)
        self.conn.commit()

    def insert_mock_data(self, conn):
        cursor = conn.cursor()
        
        # 1. Insert collection: "Theme > Subtheme"
        cursor.execute("INSERT INTO collections VALUES (1, 'Theme', NULL)")
        cursor.execute("INSERT INTO collections VALUES (2, 'Subtheme', 1)")

        # 2. Insert fields
        cursor.execute("INSERT INTO fields VALUES (1, 'title')")
        cursor.execute("INSERT INTO fields VALUES (2, 'date')")
        cursor.execute("INSERT INTO fields VALUES (3, 'publicationTitle')")
        cursor.execute("INSERT INTO fields VALUES (4, 'DOI')")

        # 3. Insert items
        cursor.execute("INSERT INTO items VALUES (101, 'ITEMKEY1', '2026-06-14 12:00:00')") # parent item
        cursor.execute("INSERT INTO items VALUES (102, 'ITEMKEY2', '2026-06-14 12:01:00')") # attachment item
        
        # Add to collectionItems
        cursor.execute("INSERT INTO collectionItems VALUES (101, 2)")

        # 4. Insert item data values
        cursor.execute("INSERT INTO itemDataValues VALUES (1, 'Hybrid Search in Retrieval-Augmented Generation')")
        cursor.execute("INSERT INTO itemDataValues VALUES (2, '2026')")
        cursor.execute("INSERT INTO itemDataValues VALUES (3, 'Journal of Neuroscience')")
        cursor.execute("INSERT INTO itemDataValues VALUES (4, '10.1234/jns.2026.01')")

        cursor.execute("INSERT INTO itemData VALUES (101, 1, 1)")
        cursor.execute("INSERT INTO itemData VALUES (101, 2, 2)")
        cursor.execute("INSERT INTO itemData VALUES (101, 3, 3)")
        cursor.execute("INSERT INTO itemData VALUES (101, 4, 4)")

        # 5. Insert creators
        cursor.execute("INSERT INTO creators VALUES (50, 'Smith', 'John')")
        cursor.execute("INSERT INTO itemCreators VALUES (101, 50, 0)")

        # 6. Insert attachment (PDF)
        cursor.execute("INSERT INTO itemAttachments VALUES (102, 101, 'storage:mock.pdf', 'application/pdf')")
        
        # Create attachment file on disk
        storage_dir = self.zotero_path / "storage" / "ITEMKEY2"
        storage_dir.mkdir(parents=True, exist_ok=True)
        (storage_dir / "mock.pdf").write_bytes(b"pdf contents")

        conn.commit()

    def test_list_collections(self):
        # Open DB, write data, then read it
        conn = sqlite3.connect(self.db_path)
        self.insert_mock_data(conn)
        conn.close()

        with ZoteroReader(str(self.zotero_path)) as reader:
            collections = reader.list_collections()
            self.assertEqual(len(collections), 1)
            self.assertEqual(collections[0]["name"], "Theme > Subtheme")
            self.assertEqual(collections[0]["total"], 1)
            self.assertEqual(collections[0]["has_attachment"], 1)

    def test_get_papers_and_items(self):
        conn = sqlite3.connect(self.db_path)
        self.insert_mock_data(conn)
        conn.close()

        with ZoteroReader(str(self.zotero_path)) as reader:
            items = reader.list_items("Theme > Subtheme")
            self.assertEqual(len(items), 1)
            item = items[0]
            self.assertEqual(item["title"], "Hybrid Search in Retrieval-Augmented Generation")
            self.assertEqual(item["authors"], "Smith, John")
            self.assertEqual(item["journal"], "Journal of Neuroscience")
            self.assertEqual(item["date"], "2026")
            self.assertEqual(item["doi"], "10.1234/jns.2026.01")
            self.assertTrue(item["pdf_available"])
            self.assertTrue(item["pdf_path"].endswith("mock.pdf"))

            papers = reader.get_papers("Theme > Subtheme")
            self.assertEqual(len(papers), 1)

    def test_resolve_pdf_path_cross_platform(self):
        with ZoteroReader(str(self.zotero_path)) as reader:
            # 1. Stored File
            storage_file = self.zotero_path / "storage" / "KEY1" / "test.pdf"
            storage_file.parent.mkdir(parents=True, exist_ok=True)
            storage_file.write_bytes(b"data")
            res = reader._resolve_pdf_path("KEY1", "storage:test.pdf")
            self.assertEqual(res, storage_file)

            # 2. Unix Absolute Path
            abs_file = (self.zotero_path / "abs_test.pdf").resolve()
            abs_file.write_bytes(b"data")
            res = reader._resolve_pdf_path("KEY1", str(abs_file))
            self.assertEqual(res, abs_file)

            # 3. Linked File (forward slash)
            linked_dir = self.zotero_path / "linked"
            linked_file = linked_dir / "subdir" / "test.pdf"
            linked_file.parent.mkdir(parents=True, exist_ok=True)
            linked_file.write_bytes(b"data")
            
            os.environ["ZOTERO_LINKED_BASE_DIR"] = str(linked_dir)
            try:
                res = reader._resolve_pdf_path("KEY1", "attachments:subdir/test.pdf")
                self.assertEqual(res, linked_file)

                # 4. Linked File (Windows backslash)
                res = reader._resolve_pdf_path("KEY1", "attachments:subdir\\test.pdf")
                self.assertEqual(res, linked_file)
            finally:
                os.environ.pop("ZOTERO_LINKED_BASE_DIR", None)

            # 5. Windows Style Absolute Path (mocked environment)
            from unittest.mock import patch, MagicMock
            with patch('review_assistant.zotero_reader.Path') as mock_path_cls:
                mock_path_instance = MagicMock()
                mock_path_instance.is_absolute.return_value = True
                mock_path_instance.exists.return_value = True
                mock_path_cls.return_value = mock_path_instance
                
                res = reader._resolve_pdf_path("KEY1", "C:\\path\\to\\file.pdf")
                mock_path_cls.assert_called_with("C:/path/to/file.pdf")
                self.assertEqual(res, mock_path_instance)

if __name__ == "__main__":
    unittest.main()
