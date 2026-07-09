import os
from fastapi import APIRouter
import webview
from pydantic import BaseModel

router = APIRouter()

@router.get("/select-file")
def select_file():
    window = webview.active_window()
    if window:
        result = window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=('PDF files (*.pdf)', 'All files (*.*)')
        )
        if result:
            return {"path": result[0]}
    return {"path": ""}

@router.get("/select-env-file")
def select_env_file():
    window = webview.active_window()
    if window:
        result = window.create_file_dialog(
            webview.FOLDER_DIALOG,
            allow_multiple=False
        )
        if result:
            folder = result[0]
            env_file = os.path.join(folder, "api.env")
            return {"path": env_file}
    return {"path": ""}

@router.get("/select-folder")
def select_folder():
    window = webview.active_window()
    if window:
        result = window.create_file_dialog(
            webview.FOLDER_DIALOG,
            allow_multiple=False
        )
        if result:
            return {"path": result[0]}
    return {"path": ""}
