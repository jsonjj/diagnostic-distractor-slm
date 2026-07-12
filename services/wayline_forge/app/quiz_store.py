"""Atomic SQLite persistence for immutable :mod:`quiz_machine` snapshots.

The caller performs question generation, sealed scoring, and event construction
before calling this module.  The store authoritatively reconstructs opaque private
batch material outside bounded writes, then atomically commits the sole ready-time
seal.  Reveal writes atomically commit the machine, receipt, and durable outbox.
The later ProfileStore handoff is intentionally an idempotent reconciliation, not
a false cross-connection transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import TYPE_CHECKING, Any, Iterable

from services.wayline_forge.app.batch_material import (
    BatchContext,
    BatchMaterialValidationError,
    VerifiedBatchMaterial,
)
from services.wayline_forge.app.contracts import BattleQuizRequest, PublicQuizBatch
from services.wayline_forge.app.events import (
    ObservationEvent,
    canonical_event_json,
    event_from_json,
)

from services.wayline_forge.app.providers.distractor import PinnedSlmManifest
from services.wayline_forge.app.question_kernel import QuestionCompiler

from services.wayline_forge.app.quiz_machine import (
    ALLOWED_CONFIDENCE,
    SCHEMA_VERSION as PUBLIC_SCHEMA_VERSION,
    FinalItemResult,
    FinalQuizResult,
    IdempotencyConflictError,
    PublicWrongCountResult,
    QuizItemLayout,
    QuizMachine,
    QuizSelection,
    QuizState,
    QuizSubmission,
    RevealedSelectionResult,
    StaleQuizStateError,
    TransitionReceipt,
    close_quiz,
    mark_ready,
    new_quiz,
)
from services.wayline_forge.app.slot_materializer import MaterializedSlot

if TYPE_CHECKING:
    from services.wayline_forge.app.profile_store import ProfileStore


STORE_SCHEMA_VERSION = 6
MACHINE_SCHEMA_VERSION = "wayline.quiz-machine-store.v2"
RECEIPT_SCHEMA_VERSION = "wayline.transition-receipt-store.v1"
STORED_RECEIPT_SCHEMA_VERSION = "wayline.stored-transition-receipt.v2"
PREPARATION_RECEIPT_SCHEMA_VERSION = "wayline.preparation-receipt.v1"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,95}")
_RECEIPT_ACTIONS = frozenset(("initial", "revision"))


class QuizStoreError(RuntimeError):
    """Base class for durable quiz-store failures."""


class QuizNotFoundError(QuizStoreError):
    """Raised when the requested batch does not exist."""


class QuizAlreadyExistsError(QuizStoreError):
    """Raised when a batch identifier is created more than once."""


class QuizStoreCorruptionError(QuizStoreError):
    """Raised when canonical JSON, hashes, indexes, or state invariants disagree."""


class QuizStoreBusyError(QuizStoreError):
    """Raised when SQLite cannot acquire the bounded write lock."""


class QuizTransitionConflictError(QuizStoreError):
    """Raised when a distinct transition tries to reuse an action slot."""


class QuizOwnershipError(QuizStoreError):
    """Raised when a batch is accessed outside its immutable local profile."""


class QuizStoreSchemaError(QuizStoreError):
    """Raised when a quiz database cannot be opened without an unsafe migration."""


@dataclass(frozen=True)
class StoredTransition:
    """The authoritative post-write machine and optional transition receipt."""

    machine: QuizMachine
    receipt: TransitionReceipt | None
    replayed: bool


@dataclass(frozen=True, slots=True)
class PreparationReceipt:
    """Immutable idempotency receipt for one prepared public quiz batch."""

    schema_version: str
    action: str
    profile_id: str
    request_id: str
    batch_id: str
    payload_sha256: str
    batch_material_sha256: str
    plan_sha256: str
    output_sha256: str
    receipt_sha256: str


@dataclass(frozen=True, slots=True)
class StoredPreparation:
    """Authoritative persisted preparation outcome and replay marker."""

    machine: QuizMachine
    material: VerifiedBatchMaterial
    receipt: PreparationReceipt
    replayed: bool

    @property
    def public_output(self) -> PublicQuizBatch:
        """Return the exact immutable learner-facing output bound by the receipt."""

        return self.material.public_batch


@dataclass(frozen=True)
class _PreparedObservation:
    event: ObservationEvent
    canonical_json: str
    event_sha256: str
    delivered: bool = False


@dataclass(frozen=True)
class _PreparedBatchMaterial:
    material: VerifiedBatchMaterial
    private_json: str
    private_json_sha256: str
    batch_material_sha256: str
    sealed_quiz_sha256: str
    context_json: str
    context_sha256: str
    item_count: int


class _DecodeError(ValueError):
    pass


class QuizStore:
    """One SQLite connection owning atomic, optimistic quiz snapshots."""

    def __init__(
        self,
        path: Path | str,
        *,
        timeout_seconds: float = 0.25,
        compiler: QuestionCompiler | None = None,
        manifest: PinnedSlmManifest | None = None,
        allow_unverified_test_material: bool = False,
    ):
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, (int, float)
        ):
            raise ValueError("timeout_seconds must be a nonnegative number")
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be a nonnegative number")
        if type(allow_unverified_test_material) is not bool:
            raise ValueError("allow_unverified_test_material must be a boolean")
        if (compiler is None) != (manifest is None):
            raise ValueError("compiler and manifest must be supplied together")
        if allow_unverified_test_material and (
            compiler is not None or manifest is not None
        ):
            raise ValueError(
                "the test-only unverified mode cannot receive production authority"
            )
        self.path = Path(path)
        self._compiler = compiler
        self._manifest = manifest
        # This escape hatch exists only for the older synthetic SealedQuiz unit
        # fixtures.  Runtime callers must retain the secure False default.
        self._allow_unverified_test_material = allow_unverified_test_material
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: sqlite3.Connection | None = sqlite3.connect(
            self.path,
            timeout=float(timeout_seconds),
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA secure_delete = ON")
        self._connection.execute(
            f"PRAGMA busy_timeout = {max(0, round(float(timeout_seconds) * 1000))}"
        )
        self._connection.execute("PRAGMA journal_mode = WAL")
        # Private deterministic fault injection used only by transaction tests.
        self._failpoint_stage: str | None = None
        try:
            self._ensure_schema()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> "QuizStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def machine_json(self, machine: QuizMachine) -> str:
        """Return the exact canonical representation written to SQLite."""

        _validate_machine(machine)
        return _canonical_json(_machine_payload(machine))

    def create(self, machine: QuizMachine, *, profile_id: str) -> QuizMachine:
        """Create one preparing snapshot; duplicate batch IDs fail closed."""

        owner = _profile_id(profile_id)
        if machine.state is not QuizState.PREPARING or machine.version != 0:
            raise QuizTransitionConflictError(
                "only a version-zero preparing machine can be created"
            )
        canonical = self.machine_json(machine)
        digest = _sha256_text(canonical)
        connection = self._require_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_live_batch_slot_in_transaction(
                    owner,
                    machine.batch_id,
                )
                connection.execute(
                    """
                    INSERT INTO quiz_machines (
                        batch_id, profile_id, state, version,
                        machine_json, machine_sha256, batch_material_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        machine.batch_id,
                        owner,
                        machine.state.value,
                        machine.version,
                        canonical,
                        digest,
                    ),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        except sqlite3.IntegrityError as exc:
            raise QuizAlreadyExistsError(
                f"quiz batch already exists: {machine.batch_id}"
            ) from exc
        except sqlite3.OperationalError as exc:
            self._raise_operational(exc)
        return machine

    def load_preparation(
        self,
        request: BattleQuizRequest,
        *,
        profile_id: str,
    ) -> StoredPreparation | None:
        """Load one exact prepared-batch replay, or return ``None`` on a miss."""

        owner, payload_sha256 = _preparation_request_identity(request, profile_id)
        connection = self._require_connection()
        try:
            connection.execute("BEGIN")
            try:
                row = connection.execute(
                    """
                    SELECT * FROM quiz_preparation_receipts
                    WHERE profile_id = ? AND request_id = ?
                    """,
                    (owner, request.request_id),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None
                receipt = _preparation_receipt_from_row(row)
                if receipt.payload_sha256 != payload_sha256:
                    raise IdempotencyConflictError(
                        "request_id was reused with a different preparation payload"
                    )
                profile_machines = (
                    self._validated_profile_machines_in_transaction(owner)
                )
                machine = next(
                    (
                        existing
                        for existing in profile_machines
                        if existing.batch_id == receipt.batch_id
                    ),
                    None,
                )
                if machine is None:
                    raise QuizStoreCorruptionError(
                        "preparation receipt has no verified owning machine"
                    )
                prepared = self._load_material_in_transaction(machine, owner)
                if prepared is None:  # pragma: no cover - strict store invariant
                    raise QuizStoreCorruptionError(
                        "prepared quiz is missing verified private material"
                    )
                _validate_preparation_binding(
                    receipt,
                    request,
                    owner,
                    machine,
                    prepared.material,
                )
                connection.commit()
                return StoredPreparation(
                    machine=machine,
                    material=prepared.material,
                    receipt=receipt,
                    replayed=True,
                )
            except BaseException:
                connection.rollback()
                raise
        except sqlite3.OperationalError as exc:
            self._raise_operational(exc)
        raise AssertionError("unreachable")

    def create_prepared(
        self,
        material: VerifiedBatchMaterial,
        *,
        request: BattleQuizRequest,
        profile_id: str,
    ) -> StoredPreparation:
        """Atomically create a fully verified READY batch and its receipt.

        Compiler replay, canonical serialization, public-output hashing, and
        READY-machine construction all finish before the bounded write lock.
        A concurrent exact winner is replayed; its newly generated competitor
        material is deliberately ignored.
        """

        owner, payload_sha256 = _preparation_request_identity(request, profile_id)
        existing = self.load_preparation(request, profile_id=owner)
        if existing is not None:
            return existing

        if type(material) is not VerifiedBatchMaterial:
            raise QuizTransitionConflictError(
                "batch material must be exact VerifiedBatchMaterial"
            )
        _validate_material_request_binding(material, request, owner)
        try:
            preparing = new_quiz(material.batch_id, _material_layouts(material))
            ready = mark_ready(
                preparing,
                sealed_quiz=material.sealed_quiz,
                expected_version=preparing.version,
            )
        except (TypeError, ValueError) as exc:
            raise QuizTransitionConflictError(
                "verified material cannot construct a READY quiz machine"
            ) from exc
        prepared = self._prepare_supplied_material(
            material,
            ready,
            owner,
            expected_context=material.context,
            planned_slots=None,
        )
        authoritative = prepared.material
        _validate_material_request_binding(authoritative, request, owner)
        output_sha256 = _canonical_sha256(authoritative.public_payload())
        receipt = _make_preparation_receipt(
            request=request,
            profile_id=owner,
            batch_id=authoritative.batch_id,
            payload_sha256=payload_sha256,
            batch_material_sha256=authoritative.batch_material_sha256,
            plan_sha256=authoritative.plan_sha256,
            output_sha256=output_sha256,
        )
        receipt_json = _canonical_json(_preparation_receipt_payload(receipt))
        receipt_json_sha256 = _sha256_text(receipt_json)
        machine_json = self.machine_json(ready)
        machine_sha256 = _sha256_text(machine_json)

        connection = self._require_connection()
        replay_after_lock = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing_row = connection.execute(
                    """
                    SELECT * FROM quiz_preparation_receipts
                    WHERE profile_id = ? AND request_id = ?
                    """,
                    (owner, request.request_id),
                ).fetchone()
                if existing_row is not None:
                    existing_receipt = _preparation_receipt_from_row(existing_row)
                    if existing_receipt.payload_sha256 != payload_sha256:
                        raise IdempotencyConflictError(
                            "request_id was reused with a different preparation payload"
                        )
                    connection.commit()
                    replay_after_lock = True
                else:
                    self._require_live_batch_slot_in_transaction(
                        owner,
                        ready.batch_id,
                    )
                    connection.execute(
                        """
                        INSERT INTO quiz_machines (
                            batch_id, profile_id, state, version,
                            machine_json, machine_sha256, batch_material_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            ready.batch_id,
                            owner,
                            ready.state.value,
                            ready.version,
                            machine_json,
                            machine_sha256,
                            prepared.batch_material_sha256,
                        ),
                    )
                    self._run_failpoint("after_prepared_machine_insert")
                    self._insert_material_in_transaction(owner, prepared)
                    self._run_failpoint("after_prepared_material_insert")
                    connection.execute(
                        """
                        INSERT INTO quiz_preparation_receipts (
                            profile_id, request_id, batch_id, payload_sha256,
                            output_sha256, receipt_json, receipt_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            owner,
                            request.request_id,
                            ready.batch_id,
                            payload_sha256,
                            output_sha256,
                            receipt_json,
                            receipt_json_sha256,
                        ),
                    )
                    self._run_failpoint("after_preparation_receipt_insert")
                    connection.commit()
            except BaseException:
                connection.rollback()
                raise
        except sqlite3.IntegrityError as exc:
            raise QuizTransitionConflictError(
                "prepared batch identity already exists"
            ) from exc
        except sqlite3.OperationalError as exc:
            self._raise_operational(exc)

        if replay_after_lock:
            replay = self.load_preparation(request, profile_id=owner)
            if replay is None:  # pragma: no cover - deletion race fail-closed guard
                raise QuizStoreCorruptionError(
                    "concurrent prepared-batch receipt disappeared"
                )
            return replay
        return StoredPreparation(
            machine=ready,
            material=authoritative,
            receipt=receipt,
            replayed=False,
        )

    def load(
        self,
        batch_id: str,
        *,
        profile_id: str,
        expected_context: BatchContext | None = None,
        planned_slots: Iterable[MaterializedSlot] | None = None,
    ) -> QuizMachine:
        """Load and fully verify one snapshot and all of its receipt rows."""

        owner = _profile_id(profile_id)
        if not _valid_identifier(batch_id):
            raise QuizNotFoundError("quiz batch was not found")
        connection = self._require_connection()
        try:
            connection.execute("BEGIN")
            try:
                machine = self._load_in_transaction(
                    batch_id,
                    owner,
                    expected_context=expected_context,
                    planned_slots=planned_slots,
                )
                connection.commit()
                return machine
            except BaseException:
                connection.rollback()
                raise
        except sqlite3.OperationalError as exc:
            self._raise_operational(exc)
        raise AssertionError("unreachable")

    def close_revealed(self, batch_id: str, *, profile_id: str) -> QuizMachine:
        """Idempotently acknowledge one revealed batch as fully consumed."""

        owner = _profile_id(profile_id)
        for _attempt in range(2):
            machine = self.load(batch_id, profile_id=owner)
            if machine.state is QuizState.CLOSED:
                return machine
            if machine.state is not QuizState.REVEALED:
                raise QuizTransitionConflictError(
                    "only a revealed quiz can be closed"
                )
            closed = close_quiz(machine, expected_version=machine.version)
            try:
                stored = self.save_transition(
                    closed,
                    profile_id=owner,
                    expected_version=machine.version,
                )
            except StaleQuizStateError:
                continue
            if stored.machine.state is not QuizState.CLOSED:
                raise QuizStoreCorruptionError(
                    "close transition did not persist a closed quiz"
                )
            return stored.machine
        machine = self.load(batch_id, profile_id=owner)
        if machine.state is QuizState.CLOSED:
            return machine
        raise QuizTransitionConflictError(
            "quiz changed repeatedly while closing"
        )

    def load_batch_material(
        self,
        batch_id: str,
        *,
        profile_id: str,
        expected_context: BatchContext | None = None,
        planned_slots: Iterable[MaterializedSlot] | None = None,
    ) -> VerifiedBatchMaterial:
        """Reconstruct and return the exact server-only material for a batch."""

        owner = _profile_id(profile_id)
        if not _valid_identifier(batch_id):
            raise QuizNotFoundError("quiz batch was not found")
        slots = None if planned_slots is None else tuple(planned_slots)
        connection = self._require_connection()
        try:
            connection.execute("BEGIN")
            try:
                machine = self._load_in_transaction(
                    batch_id,
                    owner,
                    expected_context=expected_context,
                    planned_slots=slots,
                )
                prepared = self._load_material_in_transaction(
                    machine,
                    owner,
                    expected_context=expected_context,
                    planned_slots=slots,
                )
                if prepared is None:
                    raise QuizTransitionConflictError(
                        "preparing quizzes do not yet own private batch material"
                    )
                connection.commit()
                return prepared.material
            except BaseException:
                connection.rollback()
                raise
        except sqlite3.OperationalError as exc:
            self._raise_operational(exc)
        raise AssertionError("unreachable")

    def resumable_batch_id(self, profile_id: str) -> str | None:
        """Return the sole fully verified non-closed batch for one profile.

        This is deliberately a read-only integrity check, not merely an index
        lookup.  A resumable row must survive the same machine, private-material,
        preparation/transition-receipt, and outbox replay used by ``load``.
        """

        owner = _profile_id(profile_id)
        connection = self._require_connection()
        try:
            connection.execute("BEGIN")
            try:
                rows = connection.execute(
                    """
                    SELECT batch_id FROM (
                        SELECT batch_id FROM quiz_machines WHERE profile_id = ?
                        UNION
                        SELECT batch_id FROM quiz_batch_material WHERE profile_id = ?
                        UNION
                        SELECT batch_id FROM quiz_preparation_receipts WHERE profile_id = ?
                        UNION
                        SELECT batch_id FROM quiz_transition_receipts WHERE profile_id = ?
                        UNION
                        SELECT batch_id FROM quiz_observation_outbox WHERE profile_id = ?
                    ) ORDER BY batch_id
                    """,
                    (owner, owner, owner, owner, owner),
                ).fetchall()
                resumable: list[str] = []
                for row in rows:
                    batch_id = row["batch_id"]
                    if not _valid_identifier(batch_id):
                        raise QuizStoreCorruptionError(
                            "profile quiz indexes contain an invalid batch identity"
                        )
                    machine_row = connection.execute(
                        "SELECT profile_id, state FROM quiz_machines "
                        "WHERE batch_id = ?",
                        (batch_id,),
                    ).fetchone()
                    if machine_row is None:
                        raise QuizStoreCorruptionError(
                            "profile quiz index has no owning machine"
                        )
                    owner_rows = connection.execute(
                        """
                        SELECT profile_id FROM quiz_machines WHERE batch_id = ?
                        UNION ALL
                        SELECT profile_id FROM quiz_batch_material WHERE batch_id = ?
                        UNION ALL
                        SELECT profile_id FROM quiz_preparation_receipts WHERE batch_id = ?
                        UNION ALL
                        SELECT profile_id FROM quiz_transition_receipts WHERE batch_id = ?
                        UNION ALL
                        SELECT profile_id FROM quiz_observation_outbox WHERE batch_id = ?
                        """,
                        (batch_id, batch_id, batch_id, batch_id, batch_id),
                    ).fetchall()
                    indexed_owners = tuple(item["profile_id"] for item in owner_rows)
                    if (
                        not indexed_owners
                        or any(
                            not _valid_identifier(indexed_owner)
                            or indexed_owner != owner
                            for indexed_owner in indexed_owners
                        )
                    ):
                        raise QuizStoreCorruptionError(
                            "resumable quiz ownership indexes disagree"
                        )
                    stored_state = machine_row["state"]
                    if stored_state == QuizState.CLOSED.value:
                        continue
                    resumable.append(batch_id)

                if len(resumable) > 1:
                    raise QuizStoreCorruptionError(
                        "profile has more than one resumable quiz batch"
                    )
                if not resumable:
                    connection.commit()
                    return None
                batch_id = resumable[0]
                machine = self._load_in_transaction(batch_id, owner)
                if machine.state is QuizState.CLOSED:
                    raise QuizStoreCorruptionError(
                        "resumable quiz index names a closed batch"
                    )
                connection.commit()
                return machine.batch_id
            except BaseException:
                connection.rollback()
                raise
        except sqlite3.OperationalError as exc:
            self._raise_operational(exc)
        raise AssertionError("unreachable")

    def next_profile_ordinal(self, profile_id: str) -> int:
        """Return the next unreserved contiguous ordinal for a local profile.

        This is an advisory planning read.  Reveal insertion rechecks the same
        value under ``BEGIN IMMEDIATE`` before reserving its complete range.
        """

        owner = _profile_id(profile_id)
        connection = self._require_connection()
        try:
            connection.execute("BEGIN")
            try:
                value = self._next_profile_ordinal_in_transaction(owner)
                connection.commit()
                return value
            except BaseException:
                connection.rollback()
                raise
        except sqlite3.OperationalError as exc:
            self._raise_operational(exc)
        raise AssertionError("unreachable")

    def save_transition(
        self,
        next_machine: QuizMachine,
        *,
        profile_id: str,
        expected_version: int,
        receipt: TransitionReceipt | None = None,
        observation_events: Iterable[ObservationEvent] = (),
        observation_session_id: str | None = None,
        batch_material: VerifiedBatchMaterial | None = None,
        expected_context: BatchContext | None = None,
        planned_slots: Iterable[MaterializedSlot] | None = None,
    ) -> StoredTransition:
        """Optimistically store a transition and optional receipt in one write.

        Canonicalization and semantic validation happen before ``BEGIN
        IMMEDIATE``.  Thus the bounded transaction never performs provider work,
        scoring, JSON construction, or an await.
        """

        owner = _profile_id(profile_id)
        if isinstance(expected_version, bool) or not isinstance(expected_version, int):
            raise StaleQuizStateError("expected_version must be an integer")
        slots = None if planned_slots is None else tuple(planned_slots)
        next_json = self.machine_json(next_machine)
        next_digest = _sha256_text(next_json)
        incoming_material: _PreparedBatchMaterial | None = None
        if batch_material is not None:
            incoming_material = self._prepare_supplied_material(
                batch_material,
                next_machine,
                owner,
                expected_context=expected_context,
                planned_slots=slots,
            )
        persisted_material: _PreparedBatchMaterial | None = None
        if batch_material is None and next_machine.state is not QuizState.PREPARING:
            persisted_material = self._preload_material_for_write(
                next_machine,
                owner,
                expected_context=expected_context,
                planned_slots=slots,
            )
            if (
                persisted_material is None
                and not self._allow_unverified_test_material
            ):
                raise QuizTransitionConflictError(
                    "ready or later transitions require persisted verified material"
                )
        prepared_observations, outbox_sha256 = _prepare_observations(
            next_machine,
            owner,
            observation_events,
            observation_session_id=observation_session_id,
            material=(
                None
                if persisted_material is None
                else persisted_material.material
            ),
        )
        receipt_json: str | None = None
        receipt_digest: str | None = None
        if receipt is not None:
            _validate_receipt(receipt)
            if receipt.batch_id != next_machine.batch_id:
                raise QuizTransitionConflictError(
                    "receipt batch_id does not match the machine"
                )
            receipt_json = _canonical_json(
                _stored_receipt_payload(receipt, outbox_sha256)
            )
            receipt_digest = _sha256_text(receipt_json)

        connection = self._require_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._load_in_transaction(
                    next_machine.batch_id,
                    owner,
                    prepared_material=persisted_material,
                    expected_context=expected_context,
                    planned_slots=slots,
                )

                seals_material = (
                    current.state is QuizState.PREPARING
                    and next_machine.state is QuizState.READY
                )
                if incoming_material is not None and not seals_material:
                    raise QuizTransitionConflictError(
                        "private material can be stored only by PREPARING -> READY"
                    )
                if (
                    seals_material
                    and incoming_material is None
                    and not self._allow_unverified_test_material
                ):
                    raise QuizTransitionConflictError(
                        "PREPARING -> READY requires exact verified batch material"
                    )

                if receipt is not None:
                    replay = self._existing_receipt_outcome(
                        current,
                        owner,
                        receipt,
                        receipt_json,
                        next_machine,
                        prepared_observations,
                        outbox_sha256,
                        observation_session_id,
                        None
                        if persisted_material is None
                        else persisted_material.material,
                    )
                    if replay is not None:
                        connection.commit()
                        return replay

                locked_replay = _locked_snapshot_replay(
                    current,
                    next_machine,
                    receipt,
                    expected_version=expected_version,
                )
                if locked_replay is not None:
                    connection.commit()
                    return locked_replay

                if current.version != expected_version:
                    raise StaleQuizStateError(
                        f"stale quiz version {expected_version}; "
                        f"current version is {current.version}"
                    )
                if next_machine.version <= expected_version:
                    raise StaleQuizStateError(
                        "the next quiz version must advance the stored row version"
                    )

                new_receipt = _validate_transition(current, next_machine, receipt)
                reaches_reveal = _validate_observation_transition(
                    current,
                    next_machine,
                    owner,
                    prepared_observations,
                    observation_session_id=observation_session_id,
                    material=(
                        None
                        if persisted_material is None
                        else persisted_material.material
                    ),
                )
                if reaches_reveal:
                    expected_ordinal = self._next_profile_ordinal_in_transaction(owner)
                    actual_ordinals = tuple(sorted(
                        prepared.event.ordinal
                        for prepared in prepared_observations
                    ))
                    expected_ordinals = tuple(range(
                        expected_ordinal,
                        expected_ordinal + len(prepared_observations),
                    ))
                    if actual_ordinals != expected_ordinals:
                        raise QuizTransitionConflictError(
                            "reveal observations must reserve the next contiguous "
                            f"profile ordinals beginning at {expected_ordinal}"
                        )
                if new_receipt is not None:
                    assert receipt_json is not None and receipt_digest is not None
                    try:
                        connection.execute(
                            """
                            INSERT INTO quiz_transition_receipts (
                                batch_id, profile_id, action, request_id, payload_sha256,
                                from_version, to_version, output_sha256,
                                receipt_json, receipt_sha256, outbox_sha256
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                new_receipt.batch_id,
                                owner,
                                new_receipt.action,
                                new_receipt.request_id,
                                new_receipt.payload_sha256,
                                new_receipt.from_version,
                                new_receipt.to_version,
                                new_receipt.output_sha256,
                                receipt_json,
                                receipt_digest,
                                outbox_sha256,
                            ),
                        )
                    except sqlite3.IntegrityError as exc:
                        raise QuizTransitionConflictError(
                            "a transition receipt already owns this action or request"
                        ) from exc

                if incoming_material is not None:
                    self._insert_material_in_transaction(
                        owner,
                        incoming_material,
                    )
                    self._run_failpoint("after_material_insert")

                self._run_failpoint("after_receipt_insert")
                for prepared in prepared_observations:
                    event = prepared.event
                    try:
                        connection.execute(
                            """
                            INSERT INTO quiz_observation_outbox (
                                profile_id, batch_id, item_id, ordinal,
                                event_id, idempotency_id, canonical_json,
                                event_sha256, delivered
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                            """,
                            (
                                owner,
                                event.batch_id,
                                event.item_id,
                                event.ordinal,
                                event.event_id,
                                event.idempotency_id,
                                prepared.canonical_json,
                                prepared.event_sha256,
                            ),
                        )
                    except sqlite3.IntegrityError as exc:
                        raise QuizTransitionConflictError(
                            "observation identity or ordinal already exists"
                        ) from exc
                self._run_failpoint("after_outbox_insert")
                cursor = connection.execute(
                    """
                    UPDATE quiz_machines
                    SET state = ?, version = ?, machine_json = ?, machine_sha256 = ?,
                        batch_material_sha256 = ?
                    WHERE batch_id = ? AND profile_id = ? AND version = ?
                    """,
                    (
                        next_machine.state.value,
                        next_machine.version,
                        next_json,
                        next_digest,
                        (
                            incoming_material.batch_material_sha256
                            if incoming_material is not None
                            else (
                                None
                                if persisted_material is None
                                else persisted_material.batch_material_sha256
                            )
                        ),
                        next_machine.batch_id,
                        owner,
                        expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StaleQuizStateError(
                        "the quiz row changed before the transition could be stored"
                    )
                self._run_failpoint("after_machine_update")
                connection.commit()
                if reaches_reveal:
                    self._run_failpoint("after_reveal_commit")
                return StoredTransition(next_machine, receipt, False)
            except BaseException:
                connection.rollback()
                raise
        except sqlite3.OperationalError as exc:
            self._raise_operational(exc)
        raise AssertionError("unreachable")

    def _prepare_supplied_material(
        self,
        material: VerifiedBatchMaterial,
        machine: QuizMachine,
        profile_id: str,
        *,
        expected_context: BatchContext | None,
        planned_slots: tuple[MaterializedSlot, ...] | None,
    ) -> _PreparedBatchMaterial:
        if type(material) is not VerifiedBatchMaterial:
            raise QuizTransitionConflictError(
                "batch_material must be exact VerifiedBatchMaterial"
            )
        if self._compiler is None or self._manifest is None:
            raise QuizTransitionConflictError(
                "verified material requires authoritative compiler and manifest"
            )
        try:
            private_json = material.to_private_json()
            restored = VerifiedBatchMaterial.from_private_json(
                private_json,
                compiler=self._compiler,
                manifest=self._manifest,
                expected_context=expected_context,
                planned_slots=planned_slots,
            )
        except (BatchMaterialValidationError, TypeError, ValueError) as exc:
            raise QuizTransitionConflictError(
                "batch material failed authoritative reconstruction"
            ) from exc
        if restored != material:
            raise QuizTransitionConflictError(
                "batch material changed during authoritative reconstruction"
            )
        prepared = _prepare_material_record(restored, private_json)
        _validate_material_machine_binding(prepared, machine, profile_id)
        return prepared

    def _preload_material_for_write(
        self,
        machine: QuizMachine,
        profile_id: str,
        *,
        expected_context: BatchContext | None,
        planned_slots: tuple[MaterializedSlot, ...] | None,
    ) -> _PreparedBatchMaterial | None:
        """Perform compiler replay before acquiring the bounded write lock."""

        connection = self._require_connection()
        row = connection.execute(
            "SELECT * FROM quiz_batch_material WHERE batch_id = ?",
            (machine.batch_id,),
        ).fetchone()
        if row is None:
            return None
        return self._material_from_row(
            row,
            machine,
            profile_id,
            expected_context=expected_context,
            planned_slots=planned_slots,
        )

    def _load_material_in_transaction(
        self,
        machine: QuizMachine,
        profile_id: str,
        *,
        prepared_material: _PreparedBatchMaterial | None = None,
        expected_context: BatchContext | None = None,
        planned_slots: tuple[MaterializedSlot, ...] | None = None,
    ) -> _PreparedBatchMaterial | None:
        row = self._require_connection().execute(
            "SELECT * FROM quiz_batch_material WHERE batch_id = ?",
            (machine.batch_id,),
        ).fetchone()
        if row is None:
            if machine.state is QuizState.PREPARING:
                return None
            if self._allow_unverified_test_material:
                return None
            raise QuizStoreCorruptionError(
                "ready or later quiz is missing verified private material"
            )
        if machine.state is QuizState.PREPARING:
            raise QuizStoreCorruptionError(
                "preparing quiz cannot already contain private material"
            )
        return self._material_from_row(
            row,
            machine,
            profile_id,
            prepared_material=prepared_material,
            expected_context=expected_context,
            planned_slots=planned_slots,
        )

    def _material_from_row(
        self,
        row: sqlite3.Row,
        machine: QuizMachine,
        profile_id: str,
        *,
        prepared_material: _PreparedBatchMaterial | None = None,
        expected_context: BatchContext | None = None,
        planned_slots: tuple[MaterializedSlot, ...] | None = None,
    ) -> _PreparedBatchMaterial:
        try:
            private_json = row["private_json"]
            private_json_sha256 = row["private_json_sha256"]
            context_json = row["context_json"]
            context_sha256 = row["context_sha256"]
            if (
                not isinstance(private_json, str)
                or not _valid_sha256(private_json_sha256)
                or _sha256_text(private_json) != private_json_sha256
                or not isinstance(context_json, str)
                or not _valid_sha256(context_sha256)
                or _sha256_text(context_json) != context_sha256
            ):
                raise _DecodeError("material JSON hashes do not verify")
            _parse_canonical_json(private_json)
            _parse_canonical_json(context_json)
            if prepared_material is None:
                if self._compiler is None or self._manifest is None:
                    raise _DecodeError(
                        "authoritative compiler and manifest are unavailable"
                    )
                material = VerifiedBatchMaterial.from_private_json(
                    private_json,
                    compiler=self._compiler,
                    manifest=self._manifest,
                    expected_context=expected_context,
                    planned_slots=planned_slots,
                )
                prepared = _prepare_material_record(material, private_json)
            else:
                prepared = prepared_material
            stored_shape = (
                row["batch_id"],
                row["profile_id"],
                row["batch_material_sha256"],
                row["sealed_quiz_sha256"],
                context_json,
                context_sha256,
                int(row["item_count"]),
                private_json,
                private_json_sha256,
            )
            expected_shape = (
                prepared.material.batch_id,
                prepared.material.context.profile_id,
                prepared.batch_material_sha256,
                prepared.sealed_quiz_sha256,
                prepared.context_json,
                prepared.context_sha256,
                prepared.item_count,
                prepared.private_json,
                prepared.private_json_sha256,
            )
            if stored_shape != expected_shape:
                raise _DecodeError(
                    "material row indexes differ from canonical private material"
                )
            _validate_material_machine_binding(prepared, machine, profile_id)
            return prepared
        except QuizStoreCorruptionError:
            raise
        except (
            BatchMaterialValidationError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            _DecodeError,
        ) as exc:
            raise QuizStoreCorruptionError(
                f"private batch material cannot be verified: {machine.batch_id}"
            ) from exc

    def _insert_material_in_transaction(
        self,
        profile_id: str,
        prepared: _PreparedBatchMaterial,
    ) -> None:
        material = prepared.material
        try:
            self._require_connection().execute(
                """
                INSERT INTO quiz_batch_material (
                    batch_id, profile_id, batch_material_sha256,
                    sealed_quiz_sha256, context_json, context_sha256,
                    item_count, private_json, private_json_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    material.batch_id,
                    profile_id,
                    prepared.batch_material_sha256,
                    prepared.sealed_quiz_sha256,
                    prepared.context_json,
                    prepared.context_sha256,
                    prepared.item_count,
                    prepared.private_json,
                    prepared.private_json_sha256,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise QuizTransitionConflictError(
                "private batch material already exists or changed ownership"
            ) from exc

    def delete_profile(self, profile_id: str) -> None:
        """Securely delete every quiz and receipt owned by one local profile."""

        owner = _profile_id(profile_id)
        connection = self._require_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                if "learner_projection" in tables:
                    connection.execute(
                        "DELETE FROM learner_projection WHERE profile_id = ?",
                        (owner,),
                    )
                if "event_log" in tables:
                    connection.execute(
                        "DELETE FROM event_log WHERE profile_id = ?",
                        (owner,),
                    )
                connection.execute(
                    "DELETE FROM quiz_observation_outbox WHERE profile_id = ?",
                    (owner,),
                )
                connection.execute(
                    "DELETE FROM quiz_transition_receipts WHERE profile_id = ?",
                    (owner,),
                )
                connection.execute(
                    "DELETE FROM quiz_preparation_receipts WHERE profile_id = ?",
                    (owner,),
                )
                connection.execute(
                    "DELETE FROM quiz_batch_material WHERE profile_id = ?",
                    (owner,),
                )
                connection.execute(
                    "DELETE FROM quiz_machines WHERE profile_id = ?",
                    (owner,),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            # ``secure_delete`` clears deleted cells.  Checkpointing and VACUUM
            # additionally remove prior page images from both the WAL and main DB.
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("VACUUM")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            for backup in self.path.parent.glob(self.path.name + ".backup-v*"):
                if backup.is_file():
                    backup.unlink(missing_ok=True)
        except sqlite3.OperationalError as exc:
            self._raise_operational(exc)

    def pending_observations(
        self,
        profile_id: str,
    ) -> tuple[ObservationEvent, ...]:
        """Return hash-verified undelivered observations in profile ordinal order."""

        owner = _profile_id(profile_id)
        connection = self._require_connection()
        try:
            connection.execute("BEGIN")
            try:
                rows = connection.execute(
                    """
                    SELECT * FROM quiz_observation_outbox
                    WHERE profile_id = ? AND delivered = 0
                    ORDER BY ordinal, batch_id, item_id
                    """,
                    (owner,),
                ).fetchall()
                for batch_id in sorted({str(row["batch_id"]) for row in rows}):
                    self._load_in_transaction(batch_id, owner)
                events = tuple(_outbox_record_from_row(row).event for row in rows)
                connection.commit()
                return events
            except BaseException:
                connection.rollback()
                raise
        except sqlite3.OperationalError as exc:
            self._raise_operational(exc)
        raise AssertionError("unreachable")

    def drain_observations(
        self,
        profile_id: str,
        *,
        profile_store: "ProfileStore",
    ) -> int:
        """Idempotently reconcile the durable outbox into the same-path profile log.

        Each ProfileStore append commits before a separate outbox-delivery mark.
        A crash between those operations intentionally leaves the row pending;
        ProfileStore idempotency makes the retry exactly-once in the event log.
        No quiz transaction is held while ``ProfileStore.append`` executes.
        """

        owner = _profile_id(profile_id)
        try:
            profile_path = Path(profile_store.path)
        except (AttributeError, TypeError) as exc:
            raise QuizStoreError("profile_store must expose its SQLite path") from exc
        if profile_path.resolve() != self.path.resolve():
            raise QuizStoreError(
                "observation reconciliation requires the same SQLite database path"
            )

        pending = self.pending_observations(owner)
        marked = 0
        for event in pending:
            profile_store.append(event)
            self._run_failpoint(f"after_profile_append:{event.item_id}")
            if self._mark_observation_delivered(event):
                marked += 1
            self._run_failpoint(f"after_delivery_mark:{event.item_id}")
        return marked

    def _mark_observation_delivered(self, event: ObservationEvent) -> bool:
        connection = self._require_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT * FROM quiz_observation_outbox
                    WHERE profile_id = ? AND batch_id = ? AND item_id = ?
                    """,
                    (event.profile_id, event.batch_id, event.item_id),
                ).fetchone()
                if row is None:
                    raise QuizStoreCorruptionError(
                        "pending observation disappeared before delivery mark"
                    )
                prepared = _outbox_record_from_row(row)
                if prepared.event != event:
                    raise QuizStoreCorruptionError(
                        "pending observation changed before delivery mark"
                    )
                delivered = int(row["delivered"])
                if delivered == 1:
                    connection.commit()
                    return False
                if delivered != 0:
                    raise QuizStoreCorruptionError(
                        "observation delivered marker is invalid"
                    )
                cursor = connection.execute(
                    """
                    UPDATE quiz_observation_outbox SET delivered = 1
                    WHERE profile_id = ? AND batch_id = ? AND item_id = ?
                      AND delivered = 0
                    """,
                    (event.profile_id, event.batch_id, event.item_id),
                )
                if cursor.rowcount != 1:
                    raise QuizStoreCorruptionError(
                        "observation delivery mark lost its row-version check"
                    )
                connection.commit()
                return True
            except BaseException:
                connection.rollback()
                raise
        except sqlite3.OperationalError as exc:
            self._raise_operational(exc)
        raise AssertionError("unreachable")

    def _outbox_records_for_batch(
        self,
        profile_id: str,
        batch_id: str,
    ) -> tuple[_PreparedObservation, ...]:
        rows = self._require_connection().execute(
            """
            SELECT * FROM quiz_observation_outbox
            WHERE profile_id = ? AND batch_id = ?
            ORDER BY item_id
            """,
            (profile_id, batch_id),
        ).fetchall()
        return tuple(_outbox_record_from_row(row) for row in rows)

    def _next_profile_ordinal_in_transaction(self, profile_id: str) -> int:
        connection = self._require_connection()
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        maxima = [0]
        if "event_log" in tables:
            row = connection.execute(
                "SELECT MAX(ordinal) FROM event_log WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
            if row is not None and row[0] is not None:
                maxima.append(int(row[0]))
        row = connection.execute(
            "SELECT MAX(ordinal) FROM quiz_observation_outbox WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
        if row is not None and row[0] is not None:
            maxima.append(int(row[0]))
        maximum = max(maxima)
        if maximum < 0:
            raise QuizStoreCorruptionError("profile ordinal index is invalid")
        return maximum + 1

    def _require_live_batch_slot_in_transaction(
        self,
        profile_id: str,
        batch_id: str,
    ) -> None:
        """Fail closed unless ``profile_id`` can create this live batch.

        Callers hold ``BEGIN IMMEDIATE``, so the check and subsequent insert are
        serialized across every QuizStore connection using this database.
        """

        live_machines = tuple(
            machine
            for machine in self._validated_profile_machines_in_transaction(
                profile_id
            )
            if machine.state is not QuizState.CLOSED
        )
        if live_machines and live_machines[0].batch_id != batch_id:
            raise QuizTransitionConflictError(
                "profile already owns a non-closed quiz batch"
            )
        assisted_table = self._require_connection().execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'assisted_route_material'"
        ).fetchone()
        if assisted_table is not None:
            active_assisted = self._require_connection().execute(
                """
                SELECT route.route_id
                FROM assisted_route_material AS route
                WHERE route.profile_id = ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM event_log AS event
                      WHERE event.profile_id = route.profile_id
                        AND event.event_type = 'assisted_route_completion'
                        AND event.semantic_key =
                            'assisted_route_completion:' || route.world_id
                  )
                LIMIT 1
                """,
                (profile_id,),
            ).fetchone()
            if active_assisted is not None:
                raise QuizTransitionConflictError(
                    "profile already owns an active assisted route"
                )

    def _validated_profile_machines_in_transaction(
        self,
        profile_id: str,
    ) -> tuple[QuizMachine, ...]:
        """Verify every machine owned by a profile and its live cardinality."""

        rows = self._require_connection().execute(
            "SELECT batch_id FROM quiz_machines "
            "WHERE profile_id = ? ORDER BY batch_id",
            (profile_id,),
        ).fetchall()
        machines = tuple(
            self._load_in_transaction(row["batch_id"], profile_id)
            for row in rows
        )
        if sum(
            machine.state is not QuizState.CLOSED
            for machine in machines
        ) > 1:
            raise QuizStoreCorruptionError(
                "profile has more than one non-closed quiz batch"
            )
        return machines

    def _validate_delivered_observations(
        self,
        observations: tuple[_PreparedObservation, ...],
    ) -> None:
        delivered = tuple(item for item in observations if item.delivered)
        if not delivered:
            return
        connection = self._require_connection()
        table = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'event_log'"
        ).fetchone()
        if table is None:
            raise QuizStoreCorruptionError(
                "delivered observations require the durable profile event log"
            )
        for prepared in delivered:
            event = prepared.event
            row = connection.execute(
                """
                SELECT ordinal, event_id, event_type, semantic_key, canonical_json
                FROM event_log
                WHERE profile_id = ? AND idempotency_id = ?
                """,
                (event.profile_id, event.idempotency_id),
            ).fetchone()
            if (
                row is None
                or int(row["ordinal"]) != event.ordinal
                or row["event_id"] != event.event_id
                or row["event_type"] != event.event_type
                or row["semantic_key"] != event.semantic_key
                or row["canonical_json"] != prepared.canonical_json
            ):
                raise QuizStoreCorruptionError(
                    "delivered observation lacks its canonical profile event"
                )

    def _existing_receipt_outcome(
        self,
        current: QuizMachine,
        profile_id: str,
        proposed: TransitionReceipt,
        proposed_json: str | None,
        proposed_machine: QuizMachine,
        prepared_observations: tuple[_PreparedObservation, ...],
        proposed_outbox_sha256: str | None,
        observation_session_id: str | None,
        material: VerifiedBatchMaterial | None,
    ) -> StoredTransition | None:
        connection = self._require_connection()
        row = connection.execute(
            """
            SELECT * FROM quiz_transition_receipts
            WHERE batch_id = ? AND profile_id = ? AND action = ?
            """,
            (proposed.batch_id, profile_id, proposed.action),
        ).fetchone()
        if row is not None:
            existing, embedded_outbox_sha256 = _stored_receipt_from_row(row)
            if embedded_outbox_sha256 != row["outbox_sha256"]:
                raise QuizStoreCorruptionError(
                    "receipt outbox index differs from its hashed payload"
                )
            if existing.request_id == proposed.request_id:
                if existing.payload_sha256 != proposed.payload_sha256:
                    raise IdempotencyConflictError(
                        "request_id was reused with a different payload"
                    )
                if existing != proposed:
                    raise IdempotencyConflictError(
                        "the same request and payload produced a different receipt"
                    )
                stored_outbox_sha256 = row["outbox_sha256"]
                if stored_outbox_sha256 is not None and not _valid_sha256(
                    stored_outbox_sha256
                ):
                    raise QuizStoreCorruptionError(
                        "transition receipt has an invalid outbox commitment"
                    )
                if stored_outbox_sha256 != proposed_outbox_sha256:
                    raise QuizTransitionConflictError(
                        "exact transition replay requires the same observation payload"
                    )
                if row["receipt_json"] != proposed_json:
                    raise QuizStoreCorruptionError(
                        "stored receipt canonical JSON does not verify"
                    )
                if stored_outbox_sha256 is not None:
                    persisted = self._outbox_records_for_batch(
                        profile_id,
                        proposed.batch_id,
                    )
                    _validate_observations_against_final(
                        proposed_machine,
                        profile_id,
                        persisted,
                        required=True,
                        observation_session_id=observation_session_id,
                        material=material,
                    )
                    if _outbox_sha256(persisted) != stored_outbox_sha256:
                        raise QuizStoreCorruptionError(
                            "persisted observations differ from the receipt commitment"
                        )
                    if tuple(
                        item.canonical_json for item in persisted
                    ) != tuple(
                        item.canonical_json for item in prepared_observations
                    ):
                        raise QuizTransitionConflictError(
                            "exact transition replay changed observation content"
                        )
                return StoredTransition(current, existing, True)
            raise QuizTransitionConflictError(
                f"the {proposed.action} transition already has a receipt"
            )

        reused = connection.execute(
            """
            SELECT * FROM quiz_transition_receipts
            WHERE batch_id = ? AND profile_id = ? AND request_id = ?
            """,
            (proposed.batch_id, profile_id, proposed.request_id),
        ).fetchone()
        if reused is not None:
            existing, embedded_outbox_sha256 = _stored_receipt_from_row(reused)
            if embedded_outbox_sha256 != reused["outbox_sha256"]:
                raise QuizStoreCorruptionError(
                    "receipt outbox index differs from its hashed payload"
                )
            if existing.payload_sha256 != proposed.payload_sha256:
                raise IdempotencyConflictError(
                    "request_id was reused with a different payload"
                )
            raise QuizTransitionConflictError(
                "request_id is already bound to another transition action"
            )
        return None

    def _load_in_transaction(
        self,
        batch_id: str,
        profile_id: str,
        *,
        prepared_material: _PreparedBatchMaterial | None = None,
        expected_context: BatchContext | None = None,
        planned_slots: Iterable[MaterializedSlot] | None = None,
    ) -> QuizMachine:
        connection = self._require_connection()
        row = connection.execute(
            "SELECT * FROM quiz_machines WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
        if row is None:
            raise QuizNotFoundError(f"quiz batch was not found: {batch_id}")
        if not _valid_identifier(row["profile_id"]):
            raise QuizStoreCorruptionError(
                "quiz batch has an invalid stored profile owner"
            )
        if row["profile_id"] != profile_id:
            raise QuizOwnershipError(
                "quiz batch belongs to a different local profile"
            )
        try:
            machine_json = row["machine_json"]
            machine_sha256 = row["machine_sha256"]
            if not isinstance(machine_json, str) or not _valid_sha256(machine_sha256):
                raise _DecodeError("invalid machine row encoding")
            if _sha256_text(machine_json) != machine_sha256:
                raise _DecodeError("machine content hash does not verify")
            payload = _parse_canonical_json(machine_json)
            machine = _machine_from_payload(payload)
            if (
                row["batch_id"] != machine.batch_id
                or row["profile_id"] != profile_id
                or row["state"] != machine.state.value
                or isinstance(row["version"], bool)
                or int(row["version"]) != machine.version
            ):
                raise _DecodeError("machine row index differs from its payload")

            slots = None if planned_slots is None else tuple(planned_slots)
            persisted_material = self._load_material_in_transaction(
                machine,
                profile_id,
                prepared_material=prepared_material,
                expected_context=expected_context,
                planned_slots=slots,
            )
            indexed_material_sha256 = row["batch_material_sha256"]
            if machine.state is QuizState.PREPARING:
                if indexed_material_sha256 is not None:
                    raise _DecodeError(
                        "preparing machine cannot commit batch material"
                    )
            elif persisted_material is None:
                if (
                    not self._allow_unverified_test_material
                    or indexed_material_sha256 is not None
                ):
                    raise _DecodeError(
                        "unmaterialized machine has an invalid commitment"
                    )
            elif (
                not _valid_sha256(indexed_material_sha256)
                or indexed_material_sha256
                != persisted_material.batch_material_sha256
            ):
                raise _DecodeError(
                    "machine batch-material commitment does not verify"
                )

            preparation_row = connection.execute(
                "SELECT * FROM quiz_preparation_receipts WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if preparation_row is not None:
                if persisted_material is None:
                    raise _DecodeError(
                        "prepared batch receipt has no verified private material"
                    )
                preparation_receipt = _preparation_receipt_from_row(
                    preparation_row
                )
                _validate_persisted_preparation_binding(
                    preparation_receipt,
                    profile_id,
                    machine,
                    persisted_material.material,
                )

            receipt_rows = connection.execute(
                """
                SELECT * FROM quiz_transition_receipts
                WHERE batch_id = ? AND profile_id = ? ORDER BY action
                """,
                (batch_id, profile_id),
            ).fetchall()
            actual: dict[str, TransitionReceipt] = {}
            receipt_outbox: dict[str, str | None] = {}
            for receipt_row in receipt_rows:
                if receipt_row["profile_id"] != profile_id:
                    raise _DecodeError("receipt profile differs from its owner index")
                parsed, embedded_outbox_sha256 = _stored_receipt_from_row(receipt_row)
                if parsed.action in actual:
                    raise _DecodeError("duplicate receipt action")
                outbox_sha256 = receipt_row["outbox_sha256"]
                if outbox_sha256 is not None and not _valid_sha256(outbox_sha256):
                    raise _DecodeError("receipt outbox commitment is invalid")
                if embedded_outbox_sha256 != outbox_sha256:
                    raise _DecodeError(
                        "receipt outbox index differs from its hashed payload"
                    )
                actual[parsed.action] = parsed
                receipt_outbox[parsed.action] = outbox_sha256
            expected = {
                receipt.action: receipt
                for receipt in (machine.initial_receipt, machine.revision_receipt)
                if receipt is not None
            }
            if actual != expected:
                raise _DecodeError("receipt rows differ from the machine snapshot")
            persisted_observations = self._outbox_records_for_batch(
                profile_id,
                batch_id,
            )
            self._validate_delivered_observations(persisted_observations)
            reveal_action: str | None = None
            if machine.final_result is not None:
                reveal_action = (
                    "revision" if machine.final_result.revision_used else "initial"
                )
            for action, digest in receipt_outbox.items():
                if action != reveal_action and digest is not None:
                    raise _DecodeError(
                        "a non-reveal receipt cannot commit observations"
                    )
            committed_outbox_sha256 = (
                receipt_outbox.get(reveal_action) if reveal_action is not None else None
            )
            _validate_observations_against_final(
                machine,
                profile_id,
                persisted_observations,
                required=reveal_action is not None,
                observation_session_id=(
                    persisted_observations[0].event.session_id
                    if persisted_observations
                    else None
                ),
                material=(
                    None
                    if persisted_material is None
                    else persisted_material.material
                ),
            )
            if _outbox_sha256(persisted_observations) != committed_outbox_sha256:
                raise _DecodeError(
                    "persisted observation rows differ from the reveal receipt"
                )
            return machine
        except QuizStoreCorruptionError:
            raise
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            QuizTransitionConflictError,
        ) as exc:
            raise QuizStoreCorruptionError(
                f"quiz batch cannot be verified: {batch_id}"
            ) from exc

    def _ensure_schema(self) -> None:
        connection = self._require_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                has_metadata = "quiz_store_metadata" in tables
                old_data_tables = {
                    "quiz_machines",
                    "quiz_transition_receipts",
                    "quiz_observation_outbox",
                    "quiz_batch_material",
                    "quiz_preparation_receipts",
                } & tables
                if has_metadata:
                    metadata_columns = {
                        str(row[1])
                        for row in connection.execute(
                            "PRAGMA table_info(quiz_store_metadata)"
                        )
                    }
                    if metadata_columns != {"singleton", "schema_version"}:
                        raise QuizStoreSchemaError(
                            "quiz store metadata schema is invalid"
                        )
                    existing = connection.execute(
                        "SELECT schema_version FROM quiz_store_metadata "
                        "WHERE singleton = 1"
                    ).fetchone()
                    existing_version = (
                        None if existing is None else int(existing[0])
                    )
                    if existing_version != STORE_SCHEMA_VERSION:
                        if existing_version == 5:
                            raise QuizStoreSchemaError(
                                "quiz store schema version 5 is not accepted; "
                                "an explicit v6 prepared-batch migration is required"
                            )
                        raise QuizStoreSchemaError(
                            "quiz store schema requires an explicit v6 prepared-batch migration"
                        )
                    if old_data_tables != {
                        "quiz_machines",
                        "quiz_transition_receipts",
                        "quiz_observation_outbox",
                        "quiz_batch_material",
                        "quiz_preparation_receipts",
                    }:
                        raise QuizStoreSchemaError(
                            "quiz store schema is incomplete"
                        )
                elif old_data_tables:
                    raise QuizStoreSchemaError(
                        "unversioned quiz tables cannot be migrated safely"
                    )

                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS quiz_store_metadata (
                        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                        schema_version INTEGER NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS quiz_machines (
                        batch_id TEXT PRIMARY KEY,
                        profile_id TEXT NOT NULL,
                        state TEXT NOT NULL,
                        version INTEGER NOT NULL CHECK (version >= 0),
                        machine_json TEXT NOT NULL,
                        machine_sha256 TEXT NOT NULL,
                        batch_material_sha256 TEXT,
                        UNIQUE (batch_id, profile_id)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS quiz_transition_receipts (
                        batch_id TEXT NOT NULL,
                        profile_id TEXT NOT NULL,
                        action TEXT NOT NULL CHECK (action IN ('initial', 'revision')),
                        request_id TEXT NOT NULL,
                        payload_sha256 TEXT NOT NULL,
                        from_version INTEGER NOT NULL,
                        to_version INTEGER NOT NULL,
                        output_sha256 TEXT NOT NULL,
                        receipt_json TEXT NOT NULL,
                        receipt_sha256 TEXT NOT NULL,
                        outbox_sha256 TEXT,
                        PRIMARY KEY (batch_id, action),
                        UNIQUE (batch_id, request_id),
                        FOREIGN KEY (batch_id, profile_id)
                            REFERENCES quiz_machines(batch_id, profile_id)
                            ON DELETE CASCADE
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS quiz_observation_outbox (
                        profile_id TEXT NOT NULL,
                        batch_id TEXT NOT NULL,
                        item_id TEXT NOT NULL,
                        ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
                        event_id TEXT NOT NULL,
                        idempotency_id TEXT NOT NULL,
                        canonical_json TEXT NOT NULL,
                        event_sha256 TEXT NOT NULL,
                        delivered INTEGER NOT NULL DEFAULT 0
                            CHECK (delivered IN (0, 1)),
                        PRIMARY KEY (profile_id, batch_id, item_id),
                        UNIQUE (profile_id, ordinal),
                        UNIQUE (profile_id, event_id),
                        UNIQUE (profile_id, idempotency_id),
                        FOREIGN KEY (batch_id, profile_id)
                            REFERENCES quiz_machines(batch_id, profile_id)
                            ON DELETE CASCADE
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS quiz_batch_material (
                        batch_id TEXT PRIMARY KEY,
                        profile_id TEXT NOT NULL,
                        batch_material_sha256 TEXT NOT NULL,
                        sealed_quiz_sha256 TEXT NOT NULL,
                        context_json TEXT NOT NULL,
                        context_sha256 TEXT NOT NULL,
                        item_count INTEGER NOT NULL CHECK (item_count BETWEEN 3 AND 10),
                        private_json TEXT NOT NULL,
                        private_json_sha256 TEXT NOT NULL,
                        FOREIGN KEY (batch_id, profile_id)
                            REFERENCES quiz_machines(batch_id, profile_id)
                            ON DELETE CASCADE
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS quiz_preparation_receipts (
                        profile_id TEXT NOT NULL,
                        request_id TEXT NOT NULL,
                        batch_id TEXT NOT NULL UNIQUE,
                        payload_sha256 TEXT NOT NULL,
                        output_sha256 TEXT NOT NULL,
                        receipt_json TEXT NOT NULL,
                        receipt_sha256 TEXT NOT NULL,
                        PRIMARY KEY (profile_id, request_id),
                        FOREIGN KEY (batch_id, profile_id)
                            REFERENCES quiz_machines(batch_id, profile_id)
                            ON DELETE CASCADE
                    )
                    """
                )
                machine_columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(quiz_machines)")
                }
                receipt_columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(quiz_transition_receipts)"
                    )
                }
                outbox_columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(quiz_observation_outbox)"
                    )
                }
                material_columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(quiz_batch_material)"
                    )
                }
                preparation_table_info = connection.execute(
                    "PRAGMA table_info(quiz_preparation_receipts)"
                ).fetchall()
                preparation_columns = {
                    str(row[1]) for row in preparation_table_info
                }
                preparation_primary_key = {
                    str(row[1]): int(row[5])
                    for row in preparation_table_info
                    if int(row[5]) > 0
                }
                preparation_unique_indexes = {
                    tuple(
                        str(column[2])
                        for column in connection.execute(
                            "SELECT * FROM pragma_index_info(?)",
                            (str(index_row[1]),),
                        )
                    )
                    for index_row in connection.execute(
                        "PRAGMA index_list(quiz_preparation_receipts)"
                    )
                    if int(index_row[2]) == 1
                }
                preparation_foreign_keys = {
                    (
                        int(row[0]),
                        int(row[1]),
                        str(row[2]),
                        str(row[3]),
                        str(row[4]),
                        str(row[5]),
                        str(row[6]),
                        str(row[7]),
                    )
                    for row in connection.execute(
                        "PRAGMA foreign_key_list(quiz_preparation_receipts)"
                    )
                }
                if machine_columns != {
                    "batch_id",
                    "profile_id",
                    "state",
                    "version",
                    "machine_json",
                    "machine_sha256",
                    "batch_material_sha256",
                } or receipt_columns != {
                    "batch_id",
                    "profile_id",
                    "action",
                    "request_id",
                    "payload_sha256",
                    "from_version",
                    "to_version",
                    "output_sha256",
                    "receipt_json",
                    "receipt_sha256",
                    "outbox_sha256",
                } or outbox_columns != {
                    "profile_id",
                    "batch_id",
                    "item_id",
                    "ordinal",
                    "event_id",
                    "idempotency_id",
                    "canonical_json",
                    "event_sha256",
                    "delivered",
                } or material_columns != {
                    "batch_id",
                    "profile_id",
                    "batch_material_sha256",
                    "sealed_quiz_sha256",
                    "context_json",
                    "context_sha256",
                    "item_count",
                    "private_json",
                    "private_json_sha256",
                } or preparation_columns != {
                    "profile_id",
                    "request_id",
                    "batch_id",
                    "payload_sha256",
                    "output_sha256",
                    "receipt_json",
                    "receipt_sha256",
                } or preparation_primary_key != {
                    "profile_id": 1,
                    "request_id": 2,
                } or preparation_unique_indexes != {
                    ("profile_id", "request_id"),
                    ("batch_id",),
                } or preparation_foreign_keys != {
                    (
                        0,
                        0,
                        "quiz_machines",
                        "batch_id",
                        "batch_id",
                        "NO ACTION",
                        "CASCADE",
                        "NONE",
                    ),
                    (
                        0,
                        1,
                        "quiz_machines",
                        "profile_id",
                        "profile_id",
                        "NO ACTION",
                        "CASCADE",
                        "NONE",
                    ),
                }:
                    raise QuizStoreSchemaError(
                        "quiz store tables do not match schema version 6"
                    )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS quiz_machines_by_profile "
                    "ON quiz_machines(profile_id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS quiz_receipts_by_profile "
                    "ON quiz_transition_receipts(profile_id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS quiz_outbox_pending_by_profile "
                    "ON quiz_observation_outbox(profile_id, delivered, ordinal)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS quiz_material_by_profile "
                    "ON quiz_batch_material(profile_id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS quiz_preparations_by_profile "
                    "ON quiz_preparation_receipts(profile_id)"
                )
                if not has_metadata:
                    connection.execute(
                        "INSERT INTO quiz_store_metadata (singleton, schema_version) "
                        "VALUES (1, ?)",
                        (STORE_SCHEMA_VERSION,),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        except sqlite3.OperationalError as exc:
            self._raise_operational(exc)

    def _run_failpoint(self, stage: str) -> None:
        if self._failpoint_stage == stage:
            raise RuntimeError("injected transaction failure")

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise QuizStoreError("quiz store is closed")
        return self._connection

    @staticmethod
    def _raise_operational(exc: sqlite3.OperationalError) -> None:
        message = str(exc).lower()
        if "locked" in message or "busy" in message:
            raise QuizStoreBusyError("quiz database is busy") from exc
        raise QuizStoreError("quiz database operation failed") from exc


def _preparation_request_payload(
    request: BattleQuizRequest,
    profile_id: str,
) -> dict[str, object]:
    return {
        "schemaVersion": request.schema_version,
        "requestId": request.request_id,
        "profileId": profile_id,
        "sessionId": request.session_id,
        "battleId": request.battle_id,
        "worldId": request.world_id,
        "battleTier": request.battle_tier.value,
    }


def _preparation_request_identity(
    request: BattleQuizRequest,
    profile_id: object,
) -> tuple[str, str]:
    owner = _profile_id(profile_id)
    if type(request) is not BattleQuizRequest:
        raise QuizTransitionConflictError(
            "request must be an exact BattleQuizRequest"
        )
    return owner, _canonical_sha256(_preparation_request_payload(request, owner))


def _preparation_receipt_unsigned(
    *,
    profile_id: str,
    request_id: str,
    batch_id: str,
    payload_sha256: str,
    batch_material_sha256: str,
    plan_sha256: str,
    output_sha256: str,
) -> dict[str, object]:
    return {
        "schemaVersion": PREPARATION_RECEIPT_SCHEMA_VERSION,
        "action": "prepare",
        "profileId": profile_id,
        "requestId": request_id,
        "batchId": batch_id,
        "payloadSha256": payload_sha256,
        "batchMaterialSha256": batch_material_sha256,
        "planSha256": plan_sha256,
        "outputSha256": output_sha256,
    }


def _make_preparation_receipt(
    *,
    request: BattleQuizRequest,
    profile_id: str,
    batch_id: str,
    payload_sha256: str,
    batch_material_sha256: str,
    plan_sha256: str,
    output_sha256: str,
) -> PreparationReceipt:
    unsigned = _preparation_receipt_unsigned(
        profile_id=profile_id,
        request_id=request.request_id,
        batch_id=batch_id,
        payload_sha256=payload_sha256,
        batch_material_sha256=batch_material_sha256,
        plan_sha256=plan_sha256,
        output_sha256=output_sha256,
    )
    receipt = PreparationReceipt(
        schema_version=PREPARATION_RECEIPT_SCHEMA_VERSION,
        action="prepare",
        profile_id=profile_id,
        request_id=request.request_id,
        batch_id=batch_id,
        payload_sha256=payload_sha256,
        batch_material_sha256=batch_material_sha256,
        plan_sha256=plan_sha256,
        output_sha256=output_sha256,
        receipt_sha256=_canonical_sha256(unsigned),
    )
    _validate_preparation_receipt(receipt)
    return receipt


def _preparation_receipt_payload(
    receipt: PreparationReceipt,
) -> dict[str, object]:
    payload = _preparation_receipt_unsigned(
        profile_id=receipt.profile_id,
        request_id=receipt.request_id,
        batch_id=receipt.batch_id,
        payload_sha256=receipt.payload_sha256,
        batch_material_sha256=receipt.batch_material_sha256,
        plan_sha256=receipt.plan_sha256,
        output_sha256=receipt.output_sha256,
    )
    payload["receiptSha256"] = receipt.receipt_sha256
    return payload


def _validate_preparation_receipt(receipt: PreparationReceipt) -> None:
    if (
        type(receipt) is not PreparationReceipt
        or receipt.schema_version != PREPARATION_RECEIPT_SCHEMA_VERSION
        or receipt.action != "prepare"
        or not _valid_identifier(receipt.profile_id)
        or not _valid_identifier(receipt.request_id)
        or not _valid_identifier(receipt.batch_id)
        or not _valid_sha256(receipt.payload_sha256)
        or not _valid_sha256(receipt.batch_material_sha256)
        or not _valid_sha256(receipt.plan_sha256)
        or not _valid_sha256(receipt.output_sha256)
        or not _valid_sha256(receipt.receipt_sha256)
    ):
        raise QuizTransitionConflictError("preparation receipt is invalid")
    expected = _canonical_sha256(
        _preparation_receipt_unsigned(
            profile_id=receipt.profile_id,
            request_id=receipt.request_id,
            batch_id=receipt.batch_id,
            payload_sha256=receipt.payload_sha256,
            batch_material_sha256=receipt.batch_material_sha256,
            plan_sha256=receipt.plan_sha256,
            output_sha256=receipt.output_sha256,
        )
    )
    if receipt.receipt_sha256 != expected:
        raise QuizTransitionConflictError(
            "preparation receipt hash does not verify"
        )


def _preparation_receipt_from_payload(value: object) -> PreparationReceipt:
    payload = _object(
        value,
        {
            "schemaVersion",
            "action",
            "profileId",
            "requestId",
            "batchId",
            "payloadSha256",
            "batchMaterialSha256",
            "planSha256",
            "outputSha256",
            "receiptSha256",
        },
        "preparation receipt",
    )
    receipt = PreparationReceipt(
        schema_version=_text(payload["schemaVersion"], "schemaVersion"),
        action=_text(payload["action"], "action"),
        profile_id=_text(payload["profileId"], "profileId"),
        request_id=_text(payload["requestId"], "requestId"),
        batch_id=_text(payload["batchId"], "batchId"),
        payload_sha256=_hash(payload["payloadSha256"], "payloadSha256"),
        batch_material_sha256=_hash(
            payload["batchMaterialSha256"], "batchMaterialSha256"
        ),
        plan_sha256=_hash(payload["planSha256"], "planSha256"),
        output_sha256=_hash(payload["outputSha256"], "outputSha256"),
        receipt_sha256=_hash(payload["receiptSha256"], "receiptSha256"),
    )
    _validate_preparation_receipt(receipt)
    return receipt


def _preparation_receipt_from_row(row: sqlite3.Row) -> PreparationReceipt:
    try:
        raw = row["receipt_json"]
        digest = row["receipt_sha256"]
        if not isinstance(raw, str) or not _valid_sha256(digest):
            raise _DecodeError("invalid preparation receipt row encoding")
        if _sha256_text(raw) != digest:
            raise _DecodeError("preparation receipt row hash does not verify")
        receipt = _preparation_receipt_from_payload(_parse_canonical_json(raw))
        if (
            row["profile_id"] != receipt.profile_id
            or row["request_id"] != receipt.request_id
            or row["batch_id"] != receipt.batch_id
            or row["payload_sha256"] != receipt.payload_sha256
            or row["output_sha256"] != receipt.output_sha256
        ):
            raise _DecodeError(
                "preparation receipt row index differs from its payload"
            )
        return receipt
    except QuizStoreCorruptionError:
        raise
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        QuizTransitionConflictError,
    ) as exc:
        raise QuizStoreCorruptionError(
            "preparation receipt cannot be verified"
        ) from exc


