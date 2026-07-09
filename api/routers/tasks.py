from fastapi import APIRouter
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
import asyncio
from typing import Optional
import os
import sys
import json
from .settings import parse_env_file

router = APIRouter()

def get_process_env():
    env = os.environ.copy()
    settings = parse_env_file()
    env.update(settings)
    env["PYTHONUNBUFFERED"] = "1"
    return env

async def run_subprocess_and_stream(cmd: list[str], cwd: str = "/Users/eros/Documents/review-assistant"):
    env = get_process_env()
    
    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT
    )

    try:
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            text = line.decode(errors="replace").rstrip('\r\n')
            yield f"data: {json.dumps({'status': text})}\n\n"
            
        await process.wait()
        if process.returncode != 0:
            yield f"data: {json.dumps({'status': f'[Error] Process exited with code {process.returncode}'})}\n\n"
        yield "data: {\"status\": \"[System] Task completed.\"}\n\n"
    except asyncio.CancelledError:
        process.terminate()
        await process.wait()
        raise

class VerifyRequest(BaseModel):
    collection: str
    paragraph: str
    model: str = ""
    top: int = 3

@router.post("/verify")
async def verify_claim(req: VerifyRequest):
    cmd = [sys.executable, "-m", "review_assistant.claim_verify", req.collection, "--paragraph", req.paragraph, "--top", str(req.top)]
    if req.model:
        cmd.extend(["--model", req.model])
    return StreamingResponse(run_subprocess_and_stream(cmd), media_type="text/event-stream")

class BreakdownRequest(BaseModel):
    mode: str
    collection: Optional[str] = None
    item: Optional[str] = None
    local_path: Optional[str] = None
    output_path: Optional[str] = None
    auto_pdf: bool = False

@router.post("/breakdown")
async def paper_breakdown(req: BreakdownRequest):
    cmd = [sys.executable, "-m", "review_assistant.paper_breakdown"]
    
    if req.mode == "collection":
        if not req.collection:
            async def err(): yield "data: {\"status\": \"[Error] Missing collection name\"}\n\n"
            return StreamingResponse(err(), media_type="text/event-stream")
        cmd.extend(["--zotero-collection", req.collection])
    elif req.mode == "local":
        if not req.local_path:
            async def err(): yield "data: {\"status\": \"[Error] Missing local path\"}\n\n"
            return StreamingResponse(err(), media_type="text/event-stream")
        cmd.extend(["--input", req.local_path])
        
    if req.output_path:
        cmd.extend(["--output", req.output_path])
        
    if req.auto_pdf:
        cmd.append("--auto-pdf")
        
    return StreamingResponse(run_subprocess_and_stream(cmd), media_type="text/event-stream")

class SynthesizeRequest(BaseModel):
    mode: str
    collection: Optional[str] = None
    local_path: Optional[str] = None
    question: str
    output_path: Optional[str] = None
    auto_pdf: bool = False

@router.post("/synthesize")
async def paper_synthesize(req: SynthesizeRequest):
    cmd = [sys.executable, "-m", "review_assistant.explore_synthesize", "--question", req.question]
    
    if req.mode == "collection":
        if not req.collection:
            async def err(): yield "data: {\"status\": \"[Error] Missing collection name\"}\n\n"
            return StreamingResponse(err(), media_type="text/event-stream")
        cmd.append(req.collection)
    elif req.mode == "local":
        if not req.local_path:
            async def err(): yield "data: {\"status\": \"[Error] Missing local path\"}\n\n"
            return StreamingResponse(err(), media_type="text/event-stream")
        cmd.extend(["--input", req.local_path])
        
    if req.output_path:
        cmd.extend(["--output", req.output_path])
        
    if req.auto_pdf:
        cmd.append("--auto-pdf")
        
    return StreamingResponse(run_subprocess_and_stream(cmd), media_type="text/event-stream")
