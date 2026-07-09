from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import re
import os
import json

router = APIRouter()
CONFIG_FILE = os.path.expanduser("~/.review_assistant_config.json")

def get_env_file_path() -> str:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                return data.get("env_path", "/Users/eros/Documents/api.env")
        except Exception:
            pass
    return "/Users/eros/Documents/api.env"

def set_env_file_path(path: str):
    data = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
        except Exception:
            pass
    data["env_path"] = path
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f)

# Regex to match export statements: export KEY="VALUE" or KEY=VALUE, optionally followed by comments
ENV_LINE_REGEX = re.compile(r'^\s*(?:export\s+)?([A-Za-z0-9_]+)=([\'"]?)(.*?)\2(\s+#.*)?\s*$')

def parse_env_file() -> dict:
    env_file = get_env_file_path()
    if not os.path.exists(env_file):
        return {}
    settings = {}
    with open(env_file, 'r', encoding='utf-8') as f:
        for line in f:
            match = ENV_LINE_REGEX.match(line)
            if match:
                key = match.group(1)
                val = match.group(3)
                settings[key] = val
    return settings

@router.get("/env-path")
def get_env_path():
    return {"path": get_env_file_path()}

class EnvPathUpdate(BaseModel):
    path: str

@router.post("/env-path")
def update_env_path(payload: EnvPathUpdate):
    set_env_file_path(payload.path)
    return {"status": "success", "path": payload.path}

@router.get("/")
def get_settings():
    try:
        return {"settings": parse_env_file()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class SettingsUpdate(BaseModel):
    settings: dict

@router.post("/")
def update_settings(payload: SettingsUpdate):
    try:
        env_file = get_env_file_path()
        if not os.path.exists(env_file):
            lines = []
        else:
            with open(env_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        
        new_settings = payload.settings
        updated_keys = set()
        
        new_lines = []
        for line in lines:
            match = ENV_LINE_REGEX.match(line)
            if match:
                key = match.group(1)
                quote = match.group(2) or '"'
                trailing_comment = match.group(4) or ''
                if key in new_settings:
                    # Update line
                    is_export = "export " if "export " in line else ""
                    new_val = new_settings[key]
                    new_lines.append(f'{is_export}{key}={quote}{new_val}{quote}{trailing_comment}\n')
                    updated_keys.add(key)
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
                
        # Append keys that were not found in the file
        for key, val in new_settings.items():
            if key not in updated_keys:
                new_lines.append(f'export {key}="{val}"\n')
                
        with open(env_file, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
            
        # Update running FastAPI process environment variables immediately
        os.environ.update(new_settings)
            
        return {"status": "success", "settings": parse_env_file()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
