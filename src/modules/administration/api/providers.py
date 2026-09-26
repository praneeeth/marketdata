import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from src.platform.security.secrets import keep_unless_masked, mask_secret
from src.platform.ai.ai_client import AIClient
from src.platform.persistence.database import get_db
from src.platform.persistence.models import AIModel, AIService

router = APIRouter()


# --- Service ---


class ServiceCreate(BaseModel):
    name: str
    base_url: str
    api_key: str = ""


class ServiceUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None


class ModelResponse(BaseModel):
    id: int
    name: str
    service_id: int
    model: str
    is_default: bool

    class Config:
        from_attributes = True


class ServiceResponse(BaseModel):
    id: int
    name: str
    base_url: str
    api_key: str
    models: list[ModelResponse] = []

    class Config:
        from_attributes = True


@router.get("/services", response_model=list[ServiceResponse])
def list_services(db: Session = Depends(get_db)):
    services = db.query(AIService).order_by(AIService.id).all()
    return [_service_to_response(s) for s in services]


def _service_to_response(service: AIService) -> dict:
    return {
        "id": service.id,
        "name": service.name,
        "base_url": service.base_url,
        "api_key": mask_secret(service.api_key),
        "models": [
            {
                "id": m.id,
                "name": m.name,
                "service_id": m.service_id,
                "model": m.model,
                "is_default": m.is_default,
            }
            for m in service.models
        ],
    }


@router.post("/services", response_model=ServiceResponse)
def create_service(body: ServiceCreate, db: Session = Depends(get_db)):
    service = AIService(**body.model_dump())
    db.add(service)
    db.commit()
    db.refresh(service)
    return _service_to_response(service)


@router.put("/services/{service_id}", response_model=ServiceResponse)
def update_service(service_id: int, body: ServiceUpdate, db: Session = Depends(get_db)):
    service = db.query(AIService).filter(AIService.id == service_id).first()
    if not service:
        raise HTTPException(404, "AI provider not found")

    for key, value in body.model_dump(exclude_unset=True).items():
        if key == "api_key":
            value = keep_unless_masked(service.api_key, value)
        setattr(service, key, value)

    db.commit()
    db.refresh(service)
    return _service_to_response(service)


@router.delete("/services/{service_id}")
def delete_service(service_id: int, db: Session = Depends(get_db)):
    service = db.query(AIService).filter(AIService.id == service_id).first()
    if not service:
        raise HTTPException(404, "AI provider not found")
    db.delete(service)
    db.commit()
    return {"ok": True}


# --- Model ---


class ModelCreate(BaseModel):
    name: str = ""
    service_id: int
    model: str
    is_default: bool = False


class ModelUpdate(BaseModel):
    name: str | None = None
    service_id: int | None = None
    model: str | None = None
    is_default: bool | None = None


class BatchModelItem(BaseModel):
    name: str = ""
    model: str
    is_default: bool = False


class BatchModelCreate(BaseModel):
    models: list[BatchModelItem] = []


@router.get("/models", response_model=list[ModelResponse])
def list_models(db: Session = Depends(get_db)):
    return db.query(AIModel).order_by(AIModel.id).all()


@router.post("/models", response_model=ModelResponse)
def create_model(body: ModelCreate, db: Session = Depends(get_db)):
    service = db.query(AIService).filter(AIService.id == body.service_id).first()
    if not service:
        raise HTTPException(400, "AI provider not found")

    if body.is_default:
        db.query(AIModel).update({"is_default": False})

    data = body.model_dump()
    if not data["name"]:
        data["name"] = data["model"]
    model = AIModel(**data)
    db.add(model)
    db.commit()
    db.refresh(model)
    return model


@router.put("/models/{model_id}", response_model=ModelResponse)
def update_model(model_id: int, body: ModelUpdate, db: Session = Depends(get_db)):
    model = db.query(AIModel).filter(AIModel.id == model_id).first()
    if not model:
        raise HTTPException(404, "AI model not found")

    data = body.model_dump(exclude_unset=True)
    if data.get("is_default"):
        db.query(AIModel).update({"is_default": False})

    for key, value in data.items():
        setattr(model, key, value)

    db.commit()
    db.refresh(model)
    return model


@router.delete("/models/{model_id}")
def delete_model(model_id: int, db: Session = Depends(get_db)):
    model = db.query(AIModel).filter(AIModel.id == model_id).first()
    if not model:
        raise HTTPException(404, "AI model not found")
    db.delete(model)
    db.commit()
    return {"ok": True}


@router.post("/models/{model_id}/test")
async def test_model(model_id: int, db: Session = Depends(get_db)):
    model = db.query(AIModel).filter(AIModel.id == model_id).first()
    if not model:
        raise HTTPException(404, "AI model not found")

    service = db.query(AIService).filter(AIService.id == model.service_id).first()
    if not service:
        raise HTTPException(400, "The linked provider doesn't exist")

    try:
        client = AIClient(
            base_url=service.base_url,
            api_key=service.api_key,
            model=model.model,
        )
        # The connectivity test sends no temperature: some models (e.g. o1/claude-opus) reject it,
        # and omitting it is safe for every model, so a temperature error can't make a working model look broken.
        reply = await client.chat(
            system_prompt="You are a helpful assistant.",
            user_content="Say 'OK' in one word.",
            temperature=None,
        )
        return {"ok": True, "reply": reply.strip()}
    except Exception as e:
        raise HTTPException(400, f"Test failed: {e}")


@router.post("/services/{service_id}/discover-models")
async def discover_models(service_id: int, db: Session = Depends(get_db)):
    service = db.query(AIService).filter(AIService.id == service_id).first()
    if not service:
        raise HTTPException(404, "AI provider not found")
    try:
        client = AIClient(base_url=service.base_url, api_key=service.api_key)
        models = await client.list_models()
        return {"models": models}
    except Exception as e:
        raise HTTPException(400, f"Model discovery failed: {e}")


def _batch_add_models_once(service_id: int, body: BatchModelCreate, db: Session):
    service = db.query(AIService).filter(AIService.id == service_id).first()
    if not service:
        raise HTTPException(404, "AI provider not found")

    existing = {m.model for m in service.models}
    added = 0
    for item in body.models:
        if not item.model or item.model in existing:
            continue
        if item.is_default:
            db.query(AIModel).update({"is_default": False})
        db.add(
            AIModel(
                name=item.name or item.model,
                service_id=service_id,
                model=item.model,
                is_default=item.is_default,
            )
        )
        existing.add(item.model)
        added += 1
    db.commit()
    return {"added": added}


@router.post("/services/{service_id}/models/batch")
def batch_add_models(
    service_id: int, body: BatchModelCreate, db: Session = Depends(get_db)
):
    """Write models in bulk; retry briefly on local SQLite contention and return a readable error quickly."""
    for attempt in range(3):
        try:
            return _batch_add_models_once(service_id, body, db)
        except OperationalError as exc:
            db.rollback()
            message = str(exc).lower()
            locked = (
                "database is locked" in message or "database table is locked" in message
            )
            if not locked or attempt == 2:
                if locked:
                    raise HTTPException(409, "The database is busy; please try again shortly.") from exc
                raise
            time.sleep(0.05 * (attempt + 1))
