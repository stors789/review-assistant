from fastapi import APIRouter, HTTPException, Query
from typing import List, Dict, Any, Optional
import os
from review_assistant.zotero_reader import ZoteroReader
from review_assistant.config import get_zotero_dir

router = APIRouter()

@router.get("/collections")
def list_collections(zotero_dir: Optional[str] = None):
    try:
        with ZoteroReader(zotero_dir=zotero_dir or get_zotero_dir()) as reader:
            cols = reader.list_collections()
            return {"collections": cols}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/collections/{collection_name:path}/items")
def list_items(collection_name: str, pdf_only: bool = False, zotero_dir: Optional[str] = None):
    try:
        with ZoteroReader(zotero_dir=zotero_dir or get_zotero_dir()) as reader:
            items = reader.list_items(collection_name)
            if pdf_only:
                items = [it for it in items if it.get("pdf_available")]
            return {"items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