def _validate_material_request_binding(
    material: VerifiedBatchMaterial,
    request: BattleQuizRequest,
    profile_id: str,
) -> None:
    context = material.context
    if (
        context.profile_id != profile_id
        or context.session_id != request.session_id
        or context.world_id != request.world_id
        or context.battle_id != request.battle_id
        or context.battle_tier != request.battle_tier.value
    ):
        raise QuizTransitionConflictError(
            "request, owner, and verified batch context do not match"
        )


def _validate_preparation_binding(
    receipt: PreparationReceipt,
    request: BattleQuizRequest,
    profile_id: str,
    machine: QuizMachine,
    material: VerifiedBatchMaterial,
) -> None:
    _validate_preparation_receipt(receipt)
    _validate_material_request_binding(material, request, profile_id)
    _validate_persisted_preparation_binding(
        receipt,
        profile_id,
        machine,
        material,
    )
    if (
        receipt.request_id != request.request_id
        or receipt.payload_sha256
        != _canonical_sha256(_preparation_request_payload(request, profile_id))
    ):
        raise QuizStoreCorruptionError(
            "prepared batch differs from its creation receipt"
        )


def _validate_persisted_preparation_binding(
    receipt: PreparationReceipt,
    profile_id: str,
    machine: QuizMachine,
    material: VerifiedBatchMaterial,
) -> None:
    _validate_preparation_receipt(receipt)
    if (
        receipt.profile_id != profile_id
        or receipt.batch_id != machine.batch_id
        or receipt.batch_id != material.batch_id
        or receipt.batch_material_sha256 != material.batch_material_sha256
        or receipt.plan_sha256 != material.plan_sha256
        or receipt.output_sha256
        != _canonical_sha256(material.public_payload())
    ):
        raise QuizStoreCorruptionError(
            "prepared batch differs from its creation receipt"
        )


