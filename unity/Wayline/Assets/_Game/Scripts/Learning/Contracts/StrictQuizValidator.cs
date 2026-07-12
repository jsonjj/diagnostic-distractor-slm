using System;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace Wayline.Learning.Contracts
{
    public static class StrictQuizValidator
    {
        private const string ContractVersion = "wayline.v1";

        private static readonly Regex IdentifierPattern = new Regex(
            "^[A-Za-z0-9][A-Za-z0-9._:-]{2,95}$",
            RegexOptions.CultureInvariant);

        public static T Deserialize<T>(string json)
        {
            if (json == null)
                throw new ArgumentNullException(nameof(json));

            JToken token;
            try
            {
                token = JToken.Parse(
                    json,
                    new JsonLoadSettings
                    {
                        DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error,
                        LineInfoHandling = LineInfoHandling.Load
                    });
            }
            catch (JsonReaderException exception)
            {
                throw new JsonSerializationException("JSON is malformed or has duplicate members", exception);
            }

            ValidateTokenShape(typeof(T), token, "$", false);

            var serializer = JsonSerializer.Create(
                new JsonSerializerSettings
                {
                    MissingMemberHandling = MissingMemberHandling.Error,
                    FloatParseHandling = FloatParseHandling.Decimal,
                    DateParseHandling = DateParseHandling.None
                });
            var value = token.ToObject<T>(serializer);
            ValidateObject(value);
            return value;
        }

        public static void Validate(PublicQuizBatch batch)
        {
            Require(batch, nameof(batch));
            RequireVersion(batch.SchemaVersion);
            RequireIdentifier(batch.BatchId, "batchId");
            RequireCount(batch.ItemCount, 3, 10, "itemCount");
            Require(batch.Items, "items");
            Require(batch.ItemCount == batch.Items.Count, "itemCount must equal the number of items");

            var itemIds = new HashSet<string>(StringComparer.Ordinal);
            foreach (var item in batch.Items)
            {
                Require(item, "item");
                RequireIdentifier(item.ItemId, "itemId");
                Require(itemIds.Add(item.ItemId), "itemId must be unique within a batch");
                RequireText(item.Prompt, 1, 1000, "prompt");
                Require(item.Options, "options");
                Require(item.Options.Count == 4, "every item must have exactly four options");

                var optionIds = new HashSet<string>(StringComparer.Ordinal);
                var displays = new HashSet<string>(StringComparer.Ordinal);
                foreach (var option in item.Options)
                {
                    Require(option, "option");
                    RequireIdentifier(option.OptionId, "optionId");
                    Require(optionIds.Add(option.OptionId), "optionId must be unique within an item");
                    RequireText(option.DisplayText, 1, 256, "displayText");
                    Require(
                        displays.Add(NormalizeDisplay(option.DisplayText)),
                        "displayText must be unique within an item");
                }
            }
        }

        public static void Validate(BattleQuizRequest request)
        {
            Require(request, nameof(request));
            RequireVersion(request.SchemaVersion);
            RequireIdentifier(request.RequestId, "requestId");
            RequireIdentifier(request.SessionId, "sessionId");
            RequireIdentifier(request.BattleId, "battleId");
            RequireIdentifier(request.WorldId, "worldId");
            Require(Enum.IsDefined(typeof(BattleTier), request.BattleTier), "battleTier is invalid");
        }

        public static void Validate(InitialSubmission submission)
        {
            Require(submission, nameof(submission));
            ValidateSubmission(
                submission.SchemaVersion,
                submission.RequestId,
                submission.BatchId,
                submission.ItemCount,
                submission.Selections);
        }

        public static void Validate(RevisionSubmission submission)
        {
            Require(submission, nameof(submission));
            ValidateSubmission(
                submission.SchemaVersion,
                submission.RequestId,
                submission.BatchId,
                submission.ItemCount,
                submission.Selections);
        }

        public static void Validate(WrongCountResult result)
        {
            Require(result, nameof(result));
            ValidateWrongCount(
                result.SchemaVersion,
                result.BatchId,
                result.ItemCount,
                result.WrongCount,
                result.RevisionRequired);
        }

        public static void Validate(FinalQuizResult result)
        {
            Require(result, nameof(result));
            RequireVersion(result.SchemaVersion);
            RequireIdentifier(result.BatchId, "batchId");
            RequireCount(result.ItemCount, 3, 10, "itemCount");
            RequireCount(result.FirstPassWrongCount, 0, 10, "firstPassWrongCount");
            RequireCount(result.FinalCorrectCount, 0, 10, "finalCorrectCount");
            Require(result.Items, "items");
            Require(
                result.Items.Count >= 3 && result.Items.Count <= 10,
                "items must contain between 3 and 10 final results");
            Require(
                result.ItemCount == result.Items.Count,
                "itemCount must equal the number of item results");

            var itemIds = new HashSet<string>(StringComparer.Ordinal);
            var firstWrongCount = 0;
            var finalCorrectCount = 0;
            foreach (var item in result.Items)
            {
                Require(item, "item");
                RequireIdentifier(item.ItemId, "itemId");
                Require(itemIds.Add(item.ItemId), "itemId must be unique within final results");
                ValidateSelection(item.FirstSelection, "firstSelection");
                ValidateSelection(item.FinalSelection, "finalSelection");
                RequireIdentifier(item.CorrectOptionId, "correctOptionId");
                RequireText(item.CorrectAnswer, 1, 256, "correctAnswer");
                Require(item.TrustedSteps, "trustedSteps");
                Require(
                    item.TrustedSteps.Count >= 1 && item.TrustedSteps.Count <= 8,
                    "trustedSteps must contain between 1 and 8 steps");
                foreach (var step in item.TrustedSteps)
                    RequireText(step, 1, 512, "trustedStep");
                if (item.PossibleError != null)
                    RequireText(item.PossibleError, 1, 512, "possibleError");
                RequireText(item.ReliableMethod, 1, 512, "reliableMethod");

                var firstIsCorrect = item.FirstSelection.OptionId == item.CorrectOptionId;
                var finalIsCorrect = item.FinalSelection.OptionId == item.CorrectOptionId;
                Require(
                    item.FirstSelection.IsCorrect == firstIsCorrect,
                    "firstSelection.isCorrect must match correctOptionId");
                Require(
                    item.FinalSelection.IsCorrect == finalIsCorrect,
                    "finalSelection.isCorrect must match correctOptionId");
                Require(
                    item.SelfCorrected == (!firstIsCorrect && finalIsCorrect),
                    "selfCorrected must represent wrong-to-correct revision");

                if (!firstIsCorrect)
                    firstWrongCount++;
                if (finalIsCorrect)
                    finalCorrectCount++;
            }

            Require(
                result.FirstPassWrongCount == firstWrongCount,
                "firstPassWrongCount must match item results");
            Require(
                result.FinalCorrectCount == finalCorrectCount,
                "finalCorrectCount must match item results");
            Require(
                result.RevisionUsed == (result.FirstPassWrongCount > 0),
                "revisionUsed must equal firstPassWrongCount > 0");
            if (!result.RevisionUsed)
            {
                foreach (var item in result.Items)
                {
                    Require(
                        SelectionsEqual(item.FirstSelection, item.FinalSelection),
                        "finalSelection must equal firstSelection when revision is skipped");
                }
            }
        }

        public static void Validate(InitialSubmissionResult result)
        {
            Require(result, nameof(result));
            ValidateWrongCount(
                result.SchemaVersion,
                result.BatchId,
                result.ItemCount,
                result.WrongCount,
                result.RevisionRequired);
            Require(
                (result.FinalResult != null) == (result.WrongCount == 0),
                "finalResult must be present exactly when wrongCount is zero");
            if (result.FinalResult == null)
                return;

            Validate(result.FinalResult);
            Require(
                result.FinalResult.BatchId == result.BatchId &&
                result.FinalResult.ItemCount == result.ItemCount &&
                result.FinalResult.FirstPassWrongCount == 0 &&
                !result.FinalResult.RevisionUsed,
                "finalResult must match the zero-wrong first pass");
        }

        public static void Validate(QuizSnapshot snapshot)
        {
            Require(snapshot, nameof(snapshot));
            RequireVersion(snapshot.SchemaVersion);
            RequireIdentifier(snapshot.BatchId, "batchId");
            RequireCount(snapshot.StateVersion, 1, 5, "stateVersion");
            Validate(snapshot.PublicBatch);
            Require(
                snapshot.PublicBatch.BatchId == snapshot.BatchId,
                "publicBatch.batchId must match batchId");

            var optionLayouts = new Dictionary<string, HashSet<string>>(StringComparer.Ordinal);
            var optionDisplays =
                new Dictionary<string, Dictionary<string, string>>(StringComparer.Ordinal);
            foreach (var item in snapshot.PublicBatch.Items)
            {
                var optionIds = new HashSet<string>(StringComparer.Ordinal);
                var displays = new Dictionary<string, string>(StringComparer.Ordinal);
                foreach (var option in item.Options)
                {
                    optionIds.Add(option.OptionId);
                    displays.Add(option.OptionId, option.DisplayText);
                }
                optionLayouts.Add(item.ItemId, optionIds);
                optionDisplays.Add(item.ItemId, displays);
            }

            Dictionary<string, SubmissionSelection> initialByItem = null;
            if (snapshot.InitialSubmission != null)
            {
                Validate(snapshot.InitialSubmission);
                initialByItem = ValidateSnapshotSubmission(
                    snapshot.BatchId,
                    snapshot.PublicBatch.ItemCount,
                    optionLayouts,
                    snapshot.InitialSubmission.BatchId,
                    snapshot.InitialSubmission.ItemCount,
                    snapshot.InitialSubmission.Selections,
                    "initialSubmission");
            }

            Dictionary<string, SubmissionSelection> revisionByItem = null;
            if (snapshot.RevisionSubmission != null)
            {
                Validate(snapshot.RevisionSubmission);
                revisionByItem = ValidateSnapshotSubmission(
                    snapshot.BatchId,
                    snapshot.PublicBatch.ItemCount,
                    optionLayouts,
                    snapshot.RevisionSubmission.BatchId,
                    snapshot.RevisionSubmission.ItemCount,
                    snapshot.RevisionSubmission.Selections,
                    "revisionSubmission");
            }

            if (snapshot.InitialResult != null)
            {
                Validate(snapshot.InitialResult);
                Require(
                    snapshot.InitialResult.BatchId == snapshot.BatchId &&
                    snapshot.InitialResult.ItemCount == snapshot.PublicBatch.ItemCount,
                    "initialResult must match the public batch identity");
            }

            if (snapshot.FinalResult != null)
            {
                Validate(snapshot.FinalResult);
                Require(
                    snapshot.FinalResult.BatchId == snapshot.BatchId &&
                    snapshot.FinalResult.ItemCount == snapshot.PublicBatch.ItemCount,
                    "finalResult must match the public batch identity");
                ValidateSnapshotFinalResult(
                    snapshot.FinalResult,
                    optionLayouts,
                    optionDisplays,
                    initialByItem,
                    revisionByItem ?? initialByItem);
            }

            switch (snapshot.QuizState)
            {
                case QuizSnapshotState.Ready:
                    RequireSnapshotPresence(snapshot, false, false, false, false, 1);
                    return;
                case QuizSnapshotState.InitialLocked:
                    RequireSnapshotPresence(snapshot, true, false, false, false, 2);
                    return;
                case QuizSnapshotState.RevisionOpen:
                    RequireSnapshotPresence(snapshot, true, true, false, false, 3);
                    Require(
                        snapshot.InitialResult.WrongCount > 0 &&
                        snapshot.InitialResult.RevisionRequired,
                        "revision_open requires a nonzero initialResult");
                    return;
                case QuizSnapshotState.Revealed:
                case QuizSnapshotState.Closed:
                    ValidateRevealedSnapshot(snapshot);
                    return;
                default:
                    throw new JsonSerializationException("quizState is invalid");
            }
        }

        public static void Validate(BossGateResult result)
        {
            Require(result, nameof(result));
            RequireVersion(result.SchemaVersion);
            RequireIdentifier(result.WorldId, "worldId");
            RequireCount(result.LeadInWins, 0, 4, "leadInWins");
            Require(result.RequiredLeadInWins == 4, "requiredLeadInWins must be 4");
            RequireCount(result.ValidWorldItems, 0, 10000, "validWorldItems");
            Require(result.RequiredValidWorldItems == 16, "requiredValidWorldItems must be 16");
            RequireCount(result.LatestTenItemCount, 0, 10, "latestTenItemCount");
            RequireCount(result.LatestTenCorrectCount, 0, 10, "latestTenCorrectCount");
            Require(
                result.RequiredLatestTenCorrectCount == 7,
                "requiredLatestTenCorrectCount must be 7");
            RequireCount(result.CoreSubskillCount, 1, 32, "coreSubskillCount");
            RequireCount(result.ReadyCoreSubskillCount, 0, 32, "readyCoreSubskillCount");
            Require(
                result.LatestTenCorrectCount <= result.LatestTenItemCount,
                "latestTenCorrectCount cannot exceed latestTenItemCount");
            Require(
                result.ReadyCoreSubskillCount <= result.CoreSubskillCount,
                "readyCoreSubskillCount cannot exceed coreSubskillCount");
            Require(result.UnmetRequirements, "unmetRequirements");
            Require(result.UnmetRequirements.Count <= 4, "unmetRequirements has too many values");

            var expected = new List<string>(4);
            if (result.LeadInWins < result.RequiredLeadInWins)
                expected.Add("lead_in_wins");
            if (result.ValidWorldItems < result.RequiredValidWorldItems)
                expected.Add("valid_world_items");
            if (result.LatestTenItemCount < 10 ||
                result.LatestTenCorrectCount < result.RequiredLatestTenCorrectCount)
                expected.Add("latest_ten_accuracy");
            if (result.ReadyCoreSubskillCount < result.CoreSubskillCount)
                expected.Add("core_subskill_coverage");

            Require(
                result.Unlocked == (expected.Count == 0),
                "unlocked must match the deterministic boss gate");
            Require(
                ListsEqual(result.UnmetRequirements, expected),
                "unmetRequirements must list each failed gate in canonical order");
        }

        public static void Validate(SealTrialPrepare request)
        {
            Require(request, nameof(request));
            RequireVersion(request.SchemaVersion);
            RequireIdentifier(request.RequestId, "requestId");
            RequireIdentifier(request.SessionId, "sessionId");
        }

        public static void Validate(SealTrialPrepared prepared)
        {
            Require(prepared, nameof(prepared));
            RequireVersion(prepared.SchemaVersion);
            RequireIdentifier(prepared.RequestId, "requestId");
            RequireIdentifier(prepared.WorldId, "worldId");
            Require(prepared.AttemptNumber >= 1, "attemptNumber must be positive");
            RequireIdentifier(prepared.BattleId, "battleId");
            Require(
                prepared.BattleId ==
                $"{prepared.WorldId}_seal_trial_{prepared.AttemptNumber}",
                "battleId must match the Seal Trial world and attempt");
            Validate(prepared.Batch);
            Require(
                prepared.Batch.ItemCount == 3,
                "a Seal Trial must contain exactly three items");
        }

        public static void Validate(AssistedRoutePrepare request)
        {
            Require(request, nameof(request));
            RequireVersion(request.SchemaVersion);
            RequireIdentifier(request.RequestId, "requestId");
            RequireIdentifier(request.SessionId, "sessionId");
        }

        public static void Validate(AssistedRoutePrepared prepared)
        {
            Require(prepared, nameof(prepared));
            RequireVersion(prepared.SchemaVersion);
            RequireIdentifier(prepared.RequestId, "requestId");
            RequireIdentifier(prepared.WorldId, "worldId");
            Require(prepared.Batch, "batch");
            RequireIdentifier(prepared.Batch.RouteId, "routeId");
            RequireIdentifier(prepared.Batch.WorldId, "batch.worldId");
            Require(
                prepared.Batch.WorldId == prepared.WorldId,
                "batch.worldId must match worldId");
            ValidateAssistedWorkedExample(prepared.Batch.WorkedExample);
            Require(prepared.Batch.Items, "items");
            Require(
                prepared.Batch.Items.Count == 2,
                "assisted route requires exactly two supported items");

            var itemIds = new HashSet<string>(StringComparer.Ordinal)
            {
                prepared.Batch.WorkedExample.ItemId
            };
            foreach (var item in prepared.Batch.Items)
            {
                ValidateAssistedSupportedItem(item);
                Require(
                    itemIds.Add(item.ItemId),
                    "worked and supported itemIds must be distinct");
            }
        }

        public static void Validate(AssistedRouteComplete request)
        {
            Require(request, nameof(request));
            RequireVersion(request.SchemaVersion);
            RequireIdentifier(request.RequestId, "requestId");
            RequireIdentifier(request.SessionId, "sessionId");
            Require(request.Selections, "selections");
            Require(
                request.Selections.Count == 2,
                "assisted route completion requires exactly two selections");
            var itemIds = new HashSet<string>(StringComparer.Ordinal);
            foreach (var selection in request.Selections)
            {
                ValidateAssistedSelection(selection);
                Require(
                    itemIds.Add(selection.ItemId),
                    "assisted selections must target distinct items");
            }
        }

        public static void Validate(AssistedRouteCompleted result)
        {
            Require(result, nameof(result));
            RequireVersion(result.SchemaVersion);
            RequireIdentifier(result.RequestId, "requestId");
            RequireIdentifier(result.WorldId, "worldId");
            RequireIdentifier(result.RouteId, "routeId");
            Require(result.WorkedExampleCount == 1, "workedExampleCount must be 1");
            Require(result.SupportedMcqCount == 2, "supportedMcqCount must be 2");
            RequireCount(result.FinalCorrect, 0, 2, "finalCorrect");
            Require(result.WorldCleared, "worldCleared must be true");
            Require(result.Items, "items");
            Require(
                result.Items.Count == 2,
                "assisted completion requires exactly two item results");

            var itemIds = new HashSet<string>(StringComparer.Ordinal);
            var correctCount = 0;
            foreach (var item in result.Items)
            {
                ValidateAssistedItemResult(item);
                Require(
                    itemIds.Add(item.ItemId),
                    "assisted result itemIds must be distinct");
                if (item.IsCorrect)
                    correctCount++;
            }
            Require(
                result.FinalCorrect == correctCount,
                "finalCorrect must match assisted item results");
        }

        public static void Validate(BattleComplete request)
        {
            Require(request, nameof(request));
            ValidateProgressionCommand(
                request.SchemaVersion,
                request.RequestId,
                request.SessionId);
        }

        public static void Validate(BattleCompleted result)
        {
            Require(result, nameof(result));
            RequireVersion(result.SchemaVersion);
            RequireIdentifier(result.RequestId, "requestId");
            RequireIdentifier(result.WorldId, "worldId");
            RequireIdentifier(result.BattleId, "battleId");
            RequireIdentifier(result.BatchId, "batchId");
            RequireCount(result.FinalCorrect, 0, 10, "finalCorrect");
            RequireCount(result.ItemCount, 3, 10, "itemCount");
            Require(
                result.FinalCorrect <= result.ItemCount,
                "finalCorrect cannot exceed itemCount");
            Require(
                !result.WorldCleared && !result.SealTrialRequired || result.BossBattle,
                "only a boss battle can resolve world clearance");
            Require(
                !result.WorldCleared || !result.SealTrialRequired,
                "a cleared world cannot require a Seal Trial");
        }

        public static void Validate(SealTrialComplete request)
        {
            Require(request, nameof(request));
            ValidateProgressionCommand(
                request.SchemaVersion,
                request.RequestId,
                request.SessionId);
        }

        public static void Validate(SealTrialCompleted result)
        {
            Require(result, nameof(result));
            RequireVersion(result.SchemaVersion);
            RequireIdentifier(result.RequestId, "requestId");
            RequireIdentifier(result.WorldId, "worldId");
            Require(result.AttemptNumber >= 1, "attemptNumber must be positive");
            RequireIdentifier(result.BatchId, "batchId");
            RequireCount(result.FinalCorrect, 0, 3, "finalCorrect");
            Require(result.ItemCount == 3, "itemCount must be 3");
            Require(
                result.Passed == (result.FinalCorrect >= 2),
                "passed must equal finalCorrect >= 2");
            Require(
                result.WorldCleared == result.Passed,
                "worldCleared must equal passed");
            Require(
                !result.AssistedRouteUnlocked || !result.Passed,
                "a passed Seal Trial cannot unlock the assisted route");
        }

        public static void Validate(SecondWindStart request)
        {
            Require(request, nameof(request));
            ValidateProgressionCommand(
                request.SchemaVersion,
                request.RequestId,
                request.SessionId);
            RequireIdentifier(request.PreparationRequestId, "preparationRequestId");
        }

        public static void Validate(SecondWindStarted result)
        {
            Require(result, nameof(result));
            RequireVersion(result.SchemaVersion);
            RequireIdentifier(result.RequestId, "requestId");
            RequireIdentifier(result.SecondWindId, "secondWindId");
            RequireIdentifier(result.WorldId, "worldId");
            RequireIdentifier(result.BattleId, "battleId");
            RequireIdentifier(result.CombatAttemptId, "combatAttemptId");
            RequireIdentifier(result.QuizBattleId, "quizBattleId");
            Require(
                result.SecondWindId == "second-wind-" + result.CombatAttemptId &&
                result.QuizBattleId == result.BattleId + "_second_wind",
                "Second Wind identities must be derived from combat");
            Validate(result.Batch);
            Require(
                result.Batch.ItemCount == 3,
                "Second Wind quizzes must contain exactly three items");
        }

        public static void Validate(SecondWindComplete request)
        {
            Require(request, nameof(request));
            ValidateProgressionCommand(
                request.SchemaVersion,
                request.RequestId,
                request.SessionId);
        }

        public static void Validate(SecondWindCompleted result)
        {
            Require(result, nameof(result));
            RequireVersion(result.SchemaVersion);
            RequireIdentifier(result.RequestId, "requestId");
            RequireIdentifier(result.SecondWindId, "secondWindId");
            RequireIdentifier(result.BatchId, "batchId");
            RequireCount(result.FinalCorrect, 0, 3, "finalCorrect");
            Require(result.ItemCount == 3, "itemCount must be 3");
            Require(result.ReviveHealthPercent == 35, "reviveHealthPercent must be 35");
            RequireCount(result.ShieldPercent, 0, 15, "shieldPercent");
            Require(
                result.ShieldPercent == Math.Min(result.FinalCorrect * 5, 15),
                "shieldPercent must match finalCorrect");
            Require(result.RevivedCombatPending, "revivedCombatPending must be true");
        }

        public static void Validate(RevivedCombatComplete request)
        {
            Require(request, nameof(request));
            ValidateProgressionCommand(
                request.SchemaVersion,
                request.RequestId,
                request.SessionId);
        }

        public static void Validate(RevivedCombatCompleted result)
        {
            Require(result, nameof(result));
            RequireVersion(result.SchemaVersion);
            RequireIdentifier(result.RequestId, "requestId");
            RequireIdentifier(result.SecondWindId, "secondWindId");
            RequireIdentifier(result.CombatAttemptId, "combatAttemptId");
            Require(
                result.BattleCompleted == result.CombatWon,
                "battleCompleted must equal combatWon");
            Require(result.SecondWindClosed, "secondWindClosed must be true");
        }

        public static void Validate(WorldActivate request)
        {
            Require(request, nameof(request));
            ValidateProgressionCommand(
                request.SchemaVersion,
                request.RequestId,
                request.SessionId);
        }

        public static void Validate(WorldActivated result)
        {
            Require(result, nameof(result));
            RequireVersion(result.SchemaVersion);
            RequireIdentifier(result.RequestId, "requestId");
            RequireIdentifier(result.CompletedWorldId, "completedWorldId");
            RequireIdentifier(result.ActiveWorldId, "activeWorldId");
            RequireCount(result.CampaignSequence, 2, 9, "campaignSequence");
            Require(
                result.ActiveWorldId != result.CompletedWorldId,
                "activeWorldId must differ from completedWorldId");
        }

        private static void ValidateObject<T>(T value)
        {
            if (value is PublicQuizBatch batch)
            {
                Validate(batch);
                return;
            }

            if (value is BattleQuizRequest battleQuizRequest)
            {
                Validate(battleQuizRequest);
                return;
            }

            if (value is InitialSubmission submission)
            {
                Validate(submission);
                return;
            }

            if (value is RevisionSubmission revision)
            {
                Validate(revision);
                return;
            }

            if (value is WrongCountResult wrongCount)
            {
                Validate(wrongCount);
                return;
            }

            if (value is FinalQuizResult finalResult)
            {
                Validate(finalResult);
                return;
            }

            if (value is InitialSubmissionResult initialResult)
            {
                Validate(initialResult);
                return;
            }

            if (value is QuizSnapshot snapshot)
            {
                Validate(snapshot);
                return;
            }

            if (value is BossGateResult bossGate)
            {
                Validate(bossGate);
                return;
            }

            if (value is SealTrialPrepare sealTrialRequest)
            {
                Validate(sealTrialRequest);
                return;
            }

            if (value is SealTrialPrepared sealTrialPrepared)
            {
                Validate(sealTrialPrepared);
                return;
            }

            if (value is AssistedRoutePrepare assistedRoutePrepare)
            {
                Validate(assistedRoutePrepare);
                return;
            }

            if (value is AssistedRoutePrepared assistedRoutePrepared)
            {
                Validate(assistedRoutePrepared);
                return;
            }

            if (value is AssistedRouteComplete assistedRouteComplete)
            {
                Validate(assistedRouteComplete);
                return;
            }

            if (value is AssistedRouteCompleted assistedRouteCompleted)
            {
                Validate(assistedRouteCompleted);
                return;
            }

            if (value is BattleComplete battleComplete)
            {
                Validate(battleComplete);
                return;
            }

            if (value is BattleCompleted battleCompleted)
            {
                Validate(battleCompleted);
                return;
            }

            if (value is SealTrialComplete sealTrialComplete)
            {
                Validate(sealTrialComplete);
                return;
            }

            if (value is SealTrialCompleted sealTrialCompleted)
            {
                Validate(sealTrialCompleted);
                return;
            }

            if (value is SecondWindStart secondWindStart)
            {
                Validate(secondWindStart);
                return;
            }

            if (value is SecondWindStarted secondWindStarted)
            {
                Validate(secondWindStarted);
                return;
            }

            if (value is SecondWindComplete secondWindComplete)
            {
                Validate(secondWindComplete);
                return;
            }

            if (value is SecondWindCompleted secondWindCompleted)
            {
                Validate(secondWindCompleted);
                return;
            }

            if (value is RevivedCombatComplete revivedCombatComplete)
            {
                Validate(revivedCombatComplete);
                return;
            }

            if (value is RevivedCombatCompleted revivedCombatCompleted)
            {
                Validate(revivedCombatCompleted);
                return;
            }

            if (value is WorldActivate worldActivate)
            {
                Validate(worldActivate);
                return;
            }

            if (value is WorldActivated worldActivated)
            {
                Validate(worldActivated);
                return;
            }

            throw new JsonSerializationException(
                $"No strict invariant validator exists for {typeof(T).FullName}");
        }

        private static void ValidateProgressionCommand(
            string schemaVersion,
            string requestId,
            string sessionId)
        {
            RequireVersion(schemaVersion);
            RequireIdentifier(requestId, "requestId");
            RequireIdentifier(sessionId, "sessionId");
        }

        private static void ValidateAssistedWorkedExample(AssistedWorkedExample example)
        {
            Require(example, "workedExample");
            RequireIdentifier(example.ItemId, "workedExample.itemId");
            RequireText(example.Prompt, 1, 1000, "workedExample.prompt");
            RequireText(example.CorrectAnswer, 1, 256, "workedExample.correctAnswer");
            Require(example.TrustedSteps, "workedExample.trustedSteps");
            Require(
                example.TrustedSteps.Count >= 1 && example.TrustedSteps.Count <= 8,
                "workedExample.trustedSteps must contain between 1 and 8 steps");
            foreach (var step in example.TrustedSteps)
                RequireText(step, 1, 512, "workedExample.trustedStep");
            RequireText(example.ReliableMethod, 1, 512, "workedExample.reliableMethod");
        }

        private static void ValidateAssistedSupportedItem(AssistedSupportedItem item)
        {
            Require(item, "supported item");
            RequireIdentifier(item.ItemId, "supported itemId");
            RequireText(item.Prompt, 1, 1000, "supported prompt");
            Require(item.Options, "supported options");
            Require(item.Options.Count == 4, "supported item requires exactly four options");
            var optionIds = new HashSet<string>(StringComparer.Ordinal);
            var displays = new HashSet<string>(StringComparer.Ordinal);
            foreach (var option in item.Options)
            {
                Require(option, "supported option");
                RequireIdentifier(option.OptionId, "supported optionId");
                Require(
                    optionIds.Add(option.OptionId),
                    "supported optionId must be unique");
                RequireText(option.DisplayText, 1, 256, "supported displayText");
                Require(
                    displays.Add(NormalizeDisplay(option.DisplayText)),
                    "supported displayText must be unique");
            }
        }

        private static void ValidateAssistedSelection(AssistedSelection selection)
        {
            Require(selection, "assisted selection");
            RequireIdentifier(selection.ItemId, "assisted selection.itemId");
            RequireIdentifier(selection.OptionId, "assisted selection.optionId");
            Require(
                Enum.IsDefined(typeof(Confidence), selection.Confidence),
                "assisted selection.confidence is invalid");
        }

        private static void ValidateAssistedItemResult(AssistedItemResult item)
        {
            Require(item, "assisted item result");
            RequireIdentifier(item.ItemId, "assisted result.itemId");
            RequireIdentifier(item.SelectedOptionId, "assisted result.selectedOptionId");
            RequireText(item.SelectedAnswer, 1, 256, "assisted result.selectedAnswer");
            Require(
                Enum.IsDefined(typeof(Confidence), item.Confidence),
                "assisted result.confidence is invalid");
            RequireIdentifier(item.CorrectOptionId, "assisted result.correctOptionId");
            RequireText(item.CorrectAnswer, 1, 256, "assisted result.correctAnswer");
            var expectedCorrect = item.SelectedOptionId == item.CorrectOptionId;
            Require(
                item.IsCorrect == expectedCorrect,
                "assisted isCorrect must match correctOptionId");
            if (expectedCorrect)
            {
                Require(
                    item.PossibleError == null,
                    "a correct assisted item cannot have possibleError");
            }
            else
            {
                RequireText(item.PossibleError, 1, 512, "assisted result.possibleError");
            }
            RequireText(item.ReliableMethod, 1, 512, "assisted result.reliableMethod");
            Require(item.TrustedSteps, "assisted result.trustedSteps");
            Require(
                item.TrustedSteps.Count >= 1 && item.TrustedSteps.Count <= 8,
                "assisted trustedSteps must contain between 1 and 8 steps");
            foreach (var step in item.TrustedSteps)
                RequireText(step, 1, 512, "assisted result.trustedStep");
            Require(item.CanonicalFeedback, "assisted result.canonicalFeedback");
            var expectedFeedback = new List<string>(item.TrustedSteps.Count + 2);
            if (item.PossibleError != null)
                expectedFeedback.Add(item.PossibleError);
            expectedFeedback.Add(item.ReliableMethod);
            expectedFeedback.AddRange(item.TrustedSteps);
            Require(
                ListsEqual(item.CanonicalFeedback, expectedFeedback),
                "assisted canonicalFeedback must match error, method, and steps");
        }

        private static Dictionary<string, SubmissionSelection> ValidateSnapshotSubmission(
            string batchId,
            int itemCount,
            IReadOnlyDictionary<string, HashSet<string>> optionLayouts,
            string submissionBatchId,
            int submissionItemCount,
            IReadOnlyList<SubmissionSelection> selections,
            string name)
        {
            Require(
                submissionBatchId == batchId && submissionItemCount == itemCount,
                name + " must match the public batch identity");
            var byItem = new Dictionary<string, SubmissionSelection>(StringComparer.Ordinal);
            foreach (var selection in selections)
            {
                Require(
                    optionLayouts.TryGetValue(selection.ItemId, out var options) &&
                    options.Contains(selection.OptionId),
                    name + " selects an unknown public option");
                byItem.Add(selection.ItemId, selection);
            }
            Require(
                byItem.Count == optionLayouts.Count,
                name + " must select every public item");
            return byItem;
        }

        private static void ValidateSnapshotFinalResult(
            FinalQuizResult result,
            IReadOnlyDictionary<string, HashSet<string>> optionLayouts,
            IReadOnlyDictionary<string, Dictionary<string, string>> optionDisplays,
            IReadOnlyDictionary<string, SubmissionSelection> initialByItem,
            IReadOnlyDictionary<string, SubmissionSelection> finalByItem)
        {
            Require(initialByItem, "initialSubmission");
            Require(finalByItem, "final submission");
            Require(
                result.Items.Count == optionLayouts.Count,
                "finalResult must reveal every public item");
            foreach (var item in result.Items)
            {
                Require(
                    optionLayouts.TryGetValue(item.ItemId, out var options),
                    "finalResult must reveal every public item");
                Require(
                    options.Contains(item.CorrectOptionId) &&
                    options.Contains(item.FirstSelection.OptionId) &&
                    options.Contains(item.FinalSelection.OptionId),
                    "finalResult contains an unknown public option");
                Require(
                    optionDisplays.TryGetValue(item.ItemId, out var displays) &&
                    displays.TryGetValue(item.CorrectOptionId, out var correctDisplay) &&
                    string.Equals(
                        correctDisplay,
                        item.CorrectAnswer,
                        StringComparison.Ordinal),
                    "finalResult correctAnswer must match its public option");
                Require(
                    initialByItem.TryGetValue(item.ItemId, out var initial) &&
                    item.FirstSelection.OptionId == initial.OptionId &&
                    item.FirstSelection.Confidence == initial.Confidence,
                    "finalResult firstSelection must match initialSubmission");
                Require(
                    finalByItem.TryGetValue(item.ItemId, out var final) &&
                    item.FinalSelection.OptionId == final.OptionId &&
                    item.FinalSelection.Confidence == final.Confidence,
                    "finalResult finalSelection must match the final submission");
            }
        }

        private static void RequireSnapshotPresence(
            QuizSnapshot snapshot,
            bool initialSubmission,
            bool initialResult,
            bool revisionSubmission,
            bool finalResult,
            int stateVersion)
        {
            Require(
                (snapshot.InitialSubmission != null) == initialSubmission &&
                (snapshot.InitialResult != null) == initialResult &&
                (snapshot.RevisionSubmission != null) == revisionSubmission &&
                (snapshot.FinalResult != null) == finalResult,
                snapshot.QuizState + " has an invalid public state shape");
            Require(
                snapshot.StateVersion == stateVersion,
                "stateVersion does not match the public state shape");
        }

        private static void ValidateRevealedSnapshot(QuizSnapshot snapshot)
        {
            Require(
                snapshot.InitialSubmission != null &&
                snapshot.InitialResult != null &&
                snapshot.FinalResult != null,
                "revealed and closed states require initial and final records");
            var revisionUsed = snapshot.InitialResult.RevisionRequired;
            var expectedVersion = snapshot.QuizState == QuizSnapshotState.Revealed
                ? (revisionUsed ? 4 : 3)
                : (revisionUsed ? 5 : 4);
            RequireSnapshotPresence(
                snapshot,
                true,
                true,
                revisionUsed,
                true,
                expectedVersion);
            Require(
                snapshot.FinalResult.RevisionUsed == revisionUsed,
                "finalResult.revisionUsed must match initialResult");
            Require(
                snapshot.InitialResult.WrongCount == snapshot.FinalResult.FirstPassWrongCount,
                "initialResult.wrongCount must match finalResult");
            if (snapshot.InitialResult.FinalResult != null)
            {
                Require(
                    JToken.DeepEquals(
                        JToken.FromObject(snapshot.InitialResult.FinalResult),
                        JToken.FromObject(snapshot.FinalResult)),
                    "initialResult.finalResult must match finalResult");
            }
        }

        private static void ValidateSubmission(
            string schemaVersion,
            string requestId,
            string batchId,
            int itemCount,
            IReadOnlyList<SubmissionSelection> selections)
        {
            RequireVersion(schemaVersion);
            RequireIdentifier(requestId, "requestId");
            RequireIdentifier(batchId, "batchId");
            RequireCount(itemCount, 3, 10, "itemCount");
            Require(selections, "selections");
            Require(
                selections.Count >= 3 && selections.Count <= 10,
                "selections must contain between 3 and 10 answers");
            Require(
                itemCount == selections.Count,
                "itemCount must equal the number of selections");

            var itemIds = new HashSet<string>(StringComparer.Ordinal);
            foreach (var selection in selections)
            {
                Require(selection, "selection");
                RequireIdentifier(selection.ItemId, "itemId");
                RequireIdentifier(selection.OptionId, "optionId");
                Require(
                    itemIds.Add(selection.ItemId),
                    "each itemId must be selected exactly once");
            }
        }

        private static void ValidateWrongCount(
            string schemaVersion,
            string batchId,
            int itemCount,
            int wrongCount,
            bool revisionRequired)
        {
            RequireVersion(schemaVersion);
            RequireIdentifier(batchId, "batchId");
            RequireCount(itemCount, 3, 10, "itemCount");
            RequireCount(wrongCount, 0, 10, "wrongCount");
            Require(wrongCount <= itemCount, "wrongCount cannot exceed itemCount");
            Require(
                revisionRequired == (wrongCount > 0),
                "revisionRequired must equal wrongCount > 0");
        }

        private static void ValidateSelection(RevealedSelection selection, string name)
        {
            Require(selection, name);
            RequireIdentifier(selection.OptionId, name + ".optionId");
        }

        private static bool SelectionsEqual(RevealedSelection left, RevealedSelection right)
        {
            return left.OptionId == right.OptionId &&
                   left.Confidence == right.Confidence &&
                   left.IsCorrect == right.IsCorrect;
        }

        private static bool ListsEqual(
            IReadOnlyList<string> left,
            IReadOnlyList<string> right)
        {
            if (left.Count != right.Count)
                return false;
            for (var index = 0; index < left.Count; index++)
            {
                if (!string.Equals(left[index], right[index], StringComparison.Ordinal))
                    return false;
            }

            return true;
        }

        private static void ValidateTokenShape(
            Type targetType,
            JToken token,
            string path,
            bool allowNull)
        {
            if (token.Type == JTokenType.Null)
            {
                if (allowNull)
                    return;
                throw new JsonSerializationException(path + " may not be null");
            }

            if (targetType == typeof(string))
            {
                RequireToken(token, JTokenType.String, path);
                return;
            }

            if (targetType == typeof(int) || targetType == typeof(long))
            {
                RequireToken(token, JTokenType.Integer, path);
                return;
            }

            if (targetType == typeof(bool))
            {
                RequireToken(token, JTokenType.Boolean, path);
                return;
            }

            if (targetType.IsEnum)
            {
                RequireToken(token, JTokenType.String, path);
                return;
            }

            if (targetType.IsGenericType &&
                targetType.GetGenericTypeDefinition() == typeof(IReadOnlyList<>))
            {
                RequireToken(token, JTokenType.Array, path);
                var elementType = targetType.GetGenericArguments()[0];
                var index = 0;
                foreach (var child in (JArray)token)
                {
                    ValidateTokenShape(elementType, child, $"{path}[{index}]", false);
                    index++;
                }

                return;
            }

            RequireToken(token, JTokenType.Object, path);
            var jsonObject = (JObject)token;
            var members = new Dictionary<string, PropertyInfo>(StringComparer.Ordinal);
            foreach (var property in targetType.GetProperties(BindingFlags.Instance | BindingFlags.Public))
            {
                var attribute = property.GetCustomAttribute<JsonPropertyAttribute>();
                if (attribute != null)
                    members.Add(attribute.PropertyName, property);
            }

            foreach (var property in jsonObject.Properties())
            {
                if (!members.TryGetValue(property.Name, out var member))
                    throw new JsonSerializationException(path + "." + property.Name + " is unknown");
                var attribute = member.GetCustomAttribute<JsonPropertyAttribute>();
                ValidateTokenShape(
                    member.PropertyType,
                    property.Value,
                    path + "." + property.Name,
                    attribute.Required == Required.AllowNull);
            }

            foreach (var pair in members)
            {
                var attribute = pair.Value.GetCustomAttribute<JsonPropertyAttribute>();
                if ((attribute.Required == Required.Always ||
                     attribute.Required == Required.AllowNull) &&
                    jsonObject.Property(pair.Key, StringComparison.Ordinal) == null)
                {
                    throw new JsonSerializationException(path + "." + pair.Key + " is required");
                }
            }
        }

        private static void RequireToken(JToken token, JTokenType expected, string path)
        {
            if (token.Type != expected)
            {
                throw new JsonSerializationException(
                    $"{path} must be {expected}, not {token.Type}");
            }
        }

        private static string NormalizeDisplay(string value)
        {
            var normalized = value.Normalize(NormalizationForm.FormKC);
            var builder = new StringBuilder(normalized.Length);
            var pendingSpace = false;
            foreach (var character in normalized)
            {
                if (char.IsWhiteSpace(character))
                {
                    pendingSpace = builder.Length > 0;
                    continue;
                }

                if (pendingSpace)
                {
                    builder.Append(' ');
                    pendingSpace = false;
                }

                if (character == '\u00df' || character == '\u1e9e')
                    builder.Append("ss");
                else
                    builder.Append(char.ToLower(character, CultureInfo.InvariantCulture));
            }

            return builder.ToString();
        }

        private static void RequireVersion(string value)
        {
            Require(value == ContractVersion, $"schemaVersion must be {ContractVersion}");
        }

        private static void RequireIdentifier(string value, string name)
        {
            Require(value != null && IdentifierPattern.IsMatch(value), $"{name} is invalid");
        }

        private static void RequireText(string value, int minimum, int maximum, string name)
        {
            Require(
                value != null && value.Length >= minimum && value.Length <= maximum,
                $"{name} length is invalid");
        }

        private static void RequireCount(int value, int minimum, int maximum, string name)
        {
            Require(value >= minimum && value <= maximum, $"{name} is out of range");
        }

        private static void Require(object value, string name)
        {
            Require(value != null, $"{name} is required");
        }

        private static void Require(bool condition, string message)
        {
            if (!condition)
                throw new JsonSerializationException(message);
        }
    }
}
