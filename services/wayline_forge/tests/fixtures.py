"""Deterministic fixtures for Wayline evidence and progression tests only."""

from __future__ import annotations

from services.wayline_forge.app.events import (
    AssistedRouteCompletionEvent,
    BattleOutcomeEvent,
    BossOutcomeEvent,
    ObservationEvent,
    ProvenanceReceipts,
    SealTrialOutcomeEvent,
)


_CORE = ("place_value", "mental_add_sub")
_RECEIPTS = ProvenanceReceipts(
    generator="generator-test-v1",
    model="model-test-v1",
    adapter="adapter-test-v1",
    gguf="gguf-test-v1",
    verifier="verifier-test-v1",
    registry="registry-test-v1",
    cache="cache-test-v1",
)


class EventFactory:
    """Build complete immutable events without leaking fixture logic into runtime code."""

    @staticmethod
    def activate(
        ordinal: int = 1,
        *,
        world: str = "valuehold",
        core_subskills: tuple[str, ...] = _CORE,
        profile: str = "profile-1",
        session: str = "session-1",
    ):
        from services.wayline_forge.app.events import WorldActivatedEvent

        return WorldActivatedEvent(
            schema_version="wayline.event.v1",
            event_id=f"world-activated-{ordinal}-{world}",
            idempotency_id=f"world-activation-request-{ordinal}-{world}",
            ordinal=ordinal,
            profile_id=profile,
            session_id=session,
            world_id=world,
            battle_id="campaign-map",
            core_subskill_ids=core_subskills,
            curriculum_receipt="curriculum-test-v1",
            occurred_at=f"2026-07-11T10:{ordinal % 60:02d}:00+00:00",
        )

    @staticmethod
    def observation(
        *,
        ordinal: int = 1,
        world: str = "valuehold",
        battle: str = "battle-1",
        batch: str = "batch-1",
        skill: str = "place_value",
        question: str | None = None,
        template: str = "template-a",
        operand: str | None = None,
        context: str = "context-a",
        first_correct: bool,
        final_correct: bool | None = None,
        first_procedure: str | None = None,
        final_procedure: str | None = None,
        confidence: str = "leaning",
        final_confidence: str | None = None,
        changed_context: bool = False,
        transfer: bool = False,
        targeted_procedures: tuple[str, ...] = (),
        session: str = "session-1",
        profile: str = "profile-1",
        valid: bool = True,
        core_subskills: tuple[str, ...] = _CORE,
    ) -> ObservationEvent:
        final_correct = first_correct if final_correct is None else final_correct
        final_confidence = confidence if final_confidence is None else final_confidence
        question = question or f"question-{ordinal}"
        operand = operand or f"operands-{ordinal}"
        first_option = "option-correct" if first_correct else f"option-{first_procedure or 'wrong'}"
        final_option = "option-correct" if final_correct else f"option-{final_procedure or first_procedure or 'wrong'}"
        if first_correct:
            first_procedure = None
        if final_correct:
            final_procedure = None
        elif final_procedure is None:
            final_procedure = first_procedure

        return ObservationEvent(
            schema_version="wayline.event.v1",
            event_id=f"observation-{ordinal}-{question}",
            idempotency_id=f"idempotency-{ordinal}-{question}",
            ordinal=ordinal,
            profile_id=profile,
            session_id=session,
            world_id=world,
            battle_id=battle,
            batch_id=batch,
            item_id=f"item-{ordinal}-{question}",
            question_id=question,
            template_id=template,
            content_version_id="content-test-v1",
            skill_id=skill,
            world_core_subskill_ids=core_subskills,
            operand_signature=operand,
            context_id=context,
            first_option_id=first_option,
            final_option_id=final_option,
            first_confidence=confidence,
            final_confidence=final_confidence,
            first_correct=first_correct,
            final_correct=final_correct,
            choice_changed=first_option != final_option,
            self_corrected=(not first_correct and final_correct),
            first_procedure_id=first_procedure,
            final_procedure_id=final_procedure,
            targeted_procedure_ids=targeted_procedures,
            is_transfer=transfer,
            is_changed_context_transfer=changed_context,
            valid_for_progression=valid,
            batch_wrong_count=0 if first_correct else 1,
            canonical_feedback=(
                "This answer can come from a compatible verified procedure.",
                "A reliable method is to use the trusted operation by place.",
            ),
            optional_wording_shown=None,
            receipts=_RECEIPTS,
            occurred_at=f"2026-07-11T12:{ordinal % 60:02d}:00+00:00",
        )

    @classmethod
    def wrong(
        cls,
        procedure: str,
        *,
        ordinal: int = 1,
        confidence: str = "leaning",
        template: str = "template-a",
        question: str | None = None,
        keep_wrong: bool = True,
        final_procedure: str | None = None,
        **kwargs: object,
    ) -> ObservationEvent:
        final_correct = not keep_wrong and final_procedure is None
        return cls.observation(
            ordinal=ordinal,
            confidence=confidence,
            template=template,
            question=question,
            first_correct=False,
            final_correct=final_correct,
            first_procedure=procedure,
            final_procedure=final_procedure,
            **kwargs,
        )

    @classmethod
    def correct(
        cls,
        *,
        ordinal: int = 1,
        confidence: str = "leaning",
        final_correct: bool = True,
        final_procedure: str | None = None,
        **kwargs: object,
    ) -> ObservationEvent:
        return cls.observation(
            ordinal=ordinal,
            confidence=confidence,
            first_correct=True,
            final_correct=final_correct,
            final_procedure=final_procedure,
            **kwargs,
        )

    @staticmethod
    def battle_win(
        ordinal: int,
        *,
        battle: str | None = None,
        world: str = "valuehold",
        profile: str = "profile-1",
        session: str = "session-1",
        lead_in: bool = True,
    ) -> BattleOutcomeEvent:
        battle = battle or f"battle-{ordinal}"
        return BattleOutcomeEvent(
            schema_version="wayline.event.v1",
            event_id=f"battle-outcome-{ordinal}-{battle}",
            idempotency_id=f"battle-idempotency-{ordinal}-{battle}",
            ordinal=ordinal,
            profile_id=profile,
            session_id=session,
            world_id=world,
            battle_id=battle,
            won=True,
            is_lead_in=lead_in,
            occurred_at=f"2026-07-11T11:{ordinal % 60:02d}:00+00:00",
        )

    @staticmethod
    def boss(
        ordinal: int,
        *,
        final_correct: int,
        item_count: int = 8,
        combat_won: bool = True,
        finale: bool = False,
        world: str = "valuehold",
    ) -> BossOutcomeEvent:
        return BossOutcomeEvent(
            schema_version="wayline.event.v1",
            event_id=f"boss-outcome-{ordinal}",
            idempotency_id=f"boss-idempotency-{ordinal}",
            ordinal=ordinal,
            profile_id="profile-1",
            session_id="session-1",
            world_id=world,
            battle_id="campaign-finale" if finale else f"{world}-boss",
            combat_won=combat_won,
            final_correct=final_correct,
            item_count=item_count,
            is_campaign_finale=finale,
            occurred_at=f"2026-07-11T13:{ordinal % 60:02d}:00+00:00",
        )

    @staticmethod
    def seal_trial(
        ordinal: int,
        *,
        passed: bool,
        attempt: int,
        world: str = "valuehold",
    ) -> SealTrialOutcomeEvent:
        return SealTrialOutcomeEvent(
            schema_version="wayline.event.v1",
            event_id=f"seal-trial-{ordinal}-{attempt}",
            idempotency_id=f"seal-idempotency-{ordinal}-{attempt}",
            ordinal=ordinal,
            profile_id="profile-1",
            session_id="session-1",
            world_id=world,
            battle_id=f"{world}-seal-trial",
            attempt_number=attempt,
            passed=passed,
            final_correct=3 if passed else 2,
            item_count=3,
            occurred_at=f"2026-07-11T14:{ordinal % 60:02d}:00+00:00",
        )

    @staticmethod
    def assisted_completion(
        ordinal: int = 2,
        *,
        world: str = "valuehold",
        profile: str = "profile-1",
        session: str = "session-1",
        request: str = "complete-assisted-001",
    ) -> AssistedRouteCompletionEvent:
        possible_errors = (
            "The selected value treats the digit as tens instead of thousands.",
            "The selected value treats the digit as ones instead of hundreds.",
        )
        reliable_methods = (
            "Name the digit's place, then write its value.",
            "Name the digit's place, then write its value.",
        )
        trusted_steps = (
            (
                "The 6 is in the thousands place.",
                "Six thousands equals 6000.",
            ),
            (
                "The 3 is in the hundreds place.",
                "Three hundreds equals 300.",
            ),
        )
        return AssistedRouteCompletionEvent(
            schema_version="wayline.event.v2",
            event_id=f"assisted-completion-{request}",
            idempotency_id=request,
            ordinal=ordinal,
            profile_id=profile,
            session_id=session,
            world_id=world,
            battle_id=f"{world}_assisted_route",
            occurred_at=f"2026-07-12T15:{ordinal % 60:02d}:00Z",
            route_revision="fresh-assisted-v1",
            route_id="assisted-aaaaaaaaaaaaaaaaaaaaaaaa",
            material_sha256="a" * 64,
            worked_example_item_id="item-worked-001",
            supported_item_ids=("item-supported-001", "item-supported-002"),
            supported_question_ids=("question-supported-001", "question-supported-002"),
            selected_option_ids=("opt-supported-001-b", "opt-supported-002-a"),
            selected_answers=("60", "3"),
            correct_option_ids=("opt-supported-001-d", "opt-supported-002-c"),
            correct_answers=("6000", "300"),
            confidences=("leaning", "certain"),
            correctness=(False, False),
            selected_procedure_ids=("place_value_tens", "place_value_face_value"),
            possible_errors=possible_errors,
            reliable_methods=reliable_methods,
            trusted_steps=trusted_steps,
            canonical_feedback=(
                (possible_errors[0], reliable_methods[0], *trusted_steps[0]),
                (possible_errors[1], reliable_methods[1], *trusted_steps[1]),
            ),
            receipts=(_RECEIPTS, _RECEIPTS),
            final_correct=0,
            item_count=2,
        )

    @classmethod
    def ready_valuehold_events(cls, latest_ten_correct: int = 7) -> tuple[object, ...]:
        events: list[object] = [cls.activate(1, world="valuehold", core_subskills=_CORE)]
        events.extend(
            cls.battle_win(ordinal, battle=f"lead-in-{ordinal - 1}")
            for ordinal in range(2, 6)
        )
        first_six = [True, True, False, True, False, True]
        latest = [True] * latest_ten_correct + [False] * (10 - latest_ten_correct)
        correctness = first_six + latest
        for index, correct in enumerate(correctness, start=1):
            ordinal = index + 5
            skill = _CORE[(index - 1) % len(_CORE)]
            kwargs = dict(
                ordinal=ordinal,
                world="valuehold",
                battle=f"lead-in-{min(4, (index - 1) // 4 + 1)}",
                batch=f"batch-{(index - 1) // 4 + 1}",
                skill=skill,
                template=f"template-{index}",
                question=f"question-{index}",
                confidence="leaning",
                core_subskills=_CORE,
            )
            if correct:
                events.append(cls.correct(**kwargs))
            else:
                events.append(cls.wrong(f"error-{skill}", **kwargs))
        return tuple(events)

    @classmethod
    def ready_valuehold_state(cls, latest_ten_correct: int = 7):
        from services.wayline_forge.app.evidence_reducer import reduce_events

        return reduce_events(cls.ready_valuehold_events(latest_ten_correct))


event = EventFactory()