def _material_context_payload(context: BatchContext) -> dict[str, object]:
    return {
        "profileId": context.profile_id,
        "sessionId": context.session_id,
        "worldId": context.world_id,
        "battleId": context.battle_id,
        "coreSubskillIds": list(context.core_subskill_ids),
        "contentVersionId": context.content_version_id,
        "battleTier": context.battle_tier,
    }


def _material_layouts(
    material: VerifiedBatchMaterial,
) -> tuple[QuizItemLayout, ...]:
    return tuple(
        QuizItemLayout(
            item_id=item.placement.item_instance_id,
            option_ids=tuple(
                option.option_id for option in item.placement.options
            ),
        )
        for item in material.items
    )


def _prepare_material_record(
    material: VerifiedBatchMaterial,
    private_json: str,
) -> _PreparedBatchMaterial:
    layouts = _material_layouts(material)
    preparing = new_quiz(material.batch_id, layouts)
    ready = mark_ready(
        preparing,
        sealed_quiz=material.sealed_quiz,
        expected_version=preparing.version,
    )
    if ready.sealed_quiz_sha256 is None:  # pragma: no cover - machine invariant
        raise ValueError("sealed material did not produce a commitment")
    context_json = _canonical_json(_material_context_payload(material.context))
    return _PreparedBatchMaterial(
        material=material,
        private_json=private_json,
        private_json_sha256=_sha256_text(private_json),
        batch_material_sha256=material.batch_material_sha256,
        sealed_quiz_sha256=ready.sealed_quiz_sha256,
        context_json=context_json,
        context_sha256=_sha256_text(context_json),
        item_count=len(material.items),
    )


