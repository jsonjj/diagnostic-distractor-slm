"""Learner-mode loopback launch security contract."""

import ast
from dataclasses import FrozenInstanceError, fields
import hmac
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

from services.wayline_forge.app import loopback_security
from services.wayline_forge.app.loopback_security import (
    AUTHORIZATION_HEADER_NAME,
    LEARNER_BODY_LIMIT_BYTES,
    LEARNER_LOOPBACK_HOST,
    ORIGIN_HEADER_NAME,
    SESSION_HEADER_NAME,
    LaunchSecurityPolicy,
    SecurityRejection,
    SecurityRejectionCode,
    SecurityValidation,
    generate_launch_token,
)


TOKEN_BYTES = bytes(range(32))
TOKEN = TOKEN_BYTES.hex()
UNITY_ORIGIN = "http://127.0.0.1:43117"


def policy() -> LaunchSecurityPolicy:
    return LaunchSecurityPolicy(
        unity_origin=UNITY_ORIGIN,
        launch_token=TOKEN,
    )


def authenticated_headers(
    *,
    token: str = TOKEN,
    session_id: str | None = "session-001",
    origin: str | None = UNITY_ORIGIN,
) -> list[tuple[str, str]]:
    headers = [(AUTHORIZATION_HEADER_NAME, f"Bearer {token}")]
    if session_id is not None:
        headers.append((SESSION_HEADER_NAME, session_id))
    if origin is not None:
        headers.append((ORIGIN_HEADER_NAME, origin))
    return headers


class LaunchTokenTests(unittest.TestCase):
    def test_generator_requests_exactly_256_bits_and_returns_lowercase_hex(self):
        requested_sizes: list[int] = []

        def deterministic_bytes(size: int) -> bytes:
            requested_sizes.append(size)
            return TOKEN_BYTES

        token = generate_launch_token(deterministic_bytes)

        self.assertEqual(requested_sizes, [32])
        self.assertEqual(token, TOKEN)
        self.assertEqual(len(token), 64)
        self.assertRegex(token, r"\A[0-9a-f]{64}\Z")

    def test_generator_rejects_sources_that_do_not_return_exactly_32_bytes(self):
        invalid_values = (
            b"x" * 31,
            b"x" * 33,
            bytearray(b"x" * 32),
            "x" * 32,
        )
        for invalid in invalid_values:
            with self.subTest(value_type=type(invalid).__name__, length=len(invalid)):
                with self.assertRaises((TypeError, ValueError)) as raised:
                    generate_launch_token(lambda unused_size, value=invalid: value)  # type: ignore[return-value]
                self.assertNotIn(str(invalid), str(raised.exception))

    def test_default_generator_produces_well_formed_unique_tokens(self):
        tokens = {generate_launch_token() for _ in range(32)}

        self.assertEqual(len(tokens), 32)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", item) for item in tokens))

    def test_learner_factory_accepts_an_injectable_entropy_source(self):
        requested_sizes: list[int] = []

        def deterministic_bytes(size: int) -> bytes:
            requested_sizes.append(size)
            return TOKEN_BYTES

        value = LaunchSecurityPolicy.for_learner(
            unity_origin=UNITY_ORIGIN,
            random_bytes=deterministic_bytes,
        )

        self.assertEqual(requested_sizes, [32])
        self.assertEqual(value.launch_token, TOKEN)


