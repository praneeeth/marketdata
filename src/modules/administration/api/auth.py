"""Auth API: simple single-user JWT auth."""
import os
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.orm import Session
import jwt

from src.platform.persistence.database import get_db, SessionLocal
from src.platform.persistence.models import AppSettings

router = APIRouter()
security = HTTPBearer(auto_error=False)

# JWT config
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30

# Environment variable config (for Docker deployments)
ENV_AUTH_USERNAME = os.getenv("AUTH_USERNAME")
ENV_AUTH_PASSWORD = os.getenv("AUTH_PASSWORD")

# Setting keys
AUTH_USERNAME_KEY = "auth_username"
PASSWORD_HASH_KEY = "auth_password_hash"
JWT_SECRET_KEY = "jwt_secret"

# JWT secret cache
_jwt_secret: str | None = None


def get_jwt_secret() -> str:
    """Get the JWT secret (persisted in the database)."""
    global _jwt_secret
    if _jwt_secret:
        return _jwt_secret

    # Environment variable first
    if os.getenv("JWT_SECRET"):
        _jwt_secret = os.getenv("JWT_SECRET")
        return _jwt_secret

    # Read from the database, or generate on first use
    db = SessionLocal()
    try:
        setting = db.query(AppSettings).filter(AppSettings.key == JWT_SECRET_KEY).first()
        if setting:
            _jwt_secret = setting.value
        else:
            _jwt_secret = secrets.token_hex(32)
            db.add(AppSettings(key=JWT_SECRET_KEY, value=_jwt_secret, description="JWT signing key (auto-generated)"))
            db.commit()
        return _jwt_secret
    finally:
        db.close()


class LoginRequest(BaseModel):
    username: str
    password: str


class SetupRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    token: str
    expires_at: str


def hash_password(password: str) -> str:
    """Simple password hash."""
    return hashlib.sha256(password.encode()).hexdigest()


def create_token(expires_days: int = JWT_EXPIRE_DAYS) -> tuple[str, datetime]:
    """Create a JWT token."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=expires_days)
    payload = {
        "exp": expires_at,
        "iat": now,
        "sub": "user",
    }
    token = jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)
    return token, expires_at


def verify_token(token: str) -> bool:
    """Verify a JWT token."""
    try:
        jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        return True
    except jwt.ExpiredSignatureError:
        return False
    except jwt.InvalidTokenError:
        return False


def get_stored_username(db: Session) -> Optional[str]:
    """Get the stored username."""
    setting = db.query(AppSettings).filter(AppSettings.key == AUTH_USERNAME_KEY).first()
    return setting.value if setting else None


def set_stored_username(db: Session, username: str):
    """Set the username."""
    setting = db.query(AppSettings).filter(AppSettings.key == AUTH_USERNAME_KEY).first()
    if setting:
        setting.value = username
    else:
        setting = AppSettings(key=AUTH_USERNAME_KEY, value=username, description="Auth username")
        db.add(setting)
    db.commit()


def get_password_hash(db: Session) -> Optional[str]:
    """Get the stored password hash."""
    setting = db.query(AppSettings).filter(AppSettings.key == PASSWORD_HASH_KEY).first()
    return setting.value if setting else None


def set_password_hash(db: Session, password_hash: str):
    """Set the password hash."""
    setting = db.query(AppSettings).filter(AppSettings.key == PASSWORD_HASH_KEY).first()
    if setting:
        setting.value = password_hash
    else:
        setting = AppSettings(key=PASSWORD_HASH_KEY, value=password_hash, description="Auth password hash")
        db.add(setting)
    db.commit()


def init_auth_from_env(db: Session) -> bool:
    """Initialise auth from environment variables (for Docker deployments).

    Returns:
        True if initialized from env, False otherwise
    """
    if not ENV_AUTH_USERNAME or not ENV_AUTH_PASSWORD:
        return False

    # Don't overwrite an existing account
    if get_password_hash(db):
        return False

    # Create the account from environment variables
    set_stored_username(db, ENV_AUTH_USERNAME)
    set_password_hash(db, hash_password(ENV_AUTH_PASSWORD))
    return True


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
):
    """Verify the current user (used as a dependency)."""
    # Has a password been set?
    password_hash = get_password_hash(db)
    if not password_hash:
        # No password set: allow access (initial state)
        return None

    # Password set: the token must be verified
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not logged in",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_token(credentials.credentials):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return "user"


@router.get("/status")
async def auth_status(db: Session = Depends(get_db)):
    """Auth status."""
    password_hash = get_password_hash(db)
    return {
        "initialized": password_hash is not None,
    }


@router.post("/setup", response_model=TokenResponse)
async def setup_password(data: SetupRequest, db: Session = Depends(get_db)):
    """Set the username and password the first time."""
    if get_password_hash(db):
        raise HTTPException(400, "An account is already set up; please log in")

    if not data.username or len(data.username) < 2:
        raise HTTPException(400, "Username must be at least 2 characters")

    if len(data.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")

    set_stored_username(db, data.username)
    password_hash = hash_password(data.password)
    set_password_hash(db, password_hash)

    token, expires_at = create_token()
    return TokenResponse(token=token, expires_at=expires_at.isoformat())


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, db: Session = Depends(get_db)):
    """Log in."""
    stored_hash = get_password_hash(db)
    stored_username = get_stored_username(db)
    if not stored_hash or not stored_username:
        raise HTTPException(400, "Please set up an account first")

    if data.username != stored_username:
        raise HTTPException(401, "Wrong username or password")

    if hash_password(data.password) != stored_hash:
        raise HTTPException(401, "Wrong username or password")

    token, expires_at = create_token()
    return TokenResponse(token=token, expires_at=expires_at.isoformat())


@router.post("/change-password")
async def change_password(
    data: SetupRequest,
    db: Session = Depends(get_db),
    _: str = Depends(get_current_user),
):
    """Change the password."""
    if len(data.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")

    password_hash = hash_password(data.password)
    set_password_hash(db, password_hash)

    return {"message": "Password updated"}


@router.get("/me")
async def get_me(user: str = Depends(get_current_user)):
    """Current user info."""
    return {"user": user or "guest"}