def _validate_material_machine_binding(
    prepared: _PreparedBatchMaterial,
    machine: QuizMachine,
    profile_id: str,
) -> None:
    material = prepared.material
    if (
        material.batch_id != machine.batch_id
        or material.context.profile_id != profile_id
        or machine.item_layouts != _material_layouts(material)
        or machine.state is QuizState.PREPARING
        or machine.sealed_quiz_sha256 != prepared.sealed_quiz_sha256
    ):
        raise QuizTransitionConflictError(
            "machine, owner, context, and verified material do not match"
        )


def _prepare_observations(
    machine: QuizMachine,
    profile_id: str,
    observation_events: Iterable[ObservationEvent],
    *,
    observation_session_id: str | None,
    material: VerifiedBatchMaterial | None,
) -> tuple[tuple[_PreparedObservation, ...], str | None]:
    try:
        supplied = tuple(observation_events)
    except TypeError as exc:
        raise QuizTransitionConflictError(
            "observation_events must be an iterable of immutable events"
        ) from exc
    if supplied:
        if not _valid_identifier(observation_session_id):
            raise QuizTransitionConflictError(
                "observation_session_id is required for reveal observations"
            )
    elif observation_session_id is not None:
        raise QuizTransitionConflictError(
            "observation_session_id is forbidden without observations"
        )
    prepared: list[_PreparedObservation] = []
    for event in supplied:
        if type(event) is not ObservationEvent:
            raise QuizTransitionConflictError(
                "the reveal outbox accepts only ObservationEvent values"
            )
        if event.session_id != observation_session_id:
            raise QuizTransitionConflictError(
                "observation session does not match observation_session_id"
            )
        canonical = canonical_event_json(event)
        try:
            reparsed = event_from_json(canonical)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise QuizTransitionConflictError(
                "observation event cannot round-trip canonically"
            ) from exc
        if reparsed != event:
            raise QuizTransitionConflictError(
                "observation event changed during canonical round-trip"
            )
        prepared.append(_PreparedObservation(
            event=event,
            canonical_json=canonical,
            event_sha256=_sha256_text(canonical),
        ))
    normalized = tuple(sorted(prepared, key=lambda item: item.event.item_id))
    if normalized:
        _validate_observations_against_final(
            machine,
            profile_id,
            normalized,
            required=True,
            observation_session_id=observation_session_id,
            material=material,
        )
    return normalized, _outbox_sha256(normalized)


