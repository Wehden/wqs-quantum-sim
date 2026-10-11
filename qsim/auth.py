import hashlib
import json
import secrets
from fastapi import Header, HTTPException
from . import settings


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def identity(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "An invitation API key is required", headers={"WWW-Authenticate": "Bearer"})
    hashed = digest(authorization[7:])
    try:
        users = json.loads(settings.TOKEN_FILE.read_text())
    except (OSError, ValueError):
        raise HTTPException(503, "Authentication configuration unavailable")
    for user in users:
        if user.get("active", False) and secrets.compare_digest(user["sha256"], hashed):
            return user["id"]
    raise HTTPException(401, "Invalid or revoked API key", headers={"WWW-Authenticate": "Bearer"})
