from fastapi import APIRouter, Request

from app.schemas.health import ApiInfo

router = APIRouter(prefix="/api", tags=["info"])


@router.get("/v1", response_model=ApiInfo)
def api_info(request: Request) -> ApiInfo:
    return ApiInfo(name=request.app.state.settings.service_name, version=request.app.version)