def _validate_observation_transition(
    current: QuizMachine,
    next_machine: QuizMachine,
    profile_id: str,
    observations: tuple[_PreparedObservation, ...],
    *,
    observation_session_id: str | None,
    material: VerifiedBatchMaterial | None,
) -> bool:
    reaches_reveal = (
        current.state
        in (QuizState.READY, QuizState.INITIAL_LOCKED, QuizState.REVISION_OPEN)
        and next_machine.state is QuizState.REVEALED
    )
    _validate_observations_against_final(
        next_machine,
        profile_id,
        observations,
        required=reaches_reveal,
        observation_session_id=observation_session_id,
        material=material,
    )
    return reaches_reveal


def _validate_observations_against_final(
    machine: QuizMachine,
    profile_id: str,
    observations: tuple[_PreparedObservation, ...],
    *,
    required: bool,
    observation_session_id: str | None,
    material: VerifiedBatchMaterial | None,
) -> None:
    # FinalQuizResult owns the public scoring shape.  When material is present,
    # validate_observation additionally authenticates the sealed route, teaching
    # feedback, curriculum metadata, and complete provenance receipts.
    if observations:
        if not _valid_identifier(observation_session_id) or any(
            prepared.event.session_id != observation_session_id
            for prepared in observations
        ):
            raise QuizTransitionConflictError(
                "reveal observations require one explicit matching session"
            )
    elif observation_session_id is not None:
        raise QuizTransitionConflictError(
            "observation_session_id is forbidden without observations"
        )
    if not required:
        if observations:
            raise QuizTransitionConflictError(
                "observation events are allowed only on the first reveal transition"
            )
        return
    result = machine.final_result
    if result is None:
        raise QuizTransitionConflictError(
            "a reveal outbox requires the immutable FinalQuizResult"
        )
    if len(observations) != result.item_count:
        raise QuizTransitionConflictError(
            "a reveal requires exactly one observation per item"
        )
    by_item: dict[str, _PreparedObservation] = {}
    ordinals: set[int] = set()
    event_ids: set[str] = set()
    idempotency_ids: set[str] = set()
    for prepared in observations:
        event = prepared.event
        if any(
            type(value) is not bool
            for value in (
                event.first_correct,
                event.final_correct,
                event.choice_changed,
                event.self_corrected,
                event.is_transfer,
                event.is_changed_context_transfer,
                event.valid_for_progression,
            )
        ):
            raise QuizTransitionConflictError(
                "observation boolean fields must be strict booleans"
            )
        if event.item_id in by_item:
            raise QuizTransitionConflictError(
                "a reveal contains duplicate observation item IDs"
            )
        if (
            event.ordinal in ordinals
            or event.event_id in event_ids
            or event.idempotency_id in idempotency_ids
        ):
            raise QuizTransitionConflictError(
                "observation identities and ordinals must be unique"
            )
        by_item[event.item_id] = prepared
        ordinals.add(event.ordinal)
        event_ids.add(event.event_id)
        idempotency_ids.add(event.idempotency_id)
    result_by_item = {item.item_id: item for item in result.items}
    if set(by_item) != set(result_by_item):
        raise QuizTransitionConflictError(
            "observation item IDs must equal the revealed item IDs"
        )
    for item_id, result_item in result_by_item.items():
        event = by_item[item_id].event
        if (
            event.profile_id != profile_id
            or event.batch_id != machine.batch_id
            or event.first_option_id != result_item.first_selection.option_id
            or event.final_option_id != result_item.final_selection.option_id
            or event.first_confidence != result_item.first_selection.confidence
            or event.final_confidence != result_item.final_selection.confidence
            or event.first_correct != result_item.first_selection.is_correct
            or event.final_correct != result_item.final_selection.is_correct
            or event.self_corrected != result_item.self_corrected
            or event.choice_changed
            != (
                result_item.first_selection.option_id
                != result_item.final_selection.option_id
            )
            or event.batch_wrong_count != result.first_pass_wrong_count
            or event.optional_wording_shown is not None
        ):
            raise QuizTransitionConflictError(
                "observation content differs from the final quiz result"
            )
        if material is not None:
            try:
                material.validate_observation(
                    event,
                    result_item,
                    observation_session_id=observation_session_id,
                )
            except BatchMaterialValidationError as exc:
                raise QuizTransitionConflictError(
                    "observation differs from persisted verified material"
                ) from exc


