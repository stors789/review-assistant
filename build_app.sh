#!/bin/bash

# Ensure frontend is built
echo "Building frontend..."
cd web
npm run build
cd ..

# Build with PyInstaller
echo "Packaging desktop app with PyInstaller..."
pyinstaller --noconfirm \
    --name "Review Assistant" \
    --windowed \
    --add-data "web/dist:web/dist" \
    --hidden-import "uvicorn.logging" \
    --hidden-import "uvicorn.loops" \
    --hidden-import "uvicorn.loops.auto" \
    --hidden-import "uvicorn.protocols" \
    --hidden-import "uvicorn.protocols.http" \
    --hidden-import "uvicorn.protocols.http.auto" \
    --hidden-import "uvicorn.protocols.websockets" \
    --hidden-import "uvicorn.protocols.websockets.auto" \
    --hidden-import "uvicorn.lifespan" \
    --hidden-import "uvicorn.lifespan.on" \
    --hidden-import "websockets" \
    --hidden-import "pydantic" \
    app.py

echo "Build complete! Check the dist/ folder for 'Review Assistant.app'"
