from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import re
import os

router = APIRouter()
ENV_FILE = "/Users/eros/Documents/api.env"

# Regex to match export statements: export KEY="VALUE" or KEY=VALUE
ENV_LINE_REGEX = re.compile(r'^\s*(?:export\s+)?([A-Za-z0-9_]+)=([\'"]?)(.*?)\2\s*$')

def parse_env_file() -> dict:
    if not os.path.exists(ENV_FILE):
        return {}
    settings = {}
    with open(ENV_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            match = ENV_LINE_REGEX.match(line)
            if match:
                key = match.group(1)
                val = match.group(3)
                settings[key] = val
    return settings

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
        if not os.path.exists(ENV_FILE):
            lines = []
        else:
            with open(ENV_FILE, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        
        new_settings = payload.settings
        updated_keys = set()
        
        new_lines = []
        for line in lines:
            match = ENV_LINE_REGEX.match(line)
            if match:
                key = match.group(1)
                if key in new_settings:
                    # Update line
                    is_export = "export " if "export " in line else ""
                    new_val = new_settings[key]
                    new_lines.append(f'{is_export}{key}="{new_val}"\n')
                    updated_keys.add(key)
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
                
        # Append keys that were not found in the file
        for key, val in new_settings.items():
            if key not in updated_keys:
                new_lines.append(f'export {key}="{val}"\n')
                
        with open(ENV_FILE, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
            
        return {"status": "success", "settings": parse_env_file()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