def _outbox_sha256(
    observations: tuple[_PreparedObservation, ...],
) -> str | None:
    if not observations:
        return None
    return _canonical_sha256([
        {
            "itemId": prepared.event.item_id,
            "eventSha256": prepared.event_sha256,
        }
        for prepared in observations
    ])


def _outbox_record_from_row(row: sqlite3.Row) -> _PreparedObservation:
    try:
        canonical = row["canonical_json"]
        digest = row["event_sha256"]
        if not isinstance(canonical, str) or not _valid_sha256(digest):
            raise _DecodeError("invalid outbox row encoding")
        if _sha256_text(canonical) != digest:
            raise _DecodeError("outbox event hash does not verify")
        event = event_from_json(canonical)
        if type(event) is not ObservationEvent:
            raise _DecodeError("outbox row is not an observation")
        if canonical_event_json(event) != canonical:
            raise _DecodeError("outbox event JSON is not canonical")
        delivered = row["delivered"]
        if isinstance(delivered, bool) or not isinstance(delivered, int):
            raise _DecodeError("outbox delivery marker is invalid")
        if delivered not in (0, 1):
            raise _DecodeError("outbox delivery marker is invalid")
        if (
            row["profile_id"] != event.profile_id
            or row["batch_id"] != event.batch_id
            or row["item_id"] != event.item_id
            or int(row["ordinal"]) != event.ordinal
            or row["event_id"] != event.event_id
            or row["idempotency_id"] != event.idempotency_id
        ):
            raise _DecodeError("outbox row index differs from its event")
        return _PreparedObservation(
            event,
            canonical,
            digest,
            delivered=delivered == 1,
        )
    except QuizStoreCorruptionError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise QuizStoreCorruptionError(
            "observation outbox row cannot be verified"
        ) from exc


