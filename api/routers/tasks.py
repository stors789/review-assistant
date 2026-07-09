from fastapi import APIRouter
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
import asyncio

router = APIRouter()

class VerifyRequest(BaseModel):
    collection: str
    paragraph: str
    model: str = ""
    top: int = 3

@router.post("/verify")
async def verify_claim(req: VerifyRequest):
    async def event_generator():
        yield "data: {\"status\": \"starting\"}\n\n"
        await asyncio.sleep(1)
        yield "data: {\"status\": \"finished\"}\n\n"
        
    return StreamingResponse(event_generator(), media_type="text/event-stream")

class BreakdownRequest(BaseModel):
    collection: str

@router.post("/breakdown")
async def paper_breakdown(req: BreakdownRequest):
    async def event_generator():
        yield "data: {\"status\": \"starting breakdown...\"}\n\n"
        await asyncio.sleep(1)
        yield "data: {\"status\": \"finished breakdown\"}\n\n"
        
    return StreamingResponse(event_generator(), media_type="text/event-stream")

class SynthesizeRequest(BaseModel):
    collection: str
    question: str

@router.post("/synthesize")
async def paper_synthesize(req: SynthesizeRequest):
    async def event_generator():
        yield "data: {\"status\": \"starting synthesis...\"}\n\n"
        await asyncio.sleep(1)
        yield "data: {\"status\": \"finished synthesis\"}\n\n"
        
    return StreamingResponse(event_generator(), media_type="text/event-stream")

