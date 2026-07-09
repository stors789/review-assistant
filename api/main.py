from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routers import zotero, tasks, system

app = FastAPI(
    title="Review Assistant API",
    description="Backend API for the Zotero Literature Review Assistant",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # For development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(zotero.router, prefix="/api/zotero", tags=["Zotero"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["Tasks"])
app.include_router(system.router, prefix="/api/system", tags=["System"])

@app.get("/api/health")
def health_check():
    return {"status": "ok"}
