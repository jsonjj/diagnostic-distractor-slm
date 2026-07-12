"""Dependency-free learner-mode launch and request security primitives.

The FastAPI adapter is deliberately not imported here.  It can pass raw ASGI header
pairs and the observed body size into this module without weakening duplicate-header
checks or giving this policy ownership of transport behavior.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
import hmac
import re
import secrets
from urllib.parse import urlsplit


LEARNER_LOOPBACK_HOST = "127.0.0.1"
LEARNER_BODY_LIMIT_BYTES = 64 * 1024
AUTHORIZATION_HEADER_NAME = "Authorization"
SESSION_HEADER_NAME = "X-Wayline-Session-Id"
ORIGIN_HEADER_NAME = "Origin"

_TOKEN_BYTE_COUNT = 32
_TOKEN_PATTERN = re.compile(r"[0-9a-f]{64}", re.ASCII)
_AUTHORIZATION_PATTERN = re.compile(
    r"(?i:Bearer) ([0-9a-f]{64})",
    re.ASCII,
)
_IDENTIFIER_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{2,95}",
    re.ASCII,
)
_HEADER_NAME_PATTERN = re.compile(
    r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+",
    re.ASCII,
)
_AUTHORIZATION_HEADER_KEY = AUTHORIZATION_HEADER_NAME.lower()
_SESSION_HEADER_KEY = SESSION_HEADER_NAME.lower()
_ORIGIN_HEADER_KEY = ORIGIN_HEADER_NAME.lower()
_SECURITY_HEADER_KEYS = frozenset(
    (
        _AUTHORIZATION_HEADER_KEY,
        _SESSION_HEADER_KEY,
        _ORIGIN_HEADER_KEY,
    )
)


class SecurityRejectionCode(str, Enum):
    """Stable, non-secret machine codes for learner-request rejection."""

    BIND_HOST_REJECTED = "bind_host_rejected"
    HEADERS_MALFORMED = "headers_malformed"
    AUTHORIZATION_MISSING = "authorization_missing"
    AUTHORIZATION_DUPLICATE = "authorization_duplicate"
    AUTHORIZATION_MALFORMED = "authorization_malformed"
    AUTHORIZATION_REJECTED = "authorization_rejected"
    ORIGIN_DUPLICATE = "origin_duplicate"
    ORIGIN_REJECTED = "origin_rejected"
    SESSION_ID_MISSING = "session_id_missing"
    SESSION_ID_DUPLICATE = "session_id_duplicate"
    SESSION_ID_INVALID = "session_id_invalid"
    BODY_SIZE_INVALID = "body_size_invalid"
    BODY_TOO_LARGE = "body_too_large"


@dataclass(frozen=True, slots=True)
class SecurityRejection:
    """A log-safe typed rejection with no attacker-controlled detail."""

    code: SecurityRejectionCode

    def __post_init__(self) -> None:
        if not isinstance(self.code, SecurityRejectionCode):
            raise TypeError("security rejection code must be typed")


@dataclass(frozen=True, slots=True)
class SecurityValidation:
    """All-or-nothing result returned by the pure policy checks."""

    accepted: bool
    rejection: SecurityRejection | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.accepted, bool):
            raise TypeError("accepted must be bool")
        if self.accepted != (self.rejection is None):
            raise ValueError("security validation must be all-or-nothing")
        if self.rejection is not None and not isinstance(
            self.rejection, SecurityRejection
        ):
            raise TypeError("rejection must be typed")

    @property
    def code(self) -> SecurityRejectionCode | None:
        return None if self.rejection is None else self.rejection.code


_ACCEPTED = SecurityValidation(accepted=True)


@dataclass(frozen=True, slots=True)
class _SecurityHeaders:
    authorization: tuple[str, ...]
    session_id: tuple[str, ...]
    origin: tuple[str, ...]


def _reject(code: SecurityRejectionCode) -> SecurityValidation:
    return SecurityValidation(
        accepted=False,
        rejection=SecurityRejection(code),
    )


def generate_launch_token(
    random_bytes: Callable[[int], bytes] | None = None,
) -> str:
    """Generate exactly 256 random bits and encode them as canonical hex."""

    source = secrets.token_bytes if random_bytes is None else random_bytes
    if not callable(source):
        raise TypeError("random byte source must be callable")
    entropy = source(_TOKEN_BYTE_COUNT)
    if not isinstance(entropy, bytes):
        raise TypeError("random byte source must return bytes")
    if len(entropy) != _TOKEN_BYTE_COUNT:
        raise ValueError("random byte source must return exactly 32 bytes")
    return entropy.hex()


def _is_canonical_unity_origin(origin: object) -> bool:
    if not isinstance(origin, str):
        return False
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "http"
        or parsed.hostname != LEARNER_LOOPBACK_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or not 1 <= port <= 65_535
        or parsed.path != ""
        or parsed.query != ""
        or parsed.fragment != ""
    ):
        return False
    canonical = f"http://{LEARNER_LOOPBACK_HOST}:{port}"
    return parsed.netloc == f"{LEARNER_LOOPBACK_HOST}:{port}" and origin == canonical


def _ascii_text(value: object) -> str | None:
    if isinstance(value, bytes):
        try:
            return value.decode("ascii")
        except UnicodeDecodeError:
            return None
    if isinstance(value, str):
        try:
            value.encode("ascii")
        except UnicodeEncodeError:
            return None
        return value
    return None


def _parse_security_headers(
    headers: object,
) -> tuple[_SecurityHeaders | None, SecurityValidation | None]:
    if isinstance(headers, (str, bytes, bytearray, Mapping)):
        return None, _reject(SecurityRejectionCode.HEADERS_MALFORMED)
    try:
        iterator = iter(headers)  # type: ignore[arg-type]
    except TypeError:
        return None, _reject(SecurityRejectionCode.HEADERS_MALFORMED)

    collected: dict[str, list[str]] = {
        key: [] for key in _SECURITY_HEADER_KEYS
    }
    try:
        for pair in iterator:
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                return None, _reject(SecurityRejectionCode.HEADERS_MALFORMED)
            raw_name, raw_value = pair
            name = _ascii_text(raw_name)
            if name is None or _HEADER_NAME_PATTERN.fullmatch(name) is None:
                return None, _reject(SecurityRejectionCode.HEADERS_MALFORMED)
            key = name.lower()
            if key not in _SECURITY_HEADER_KEYS:
                continue
            value = raw_value if isinstance(raw_value, str) else _ascii_text(raw_value)
            if value is None:
                return None, _reject(SecurityRejectionCode.HEADERS_MALFORMED)
            collected[key].append(value)
    except (TypeError, ValueError):
        return None, _reject(SecurityRejectionCode.HEADERS_MALFORMED)

    return (
        _SecurityHeaders(
            authorization=tuple(collected[_AUTHORIZATION_HEADER_KEY]),
            session_id=tuple(collected[_SESSION_HEADER_KEY]),
            origin=tuple(collected[_ORIGIN_HEADER_KEY]),
        ),
        None,
    )


@dataclass(frozen=True, slots=True)
class LaunchSecurityPolicy:
    """Immutable security settings for one learner-mode sidecar launch."""

    unity_origin: str
    launch_token: str = field(repr=False)
    host: str = field(default=LEARNER_LOOPBACK_HOST, init=False)
    authorization_header_name: str = field(
        default=AUTHORIZATION_HEADER_NAME,
        init=False,
    )
    session_header_name: str = field(default=SESSION_HEADER_NAME, init=False)
    origin_header_name: str = field(default=ORIGIN_HEADER_NAME, init=False)
    max_request_body_bytes: int = field(
        default=LEARNER_BODY_LIMIT_BYTES,
        init=False,
    )
    docs_url: None = field(default=None, init=False)
    redoc_url: None = field(default=None, init=False)
    openapi_url: None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.launch_token, str)
            or _TOKEN_PATTERN.fullmatch(self.launch_token) is None
        ):
            raise ValueError("launch token must be 64 lowercase hex characters")
        if not _is_canonical_unity_origin(self.unity_origin):
            raise ValueError("Unity origin must be a canonical local HTTP origin")

    @classmethod
    def for_learner(
        cls,
        *,
        unity_origin: str,
        random_bytes: Callable[[int], bytes] | None = None,
    ) -> LaunchSecurityPolicy:
        """Create one policy with fresh launch-scoped bearer material."""

        return cls(
            unity_origin=unity_origin,
            launch_token=generate_launch_token(random_bytes),
        )

    def validate_bind_host(self, host: object) -> SecurityValidation:
        """Allow only the canonical IPv4 loopback bind target."""

        if host != LEARNER_LOOPBACK_HOST:
            return _reject(SecurityRejectionCode.BIND_HOST_REJECTED)
        return _ACCEPTED

    def validate_body_size(self, body_size: object) -> SecurityValidation:
        """Validate an observed decoded request-body byte count."""

        if (
            not isinstance(body_size, int)
            or isinstance(body_size, bool)
            or body_size < 0
        ):
            return _reject(SecurityRejectionCode.BODY_SIZE_INVALID)
        if body_size > LEARNER_BODY_LIMIT_BYTES:
            return _reject(SecurityRejectionCode.BODY_TOO_LARGE)
        return _ACCEPTED

    def validate_request_headers(
        self,
        headers: Iterable[tuple[str | bytes, str | bytes]],
        *,
        session_scope_required: bool,
    ) -> SecurityValidation:
        """Validate duplicate-preserving raw headers without transport dependencies."""

        parsed, parse_rejection = _parse_security_headers(headers)
        if parse_rejection is not None:
            return parse_rejection
        assert parsed is not None

        if not parsed.authorization:
            return _reject(SecurityRejectionCode.AUTHORIZATION_MISSING)
        if len(parsed.authorization) != 1:
            return _reject(SecurityRejectionCode.AUTHORIZATION_DUPLICATE)
        authorization_match = _AUTHORIZATION_PATTERN.fullmatch(
            parsed.authorization[0]
        )
        if authorization_match is None:
            return _reject(SecurityRejectionCode.AUTHORIZATION_MALFORMED)
        candidate_token = authorization_match.group(1)
        if not hmac.compare_digest(candidate_token, self.launch_token):
            return _reject(SecurityRejectionCode.AUTHORIZATION_REJECTED)

        if len(parsed.origin) > 1:
            return _reject(SecurityRejectionCode.ORIGIN_DUPLICATE)
        if parsed.origin and parsed.origin[0] != self.unity_origin:
            return _reject(SecurityRejectionCode.ORIGIN_REJECTED)

        if session_scope_required:
            if not parsed.session_id:
                return _reject(SecurityRejectionCode.SESSION_ID_MISSING)
            if len(parsed.session_id) != 1:
                return _reject(SecurityRejectionCode.SESSION_ID_DUPLICATE)
            if _IDENTIFIER_PATTERN.fullmatch(parsed.session_id[0]) is None:
                return _reject(SecurityRejectionCode.SESSION_ID_INVALID)

        return _ACCEPTED

    def validate_request(
        self,
        *,
        headers: Iterable[tuple[str | bytes, str | bytes]],
        body_size: object,
        session_scope_required: bool,
    ) -> SecurityValidation:
        """Apply request checks in stable header-before-body order."""

        header_validation = self.validate_request_headers(
            headers,
            session_scope_required=session_scope_required,
        )
        if not header_validation.accepted:
            return header_validation
        return self.validate_body_size(body_size)