def _validate_transition(
    current: QuizMachine,
    next_machine: QuizMachine,
    receipt: TransitionReceipt | None,
) -> TransitionReceipt | None:
    if current.batch_id != next_machine.batch_id:
        raise QuizTransitionConflictError("a transition cannot change batch_id")
    if current.item_layouts != next_machine.item_layouts:
        raise QuizTransitionConflictError("a transition cannot change item layouts")
    if current.state is QuizState.PREPARING:
        if current.sealed_quiz_sha256 is not None:
            raise QuizTransitionConflictError(
                "preparing state cannot already own a sealed quiz commitment"
            )
    elif current.sealed_quiz_sha256 != next_machine.sealed_quiz_sha256:
        raise QuizTransitionConflictError(
            "a transition cannot change the sealed quiz commitment"
        )
    allowed = {
        (QuizState.PREPARING, QuizState.READY): 1,
        (QuizState.READY, QuizState.INITIAL_LOCKED): 1,
        (QuizState.READY, QuizState.REVISION_OPEN): 2,
        (QuizState.READY, QuizState.REVEALED): 2,
        (QuizState.INITIAL_LOCKED, QuizState.REVISION_OPEN): 1,
        (QuizState.INITIAL_LOCKED, QuizState.REVEALED): 1,
        (QuizState.REVISION_OPEN, QuizState.REVEALED): 1,
        (QuizState.REVEALED, QuizState.CLOSED): 1,
    }
    expected_delta = allowed.get((current.state, next_machine.state))
    if expected_delta is None:
        raise QuizTransitionConflictError(
            f"illegal stored transition {current.state.value} -> "
            f"{next_machine.state.value}"
        )
    if next_machine.version != current.version + expected_delta:
        raise QuizTransitionConflictError("stored transition has an invalid version delta")

    immutable_fields = (
        "initial_submission",
        "initial_payload_sha256",
        "initial_result",
        "initial_receipt",
        "revision_submission",
        "revision_payload_sha256",
        "revision_receipt",
        "final_result",
    )
    for field_name in immutable_fields:
        before = getattr(current, field_name)
        after = getattr(next_machine, field_name)
        if before is not None and before != after:
            raise QuizTransitionConflictError(
                f"stored transition changed immutable {field_name}"
            )

    new_receipts = tuple(
        after
        for before, after in (
            (current.initial_receipt, next_machine.initial_receipt),
            (current.revision_receipt, next_machine.revision_receipt),
        )
        if before is None and after is not None
    )
    if not new_receipts:
        if receipt is not None:
            raise QuizTransitionConflictError(
                "receipt was supplied for a transition that creates none"
            )
        return None
    if len(new_receipts) != 1 or receipt != new_receipts[0]:
        raise QuizTransitionConflictError(
            "the new machine receipt must be stored with the same transition"
        )
    return new_receipts[0]


def _locked_snapshot_replay(
    current: QuizMachine,
    proposed: QuizMachine,
    receipt: TransitionReceipt | None,
    *,
    expected_version: int,
) -> StoredTransition | None:
    if (
        receipt is not None
        or current.state is not QuizState.INITIAL_LOCKED
        or proposed.state is not QuizState.INITIAL_LOCKED
        or proposed.version != current.version
    ):
        return None
    if expected_version not in (current.version - 1, current.version):
        raise StaleQuizStateError(
            f"stale quiz version {expected_version}; current version is {current.version}"
        )
    if proposed == current:
        return StoredTransition(current, None, True)
    if (
        proposed.initial_submission is not None
        and current.initial_submission is not None
        and proposed.initial_submission.request_id
        == current.initial_submission.request_id
        and proposed.initial_payload_sha256 != current.initial_payload_sha256
    ):
        raise IdempotencyConflictError(
            "locked initial request_id was reused with a different payload"
        )
    raise QuizTransitionConflictError(
        "a different initial payload is already durably locked"
    )


def _machine_payload(machine: QuizMachine) -> dict[str, Any]:
    return {
        "schemaVersion": MACHINE_SCHEMA_VERSION,
        "batchId": machine.batch_id,
        "state": machine.state.value,
        "version": machine.version,
        "sealedQuizSha256": machine.sealed_quiz_sha256,
        "itemLayouts": [
            {"itemId": item.item_id, "optionIds": list(item.option_ids)}
            for item in machine.item_layouts
        ],
        "initialSubmission": _submission_payload(machine.initial_submission),
        "initialPayloadSha256": machine.initial_payload_sha256,
        "initialResult": _wrong_count_payload(machine.initial_result),
        "initialReceipt": _optional_receipt_payload(machine.initial_receipt),
        "revisionSubmission": _submission_payload(machine.revision_submission),
        "revisionPayloadSha256": machine.revision_payload_sha256,
        "revisionReceipt": _optional_receipt_payload(machine.revision_receipt),
        "finalResult": _final_result_payload(machine.final_result),
    }


def _submission_payload(value: QuizSubmission | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "schemaVersion": value.schema_version,
        "requestId": value.request_id,
        "batchId": value.batch_id,
        "itemCount": value.item_count,
        "selections": [
            {
                "itemId": selection.item_id,
                "optionId": selection.option_id,
                "confidence": selection.confidence,
            }
            for selection in value.selections
        ],
    }


def _wrong_count_payload(
    value: PublicWrongCountResult | None,
) -> dict[str, Any] | None:
    return None if value is None else value.to_public_dict()


def _receipt_payload(value: TransitionReceipt) -> dict[str, Any]:
    return {
        "schemaVersion": RECEIPT_SCHEMA_VERSION,
        "action": value.action,
        "batchId": value.batch_id,
        "requestId": value.request_id,
        "payloadSha256": value.payload_sha256,
        "fromVersion": value.from_version,
        "toVersion": value.to_version,
        "outputSha256": value.output_sha256,
        "receiptSha256": value.receipt_sha256,
    }


def _stored_receipt_payload(
    value: TransitionReceipt,
    outbox_sha256: str | None,
) -> dict[str, Any]:
    payload = _receipt_payload(value)
    payload["schemaVersion"] = STORED_RECEIPT_SCHEMA_VERSION
    payload["outboxSha256"] = outbox_sha256
    return payload


def _optional_receipt_payload(
    value: TransitionReceipt | None,
) -> dict[str, Any] | None:
    return None if value is None else _receipt_payload(value)


def _final_result_payload(value: FinalQuizResult | None) -> dict[str, Any] | None:
    return None if value is None else value.to_public_dict()


def _machine_from_payload(value: object) -> QuizMachine:
    payload = _object(
        value,
        {
            "schemaVersion",
            "batchId",
            "state",
            "version",
            "sealedQuizSha256",
            "itemLayouts",
            "initialSubmission",
            "initialPayloadSha256",
            "initialResult",
            "initialReceipt",
            "revisionSubmission",
            "revisionPayloadSha256",
            "revisionReceipt",
            "finalResult",
        },
        "machine",
    )
    if payload["schemaVersion"] != MACHINE_SCHEMA_VERSION:
        raise _DecodeError("unsupported machine schema")
    try:
        state = QuizState(_text(payload["state"], "state"))
    except ValueError as exc:
        raise _DecodeError("unknown quiz state") from exc
    machine = QuizMachine(
        batch_id=_text(payload["batchId"], "batchId"),
        state=state,
        version=_integer(payload["version"], "version", minimum=0),
        sealed_quiz_sha256=_optional_hash(payload["sealedQuizSha256"]),
        item_layouts=tuple(
            _layout_from_payload(item)
            for item in _array(payload["itemLayouts"], "itemLayouts")
        ),
        initial_submission=_optional_submission(payload["initialSubmission"]),
        initial_payload_sha256=_optional_hash(payload["initialPayloadSha256"]),
        initial_result=_optional_wrong_count(payload["initialResult"]),
        initial_receipt=_optional_receipt(payload["initialReceipt"]),
        revision_submission=_optional_submission(payload["revisionSubmission"]),
        revision_payload_sha256=_optional_hash(payload["revisionPayloadSha256"]),
        revision_receipt=_optional_receipt(payload["revisionReceipt"]),
        final_result=_optional_final_result(payload["finalResult"]),
    )
    _validate_machine(machine)
    return machine


def _layout_from_payload(value: object) -> QuizItemLayout:
    payload = _object(value, {"itemId", "optionIds"}, "item layout")
    return QuizItemLayout(
        item_id=_text(payload["itemId"], "itemId"),
        option_ids=tuple(
            _text(option, "optionId")
            for option in _array(payload["optionIds"], "optionIds")
        ),
    )


def _optional_submission(value: object) -> QuizSubmission | None:
    if value is None:
        return None
    payload = _object(
        value,
        {"schemaVersion", "requestId", "batchId", "itemCount", "selections"},
        "submission",
    )
    return QuizSubmission(
        schema_version=_text(payload["schemaVersion"], "schemaVersion"),
        request_id=_text(payload["requestId"], "requestId"),
        batch_id=_text(payload["batchId"], "batchId"),
        item_count=_integer(payload["itemCount"], "itemCount", minimum=0),
        selections=tuple(
            _selection_from_payload(selection)
            for selection in _array(payload["selections"], "selections")
        ),
    )


def _selection_from_payload(value: object) -> QuizSelection:
    payload = _object(
        value,
        {"itemId", "optionId", "confidence"},
        "selection",
    )
    return QuizSelection(
        item_id=_text(payload["itemId"], "itemId"),
        option_id=_text(payload["optionId"], "optionId"),
        confidence=_text(payload["confidence"], "confidence"),
    )


def _optional_wrong_count(value: object) -> PublicWrongCountResult | None:
    if value is None:
        return None
    payload = _object(
        value,
        {"schemaVersion", "batchId", "itemCount", "wrongCount", "revisionRequired"},
        "initial result",
    )
    return PublicWrongCountResult(
        schema_version=_text(payload["schemaVersion"], "schemaVersion"),
        batch_id=_text(payload["batchId"], "batchId"),
        item_count=_integer(payload["itemCount"], "itemCount", minimum=0),
        wrong_count=_integer(payload["wrongCount"], "wrongCount", minimum=0),
        revision_required=_boolean(payload["revisionRequired"], "revisionRequired"),
    )


def _optional_receipt(value: object) -> TransitionReceipt | None:
    if value is None:
        return None
    payload = _object(
        value,
        {
            "schemaVersion",
            "action",
            "batchId",
            "requestId",
            "payloadSha256",
            "fromVersion",
            "toVersion",
            "outputSha256",
            "receiptSha256",
        },
        "transition receipt",
    )
    if payload["schemaVersion"] != RECEIPT_SCHEMA_VERSION:
        raise _DecodeError("unsupported receipt schema")
    receipt = TransitionReceipt(
        action=_text(payload["action"], "action"),
        batch_id=_text(payload["batchId"], "batchId"),
        request_id=_text(payload["requestId"], "requestId"),
        payload_sha256=_hash(payload["payloadSha256"], "payloadSha256"),
        from_version=_integer(payload["fromVersion"], "fromVersion", minimum=0),
        to_version=_integer(payload["toVersion"], "toVersion", minimum=0),
        output_sha256=_hash(payload["outputSha256"], "outputSha256"),
        receipt_sha256=_hash(payload["receiptSha256"], "receiptSha256"),
    )
    _validate_receipt(receipt)
    return receipt


def _optional_final_result(value: object) -> FinalQuizResult | None:
    if value is None:
        return None
    payload = _object(
        value,
        {
            "schemaVersion",
            "batchId",
            "itemCount",
            "firstPassWrongCount",
            "finalCorrectCount",
            "revisionUsed",
            "items",
        },
        "final result",
    )
    return FinalQuizResult(
        schema_version=_text(payload["schemaVersion"], "schemaVersion"),
        batch_id=_text(payload["batchId"], "batchId"),
        item_count=_integer(payload["itemCount"], "itemCount", minimum=0),
        first_pass_wrong_count=_integer(
            payload["firstPassWrongCount"], "firstPassWrongCount", minimum=0
        ),
        final_correct_count=_integer(
            payload["finalCorrectCount"], "finalCorrectCount", minimum=0
        ),
        revision_used=_boolean(payload["revisionUsed"], "revisionUsed"),
        items=tuple(
            _final_item_from_payload(item)
            for item in _array(payload["items"], "items")
        ),
    )


def _final_item_from_payload(value: object) -> FinalItemResult:
    payload = _object(
        value,
        {
            "itemId",
            "firstSelection",
            "finalSelection",
            "correctOptionId",
            "correctAnswer",
            "trustedSteps",
            "possibleError",
            "reliableMethod",
            "selfCorrected",
        },
        "final item",
    )
    possible_error = payload["possibleError"]
    if possible_error is not None:
        possible_error = _text(possible_error, "possibleError")
    return FinalItemResult(
        item_id=_text(payload["itemId"], "itemId"),
        first_selection=_revealed_selection(payload["firstSelection"]),
        final_selection=_revealed_selection(payload["finalSelection"]),
        correct_option_id=_text(payload["correctOptionId"], "correctOptionId"),
        correct_answer=_text(payload["correctAnswer"], "correctAnswer"),
        trusted_steps=tuple(
            _text(step, "trustedStep")
            for step in _array(payload["trustedSteps"], "trustedSteps")
        ),
        possible_error=possible_error,
        reliable_method=_text(payload["reliableMethod"], "reliableMethod"),
        self_corrected=_boolean(payload["selfCorrected"], "selfCorrected"),
    )


def _revealed_selection(value: object) -> RevealedSelectionResult:
    payload = _object(
        value,
        {"optionId", "confidence", "isCorrect"},
        "revealed selection",
    )
    return RevealedSelectionResult(
        option_id=_text(payload["optionId"], "optionId"),
        confidence=_text(payload["confidence"], "confidence"),
        is_correct=_boolean(payload["isCorrect"], "isCorrect"),
    )


def _stored_receipt_from_payload(
    value: object,
) -> tuple[TransitionReceipt, str | None]:
    payload = _object(
        value,
        {
            "schemaVersion",
            "action",
            "batchId",
            "requestId",
            "payloadSha256",
            "fromVersion",
            "toVersion",
            "outputSha256",
            "receiptSha256",
            "outboxSha256",
        },
        "stored transition receipt",
    )
    if payload["schemaVersion"] != STORED_RECEIPT_SCHEMA_VERSION:
        raise _DecodeError("unsupported stored receipt schema")
    receipt = TransitionReceipt(
        action=_text(payload["action"], "action"),
        batch_id=_text(payload["batchId"], "batchId"),
        request_id=_text(payload["requestId"], "requestId"),
        payload_sha256=_hash(payload["payloadSha256"], "payloadSha256"),
        from_version=_integer(payload["fromVersion"], "fromVersion", minimum=0),
        to_version=_integer(payload["toVersion"], "toVersion", minimum=0),
        output_sha256=_hash(payload["outputSha256"], "outputSha256"),
        receipt_sha256=_hash(payload["receiptSha256"], "receiptSha256"),
    )
    _validate_receipt(receipt)
    return receipt, _optional_hash(payload["outboxSha256"])


