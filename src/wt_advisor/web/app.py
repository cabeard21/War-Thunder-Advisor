from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from wt_advisor.domain.models import VehicleStatus
from wt_advisor.services.advisor import AdvisorService


class PresetInput(BaseModel):
    name: str
    slots: list[str] = Field(default_factory=list)
    required_vehicle_ids: list[str] = Field(default_factory=list)
    excluded_vehicle_ids: list[str] = Field(default_factory=list)
    expected_revision: str | int | None = None


class ContextInput(BaseModel):
    expected_revision: str | int
    selected_preset_id: str | None = None
    target_br: int | None = None
    required_vehicle_ids: list[str] = Field(default_factory=list)
    excluded_vehicle_ids: list[str] = Field(default_factory=list)


class StatusInput(BaseModel):
    status: VehicleStatus
    expected_revision: str | None = None
    expected_write_revision: str | None = None


def _dump(value: Any) -> Any:
    return (
        value.model_dump(mode="json")
        if hasattr(value, "model_dump")
        else (value.__dict__ if hasattr(value, "__dict__") else value)
    )


def create_app(service: AdvisorService | None = None) -> FastAPI:
    app = FastAPI(title="War Thunder Advisor", docs_url=None, redoc_url=None)
    resolved = service or AdvisorService.from_database(
        Path(os.environ.get("WT_ADVISOR_DB", "wt-advisor.sqlite"))
    )

    @app.exception_handler(ValueError)
    async def domain_error(_: Request, exc: ValueError) -> JSONResponse:
        message = str(exc)
        conflict = "revision changed" in message
        unsatisfiable = any(
            marker in message
            for marker in (
                "no eligible owned vehicles",
                "not enough eligible vehicles",
                "required vehicle is not eligible",
            )
        )
        code = "conflict" if conflict else (
            "unsatisfiable_constraints" if unsatisfiable else "validation"
        )
        return JSONResponse(
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "details": {
                        "action": (
                            "refresh current state and retry"
                            if conflict
                            else "relax exclusions, pins, or partial-lineup settings"
                            if unsatisfiable
                            else "correct the request fields and retry"
                        )
                    },
                }
            },
            status_code=409 if conflict else 422,
        )

    @app.exception_handler(LookupError)
    async def missing(_: Request, exc: LookupError) -> JSONResponse:
        return JSONResponse(
            content={"error": {"code": "not_found", "message": str(exc), "details": {}}},
            status_code=404,
        )

    @app.middleware("http")
    async def local_only(request: Request, call_next: Any) -> Any:
        host = request.headers.get("host", "").split(":")[0]
        if host not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "code": "host_rejected",
                        "message": "loopback host required",
                        "details": {},
                    }
                },
            )
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and host not in origin:
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": {
                            "code": "origin_rejected",
                            "message": "same-origin request required",
                            "details": {},
                        }
                    },
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/api/data-status")
    def data_status() -> dict[str, Any]:
        return {"data": resolved.data_status()}

    @app.get("/api/vehicles")
    def vehicles() -> dict[str, Any]:
        return {
            "data": [
                {
                    **view.vehicle.model_dump(mode="json"),
                    "battle_rating": view.br,
                    "roles": [role.value for role in view.roles],
                    "provenance": view.provenance,
                }
                for view in resolved.list_vehicles()
            ]
        }

    @app.get("/api/profiles/{profile_id}/progress")
    def progress(profile_id: str) -> dict[str, Any]:
        return {"data": _dump(resolved.get_user_progress(profile_id))}

    @app.patch("/api/profiles/{profile_id}/vehicles/{vehicle_id}")
    def set_status(profile_id: str, vehicle_id: str, body: StatusInput) -> dict[str, Any]:
        return {
            "data": _dump(
                resolved.set_user_vehicle_status(
                    profile_id, vehicle_id, body.status, expected_revision=body.expected_revision
                    , expected_write_revision=body.expected_write_revision
                )
            )
        }

    @app.get("/api/profiles/{profile_id}/presets")
    def presets(profile_id: str) -> dict[str, Any]:
        return {"data": [_dump(x) for x in resolved.list_presets(profile_id)]}

    @app.post("/api/profiles/{profile_id}/presets")
    def create_preset(profile_id: str, body: PresetInput) -> dict[str, Any]:
        return {
            "data": _dump(
                resolved.create_preset(
                    profile_id,
                    name=body.name,
                    slots=body.slots,
                    required_vehicle_ids=body.required_vehicle_ids,
                    excluded_vehicle_ids=body.excluded_vehicle_ids,
                )
            )
        }

    @app.get("/api/profiles/{profile_id}/context")
    def context(profile_id: str) -> dict[str, Any]:
        return {"data": _dump(resolved.get_advisor_context(profile_id))}

    @app.put("/api/profiles/{profile_id}/context")
    def update_context(profile_id: str, body: ContextInput) -> dict[str, Any]:
        return {"data": _dump(resolved.update_advisor_context(profile_id, **body.model_dump()))}

    @app.patch("/api/profiles/{profile_id}/presets/{preset_id}")
    def update_preset(profile_id: str, preset_id: str, body: PresetInput) -> dict[str, Any]:
        if body.expected_revision is None:
            raise ValueError("expected_revision is required for preset updates")
        preset = resolved.update_preset(
            profile_id,
            preset_id,
            name=body.name,
            slots=body.slots,
            required_vehicle_ids=body.required_vehicle_ids,
            excluded_vehicle_ids=body.excluded_vehicle_ids,
            expected_revision=str(body.expected_revision),
        )
        return {"data": _dump(preset)}

    @app.delete("/api/profiles/{profile_id}/presets/{preset_id}")
    def delete_preset(
        profile_id: str, preset_id: str, body: dict[str, str | int]
    ) -> dict[str, Any]:
        expected = body.get("expected_revision")
        if not expected:
            raise ValueError("expected_revision is required for preset deletes")
        resolved.delete_preset(profile_id, preset_id, expected_revision=str(expected))
        return {"data": {"deleted": True}}

    @app.post("/api/evaluate")
    def evaluate(body: dict[str, Any]) -> dict[str, Any]:
        if "vehicle_ids" in body:
            return {"data": _dump(resolved.analyze_lineup(body["vehicle_ids"]))}
        result = resolved.generate_lineups(
            profile_id=str(body.get("profile_id", "acceptance")),
            target_br=body.get("target_br"),
            hypothetical_owned=frozenset(body.get("hypothetical_owned", ())),
            required_vehicle_ids=frozenset(body["required_vehicle_ids"])
            if "required_vehicle_ids" in body
            else None,
            excluded_vehicle_ids=frozenset(body.get("excluded_vehicle_ids", ())),
        )
        return {"data": _dump(result)}

    @app.post("/api/evaluations")
    def evaluate_and_store(body: dict[str, Any]) -> dict[str, Any]:
        return {"data": resolved.evaluate_and_store(
            profile_id=str(body.get("profile_id", "acceptance")),
            target_br=body.get("target_br"),
            top_n=int(body.get("top_n", 10)),
            hypothetical_owned=frozenset(body.get("hypothetical_owned", ())),
            required_vehicle_ids=(
                frozenset(body["required_vehicle_ids"])
                if "required_vehicle_ids" in body else None
            ),
            excluded_vehicle_ids=frozenset(body.get("excluded_vehicle_ids", ())),
            allow_partial=bool(body.get("allow_partial", False)),
            preset_id=body.get("preset_id"),
        )}

    @app.post("/api/profiles/{profile_id}/evaluations/{evaluation_id}/reevaluate")
    def reevaluate(profile_id: str, evaluation_id: str) -> dict[str, Any]:
        return {"data": resolved.reevaluate_stored_evaluation(profile_id, evaluation_id)}

    @app.get("/api/profiles/{profile_id}/evaluations/{evaluation_id}")
    def stored_evaluation(profile_id: str, evaluation_id: str) -> dict[str, Any]:
        return {"data": resolved.get_stored_evaluation(profile_id, evaluation_id)}

    @app.post("/api/compare")
    def compare(body: dict[str, Any]) -> dict[str, Any]:
        return {"data": _dump(resolved.compare_lineups(body["lineup_a"], body["lineup_b"]))}

    @app.get("/api/profiles/{profile_id}/unlock-evaluations")
    def unlocks(profile_id: str) -> dict[str, Any]:
        return {"data": resolved.evaluate_next_unlocks_status(profile_id)}

    static = Path(__file__).with_name("static")
    if static.exists():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/")
        def dashboard() -> FileResponse:
            return FileResponse(static / "index.html")

    return app
