from __future__ import annotations

from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .mia_license_v2 import LicenseServiceError, MiaLicenseService


class StrictModel(BaseModel):
    class Config:
        extra = "forbid"


class ChallengeRequest(StrictModel):
    tool: Literal["MIA"]
    action: Literal["verify", "migrate", "activate", "recover", "update_phone"]
    public_key: str = Field(min_length=80, max_length=2048)
    device_fingerprint: str = Field(min_length=64, max_length=64)


class ProofRequest(StrictModel):
    tool: Literal["MIA"]
    challenge_id: str = Field(min_length=36, max_length=36)
    challenge: str = Field(min_length=32, max_length=256)
    public_key: str = Field(min_length=80, max_length=2048)
    device_fingerprint: str = Field(min_length=64, max_length=64)
    signature: str = Field(min_length=40, max_length=256)


class VerifyRequest(ProofRequest):
    license_token: str = Field(min_length=32, max_length=8192)


class MigrateRequest(ProofRequest):
    key: Optional[str] = Field(default=None, max_length=96)
    device_id: str = Field(min_length=36, max_length=36)
    phone: Optional[str] = Field(default=None, max_length=32)
    hardware: Dict[str, str]
    legacy_keys: List[str] = Field(default_factory=list, max_length=32)
    legacy_hashes: List[str] = Field(default_factory=list, max_length=32)


class ActivateRequest(ProofRequest):
    key: Optional[str] = Field(default=None, max_length=96)
    device_id: str = Field(min_length=36, max_length=36)
    phone: str = Field(min_length=10, max_length=32)
    hardware: Dict[str, str]


class RecoverRequest(ProofRequest):
    phone: str = Field(min_length=10, max_length=32)
    hardware: Dict[str, str]


class UpdatePhoneRequest(ProofRequest):
    phone: str = Field(min_length=10, max_length=32)
    license_token: str = Field(min_length=32, max_length=8192)


def create_router(service: MiaLicenseService) -> APIRouter:
    router = APIRouter(prefix="/license/v2", tags=["MIA License V2"])

    def call(method, request: BaseModel):
        try:
            payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
            return method(payload)
        except LicenseServiceError as exc:
            raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)}) from exc

    @router.post("/challenge")
    def challenge(request: ChallengeRequest):
        return call(service.create_challenge, request)

    @router.post("/verify")
    def verify(request: VerifyRequest):
        return call(service.verify, request)

    @router.post("/migrate")
    def migrate(request: MigrateRequest):
        return call(service.migrate, request)

    @router.post("/activate")
    def activate(request: ActivateRequest):
        return call(service.activate, request)

    @router.post("/recover")
    def recover(request: RecoverRequest):
        return call(service.recover, request)

    @router.post("/update-phone")
    def update_phone(request: UpdatePhoneRequest):
        return call(service.update_phone, request)

    return router