class PolicyShapeTests(unittest.TestCase):
    def test_learner_policy_has_fixed_loopback_headers_limit_and_disabled_docs(self):
        value = policy()

        self.assertEqual(value.host, "127.0.0.1")
        self.assertEqual(value.host, LEARNER_LOOPBACK_HOST)
        self.assertEqual(value.authorization_header_name, "Authorization")
        self.assertEqual(value.authorization_header_name, AUTHORIZATION_HEADER_NAME)
        self.assertEqual(value.session_header_name, "X-Wayline-Session-Id")
        self.assertEqual(value.session_header_name, SESSION_HEADER_NAME)
        self.assertEqual(value.origin_header_name, "Origin")
        self.assertEqual(value.origin_header_name, ORIGIN_HEADER_NAME)
        self.assertEqual(value.max_request_body_bytes, 65_536)
        self.assertEqual(value.max_request_body_bytes, LEARNER_BODY_LIMIT_BYTES)
        self.assertIsNone(value.docs_url)
        self.assertIsNone(value.redoc_url)
        self.assertIsNone(value.openapi_url)

    def test_policy_is_frozen_slotted_and_token_is_absent_from_repr_and_str(self):
        value = policy()

        with self.assertRaises(FrozenInstanceError):
            value.host = "0.0.0.0"  # type: ignore[misc]
        with self.assertRaises((AttributeError, TypeError)):
            value.remote_mode = True  # type: ignore[attr-defined]
        self.assertNotIn(TOKEN, repr(value))
        self.assertNotIn(TOKEN, str(value))
        token_field = next(item for item in fields(value) if item.name == "launch_token")
        self.assertFalse(token_field.repr)

    def test_invalid_token_errors_are_stable_and_secret_free(self):
        secret_like_invalid_token = "private-token-that-must-not-be-logged"

        with self.assertRaises(ValueError) as raised:
            LaunchSecurityPolicy(
                unity_origin=UNITY_ORIGIN,
                launch_token=secret_like_invalid_token,
            )

        self.assertEqual(str(raised.exception), "launch token must be 64 lowercase hex characters")
        self.assertNotIn(secret_like_invalid_token, str(raised.exception))

    def test_policy_exposes_no_command_line_serialization_helper(self):
        names = set(dir(LaunchSecurityPolicy))

        self.assertTrue(
            {
                "as_argv",
                "to_argv",
                "to_cli_args",
                "command_line",
                "serialize_command_line",
            }.isdisjoint(names)
        )

    def test_configured_unity_origin_must_be_canonical_ipv4_loopback_http_origin(self):
        invalid_origins = (
            "https://127.0.0.1:43117",
            "http://localhost:43117",
            "http://[::1]:43117",
            "http://127.0.0.1",
            "http://127.0.0.1:0",
            "http://127.0.0.1:65536",
            "http://user@127.0.0.1:43117",
            "http://127.0.0.1:43117/",
            "http://127.0.0.1:43117/path",
            "http://127.0.0.1:43117?query",
            "http://127.0.0.1:43117#fragment",
            "HTTP://127.0.0.1:43117",
            "http://127.0.0.1:043117",
            "null",
            "*",
            "",
        )
        for invalid_origin in invalid_origins:
            with self.subTest(origin=invalid_origin):
                with self.assertRaises(ValueError) as raised:
                    LaunchSecurityPolicy(
                        unity_origin=invalid_origin,
                        launch_token=TOKEN,
                    )
                self.assertEqual(
                    str(raised.exception),
                    "Unity origin must be a canonical local HTTP origin",
                )