def _stored_receipt_from_row(
    row: sqlite3.Row,
) -> tuple[TransitionReceipt, str | None]:
    try:
        raw = row["receipt_json"]
        digest = row["receipt_sha256"]
        if not isinstance(raw, str) or not _valid_sha256(digest):
            raise _DecodeError("invalid receipt row encoding")
        if _sha256_text(raw) != digest:
            raise _DecodeError("receipt row hash does not verify")
        receipt, outbox_sha256 = _stored_receipt_from_payload(
            _parse_canonical_json(raw)
        )
        if (
            row["batch_id"] != receipt.batch_id
            or row["action"] != receipt.action
            or row["request_id"] != receipt.request_id
            or row["payload_sha256"] != receipt.payload_sha256
            or int(row["from_version"]) != receipt.from_version
            or int(row["to_version"]) != receipt.to_version
            or row["output_sha256"] != receipt.output_sha256
        ):
            raise _DecodeError("receipt row index differs from its payload")
        return receipt, outbox_sha256
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        QuizTransitionConflictError,
    ) as exc:
        raise QuizStoreCorruptionError("transition receipt cannot be verified") from exc


def _validate_machine(machine: QuizMachine) -> None:
    if not isinstance(machine, QuizMachine):
        raise QuizTransitionConflictError("value is not a QuizMachine")
    try:
        public_shape = new_quiz(machine.batch_id, machine.item_layouts)
    except ValueError as exc:
        raise QuizTransitionConflictError("machine layout is invalid") from exc
    if public_shape.item_layouts != machine.item_layouts:
        raise QuizTransitionConflictError("machine layouts are not normalized")
    if isinstance(machine.version, bool) or not isinstance(machine.version, int):
        raise QuizTransitionConflictError("machine version must be an integer")
    if machine.state is QuizState.PREPARING:
        if machine.sealed_quiz_sha256 is not None:
            raise QuizTransitionConflictError(
                "preparing machine cannot contain a sealed quiz commitment"
            )
    elif not _valid_sha256(machine.sealed_quiz_sha256):
        raise QuizTransitionConflictError(
            "ready or later machine requires a lowercase sealed quiz SHA-256"
        )

    _validate_submission(machine, machine.initial_submission, "initial")
    _validate_submission(machine, machine.revision_submission, "revision")
    _validate_payload_hash(
        machine.initial_submission,
        machine.initial_payload_sha256,
        "initial",
    )
    _validate_payload_hash(
        machine.revision_submission,
        machine.revision_payload_sha256,
        "revision",
    )
    _validate_initial_result(machine)
    _validate_final_result(machine)
    _validate_machine_receipts(machine)

    empty = (
        machine.initial_submission,
        machine.initial_payload_sha256,
        machine.initial_result,
        machine.initial_receipt,
        machine.revision_submission,
        machine.revision_payload_sha256,
        machine.revision_receipt,
        machine.final_result,
    )
    initial_complete = all(
        value is not None
        for value in (
            machine.initial_submission,
            machine.initial_payload_sha256,
            machine.initial_result,
            machine.initial_receipt,
        )
    )
    revision_complete = all(
        value is not None
        for value in (
            machine.revision_submission,
            machine.revision_payload_sha256,
            machine.revision_receipt,
        )
    )

    if machine.state is QuizState.PREPARING:
        valid = machine.version == 0 and all(value is None for value in empty)
    elif machine.state is QuizState.READY:
        valid = machine.version == 1 and all(value is None for value in empty)
    elif machine.state is QuizState.INITIAL_LOCKED:
        valid = (
            machine.version == 2
            and machine.initial_submission is not None
            and machine.initial_payload_sha256 is not None
            and all(value is None for value in empty[2:])
        )
    elif machine.state is QuizState.REVISION_OPEN:
        valid = (
            machine.version == 3
            and initial_complete
            and machine.initial_result is not None
            and machine.initial_result.revision_required
            and all(value is None for value in empty[4:])
        )
    elif machine.state in (QuizState.REVEALED, QuizState.CLOSED):
        close_delta = 1 if machine.state is QuizState.CLOSED else 0
        zero_wrong = (
            initial_complete
            and machine.initial_result is not None
            and not machine.initial_result.revision_required
            and machine.revision_submission is None
            and machine.revision_payload_sha256 is None
            and machine.revision_receipt is None
            and machine.final_result is not None
            and not machine.final_result.revision_used
            and machine.version == 3 + close_delta
        )
        revised = (
            initial_complete
            and machine.initial_result is not None
            and machine.initial_result.revision_required
            and revision_complete
            and machine.final_result is not None
            and machine.final_result.revision_used
            and machine.version == 4 + close_delta
        )
        valid = zero_wrong or revised
    else:  # pragma: no cover - Enum exhaustiveness guard
        valid = False
    if not valid:
        raise QuizTransitionConflictError(
            f"machine fields do not match {machine.state.value} state"
        )


def _validate_submission(
    machine: QuizMachine,
    submission: QuizSubmission | None,
    label: str,
) -> None:
    if submission is None:
        return
    if (
        submission.schema_version != PUBLIC_SCHEMA_VERSION
        or submission.batch_id != machine.batch_id
        or not _valid_identifier(submission.request_id)
        or submission.item_count != len(machine.item_layouts)
        or len(submission.selections) != len(machine.item_layouts)
    ):
        raise QuizTransitionConflictError(f"{label} submission is invalid")
    layout_by_item = {layout.item_id: layout for layout in machine.item_layouts}
    selection_by_item: dict[str, QuizSelection] = {}
    for selection in submission.selections:
        if (
            selection.item_id in selection_by_item
            or selection.item_id not in layout_by_item
            or selection.option_id not in layout_by_item[selection.item_id].option_ids
            or selection.confidence not in ALLOWED_CONFIDENCE
        ):
            raise QuizTransitionConflictError(f"{label} selection is invalid")
        selection_by_item[selection.item_id] = selection
    if set(selection_by_item) != set(layout_by_item):
        raise QuizTransitionConflictError(f"{label} submission is incomplete")


def _validate_payload_hash(
    submission: QuizSubmission | None,
    digest: str | None,
    label: str,
) -> None:
    if submission is None:
        if digest is not None:
            raise QuizTransitionConflictError(f"{label} hash has no submission")
        return
    if digest is None or not _valid_sha256(digest):
        raise QuizTransitionConflictError(f"{label} submission hash is invalid")
    if _canonical_sha256(_submission_payload(submission)) != digest:
        raise QuizTransitionConflictError(f"{label} submission hash does not verify")


def _validate_initial_result(machine: QuizMachine) -> None:
    value = machine.initial_result
    if value is None:
        return
    item_count = len(machine.item_layouts)
    if (
        value.schema_version != PUBLIC_SCHEMA_VERSION
        or value.batch_id != machine.batch_id
        or value.item_count != item_count
        or isinstance(value.wrong_count, bool)
        or not isinstance(value.wrong_count, int)
        or not 0 <= value.wrong_count <= item_count
        or value.revision_required != (value.wrong_count > 0)
    ):
        raise QuizTransitionConflictError("initial result is invalid")


def _validate_final_result(machine: QuizMachine) -> None:
    result = machine.final_result
    if result is None:
        return
    layouts = machine.item_layouts
    layout_by_item = {layout.item_id: layout for layout in layouts}
    if (
        result.schema_version != PUBLIC_SCHEMA_VERSION
        or result.batch_id != machine.batch_id
        or result.item_count != len(layouts)
        or len(result.items) != len(layouts)
        or tuple(item.item_id for item in result.items)
        != tuple(layout.item_id for layout in layouts)
    ):
        raise QuizTransitionConflictError("final result shape is invalid")
    first_by_item = _submission_selection_map(machine.initial_submission)
    final_submission = (
        machine.revision_submission if result.revision_used else machine.initial_submission
    )
    final_by_item = _submission_selection_map(final_submission)
    for item in result.items:
        layout = layout_by_item[item.item_id]
        first = first_by_item.get(item.item_id)
        final = final_by_item.get(item.item_id)
        if first is None or final is None:
            raise QuizTransitionConflictError("final result lacks a stored submission")
        if (
            item.correct_option_id not in layout.option_ids
            or item.first_selection.option_id != first.option_id
            or item.first_selection.confidence != first.confidence
            or item.final_selection.option_id != final.option_id
            or item.final_selection.confidence != final.confidence
            or item.first_selection.is_correct
            != (first.option_id == item.correct_option_id)
            or item.final_selection.is_correct
            != (final.option_id == item.correct_option_id)
            or item.self_corrected
            != (
                not item.first_selection.is_correct
                and item.final_selection.is_correct
            )
            or not isinstance(item.correct_answer, str)
            or not 1 <= len(item.correct_answer) <= 256
            or not 1 <= len(item.trusted_steps) <= 8
            or any(
                not isinstance(step, str) or not 1 <= len(step) <= 512
                for step in item.trusted_steps
            )
            or (
                item.possible_error is not None
                and (
                    not isinstance(item.possible_error, str)
                    or not 1 <= len(item.possible_error) <= 512
                )
            )
            or not isinstance(item.reliable_method, str)
            or not 1 <= len(item.reliable_method) <= 512
        ):
            raise QuizTransitionConflictError("final item result is invalid")
    first_wrong = sum(not item.first_selection.is_correct for item in result.items)
    final_correct = sum(item.final_selection.is_correct for item in result.items)
    if (
        result.first_pass_wrong_count != first_wrong
        or result.final_correct_count != final_correct
        or machine.initial_result is None
        or machine.initial_result.wrong_count != first_wrong
    ):
        raise QuizTransitionConflictError("final result counts do not verify")


def _validate_machine_receipts(machine: QuizMachine) -> None:
    if machine.initial_receipt is not None:
        receipt = machine.initial_receipt
        _validate_receipt(receipt)
        if (
            receipt.action != "initial"
            or receipt.batch_id != machine.batch_id
            or machine.initial_submission is None
            or machine.initial_result is None
            or receipt.request_id != machine.initial_submission.request_id
            or receipt.payload_sha256 != machine.initial_payload_sha256
            or receipt.from_version != 1
            or receipt.to_version != 3
            or receipt.output_sha256
            != _canonical_sha256(machine.initial_result.to_public_dict())
        ):
            raise QuizTransitionConflictError("initial receipt does not verify")
    if machine.revision_receipt is not None:
        receipt = machine.revision_receipt
        _validate_receipt(receipt)
        if (
            receipt.action != "revision"
            or receipt.batch_id != machine.batch_id
            or machine.revision_submission is None
            or machine.final_result is None
            or receipt.request_id != machine.revision_submission.request_id
            or receipt.payload_sha256 != machine.revision_payload_sha256
            or receipt.from_version != 3
            or receipt.to_version != 4
            or receipt.output_sha256
            != _canonical_sha256(machine.final_result.to_public_dict())
        ):
            raise QuizTransitionConflictError("revision receipt does not verify")


def _validate_receipt(receipt: TransitionReceipt) -> None:
    if (
        receipt.action not in _RECEIPT_ACTIONS
        or not _valid_identifier(receipt.batch_id)
        or not _valid_identifier(receipt.request_id)
        or not _valid_sha256(receipt.payload_sha256)
        or isinstance(receipt.from_version, bool)
        or not isinstance(receipt.from_version, int)
        or isinstance(receipt.to_version, bool)
        or not isinstance(receipt.to_version, int)
        or not 0 <= receipt.from_version < receipt.to_version
        or not _valid_sha256(receipt.output_sha256)
        or not _valid_sha256(receipt.receipt_sha256)
    ):
        raise QuizTransitionConflictError("transition receipt is invalid")
    fields = {
        "action": receipt.action,
        "batchId": receipt.batch_id,
        "requestId": receipt.request_id,
        "payloadSha256": receipt.payload_sha256,
        "fromVersion": receipt.from_version,
        "toVersion": receipt.to_version,
        "outputSha256": receipt.output_sha256,
    }
    if _canonical_sha256(fields) != receipt.receipt_sha256:
        raise QuizTransitionConflictError("transition receipt hash does not verify")


def _submission_selection_map(
    submission: QuizSubmission | None,
) -> dict[str, QuizSelection]:
    if submission is None:
        return {}
    return {selection.item_id: selection for selection in submission.selections}


def _parse_canonical_json(raw: str) -> object:
    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _DecodeError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise _DecodeError(f"non-finite JSON number: {value}")

    value = json.loads(
        raw,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_constant,
    )
    if _canonical_json(value) != raw:
        raise _DecodeError("JSON is not canonical")
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonical_sha256(value: object) -> str:
    return _sha256_text(_canonical_json(value))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


def _valid_identifier(value: object) -> bool:
    return isinstance(value, str) and _IDENTIFIER_PATTERN.fullmatch(value) is not None


def _profile_id(value: object) -> str:
    if not _valid_identifier(value):
        raise QuizOwnershipError("profile_id must be a strict local identifier")
    return value


def _object(
    value: object,
    keys: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise _DecodeError(f"{label} has unknown or missing fields")
    return value


def _array(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise _DecodeError(f"{label} must be an array")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise _DecodeError(f"{label} must be text")
    return value


def _integer(value: object, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _DecodeError(f"{label} must be an integer at least {minimum}")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise _DecodeError(f"{label} must be a boolean")
    return value


def _hash(value: object, label: str) -> str:
    if not _valid_sha256(value):
        raise _DecodeError(f"{label} must be lowercase SHA-256")
    return value


def _optional_hash(value: object) -> str | None:
    if value is None:
        return None
    return _hash(value, "optional hash")
