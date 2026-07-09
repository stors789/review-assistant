from fastapi import APIRouter
import tkinter as tk
from tkinter import filedialog
from pydantic import BaseModel

router = APIRouter()

@router.get("/select-file")
def select_file():
    root = tk.Tk()
    root.withdraw() # Hide the main window
    root.attributes('-topmost', True) # Bring dialog to front
    file_path = filedialog.askopenfilename(
        title="Select a PDF File",
        filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")]
    )
    root.destroy()
    return {"path": file_path}

@router.get("/select-env-file")
def select_env_file():
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    file_path = filedialog.askopenfilename(
        title="Select API Environment File",
        filetypes=[("Env files", "*.env"), ("All files", "*.*")]
    )
    root.destroy()
    return {"path": file_path}

@router.get("/select-folder")
def select_folder():
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    folder_path = filedialog.askdirectory(
        title="Select a Folder containing PDFs"
    )
    root.destroy()
    return {"path": folder_path}
