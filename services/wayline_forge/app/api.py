"""Learner-safe FastAPI transport for the Wayline application facade.

This module owns HTTP concerns only.  It parses duplicate-preserving request
data, applies the launch-scoped loopback policy, resolves a profile from the
authenticated session on the server side, and delegates every product decision
to the injected transport-neutral facade.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import inspect
import json
import re
from typing import Any, Protocol

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHttpException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from services.wayline_forge.app.contracts import (
    AssistedRouteComplete,
    AssistedRouteCompleted,
    AssistedRouteBatch,
    AssistedRoutePrepare,
    AssistedRoutePrepared,
    BattleComplete,
    BattleCompleted,
    BattleQuizRequest,
    BossGateResult,
    DuplicateJsonKeyError,
    FinalQuizResult,
    InitialSubmission,
    InitialSubmissionResult,
    ProfileCreate,
    ProfileCreated,
    ProfileExportV1,
    PublicError,
    PublicErrorCode,
    PublicQuizBatch,
    QuizSnapshot,
    RevivedCombatComplete,
    RevivedCombatCompleted,
    RevisionSubmission,
    RuntimeState,
    SealTrialComplete,
    SealTrialCompleted,
    SealTrialPrepare,
    SealTrialPrepared,
    SecondWindComplete,
    SecondWindCompleted,
    SecondWindStart,
    SecondWindStarted,
    SessionCreate,
    SessionCreated,
    StrictModel,
    WorldActivate,
    WorldActivated,
    parse_public_json,
)
from services.wayline_forge.app.loopback_security import (
    LaunchSecurityPolicy,
    SecurityRejectionCode,
)
from services.wayline_forge.app.progression import (
    AssistedRouteCompletionRequest,
    AssistedRouteCompletionResult,
    AssistedRoutePreparationRequest,
    AssistedRoutePreparationResult,
    BattleCompletionRequest,
    BattleCompletionResult,
    RevivedCombatCompletionRequest,
    RevivedCombatCompletionResult,
    SealTrialCompletionRequest,
    SealTrialCompletionResult,
    SealTrialPreparationRequest,
    SealTrialPreparationResult,
    SecondWindCompletionRequest,
    SecondWindCompletionResult,
    SecondWindStartRequest,
    SecondWindStartResult,
    WorldActivationRequest,
    WorldActivationResult,
)


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,95}", re.ASCII)
_JSON_CONTENT_TYPE = re.compile(
    r"application/json(?:\s*;\s*charset=utf-8)?",
    re.IGNORECASE | re.ASCII,
)
_FACADE_METHODS = (
    "create_profile",
    "create_session",
    "prepare_battle",
    "prepare_seal_trial",
    "prepare_assisted_route",
    "complete_battle",
    "complete_seal_trial",
    "complete_assisted_route",
    "start_second_wind",
    "complete_second_wind",
    "complete_revived_combat",
    "activate_world",
    "submit_initial",
    "submit_revision",
    "get_quiz_snapshot",
    "get_runtime_state",
    "get_boss_gate",
    "export_profile",
    "delete_profile",
)
_PROGRESSION_PUBLIC_ERROR_MAP = {
    "target_already_completed": PublicErrorCode.QUIZ_STATE_CONFLICT,
    "target_in_progress": PublicErrorCode.QUIZ_IN_PROGRESS,
    "invalid_transition": PublicErrorCode.QUIZ_STATE_CONFLICT,
    "quiz_not_revealed": PublicErrorCode.QUIZ_STATE_CONFLICT,
    "quiz_context_mismatch": PublicErrorCode.QUIZ_STATE_CONFLICT,
    "legacy_profile_blocked": PublicErrorCode.CATALOG_CONFLICT,
}


class WaylineApiFacade(Protocol):
    """The transport-neutral methods consumed by the public API."""

    def create_profile(self, request: ProfileCreate) -> ProfileCreated: ...

    def create_session(self, request: SessionCreate) -> SessionCreated: ...

    async def prepare_battle(
        self,
        request: BattleQuizRequest,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> PublicQuizBatch: ...

    async def prepare_seal_trial(
        self,
        request: SealTrialPreparationRequest,
    ) -> SealTrialPreparationResult: ...

    def complete_battle(
        self,
        request: BattleCompletionRequest,
    ) -> BattleCompletionResult: ...

    def complete_seal_trial(
        self,
        request: SealTrialCompletionRequest,
    ) -> SealTrialCompletionResult: ...

    async def prepare_assisted_route(
        self,
        request: AssistedRoutePreparationRequest,
    ) -> AssistedRoutePreparationResult: ...

    def complete_assisted_route(
        self,
        request: AssistedRouteCompletionRequest,
    ) -> AssistedRouteCompletionResult: ...

    async def start_second_wind(
        self,
        request: SecondWindStartRequest,
    ) -> SecondWindStartResult: ...

    def complete_second_wind(
        self,
        request: SecondWindCompletionRequest,
    ) -> SecondWindCompletionResult: ...

    def complete_revived_combat(
        self,
        request: RevivedCombatCompletionRequest,
    ) -> RevivedCombatCompletionResult: ...

    def activate_world(
        self,
        request: WorldActivationRequest,
    ) -> WorldActivationResult: ...

    def submit_initial(
        self,
        submission: InitialSubmission,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> InitialSubmissionResult: ...

    def submit_revision(
        self,
        submission: RevisionSubmission,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> FinalQuizResult: ...

    def get_quiz_snapshot(
        self,
        batch_id: str,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> QuizSnapshot: ...

    def get_runtime_state(
        self,
        profile_id: str,
        session_id: str,
    ) -> RuntimeState: ...

    def get_boss_gate(
        self,
        *,
        profile_id: str,
        current_session_id: str,
        world_id: str,
    ) -> BossGateResult: ...

    def delete_profile(
        self,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> None: ...

    def export_profile(
        self,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> ProfileExportV1: ...


ProfileResolver = Callable[[str], str | Awaitable[str]]


class _ApiError(RuntimeError):
    __slots__ = ("code", "status_code")

    def __init__(self, code: PublicErrorCode, status_code: int) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code.value)


class _BodyLimitMiddleware:
    """Buffer at most one policy-sized request body, then replay it once."""

    def __init__(self, app: ASGIApp, *, maximum_bytes: int) -> None:
        self._app = app
        self._maximum_bytes = maximum_bytes

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        chunks: list[bytes] = []
        body_size = 0
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                await _public_error_response(
                    PublicErrorCode.REQUEST_MALFORMED,
                    400,
                )(scope, receive, send)
                return
            raw = message.get("body", b"")
            if not isinstance(raw, bytes):
                await _public_error_response(
                    PublicErrorCode.REQUEST_MALFORMED,
                    400,
                )(scope, receive, send)
                return
            body_size += len(raw)
            if body_size > self._maximum_bytes:
                await _public_error_response(
                    PublicErrorCode.BODY_TOO_LARGE,
                    413,
                )(scope, receive, send)
                return
            chunks.append(raw)
            if not message.get("more_body", False):
                break

        body = b"".join(chunks)
        scope["wayline.body_size"] = body_size
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {
                    "type": "http.request",
                    "body": body,
                    "more_body": False,
                }
            return {"type": "http.disconnect"}

        await self._app(scope, replay, send)


def _public_error_response(
    code: PublicErrorCode,
    status_code: int,
) -> JSONResponse:
    payload = PublicError(
        schemaVersion="wayline.error.v1",
        code=code,
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json", by_alias=True),
    )


def _raise(code: PublicErrorCode, status_code: int) -> None:
    raise _ApiError(code, status_code)


def _domain_status(code: PublicErrorCode) -> int:
    if code in {
        PublicErrorCode.AUTHORIZATION_REQUIRED,
        PublicErrorCode.SESSION_NOT_CURRENT,
    }:
        return 401
    if code is PublicErrorCode.ORIGIN_FORBIDDEN:
        return 403
    if code in {
        PublicErrorCode.BATCH_UNAVAILABLE,
        PublicErrorCode.PROFILE_NOT_FOUND,
        PublicErrorCode.ROUTE_NOT_FOUND,
        PublicErrorCode.SNAPSHOT_UNAVAILABLE,
    }:
        return 404
    if code in {
        PublicErrorCode.BOSS_GATE_LOCKED,
        PublicErrorCode.CATALOG_CONFLICT,
        PublicErrorCode.IDEMPOTENCY_CONFLICT,
        PublicErrorCode.QUIZ_IN_PROGRESS,
        PublicErrorCode.QUIZ_STATE_CONFLICT,
        PublicErrorCode.SNAPSHOT_NOT_READY,
    }:
        return 409
    if code is PublicErrorCode.BODY_TOO_LARGE:
        return 413
    if code is PublicErrorCode.CONTENT_TYPE_UNSUPPORTED:
        return 415
    if code in {
        PublicErrorCode.CONTRACT_INVALID,
        PublicErrorCode.INVALID_SUBMISSION,
    }:
        return 422
    if code is PublicErrorCode.METHOD_NOT_ALLOWED:
        return 405
    if code is PublicErrorCode.REQUEST_MALFORMED:
        return 400
    if code in {
        PublicErrorCode.EVIDENCE_SYNC_UNAVAILABLE,
        PublicErrorCode.RUNTIME_STATE_UNAVAILABLE,
        PublicErrorCode.SAFE_CONTENT_UNAVAILABLE,
        PublicErrorCode.STORAGE_BUSY,
    }:
        return 503
    return 500


def _translate_domain_error(error: BaseException) -> _ApiError:
    raw_code = getattr(error, "code", None)
    if isinstance(raw_code, PublicErrorCode):
        code = raw_code
    elif type(raw_code) is str and raw_code in _PROGRESSION_PUBLIC_ERROR_MAP:
        code = _PROGRESSION_PUBLIC_ERROR_MAP[raw_code]
    elif type(raw_code) is str:
        try:
            code = PublicErrorCode(raw_code)
        except ValueError:
            code = PublicErrorCode.INTEGRITY_FAILURE
    else:
        code = PublicErrorCode.INTEGRITY_FAILURE
    return _ApiError(code, _domain_status(code))


def _strict_contract(value: object, expected: type[StrictModel]) -> StrictModel:
    if type(value) is not expected:
        _raise(PublicErrorCode.INTEGRITY_FAILURE, 500)
    return value


def _validated_output(
    model_type: type[StrictModel],
    payload: dict[str, object],
) -> StrictModel:
    try:
        return model_type.model_validate(payload)
    except (TypeError, ValueError, ValidationError):
        _raise(PublicErrorCode.INTEGRITY_FAILURE, 500)
    raise AssertionError("unreachable")


def _contract_response(value: StrictModel, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=value.model_dump(mode="json", by_alias=True),
    )


def _identifier(value: str) -> str:
    if _IDENTIFIER.fullmatch(value) is None:
        _raise(PublicErrorCode.CONTRACT_INVALID, 422)
    return value


def _raw_header_values(request: Request, name: bytes) -> tuple[bytes, ...]:
    return tuple(
        value
        for raw_name, value in request.scope.get("headers", ())
        if raw_name.lower() == name
    )


def _require_json_content_type(request: Request) -> None:
    values = _raw_header_values(request, b"content-type")
    if len(values) != 1:
        _raise(PublicErrorCode.CONTENT_TYPE_UNSUPPORTED, 415)
    try:
        content_type = values[0].decode("ascii")
    except UnicodeDecodeError:
        _raise(PublicErrorCode.CONTENT_TYPE_UNSUPPORTED, 415)
    if _JSON_CONTENT_TYPE.fullmatch(content_type) is None:
        _raise(PublicErrorCode.CONTENT_TYPE_UNSUPPORTED, 415)


async def _parse_body(
    request: Request,
    model_type: type[StrictModel],
) -> StrictModel:
    _require_json_content_type(request)
    raw = await request.body()
    try:
        return parse_public_json(model_type, raw)
    except DuplicateJsonKeyError:
        _raise(PublicErrorCode.REQUEST_MALFORMED, 400)
    except (json.JSONDecodeError, UnicodeError, RecursionError, TypeError):
        _raise(PublicErrorCode.REQUEST_MALFORMED, 400)
    except ValidationError:
        _raise(PublicErrorCode.CONTRACT_INVALID, 422)
    except ValueError:
        _raise(PublicErrorCode.REQUEST_MALFORMED, 400)
    raise AssertionError("unreachable")


def _security_code(code: SecurityRejectionCode | None) -> _ApiError:
    if code is SecurityRejectionCode.BODY_TOO_LARGE:
        return _ApiError(PublicErrorCode.BODY_TOO_LARGE, 413)
    if code in {
        SecurityRejectionCode.ORIGIN_DUPLICATE,
        SecurityRejectionCode.ORIGIN_REJECTED,
    }:
        return _ApiError(PublicErrorCode.ORIGIN_FORBIDDEN, 403)
    return _ApiError(PublicErrorCode.AUTHORIZATION_REQUIRED, 401)


def create_api(
    facade: WaylineApiFacade,
    *,
    security: LaunchSecurityPolicy,
    resolve_profile_id: ProfileResolver,
) -> FastAPI:
    """Compose one docs-free learner API around injected runtime authority."""

    if not isinstance(security, LaunchSecurityPolicy):
        raise TypeError("security must be a LaunchSecurityPolicy")
    if not callable(resolve_profile_id):
        raise TypeError("resolve_profile_id must be callable")
    for method_name in _FACADE_METHODS:
        if not callable(getattr(facade, method_name, None)):
            raise TypeError(f"facade is missing {method_name}")

    api = FastAPI(
        title="Wayline Forge",
        docs_url=security.docs_url,
        redoc_url=security.redoc_url,
        openapi_url=security.openapi_url,
        separate_input_output_schemas=False,
    )
    api.router.redirect_slashes = False

    async def authorize(
        request: Request,
        *,
        session_required: bool,
    ) -> tuple[str | None, str | None]:
        validation = security.validate_request(
            headers=request.scope.get("headers", ()),
            body_size=request.scope.get("wayline.body_size", 0),
            session_scope_required=session_required,
        )
        if not validation.accepted:
            raise _security_code(validation.code)
        if not session_required:
            return None, None

        raw_sessions = _raw_header_values(request, b"x-wayline-session-id")
        if len(raw_sessions) != 1:
            _raise(PublicErrorCode.AUTHORIZATION_REQUIRED, 401)
        try:
            session_id = raw_sessions[0].decode("ascii")
        except UnicodeDecodeError:
            _raise(PublicErrorCode.AUTHORIZATION_REQUIRED, 401)
        try:
            resolved = resolve_profile_id(session_id)
            profile_id = await resolved if inspect.isawaitable(resolved) else resolved
        except Exception:
            _raise(PublicErrorCode.SESSION_NOT_CURRENT, 401)
        if (
            type(profile_id) is not str
            or _IDENTIFIER.fullmatch(profile_id) is None
        ):
            _raise(PublicErrorCode.INTEGRITY_FAILURE, 500)
        return session_id, profile_id

    def domain_call(function: Callable[..., object], *args: object, **kwargs: object) -> object:
        try:
            return function(*args, **kwargs)
        except _ApiError:
            raise
        except Exception as error:
            raise _translate_domain_error(error) from None

    async def domain_call_async(
        function: Callable[..., Awaitable[object]],
        *args: object,
        **kwargs: object,
    ) -> object:
        try:
            return await function(*args, **kwargs)
        except _ApiError:
            raise
        except Exception as error:
            raise _translate_domain_error(error) from None

    @api.exception_handler(_ApiError)
    async def api_error_handler(_request: Request, error: _ApiError) -> JSONResponse:
        return _public_error_response(error.code, error.status_code)

    @api.exception_handler(StarletteHttpException)
    async def http_error_handler(
        _request: Request,
        error: StarletteHttpException,
    ) -> JSONResponse:
        if error.status_code == 404:
            code = PublicErrorCode.ROUTE_NOT_FOUND
        elif error.status_code == 405:
            code = PublicErrorCode.METHOD_NOT_ALLOWED
        else:
            code = PublicErrorCode.REQUEST_MALFORMED
        return _public_error_response(code, error.status_code)

    @api.exception_handler(Exception)
    async def unexpected_error_handler(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        return _public_error_response(PublicErrorCode.INTEGRITY_FAILURE, 500)

    @api.get("/v1/health")
    async def health(request: Request) -> JSONResponse:
        await authorize(request, session_required=False)
        return JSONResponse(
            content={
                "schemaVersion": "wayline.health.v1",
                "status": "ready",
            }
        )

    @api.post("/v1/profiles", status_code=201)
    async def create_profile(request: Request) -> JSONResponse:
        await authorize(request, session_required=False)
        payload = await _parse_body(request, ProfileCreate)
        result = domain_call(facade.create_profile, payload)
        return _contract_response(
            _strict_contract(result, ProfileCreated),
            status_code=201,
        )

    @api.post("/v1/sessions", status_code=201)
    async def create_session(request: Request) -> JSONResponse:
        await authorize(request, session_required=False)
        payload = await _parse_body(request, SessionCreate)
        result = domain_call(facade.create_session, payload)
        return _contract_response(
            _strict_contract(result, SessionCreated),
            status_code=201,
        )

    @api.post("/v1/quiz-batches", status_code=201)
    async def prepare_battle(request: Request) -> JSONResponse:
        session_id, profile_id = await authorize(
            request,
            session_required=True,
        )
        assert session_id is not None and profile_id is not None
        payload = await _parse_body(request, BattleQuizRequest)
        assert isinstance(payload, BattleQuizRequest)
        if payload.session_id != session_id:
            _raise(PublicErrorCode.SESSION_NOT_CURRENT, 401)
        result = await domain_call_async(
            facade.prepare_battle,
            payload,
            profile_id=profile_id,
            current_session_id=session_id,
        )
        return _contract_response(
            _strict_contract(result, PublicQuizBatch),
            status_code=201,
        )

    @api.post("/v1/worlds/{world_id}/seal-trials", status_code=201)
    async def prepare_seal_trial(world_id: str, request: Request) -> JSONResponse:
        world_id = _identifier(world_id)
        session_id, profile_id = await authorize(
            request,
            session_required=True,
        )
        assert session_id is not None and profile_id is not None
        payload = await _parse_body(request, SealTrialPrepare)
        assert isinstance(payload, SealTrialPrepare)
        if payload.session_id != session_id:
            _raise(PublicErrorCode.SESSION_NOT_CURRENT, 401)
        command = SealTrialPreparationRequest(
            request_id=payload.request_id,
            profile_id=profile_id,
            session_id=session_id,
            world_id=world_id,
        )
        result = await domain_call_async(
            facade.prepare_seal_trial,
            command,
        )
        if type(result) is not SealTrialPreparationResult:
            _raise(PublicErrorCode.INTEGRITY_FAILURE, 500)
        try:
            response = SealTrialPrepared(
                schemaVersion="wayline.v1",
                requestId=result.request_id,
                worldId=result.world_id,
                attemptNumber=result.attempt_number,
                battleId=result.battle_id,
                batch=result.batch,
            )
        except (TypeError, ValueError, ValidationError):
            _raise(PublicErrorCode.INTEGRITY_FAILURE, 500)
        return _contract_response(response, status_code=201)

    @api.post("/v1/worlds/{world_id}/assisted-routes", status_code=201)
    async def prepare_assisted_route(
        world_id: str,
        request: Request,
    ) -> JSONResponse:
        world_id = _identifier(world_id)
        session_id, profile_id = await authorize(
            request,
            session_required=True,
        )
        assert session_id is not None and profile_id is not None
        payload = await _parse_body(request, AssistedRoutePrepare)
        assert isinstance(payload, AssistedRoutePrepare)
        if payload.session_id != session_id:
            _raise(PublicErrorCode.SESSION_NOT_CURRENT, 401)
        result = await domain_call_async(
            facade.prepare_assisted_route,
            AssistedRoutePreparationRequest(
                request_id=payload.request_id,
                profile_id=profile_id,
                session_id=session_id,
                world_id=world_id,
            ),
        )
        if (
            type(result) is not AssistedRoutePreparationResult
            or result.request_id != payload.request_id
            or type(result.batch) is not AssistedRouteBatch
            or result.batch.world_id != world_id
        ):
            _raise(PublicErrorCode.INTEGRITY_FAILURE, 500)
        response = _validated_output(
            AssistedRoutePrepared,
            {
                "schemaVersion": "wayline.v1",
                "requestId": result.request_id,
                "worldId": world_id,
                "batch": result.batch,
            },
        )
        return _contract_response(response, status_code=201)

    @api.post(
        "/v1/worlds/{world_id}/assisted-routes/{route_id}/completion"
    )
    async def complete_assisted_route(
        world_id: str,
        route_id: str,
        request: Request,
    ) -> JSONResponse:
        world_id = _identifier(world_id)
        route_id = _identifier(route_id)
        session_id, profile_id = await authorize(
            request,
            session_required=True,
        )
        assert session_id is not None and profile_id is not None
        payload = await _parse_body(request, AssistedRouteComplete)
        assert isinstance(payload, AssistedRouteComplete)
        if payload.session_id != session_id:
            _raise(PublicErrorCode.SESSION_NOT_CURRENT, 401)
        result = domain_call(
            facade.complete_assisted_route,
            AssistedRouteCompletionRequest(
                request_id=payload.request_id,
                profile_id=profile_id,
                session_id=session_id,
                world_id=world_id,
                route_id=route_id,
                selections=payload.selections,
            ),
        )
        if (
            type(result) is not AssistedRouteCompletionResult
            or result.request_id != payload.request_id
            or result.world_id != world_id
            or result.route_id != route_id
        ):
            _raise(PublicErrorCode.INTEGRITY_FAILURE, 500)
        response = _validated_output(
            AssistedRouteCompleted,
            {
                "schemaVersion": "wayline.v1",
                "requestId": result.request_id,
                "worldId": result.world_id,
                "routeId": result.route_id,
                "workedExampleCount": result.worked_example_count,
                "supportedMcqCount": result.supported_mcq_count,
                "finalCorrect": result.final_correct,
                "worldCleared": result.world_cleared,
                "items": result.items,
            },
        )
        return _contract_response(response)

    @api.post(
        "/v1/worlds/{world_id}/battles/{battle_id}/"
        "quiz-batches/{batch_id}/completion"
    )
    async def complete_battle(
        world_id: str,
        battle_id: str,
        batch_id: str,
        request: Request,
    ) -> JSONResponse:
        world_id = _identifier(world_id)
        battle_id = _identifier(battle_id)
        batch_id = _identifier(batch_id)
        session_id, profile_id = await authorize(request, session_required=True)
        assert session_id is not None and profile_id is not None
        payload = await _parse_body(request, BattleComplete)
        assert isinstance(payload, BattleComplete)
        if payload.session_id != session_id:
            _raise(PublicErrorCode.SESSION_NOT_CURRENT, 401)
        result = domain_call(
            facade.complete_battle,
            BattleCompletionRequest(
                payload.request_id,
                profile_id,
                session_id,
                world_id,
                battle_id,
                batch_id,
                payload.combat_won,
            ),
        )
        if (
            type(result) is not BattleCompletionResult
            or result.request_id != payload.request_id
            or result.world_id != world_id
            or result.battle_id != battle_id
            or result.batch_id != batch_id
        ):
            _raise(PublicErrorCode.INTEGRITY_FAILURE, 500)
        response = _validated_output(
            BattleCompleted,
            {
                "schemaVersion": "wayline.v1",
                "requestId": result.request_id,
                "worldId": result.world_id,
                "battleId": result.battle_id,
                "batchId": result.batch_id,
                "finalCorrect": result.final_correct,
                "itemCount": result.item_count,
                "bossBattle": result.boss_battle,
                "worldCleared": result.world_cleared,
                "sealTrialRequired": result.seal_trial_required,
            },
        )
        return _contract_response(response)

    @api.post("/v1/worlds/{world_id}/seal-trials/{batch_id}/completion")
    async def complete_seal_trial(
        world_id: str,
        batch_id: str,
        request: Request,
    ) -> JSONResponse:
        world_id = _identifier(world_id)
        batch_id = _identifier(batch_id)
        session_id, profile_id = await authorize(request, session_required=True)
        assert session_id is not None and profile_id is not None
        payload = await _parse_body(request, SealTrialComplete)
        assert isinstance(payload, SealTrialComplete)
        if payload.session_id != session_id:
            _raise(PublicErrorCode.SESSION_NOT_CURRENT, 401)
        result = domain_call(
            facade.complete_seal_trial,
            SealTrialCompletionRequest(
                payload.request_id,
                profile_id,
                session_id,
                world_id,
                batch_id,
            ),
        )
        if (
            type(result) is not SealTrialCompletionResult
            or result.request_id != payload.request_id
            or result.world_id != world_id
            or result.batch_id != batch_id
        ):
            _raise(PublicErrorCode.INTEGRITY_FAILURE, 500)
        response = _validated_output(
            SealTrialCompleted,
            {
                "schemaVersion": "wayline.v1",
                "requestId": result.request_id,
                "worldId": result.world_id,
                "attemptNumber": result.attempt_number,
                "batchId": result.batch_id,
                "finalCorrect": result.final_correct,
                "itemCount": result.item_count,
                "passed": result.passed,
                "worldCleared": result.world_cleared,
                "assistedRouteUnlocked": result.assisted_route_unlocked,
            },
        )
        return _contract_response(response)

    @api.post(
        "/v1/worlds/{world_id}/battles/{battle_id}/"
        "combat-attempts/{combat_attempt_id}/second-winds",
        status_code=201,
    )
    async def start_second_wind(
        world_id: str,
        battle_id: str,
        combat_attempt_id: str,
        request: Request,
    ) -> JSONResponse:
        world_id = _identifier(world_id)
        battle_id = _identifier(battle_id)
        combat_attempt_id = _identifier(combat_attempt_id)
        session_id, profile_id = await authorize(request, session_required=True)
        assert session_id is not None and profile_id is not None
        payload = await _parse_body(request, SecondWindStart)
        assert isinstance(payload, SecondWindStart)
        if payload.session_id != session_id:
            _raise(PublicErrorCode.SESSION_NOT_CURRENT, 401)
        result = await domain_call_async(
            facade.start_second_wind,
            SecondWindStartRequest(
                payload.request_id,
                payload.preparation_request_id,
                profile_id,
                session_id,
                world_id,
                battle_id,
                combat_attempt_id,
            ),
        )
        if (
            type(result) is not SecondWindStartResult
            or result.request_id != payload.request_id
            or result.world_id != world_id
            or result.battle_id != battle_id
            or result.combat_attempt_id != combat_attempt_id
        ):
            _raise(PublicErrorCode.INTEGRITY_FAILURE, 500)
        response = _validated_output(
            SecondWindStarted,
            {
                "schemaVersion": "wayline.v1",
                "requestId": result.request_id,
                "secondWindId": result.second_wind_id,
                "worldId": result.world_id,
                "battleId": result.battle_id,
                "combatAttemptId": result.combat_attempt_id,
                "quizBattleId": result.quiz_battle_id,
                "batch": result.batch,
            },
        )
        return _contract_response(response, status_code=201)

    @api.post(
        "/v1/second-winds/{second_wind_id}/quiz-batches/{batch_id}/completion"
    )
    async def complete_second_wind(
        second_wind_id: str,
        batch_id: str,
        request: Request,
    ) -> JSONResponse:
        second_wind_id = _identifier(second_wind_id)
        batch_id = _identifier(batch_id)
        session_id, profile_id = await authorize(request, session_required=True)
        assert session_id is not None and profile_id is not None
        payload = await _parse_body(request, SecondWindComplete)
        assert isinstance(payload, SecondWindComplete)
        if payload.session_id != session_id:
            _raise(PublicErrorCode.SESSION_NOT_CURRENT, 401)
        result = domain_call(
            facade.complete_second_wind,
            SecondWindCompletionRequest(
                payload.request_id,
                profile_id,
                session_id,
                second_wind_id,
                batch_id,
            ),
        )
        if (
            type(result) is not SecondWindCompletionResult
            or result.request_id != payload.request_id
            or result.second_wind_id != second_wind_id
            or result.batch_id != batch_id
        ):
            _raise(PublicErrorCode.INTEGRITY_FAILURE, 500)
        response = _validated_output(
            SecondWindCompleted,
            {
                "schemaVersion": "wayline.v1",
                "requestId": result.request_id,
                "secondWindId": result.second_wind_id,
                "batchId": result.batch_id,
                "finalCorrect": result.final_correct,
                "itemCount": result.item_count,
                "reviveHealthPercent": result.revive_health_percent,
                "shieldPercent": result.shield_percent,
                "revivedCombatPending": result.revived_combat_pending,
            },
        )
        return _contract_response(response)

    @api.post(
        "/v1/second-winds/{second_wind_id}/"
        "combat-attempts/{combat_attempt_id}/completion"
    )
    async def complete_revived_combat(
        second_wind_id: str,
        combat_attempt_id: str,
        request: Request,
    ) -> JSONResponse:
        second_wind_id = _identifier(second_wind_id)
        combat_attempt_id = _identifier(combat_attempt_id)
        session_id, profile_id = await authorize(request, session_required=True)
        assert session_id is not None and profile_id is not None
        payload = await _parse_body(request, RevivedCombatComplete)
        assert isinstance(payload, RevivedCombatComplete)
        if payload.session_id != session_id:
            _raise(PublicErrorCode.SESSION_NOT_CURRENT, 401)
        result = domain_call(
            facade.complete_revived_combat,
            RevivedCombatCompletionRequest(
                payload.request_id,
                profile_id,
                session_id,
                second_wind_id,
                combat_attempt_id,
                payload.combat_won,
            ),
        )
        if (
            type(result) is not RevivedCombatCompletionResult
            or result.request_id != payload.request_id
            or result.second_wind_id != second_wind_id
            or result.combat_attempt_id != combat_attempt_id
            or result.combat_won != payload.combat_won
        ):
            _raise(PublicErrorCode.INTEGRITY_FAILURE, 500)
        response = _validated_output(
            RevivedCombatCompleted,
            {
                "schemaVersion": "wayline.v1",
                "requestId": result.request_id,
                "secondWindId": result.second_wind_id,
                "combatAttemptId": result.combat_attempt_id,
                "combatWon": result.combat_won,
                "battleCompleted": result.battle_completed,
                "secondWindClosed": result.second_wind_closed,
            },
        )
        return _contract_response(response)

    @api.post(
        "/v1/worlds/{completed_world_id}/successors/"
        "{next_world_id}/activation"
    )
    async def activate_world(
        completed_world_id: str,
        next_world_id: str,
        request: Request,
    ) -> JSONResponse:
        completed_world_id = _identifier(completed_world_id)
        next_world_id = _identifier(next_world_id)
        session_id, profile_id = await authorize(request, session_required=True)
        assert session_id is not None and profile_id is not None
        payload = await _parse_body(request, WorldActivate)
        assert isinstance(payload, WorldActivate)
        if payload.session_id != session_id:
            _raise(PublicErrorCode.SESSION_NOT_CURRENT, 401)
        result = domain_call(
            facade.activate_world,
            WorldActivationRequest(
                payload.request_id,
                profile_id,
                session_id,
                completed_world_id,
                next_world_id,
            ),
        )
        if (
            type(result) is not WorldActivationResult
            or result.request_id != payload.request_id
            or result.completed_world_id != completed_world_id
            or result.active_world_id != next_world_id
        ):
            _raise(PublicErrorCode.INTEGRITY_FAILURE, 500)
        response = _validated_output(
            WorldActivated,
            {
                "schemaVersion": "wayline.v1",
                "requestId": result.request_id,
                "completedWorldId": result.completed_world_id,
                "activeWorldId": result.active_world_id,
                "campaignSequence": result.campaign_sequence,
            },
        )
        return _contract_response(response)

    @api.get("/v1/quiz-batches/{batch_id}")
    async def get_quiz_snapshot(batch_id: str, request: Request) -> JSONResponse:
        batch_id = _identifier(batch_id)
        session_id, profile_id = await authorize(
            request,
            session_required=True,
        )
        assert session_id is not None and profile_id is not None
        result = domain_call(
            facade.get_quiz_snapshot,
            batch_id,
            profile_id=profile_id,
            current_session_id=session_id,
        )
        return _contract_response(_strict_contract(result, QuizSnapshot))

    @api.post("/v1/quiz-batches/{batch_id}/initial")
    async def submit_initial(batch_id: str, request: Request) -> JSONResponse:
        batch_id = _identifier(batch_id)
        session_id, profile_id = await authorize(
            request,
            session_required=True,
        )
        assert session_id is not None and profile_id is not None
        payload = await _parse_body(request, InitialSubmission)
        assert isinstance(payload, InitialSubmission)
        if payload.batch_id != batch_id:
            _raise(PublicErrorCode.CONTRACT_INVALID, 422)
        result = domain_call(
            facade.submit_initial,
            payload,
            profile_id=profile_id,
            current_session_id=session_id,
        )
        return _contract_response(
            _strict_contract(result, InitialSubmissionResult)
        )

    @api.post("/v1/quiz-batches/{batch_id}/revision")
    async def submit_revision(batch_id: str, request: Request) -> JSONResponse:
        batch_id = _identifier(batch_id)
        session_id, profile_id = await authorize(
            request,
            session_required=True,
        )
        assert session_id is not None and profile_id is not None
        payload = await _parse_body(request, RevisionSubmission)
        assert isinstance(payload, RevisionSubmission)
        if payload.batch_id != batch_id:
            _raise(PublicErrorCode.CONTRACT_INVALID, 422)
        result = domain_call(
            facade.submit_revision,
            payload,
            profile_id=profile_id,
            current_session_id=session_id,
        )
        return _contract_response(_strict_contract(result, FinalQuizResult))

    @api.get("/v1/runtime-state")
    async def get_runtime_state(request: Request) -> JSONResponse:
        session_id, profile_id = await authorize(
            request,
            session_required=True,
        )
        assert session_id is not None and profile_id is not None
        result = domain_call(
            facade.get_runtime_state,
            profile_id,
            session_id,
        )
        return _contract_response(_strict_contract(result, RuntimeState))

    @api.get("/v1/worlds/{world_id}/gate")
    async def get_boss_gate(world_id: str, request: Request) -> JSONResponse:
        world_id = _identifier(world_id)
        session_id, profile_id = await authorize(
            request,
            session_required=True,
        )
        assert session_id is not None and profile_id is not None
        result = domain_call(
            facade.get_boss_gate,
            profile_id=profile_id,
            current_session_id=session_id,
            world_id=world_id,
        )
        return _contract_response(_strict_contract(result, BossGateResult))

    @api.delete("/v1/profiles/{profile_id}", status_code=204)
    async def delete_profile(profile_id: str, request: Request) -> Response:
        profile_id = _identifier(profile_id)
        session_id, resolved_profile_id = await authorize(
            request,
            session_required=True,
        )
        assert session_id is not None and resolved_profile_id is not None
        if profile_id != resolved_profile_id:
            _raise(PublicErrorCode.SESSION_NOT_CURRENT, 401)
        result = domain_call(
            facade.delete_profile,
            profile_id=profile_id,
            current_session_id=session_id,
        )
        if result is not None:
            _raise(PublicErrorCode.INTEGRITY_FAILURE, 500)
        return Response(status_code=204)

    @api.get("/v1/profiles/{profile_id}/export")
    async def export_profile(profile_id: str, request: Request) -> JSONResponse:
        profile_id = _identifier(profile_id)
        session_id, resolved_profile_id = await authorize(
            request,
            session_required=True,
        )
        assert session_id is not None and resolved_profile_id is not None
        if profile_id != resolved_profile_id:
            _raise(PublicErrorCode.SESSION_NOT_CURRENT, 401)
        result = domain_call(
            facade.export_profile,
            profile_id=resolved_profile_id,
            current_session_id=session_id,
        )
        return _contract_response(_strict_contract(result, ProfileExportV1))

    api.add_middleware(
        _BodyLimitMiddleware,
        maximum_bytes=security.max_request_body_bytes,
    )
    api.add_middleware(
        CORSMiddleware,
        allow_origins=[security.unity_origin],
        allow_credentials=False,
        allow_methods=["DELETE", "GET", "POST"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Wayline-Session-Id",
        ],
        max_age=600,
    )
    return api


__all__ = ["ProfileResolver", "WaylineApiFacade", "create_api"]