class StableDecisionTests(unittest.TestCase):
    def test_rejection_codes_are_typed_and_stable(self):
        self.assertEqual(
            {code.value for code in SecurityRejectionCode},
            {
                "bind_host_rejected",
                "headers_malformed",
                "authorization_missing",
                "authorization_duplicate",
                "authorization_malformed",
                "authorization_rejected",
                "origin_duplicate",
                "origin_rejected",
                "session_id_missing",
                "session_id_duplicate",
                "session_id_invalid",
                "body_size_invalid",
                "body_too_large",
            },
        )

    def test_validation_result_is_frozen_internally_consistent_and_log_safe(self):
        allowed = SecurityValidation(accepted=True)
        rejected = SecurityValidation(
            accepted=False,
            rejection=SecurityRejection(
                SecurityRejectionCode.AUTHORIZATION_REJECTED
            ),
        )

        self.assertIsNone(allowed.code)
        self.assertEqual(
            rejected.code,
            SecurityRejectionCode.AUTHORIZATION_REJECTED,
        )
        self.assertNotIn(TOKEN, repr(rejected))
        self.assertNotIn(TOKEN, str(rejected))
        with self.assertRaises(FrozenInstanceError):
            rejected.accepted = True  # type: ignore[misc]
        for invalid in (
            {"accepted": True, "rejection": rejected.rejection},
            {"accepted": False, "rejection": None},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                SecurityValidation(**invalid)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            SecurityRejection("authorization_rejected")  # type: ignore[arg-type]


class BindAndBodyValidationTests(unittest.TestCase):
    def test_only_exact_ipv4_loopback_bind_host_is_allowed(self):
        value = policy()

        self.assertTrue(value.validate_bind_host("127.0.0.1").accepted)
        for rejected_host in (
            "localhost",
            "::1",
            "[::1]",
            "0.0.0.0",
            "127.0.0.2",
            "127.0.0.1 ",
            "127.0.0.1:43117",
            "",
        ):
            with self.subTest(host=rejected_host):
                decision = value.validate_bind_host(rejected_host)
                self.assertFalse(decision.accepted)
                self.assertEqual(
                    decision.code,
                    SecurityRejectionCode.BIND_HOST_REJECTED,
                )

    def test_body_size_accepts_zero_and_the_fixed_inclusive_boundary(self):
        value = policy()

        self.assertTrue(value.validate_body_size(0).accepted)
        self.assertTrue(value.validate_body_size(LEARNER_BODY_LIMIT_BYTES).accepted)

    def test_body_size_rejects_over_limit_negative_boolean_and_non_integer_values(self):
        value = policy()

        over_limit = value.validate_body_size(LEARNER_BODY_LIMIT_BYTES + 1)
        self.assertEqual(over_limit.code, SecurityRejectionCode.BODY_TOO_LARGE)
        for invalid_size in (-1, True, 1.0, "12", None):
            with self.subTest(body_size=invalid_size):
                decision = value.validate_body_size(invalid_size)  # type: ignore[arg-type]
                self.assertEqual(
                    decision.code,
                    SecurityRejectionCode.BODY_SIZE_INVALID,
                )


class AuthorizationHeaderTests(unittest.TestCase):
    def test_raw_asgi_bytes_and_case_insensitive_header_names_are_accepted(self):
        value = policy()
        headers = [
            (b"aUtHoRiZaTiOn", f"bEaReR {TOKEN}".encode("ascii")),
            (b"X-WAYLINE-SESSION-ID", b"session-001"),
            (b"oRiGiN", UNITY_ORIGIN.encode("ascii")),
        ]

        decision = value.validate_request_headers(
            headers,
            session_scope_required=True,
        )

        self.assertTrue(decision.accepted)

    def test_missing_authorization_is_rejected(self):
        decision = policy().validate_request_headers(
            [(SESSION_HEADER_NAME, "session-001")],
            session_scope_required=True,
        )

        self.assertEqual(
            decision.code,
            SecurityRejectionCode.AUTHORIZATION_MISSING,
        )

    def test_mixed_case_duplicate_authorization_headers_are_rejected(self):
        headers = authenticated_headers()
        headers.append(("authorization", f"Bearer {TOKEN}"))

        decision = policy().validate_request_headers(
            headers,
            session_scope_required=True,
        )

        self.assertEqual(
            decision.code,
            SecurityRejectionCode.AUTHORIZATION_DUPLICATE,
        )

    def test_malformed_authorization_never_falls_through_to_token_comparison(self):
        malformed_values = (
            f"Basic {TOKEN}",
            f"Bearer  {TOKEN}",
            f"Bearer\t{TOKEN}",
            f" Bearer {TOKEN}",
            f"Bearer {TOKEN} ",
            f"Bearer {TOKEN},Bearer {TOKEN}",
            "Bearer short",
            f"Bearer {TOKEN.upper()}",
            "Bearer " + "g" * 64,
        )
        value = policy()
        for malformed in malformed_values:
            with self.subTest(authorization=malformed):
                headers = authenticated_headers()
                headers[0] = (AUTHORIZATION_HEADER_NAME, malformed)
                with patch(
                    "services.wayline_forge.app.loopback_security.hmac.compare_digest",
                    wraps=hmac.compare_digest,
                ) as compare_digest:
                    decision = value.validate_request_headers(
                        headers,
                        session_scope_required=True,
                    )
                self.assertEqual(
                    decision.code,
                    SecurityRejectionCode.AUTHORIZATION_MALFORMED,
                )
                compare_digest.assert_not_called()

    def test_well_formed_credentials_use_constant_time_comparison(self):
        wrong_token = "f" * 64
        self.assertNotEqual(wrong_token, TOKEN)
        value = policy()

        with patch(
            "services.wayline_forge.app.loopback_security.hmac.compare_digest",
            wraps=hmac.compare_digest,
        ) as compare_digest:
            decision = value.validate_request_headers(
                authenticated_headers(token=wrong_token),
                session_scope_required=True,
            )

        self.assertEqual(
            decision.code,
            SecurityRejectionCode.AUTHORIZATION_REJECTED,
        )
        compare_digest.assert_called_once_with(wrong_token, TOKEN)

    def test_matching_credentials_also_use_constant_time_comparison(self):
        value = policy()

        with patch(
            "services.wayline_forge.app.loopback_security.hmac.compare_digest",
            wraps=hmac.compare_digest,
        ) as compare_digest:
            decision = value.validate_request_headers(
                authenticated_headers(),
                session_scope_required=True,
            )

        self.assertTrue(decision.accepted)
        compare_digest.assert_called_once_with(TOKEN, TOKEN)


class OriginHeaderTests(unittest.TestCase):
    def test_absent_origin_is_allowed_for_native_unity_requests(self):
        decision = policy().validate_request_headers(
            authenticated_headers(origin=None),
            session_scope_required=True,
        )

        self.assertTrue(decision.accepted)

    def test_only_exact_configured_origin_is_allowed(self):
        rejected_origins = (
            "null",
            "*",
            "",
            " http://127.0.0.1:43117",
            "http://127.0.0.1:43117 ",
            "http://127.0.0.1:43117/",
            "https://127.0.0.1:43117",
            "http://127.0.0.1:43118",
            "http://localhost:43117",
            "http://[::1]:43117",
            "http://127.0.0.2:43117",
            "http://127.0.0.1.example:43117",
            "http://127.0.0.1@evil.example:43117",
            "http://evil.example@127.0.0.1:43117",
            "HTTP://127.0.0.1:43117",
            "http://127.0.0.1:43117,http://evil.example",
        )
        value = policy()
        for rejected_origin in rejected_origins:
            with self.subTest(origin=rejected_origin):
                decision = value.validate_request_headers(
                    authenticated_headers(origin=rejected_origin),
                    session_scope_required=True,
                )
                self.assertEqual(
                    decision.code,
                    SecurityRejectionCode.ORIGIN_REJECTED,
                )

    def test_duplicate_origin_is_rejected_even_when_values_match(self):
        headers = authenticated_headers()
        headers.append(("origin", UNITY_ORIGIN))

        decision = policy().validate_request_headers(
            headers,
            session_scope_required=True,
        )

        self.assertEqual(decision.code, SecurityRejectionCode.ORIGIN_DUPLICATE)


class SessionHeaderTests(unittest.TestCase):
    def test_session_header_is_required_only_for_session_scoped_routes(self):
        no_session = authenticated_headers(session_id=None)
        value = policy()

        required = value.validate_request_headers(
            no_session,
            session_scope_required=True,
        )
        not_required = value.validate_request_headers(
            no_session,
            session_scope_required=False,
        )

        self.assertEqual(required.code, SecurityRejectionCode.SESSION_ID_MISSING)
        self.assertTrue(not_required.accepted)

    def test_valid_session_identifiers_follow_the_public_contract_grammar(self):
        valid_session_ids = (
            "abc",
            "session-001",
            "S:abc_def.1",
            "a" + "z" * 95,
        )
        value = policy()
        for session_id in valid_session_ids:
            with self.subTest(session_id=session_id):
                decision = value.validate_request_headers(
                    authenticated_headers(session_id=session_id),
                    session_scope_required=True,
                )
                self.assertTrue(decision.accepted)

    def test_invalid_session_identifiers_are_rejected(self):
        invalid_session_ids = (
            "ab",
            "a" + "z" * 96,
            "-session",
            "_session",
            "session id",
            "session/id",
            "session@id",
            "séssion",
            "session\n001",
            "",
        )
        value = policy()
        for session_id in invalid_session_ids:
            with self.subTest(session_id=session_id):
                decision = value.validate_request_headers(
                    authenticated_headers(session_id=session_id),
                    session_scope_required=True,
                )
                self.assertEqual(
                    decision.code,
                    SecurityRejectionCode.SESSION_ID_INVALID,
                )

    def test_mixed_case_duplicate_session_headers_are_rejected(self):
        headers = authenticated_headers()
        headers.append(("x-wayline-session-id", "session-001"))

        decision = policy().validate_request_headers(
            headers,
            session_scope_required=True,
        )

        self.assertEqual(
            decision.code,
            SecurityRejectionCode.SESSION_ID_DUPLICATE,
        )


class HeaderContainerAndCombinedValidationTests(unittest.TestCase):
    def test_malformed_raw_header_shapes_or_non_ascii_security_headers_are_rejected(self):
        invalid_headers = (
            [("Authorization",)],
            [("Authorization", f"Bearer {TOKEN}", "extra")],
            [("Authorizatiön", f"Bearer {TOKEN}")],
            [(b"authorization\xff", f"Bearer {TOKEN}".encode("ascii"))],
            [(b"authorization", b"Bearer \xff")],
            [([], f"Bearer {TOKEN}")],
        )
        value = policy()
        for headers in invalid_headers:
            with self.subTest(headers=headers):
                decision = value.validate_request_headers(  # type: ignore[arg-type]
                    headers,
                    session_scope_required=False,
                )
                self.assertEqual(
                    decision.code,
                    SecurityRejectionCode.HEADERS_MALFORMED,
                )

    def test_header_container_must_preserve_duplicates_not_be_a_mapping_or_text(self):
        invalid_containers = (
            {AUTHORIZATION_HEADER_NAME: f"Bearer {TOKEN}"},
            AUTHORIZATION_HEADER_NAME,
            b"authorization",
            None,
        )
        value = policy()
        for headers in invalid_containers:
            with self.subTest(headers=headers):
                decision = value.validate_request_headers(  # type: ignore[arg-type]
                    headers,
                    session_scope_required=False,
                )
                self.assertEqual(
                    decision.code,
                    SecurityRejectionCode.HEADERS_MALFORMED,
                )

    def test_combined_request_validation_checks_headers_then_body(self):
        value = policy()

        accepted = value.validate_request(
            headers=authenticated_headers(),
            body_size=LEARNER_BODY_LIMIT_BYTES,
            session_scope_required=True,
        )
        body_rejected = value.validate_request(
            headers=authenticated_headers(),
            body_size=LEARNER_BODY_LIMIT_BYTES + 1,
            session_scope_required=True,
        )
        headers_win = value.validate_request(
            headers=[],
            body_size=LEARNER_BODY_LIMIT_BYTES + 1,
            session_scope_required=True,
        )

        self.assertTrue(accepted.accepted)
        self.assertEqual(body_rejected.code, SecurityRejectionCode.BODY_TOO_LARGE)
        self.assertEqual(
            headers_win.code,
            SecurityRejectionCode.AUTHORIZATION_MISSING,
        )


class DependencyBoundaryTests(unittest.TestCase):
    def test_security_core_imports_only_standard_library_modules(self):
        source_path = Path(loopback_security.__file__).resolve()
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

        self.assertLessEqual(imported_roots, set(sys.stdlib_module_names))


if __name__ == "__main__":
    unittest.main()
