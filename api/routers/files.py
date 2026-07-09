import os
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

class ReadFileRequest(BaseModel):
    path: str

@router.post("/read")
def read_file(req: ReadFileRequest):
    if not os.path.exists(req.path):
        return {"error": "File not found", "content": ""}
    
    try:
        with open(req.path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"content": content}
    except Exception as e:
        return {"error": str(e), "content": ""}
