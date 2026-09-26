"""System self-check API."""

from fastapi import APIRouter, Query

from src.modules.administration.selfcheck import list_selfcheck_items, run_selfcheck

router = APIRouter()


@router.get("/selfcheck")
async def selfcheck(
    notify_send: bool = Query(False, description="Really send a test notification (by default only the config is validated)"),
    list_only: bool = Query(False, alias="list", description="Only list the items to check, without probing (the frontend renders the list first)"),
    keys: str | None = Query(None, description="Comma-separated; only probe these keys (the frontend updates progress item by item)"),
):
    """Check data sources / AI / notifications in one go.

    - `?list=1`: return only the identities of the items to check `{items:[{category,key,name}]}`, without probing.
    - `?keys=ds:1,ai:2`: probe only these items (progress item by item).
    - no parameters: probe everything.
    """
    if list_only:
        return {"items": list_selfcheck_items()}
    key_list = [k for k in keys.split(",") if k] if keys else None
    return await run_selfcheck(notify_send=notify_send, keys=key_list)
