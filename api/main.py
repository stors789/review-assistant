from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from .routers import zotero, tasks, system, settings, files
import os

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
app.include_router(settings.router, prefix="/api/settings", tags=["Settings"])
app.include_router(files.router, prefix="/api/files", tags=["Files"])

@app.get("/api/health")
def health_check():
    return {"status": "ok"}

# Serve the compiled React frontend
# Determine path relative to this file or current working directory
dist_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web", "dist")
if not os.path.exists(dist_path):
    dist_path = os.path.join(os.getcwd(), "web", "dist")

if os.path.exists(dist_path):
    app.mount("/assets", StaticFiles(directory=os.path.join(dist_path, "assets")), name="assets")

    # Serve index.html for all other routes to support React Router SPA
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        index_file = os.path.join(dist_path, "index.html")
        if os.path.exists(index_file):
            return FileResponse(index_file)
        return {"error": "Frontend not built"}
