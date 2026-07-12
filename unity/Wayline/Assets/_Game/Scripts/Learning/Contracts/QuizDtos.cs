using System;
using System.Collections.Generic;
using Newtonsoft.Json;

namespace Wayline.Learning.Contracts
{
    [JsonConverter(typeof(ConfidenceJsonConverter))]
    public enum Confidence
    {
        Certain,
        Leaning,
        Guessing
    }

    [JsonConverter(typeof(BattleTierJsonConverter))]
    public enum BattleTier
    {
        Route1,
        Route2,
        Route3,
        Elite,
        WorldBoss,
        CampaignFinale,
        SealTrial
    }

    [JsonConverter(typeof(QuizSnapshotStateJsonConverter))]
    public enum QuizSnapshotState
    {
        Ready,
        InitialLocked,
        RevisionOpen,
        Revealed,
        Closed
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class BattleQuizRequest
    {
        [JsonConstructor]
        public BattleQuizRequest(
            string schemaVersion,
            string requestId,
            string sessionId,
            string battleId,
            string worldId,
            BattleTier battleTier)
        {
            SchemaVersion = schemaVersion;
            RequestId = requestId;
            SessionId = sessionId;
            BattleId = battleId;
            WorldId = worldId;
            BattleTier = battleTier;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("requestId", Required = Required.Always)]
        public string RequestId { get; }

        [JsonProperty("sessionId", Required = Required.Always)]
        public string SessionId { get; }

        [JsonProperty("battleId", Required = Required.Always)]
        public string BattleId { get; }

        [JsonProperty("worldId", Required = Required.Always)]
        public string WorldId { get; }

        [JsonProperty("battleTier", Required = Required.Always)]
        public BattleTier BattleTier { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class PublicQuizOption
    {
        [JsonConstructor]
        public PublicQuizOption(string optionId, string displayText)
        {
            OptionId = optionId;
            DisplayText = displayText;
        }

        [JsonProperty("optionId", Required = Required.Always)]
        public string OptionId { get; }

        [JsonProperty("displayText", Required = Required.Always)]
        public string DisplayText { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class PublicQuizItem
    {
        [JsonConstructor]
        public PublicQuizItem(
            string itemId,
            string prompt,
            IReadOnlyList<PublicQuizOption> options)
        {
            ItemId = itemId;
            Prompt = prompt;
            Options = options;
        }

        [JsonProperty("itemId", Required = Required.Always)]
        public string ItemId { get; }

        [JsonProperty("prompt", Required = Required.Always)]
        public string Prompt { get; }

        [JsonProperty("options", Required = Required.Always)]
        public IReadOnlyList<PublicQuizOption> Options { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class PublicQuizBatch
    {
        [JsonConstructor]
        public PublicQuizBatch(
            string schemaVersion,
            string batchId,
            int itemCount,
            IReadOnlyList<PublicQuizItem> items)
        {
            SchemaVersion = schemaVersion;
            BatchId = batchId;
            ItemCount = itemCount;
            Items = items;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("batchId", Required = Required.Always)]
        public string BatchId { get; }

        [JsonProperty("itemCount", Required = Required.Always)]
        public int ItemCount { get; }

        [JsonProperty("items", Required = Required.Always)]
        public IReadOnlyList<PublicQuizItem> Items { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class SubmissionSelection
    {
        [JsonConstructor]
        public SubmissionSelection(string itemId, string optionId, Confidence confidence)
        {
            ItemId = itemId;
            OptionId = optionId;
            Confidence = confidence;
        }

        [JsonProperty("itemId", Required = Required.Always)]
        public string ItemId { get; }

        [JsonProperty("optionId", Required = Required.Always)]
        public string OptionId { get; }

        [JsonProperty("confidence", Required = Required.Always)]
        public Confidence Confidence { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class InitialSubmission
    {
        [JsonConstructor]
        public InitialSubmission(
            string schemaVersion,
            string requestId,
            string batchId,
            int itemCount,
            IReadOnlyList<SubmissionSelection> selections)
        {
            SchemaVersion = schemaVersion;
            RequestId = requestId;
            BatchId = batchId;
            ItemCount = itemCount;
            Selections = selections;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("requestId", Required = Required.Always)]
        public string RequestId { get; }

        [JsonProperty("batchId", Required = Required.Always)]
        public string BatchId { get; }

        [JsonProperty("itemCount", Required = Required.Always)]
        public int ItemCount { get; }

        [JsonProperty("selections", Required = Required.Always)]
        public IReadOnlyList<SubmissionSelection> Selections { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class RevisionSubmission
    {
        [JsonConstructor]
        public RevisionSubmission(
            string schemaVersion,
            string requestId,
            string batchId,
            int itemCount,
            IReadOnlyList<SubmissionSelection> selections)
        {
            SchemaVersion = schemaVersion;
            RequestId = requestId;
            BatchId = batchId;
            ItemCount = itemCount;
            Selections = selections;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("requestId", Required = Required.Always)]
        public string RequestId { get; }

        [JsonProperty("batchId", Required = Required.Always)]
        public string BatchId { get; }

        [JsonProperty("itemCount", Required = Required.Always)]
        public int ItemCount { get; }

        [JsonProperty("selections", Required = Required.Always)]
        public IReadOnlyList<SubmissionSelection> Selections { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class WrongCountResult
    {
        [JsonConstructor]
        public WrongCountResult(
            string schemaVersion,
            string batchId,
            int itemCount,
            int wrongCount,
            bool revisionRequired)
        {
            SchemaVersion = schemaVersion;
            BatchId = batchId;
            ItemCount = itemCount;
            WrongCount = wrongCount;
            RevisionRequired = revisionRequired;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("batchId", Required = Required.Always)]
        public string BatchId { get; }

        [JsonProperty("itemCount", Required = Required.Always)]
        public int ItemCount { get; }

        [JsonProperty("wrongCount", Required = Required.Always)]
        public int WrongCount { get; }

        [JsonProperty("revisionRequired", Required = Required.Always)]
        public bool RevisionRequired { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class RevealedSelection
    {
        [JsonConstructor]
        public RevealedSelection(string optionId, Confidence confidence, bool isCorrect)
        {
            OptionId = optionId;
            Confidence = confidence;
            IsCorrect = isCorrect;
        }

        [JsonProperty("optionId", Required = Required.Always)]
        public string OptionId { get; }

        [JsonProperty("confidence", Required = Required.Always)]
        public Confidence Confidence { get; }

        [JsonProperty("isCorrect", Required = Required.Always)]
        public bool IsCorrect { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class FinalQuizItemResult
    {
        [JsonConstructor]
        public FinalQuizItemResult(
            string itemId,
            RevealedSelection firstSelection,
            RevealedSelection finalSelection,
            string correctOptionId,
            string correctAnswer,
            IReadOnlyList<string> trustedSteps,
            string possibleError,
            string reliableMethod,
            bool selfCorrected)
        {
            ItemId = itemId;
            FirstSelection = firstSelection;
            FinalSelection = finalSelection;
            CorrectOptionId = correctOptionId;
            CorrectAnswer = correctAnswer;
            TrustedSteps = trustedSteps;
            PossibleError = possibleError;
            ReliableMethod = reliableMethod;
            SelfCorrected = selfCorrected;
        }

        [JsonProperty("itemId", Required = Required.Always)]
        public string ItemId { get; }

        [JsonProperty("firstSelection", Required = Required.Always)]
        public RevealedSelection FirstSelection { get; }

        [JsonProperty("finalSelection", Required = Required.Always)]
        public RevealedSelection FinalSelection { get; }

        [JsonProperty("correctOptionId", Required = Required.Always)]
        public string CorrectOptionId { get; }

        [JsonProperty("correctAnswer", Required = Required.Always)]
        public string CorrectAnswer { get; }

        [JsonProperty("trustedSteps", Required = Required.Always)]
        public IReadOnlyList<string> TrustedSteps { get; }

        [JsonProperty("possibleError", Required = Required.AllowNull)]
        public string PossibleError { get; }

        [JsonProperty("reliableMethod", Required = Required.Always)]
        public string ReliableMethod { get; }

        [JsonProperty("selfCorrected", Required = Required.Always)]
        public bool SelfCorrected { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class FinalQuizResult
    {
        [JsonConstructor]
        public FinalQuizResult(
            string schemaVersion,
            string batchId,
            int itemCount,
            int firstPassWrongCount,
            int finalCorrectCount,
            bool revisionUsed,
            IReadOnlyList<FinalQuizItemResult> items)
        {
            SchemaVersion = schemaVersion;
            BatchId = batchId;
            ItemCount = itemCount;
            FirstPassWrongCount = firstPassWrongCount;
            FinalCorrectCount = finalCorrectCount;
            RevisionUsed = revisionUsed;
            Items = items;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("batchId", Required = Required.Always)]
        public string BatchId { get; }

        [JsonProperty("itemCount", Required = Required.Always)]
        public int ItemCount { get; }

        [JsonProperty("firstPassWrongCount", Required = Required.Always)]
        public int FirstPassWrongCount { get; }

        [JsonProperty("finalCorrectCount", Required = Required.Always)]
        public int FinalCorrectCount { get; }

        [JsonProperty("revisionUsed", Required = Required.Always)]
        public bool RevisionUsed { get; }

        [JsonProperty("items", Required = Required.Always)]
        public IReadOnlyList<FinalQuizItemResult> Items { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class InitialSubmissionResult
    {
        [JsonConstructor]
        public InitialSubmissionResult(
            string schemaVersion,
            string batchId,
            int itemCount,
            int wrongCount,
            bool revisionRequired,
            FinalQuizResult finalResult)
        {
            SchemaVersion = schemaVersion;
            BatchId = batchId;
            ItemCount = itemCount;
            WrongCount = wrongCount;
            RevisionRequired = revisionRequired;
            FinalResult = finalResult;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("batchId", Required = Required.Always)]
        public string BatchId { get; }

        [JsonProperty("itemCount", Required = Required.Always)]
        public int ItemCount { get; }

        [JsonProperty("wrongCount", Required = Required.Always)]
        public int WrongCount { get; }

        [JsonProperty("revisionRequired", Required = Required.Always)]
        public bool RevisionRequired { get; }

        [JsonProperty("finalResult", Required = Required.AllowNull)]
        public FinalQuizResult FinalResult { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class QuizSnapshot
    {
        [JsonConstructor]
        public QuizSnapshot(
            string schemaVersion,
            string batchId,
            QuizSnapshotState quizState,
            int stateVersion,
            PublicQuizBatch publicBatch,
            InitialSubmission initialSubmission,
            InitialSubmissionResult initialResult,
            RevisionSubmission revisionSubmission,
            FinalQuizResult finalResult)
        {
            SchemaVersion = schemaVersion;
            BatchId = batchId;
            QuizState = quizState;
            StateVersion = stateVersion;
            PublicBatch = publicBatch;
            InitialSubmission = initialSubmission;
            InitialResult = initialResult;
            RevisionSubmission = revisionSubmission;
            FinalResult = finalResult;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("batchId", Required = Required.Always)]
        public string BatchId { get; }

        [JsonProperty("quizState", Required = Required.Always)]
        public QuizSnapshotState QuizState { get; }

        [JsonProperty("stateVersion", Required = Required.Always)]
        public int StateVersion { get; }

        [JsonProperty("publicBatch", Required = Required.Always)]
        public PublicQuizBatch PublicBatch { get; }

        [JsonProperty("initialSubmission", Required = Required.AllowNull)]
        public InitialSubmission InitialSubmission { get; }

        [JsonProperty("initialResult", Required = Required.AllowNull)]
        public InitialSubmissionResult InitialResult { get; }

        [JsonProperty("revisionSubmission", Required = Required.AllowNull)]
        public RevisionSubmission RevisionSubmission { get; }

        [JsonProperty("finalResult", Required = Required.AllowNull)]
        public FinalQuizResult FinalResult { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class BossGateResult
    {
        [JsonConstructor]
        public BossGateResult(
            string schemaVersion,
            string worldId,
            bool unlocked,
            int leadInWins,
            int requiredLeadInWins,
            int validWorldItems,
            int requiredValidWorldItems,
            int latestTenItemCount,
            int latestTenCorrectCount,
            int requiredLatestTenCorrectCount,
            int coreSubskillCount,
            int readyCoreSubskillCount,
            IReadOnlyList<string> unmetRequirements)
        {
            SchemaVersion = schemaVersion;
            WorldId = worldId;
            Unlocked = unlocked;
            LeadInWins = leadInWins;
            RequiredLeadInWins = requiredLeadInWins;
            ValidWorldItems = validWorldItems;
            RequiredValidWorldItems = requiredValidWorldItems;
            LatestTenItemCount = latestTenItemCount;
            LatestTenCorrectCount = latestTenCorrectCount;
            RequiredLatestTenCorrectCount = requiredLatestTenCorrectCount;
            CoreSubskillCount = coreSubskillCount;
            ReadyCoreSubskillCount = readyCoreSubskillCount;
            UnmetRequirements = unmetRequirements;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("worldId", Required = Required.Always)]
        public string WorldId { get; }

        [JsonProperty("unlocked", Required = Required.Always)]
        public bool Unlocked { get; }

        [JsonProperty("leadInWins", Required = Required.Always)]
        public int LeadInWins { get; }

        [JsonProperty("requiredLeadInWins", Required = Required.Always)]
        public int RequiredLeadInWins { get; }

        [JsonProperty("validWorldItems", Required = Required.Always)]
        public int ValidWorldItems { get; }

        [JsonProperty("requiredValidWorldItems", Required = Required.Always)]
        public int RequiredValidWorldItems { get; }

        [JsonProperty("latestTenItemCount", Required = Required.Always)]
        public int LatestTenItemCount { get; }

        [JsonProperty("latestTenCorrectCount", Required = Required.Always)]
        public int LatestTenCorrectCount { get; }

        [JsonProperty("requiredLatestTenCorrectCount", Required = Required.Always)]
        public int RequiredLatestTenCorrectCount { get; }

        [JsonProperty("coreSubskillCount", Required = Required.Always)]
        public int CoreSubskillCount { get; }

        [JsonProperty("readyCoreSubskillCount", Required = Required.Always)]
        public int ReadyCoreSubskillCount { get; }

        [JsonProperty("unmetRequirements", Required = Required.Always)]
        public IReadOnlyList<string> UnmetRequirements { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class SealTrialPrepare
    {
        [JsonConstructor]
        public SealTrialPrepare(string schemaVersion, string requestId, string sessionId)
        {
            SchemaVersion = schemaVersion;
            RequestId = requestId;
            SessionId = sessionId;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("requestId", Required = Required.Always)]
        public string RequestId { get; }

        [JsonProperty("sessionId", Required = Required.Always)]
        public string SessionId { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class SealTrialPrepared
    {
        [JsonConstructor]
        public SealTrialPrepared(
            string schemaVersion,
            string requestId,
            string worldId,
            int attemptNumber,
            string battleId,
            PublicQuizBatch batch)
        {
            SchemaVersion = schemaVersion;
            RequestId = requestId;
            WorldId = worldId;
            AttemptNumber = attemptNumber;
            BattleId = battleId;
            Batch = batch;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("requestId", Required = Required.Always)]
        public string RequestId { get; }

        [JsonProperty("worldId", Required = Required.Always)]
        public string WorldId { get; }

        [JsonProperty("attemptNumber", Required = Required.Always)]
        public int AttemptNumber { get; }

        [JsonProperty("battleId", Required = Required.Always)]
        public string BattleId { get; }

        [JsonProperty("batch", Required = Required.Always)]
        public PublicQuizBatch Batch { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class AssistedRoutePrepare
    {
        [JsonConstructor]
        public AssistedRoutePrepare(string schemaVersion, string requestId, string sessionId)
        {
            SchemaVersion = schemaVersion;
            RequestId = requestId;
            SessionId = sessionId;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("requestId", Required = Required.Always)]
        public string RequestId { get; }

        [JsonProperty("sessionId", Required = Required.Always)]
        public string SessionId { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class AssistedWorkedExample
    {
        [JsonConstructor]
        public AssistedWorkedExample(
            string itemId,
            string prompt,
            string correctAnswer,
            IReadOnlyList<string> trustedSteps,
            string reliableMethod)
        {
            ItemId = itemId;
            Prompt = prompt;
            CorrectAnswer = correctAnswer;
            TrustedSteps = trustedSteps;
            ReliableMethod = reliableMethod;
        }

        [JsonProperty("itemId", Required = Required.Always)]
        public string ItemId { get; }

        [JsonProperty("prompt", Required = Required.Always)]
        public string Prompt { get; }

        [JsonProperty("correctAnswer", Required = Required.Always)]
        public string CorrectAnswer { get; }

        [JsonProperty("trustedSteps", Required = Required.Always)]
        public IReadOnlyList<string> TrustedSteps { get; }

        [JsonProperty("reliableMethod", Required = Required.Always)]
        public string ReliableMethod { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class AssistedSupportedItem
    {
        [JsonConstructor]
        public AssistedSupportedItem(
            string itemId,
            string prompt,
            IReadOnlyList<PublicQuizOption> options)
        {
            ItemId = itemId;
            Prompt = prompt;
            Options = options;
        }

        [JsonProperty("itemId", Required = Required.Always)]
        public string ItemId { get; }

        [JsonProperty("prompt", Required = Required.Always)]
        public string Prompt { get; }

        [JsonProperty("options", Required = Required.Always)]
        public IReadOnlyList<PublicQuizOption> Options { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class AssistedRouteBatch
    {
        [JsonConstructor]
        public AssistedRouteBatch(
            string routeId,
            string worldId,
            AssistedWorkedExample workedExample,
            IReadOnlyList<AssistedSupportedItem> items)
        {
            RouteId = routeId;
            WorldId = worldId;
            WorkedExample = workedExample;
            Items = items;
        }

        [JsonProperty("routeId", Required = Required.Always)]
        public string RouteId { get; }

        [JsonProperty("worldId", Required = Required.Always)]
        public string WorldId { get; }

        [JsonProperty("workedExample", Required = Required.Always)]
        public AssistedWorkedExample WorkedExample { get; }

        [JsonProperty("items", Required = Required.Always)]
        public IReadOnlyList<AssistedSupportedItem> Items { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class AssistedRoutePrepared
    {
        [JsonConstructor]
        public AssistedRoutePrepared(
            string schemaVersion,
            string requestId,
            string worldId,
            AssistedRouteBatch batch)
        {
            SchemaVersion = schemaVersion;
            RequestId = requestId;
            WorldId = worldId;
            Batch = batch;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("requestId", Required = Required.Always)]
        public string RequestId { get; }

        [JsonProperty("worldId", Required = Required.Always)]
        public string WorldId { get; }

        [JsonProperty("batch", Required = Required.Always)]
        public AssistedRouteBatch Batch { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class AssistedSelection
    {
        [JsonConstructor]
        public AssistedSelection(string itemId, string optionId, Confidence confidence)
        {
            ItemId = itemId;
            OptionId = optionId;
            Confidence = confidence;
        }

        [JsonProperty("itemId", Required = Required.Always)]
        public string ItemId { get; }

        [JsonProperty("optionId", Required = Required.Always)]
        public string OptionId { get; }

        [JsonProperty("confidence", Required = Required.Always)]
        public Confidence Confidence { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class AssistedRouteComplete
    {
        [JsonConstructor]
        public AssistedRouteComplete(
            string schemaVersion,
            string requestId,
            string sessionId,
            IReadOnlyList<AssistedSelection> selections)
        {
            SchemaVersion = schemaVersion;
            RequestId = requestId;
            SessionId = sessionId;
            Selections = selections;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("requestId", Required = Required.Always)]
        public string RequestId { get; }

        [JsonProperty("sessionId", Required = Required.Always)]
        public string SessionId { get; }

        [JsonProperty("selections", Required = Required.Always)]
        public IReadOnlyList<AssistedSelection> Selections { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class AssistedItemResult
    {
        [JsonConstructor]
        public AssistedItemResult(
            string itemId,
            string selectedOptionId,
            string selectedAnswer,
            Confidence confidence,
            string correctOptionId,
            string correctAnswer,
            bool isCorrect,
            string possibleError,
            string reliableMethod,
            IReadOnlyList<string> trustedSteps,
            IReadOnlyList<string> canonicalFeedback)
        {
            ItemId = itemId;
            SelectedOptionId = selectedOptionId;
            SelectedAnswer = selectedAnswer;
            Confidence = confidence;
            CorrectOptionId = correctOptionId;
            CorrectAnswer = correctAnswer;
            IsCorrect = isCorrect;
            PossibleError = possibleError;
            ReliableMethod = reliableMethod;
            TrustedSteps = trustedSteps;
            CanonicalFeedback = canonicalFeedback;
        }

        [JsonProperty("itemId", Required = Required.Always)]
        public string ItemId { get; }

        [JsonProperty("selectedOptionId", Required = Required.Always)]
        public string SelectedOptionId { get; }

        [JsonProperty("selectedAnswer", Required = Required.Always)]
        public string SelectedAnswer { get; }

        [JsonProperty("confidence", Required = Required.Always)]
        public Confidence Confidence { get; }

        [JsonProperty("correctOptionId", Required = Required.Always)]
        public string CorrectOptionId { get; }

        [JsonProperty("correctAnswer", Required = Required.Always)]
        public string CorrectAnswer { get; }

        [JsonProperty("isCorrect", Required = Required.Always)]
        public bool IsCorrect { get; }

        [JsonProperty("possibleError", Required = Required.AllowNull)]
        public string PossibleError { get; }

        [JsonProperty("reliableMethod", Required = Required.Always)]
        public string ReliableMethod { get; }

        [JsonProperty("trustedSteps", Required = Required.Always)]
        public IReadOnlyList<string> TrustedSteps { get; }

        [JsonProperty("canonicalFeedback", Required = Required.Always)]
        public IReadOnlyList<string> CanonicalFeedback { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class AssistedRouteCompleted
    {
        [JsonConstructor]
        public AssistedRouteCompleted(
            string schemaVersion,
            string requestId,
            string worldId,
            string routeId,
            int workedExampleCount,
            int supportedMcqCount,
            int finalCorrect,
            bool worldCleared,
            IReadOnlyList<AssistedItemResult> items)
        {
            SchemaVersion = schemaVersion;
            RequestId = requestId;
            WorldId = worldId;
            RouteId = routeId;
            WorkedExampleCount = workedExampleCount;
            SupportedMcqCount = supportedMcqCount;
            FinalCorrect = finalCorrect;
            WorldCleared = worldCleared;
            Items = items;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("requestId", Required = Required.Always)]
        public string RequestId { get; }

        [JsonProperty("worldId", Required = Required.Always)]
        public string WorldId { get; }

        [JsonProperty("routeId", Required = Required.Always)]
        public string RouteId { get; }

        [JsonProperty("workedExampleCount", Required = Required.Always)]
        public int WorkedExampleCount { get; }

        [JsonProperty("supportedMcqCount", Required = Required.Always)]
        public int SupportedMcqCount { get; }

        [JsonProperty("finalCorrect", Required = Required.Always)]
        public int FinalCorrect { get; }

        [JsonProperty("worldCleared", Required = Required.Always)]
        public bool WorldCleared { get; }

        [JsonProperty("items", Required = Required.Always)]
        public IReadOnlyList<AssistedItemResult> Items { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class BattleComplete
    {
        [JsonConstructor]
        public BattleComplete(
            string schemaVersion,
            string requestId,
            string sessionId,
            bool combatWon)
        {
            SchemaVersion = schemaVersion;
            RequestId = requestId;
            SessionId = sessionId;
            CombatWon = combatWon;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("requestId", Required = Required.Always)]
        public string RequestId { get; }

        [JsonProperty("sessionId", Required = Required.Always)]
        public string SessionId { get; }

        [JsonProperty("combatWon", Required = Required.Always)]
        public bool CombatWon { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class BattleCompleted
    {
        [JsonConstructor]
        public BattleCompleted(
            string schemaVersion,
            string requestId,
            string worldId,
            string battleId,
            string batchId,
            int finalCorrect,
            int itemCount,
            bool bossBattle,
            bool worldCleared,
            bool sealTrialRequired)
        {
            SchemaVersion = schemaVersion;
            RequestId = requestId;
            WorldId = worldId;
            BattleId = battleId;
            BatchId = batchId;
            FinalCorrect = finalCorrect;
            ItemCount = itemCount;
            BossBattle = bossBattle;
            WorldCleared = worldCleared;
            SealTrialRequired = sealTrialRequired;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("requestId", Required = Required.Always)]
        public string RequestId { get; }

        [JsonProperty("worldId", Required = Required.Always)]
        public string WorldId { get; }

        [JsonProperty("battleId", Required = Required.Always)]
        public string BattleId { get; }

        [JsonProperty("batchId", Required = Required.Always)]
        public string BatchId { get; }

        [JsonProperty("finalCorrect", Required = Required.Always)]
        public int FinalCorrect { get; }

        [JsonProperty("itemCount", Required = Required.Always)]
        public int ItemCount { get; }

        [JsonProperty("bossBattle", Required = Required.Always)]
        public bool BossBattle { get; }

        [JsonProperty("worldCleared", Required = Required.Always)]
        public bool WorldCleared { get; }

        [JsonProperty("sealTrialRequired", Required = Required.Always)]
        public bool SealTrialRequired { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class SealTrialComplete
    {
        [JsonConstructor]
        public SealTrialComplete(string schemaVersion, string requestId, string sessionId)
        {
            SchemaVersion = schemaVersion;
            RequestId = requestId;
            SessionId = sessionId;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("requestId", Required = Required.Always)]
        public string RequestId { get; }

        [JsonProperty("sessionId", Required = Required.Always)]
        public string SessionId { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class SealTrialCompleted
    {
        [JsonConstructor]
        public SealTrialCompleted(
            string schemaVersion,
            string requestId,
            string worldId,
            int attemptNumber,
            string batchId,
            int finalCorrect,
            int itemCount,
            bool passed,
            bool worldCleared,
            bool assistedRouteUnlocked)
        {
            SchemaVersion = schemaVersion;
            RequestId = requestId;
            WorldId = worldId;
            AttemptNumber = attemptNumber;
            BatchId = batchId;
            FinalCorrect = finalCorrect;
            ItemCount = itemCount;
            Passed = passed;
            WorldCleared = worldCleared;
            AssistedRouteUnlocked = assistedRouteUnlocked;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("requestId", Required = Required.Always)]
        public string RequestId { get; }

        [JsonProperty("worldId", Required = Required.Always)]
        public string WorldId { get; }

        [JsonProperty("attemptNumber", Required = Required.Always)]
        public int AttemptNumber { get; }

        [JsonProperty("batchId", Required = Required.Always)]
        public string BatchId { get; }

        [JsonProperty("finalCorrect", Required = Required.Always)]
        public int FinalCorrect { get; }

        [JsonProperty("itemCount", Required = Required.Always)]
        public int ItemCount { get; }

        [JsonProperty("passed", Required = Required.Always)]
        public bool Passed { get; }

        [JsonProperty("worldCleared", Required = Required.Always)]
        public bool WorldCleared { get; }

        [JsonProperty("assistedRouteUnlocked", Required = Required.Always)]
        public bool AssistedRouteUnlocked { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class SecondWindStart
    {
        [JsonConstructor]
        public SecondWindStart(
            string schemaVersion,
            string requestId,
            string sessionId,
            string preparationRequestId)
        {
            SchemaVersion = schemaVersion;
            RequestId = requestId;
            SessionId = sessionId;
            PreparationRequestId = preparationRequestId;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("requestId", Required = Required.Always)]
        public string RequestId { get; }

        [JsonProperty("sessionId", Required = Required.Always)]
        public string SessionId { get; }

        [JsonProperty("preparationRequestId", Required = Required.Always)]
        public string PreparationRequestId { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class SecondWindStarted
    {
        [JsonConstructor]
        public SecondWindStarted(
            string schemaVersion,
            string requestId,
            string secondWindId,
            string worldId,
            string battleId,
            string combatAttemptId,
            string quizBattleId,
            PublicQuizBatch batch)
        {
            SchemaVersion = schemaVersion;
            RequestId = requestId;
            SecondWindId = secondWindId;
            WorldId = worldId;
            BattleId = battleId;
            CombatAttemptId = combatAttemptId;
            QuizBattleId = quizBattleId;
            Batch = batch;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("requestId", Required = Required.Always)]
        public string RequestId { get; }

        [JsonProperty("secondWindId", Required = Required.Always)]
        public string SecondWindId { get; }

        [JsonProperty("worldId", Required = Required.Always)]
        public string WorldId { get; }

        [JsonProperty("battleId", Required = Required.Always)]
        public string BattleId { get; }

        [JsonProperty("combatAttemptId", Required = Required.Always)]
        public string CombatAttemptId { get; }

        [JsonProperty("quizBattleId", Required = Required.Always)]
        public string QuizBattleId { get; }

        [JsonProperty("batch", Required = Required.Always)]
        public PublicQuizBatch Batch { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class SecondWindComplete
    {
        [JsonConstructor]
        public SecondWindComplete(string schemaVersion, string requestId, string sessionId)
        {
            SchemaVersion = schemaVersion;
            RequestId = requestId;
            SessionId = sessionId;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("requestId", Required = Required.Always)]
        public string RequestId { get; }

        [JsonProperty("sessionId", Required = Required.Always)]
        public string SessionId { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class SecondWindCompleted
    {
        [JsonConstructor]
        public SecondWindCompleted(
            string schemaVersion,
            string requestId,
            string secondWindId,
            string batchId,
            int finalCorrect,
            int itemCount,
            int reviveHealthPercent,
            int shieldPercent,
            bool revivedCombatPending)
        {
            SchemaVersion = schemaVersion;
            RequestId = requestId;
            SecondWindId = secondWindId;
            BatchId = batchId;
            FinalCorrect = finalCorrect;
            ItemCount = itemCount;
            ReviveHealthPercent = reviveHealthPercent;
            ShieldPercent = shieldPercent;
            RevivedCombatPending = revivedCombatPending;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("requestId", Required = Required.Always)]
        public string RequestId { get; }

        [JsonProperty("secondWindId", Required = Required.Always)]
        public string SecondWindId { get; }

        [JsonProperty("batchId", Required = Required.Always)]
        public string BatchId { get; }

        [JsonProperty("finalCorrect", Required = Required.Always)]
        public int FinalCorrect { get; }

        [JsonProperty("itemCount", Required = Required.Always)]
        public int ItemCount { get; }

        [JsonProperty("reviveHealthPercent", Required = Required.Always)]
        public int ReviveHealthPercent { get; }

        [JsonProperty("shieldPercent", Required = Required.Always)]
        public int ShieldPercent { get; }

        [JsonProperty("revivedCombatPending", Required = Required.Always)]
        public bool RevivedCombatPending { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class RevivedCombatComplete
    {
        [JsonConstructor]
        public RevivedCombatComplete(
            string schemaVersion,
            string requestId,
            string sessionId,
            bool combatWon)
        {
            SchemaVersion = schemaVersion;
            RequestId = requestId;
            SessionId = sessionId;
            CombatWon = combatWon;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("requestId", Required = Required.Always)]
        public string RequestId { get; }

        [JsonProperty("sessionId", Required = Required.Always)]
        public string SessionId { get; }

        [JsonProperty("combatWon", Required = Required.Always)]
        public bool CombatWon { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class RevivedCombatCompleted
    {
        [JsonConstructor]
        public RevivedCombatCompleted(
            string schemaVersion,
            string requestId,
            string secondWindId,
            string combatAttemptId,
            bool combatWon,
            bool battleCompleted,
            bool secondWindClosed)
        {
            SchemaVersion = schemaVersion;
            RequestId = requestId;
            SecondWindId = secondWindId;
            CombatAttemptId = combatAttemptId;
            CombatWon = combatWon;
            BattleCompleted = battleCompleted;
            SecondWindClosed = secondWindClosed;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("requestId", Required = Required.Always)]
        public string RequestId { get; }

        [JsonProperty("secondWindId", Required = Required.Always)]
        public string SecondWindId { get; }

        [JsonProperty("combatAttemptId", Required = Required.Always)]
        public string CombatAttemptId { get; }

        [JsonProperty("combatWon", Required = Required.Always)]
        public bool CombatWon { get; }

        [JsonProperty("battleCompleted", Required = Required.Always)]
        public bool BattleCompleted { get; }

        [JsonProperty("secondWindClosed", Required = Required.Always)]
        public bool SecondWindClosed { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class WorldActivate
    {
        [JsonConstructor]
        public WorldActivate(string schemaVersion, string requestId, string sessionId)
        {
            SchemaVersion = schemaVersion;
            RequestId = requestId;
            SessionId = sessionId;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("requestId", Required = Required.Always)]
        public string RequestId { get; }

        [JsonProperty("sessionId", Required = Required.Always)]
        public string SessionId { get; }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class WorldActivated
    {
        [JsonConstructor]
        public WorldActivated(
            string schemaVersion,
            string requestId,
            string completedWorldId,
            string activeWorldId,
            int campaignSequence)
        {
            SchemaVersion = schemaVersion;
            RequestId = requestId;
            CompletedWorldId = completedWorldId;
            ActiveWorldId = activeWorldId;
            CampaignSequence = campaignSequence;
        }

        [JsonProperty("schemaVersion", Required = Required.Always)]
        public string SchemaVersion { get; }

        [JsonProperty("requestId", Required = Required.Always)]
        public string RequestId { get; }

        [JsonProperty("completedWorldId", Required = Required.Always)]
        public string CompletedWorldId { get; }

        [JsonProperty("activeWorldId", Required = Required.Always)]
        public string ActiveWorldId { get; }

        [JsonProperty("campaignSequence", Required = Required.Always)]
        public int CampaignSequence { get; }
    }

    internal sealed class ConfidenceJsonConverter : JsonConverter
    {
        public override bool CanConvert(Type objectType)
        {
            return objectType == typeof(Confidence);
        }

        public override object ReadJson(
            JsonReader reader,
            Type objectType,
            object existingValue,
            JsonSerializer serializer)
        {
            if (reader.TokenType != JsonToken.String)
                throw new JsonSerializationException("confidence must be a string");

            switch ((string)reader.Value)
            {
                case "certain":
                    return Confidence.Certain;
                case "leaning":
                    return Confidence.Leaning;
                case "guessing":
                    return Confidence.Guessing;
                default:
                    throw new JsonSerializationException("confidence is not recognized");
            }
        }

        public override void WriteJson(
            JsonWriter writer,
            object value,
            JsonSerializer serializer)
        {
            switch ((Confidence)value)
            {
                case Confidence.Certain:
                    writer.WriteValue("certain");
                    return;
                case Confidence.Leaning:
                    writer.WriteValue("leaning");
                    return;
                case Confidence.Guessing:
                    writer.WriteValue("guessing");
                    return;
                default:
                    throw new JsonSerializationException("confidence is not recognized");
            }
        }
    }

    internal sealed class BattleTierJsonConverter : JsonConverter
    {
        public override bool CanConvert(Type objectType)
        {
            return objectType == typeof(BattleTier);
        }

        public override object ReadJson(
            JsonReader reader,
            Type objectType,
            object existingValue,
            JsonSerializer serializer)
        {
            if (reader.TokenType != JsonToken.String)
                throw new JsonSerializationException("battleTier must be a string");

            switch ((string)reader.Value)
            {
                case "route_1": return BattleTier.Route1;
                case "route_2": return BattleTier.Route2;
                case "route_3": return BattleTier.Route3;
                case "elite": return BattleTier.Elite;
                case "world_boss": return BattleTier.WorldBoss;
                case "campaign_finale": return BattleTier.CampaignFinale;
                case "seal_trial": return BattleTier.SealTrial;
                default: throw new JsonSerializationException("battleTier is not recognized");
            }
        }

        public override void WriteJson(
            JsonWriter writer,
            object value,
            JsonSerializer serializer)
        {
            switch ((BattleTier)value)
            {
                case BattleTier.Route1: writer.WriteValue("route_1"); return;
                case BattleTier.Route2: writer.WriteValue("route_2"); return;
                case BattleTier.Route3: writer.WriteValue("route_3"); return;
                case BattleTier.Elite: writer.WriteValue("elite"); return;
                case BattleTier.WorldBoss: writer.WriteValue("world_boss"); return;
                case BattleTier.CampaignFinale: writer.WriteValue("campaign_finale"); return;
                case BattleTier.SealTrial: writer.WriteValue("seal_trial"); return;
                default: throw new JsonSerializationException("battleTier is not recognized");
            }
        }
    }

    internal sealed class QuizSnapshotStateJsonConverter : JsonConverter
    {
        public override bool CanConvert(Type objectType)
        {
            return objectType == typeof(QuizSnapshotState);
        }

        public override object ReadJson(
            JsonReader reader,
            Type objectType,
            object existingValue,
            JsonSerializer serializer)
        {
            if (reader.TokenType != JsonToken.String)
                throw new JsonSerializationException("quizState must be a string");

            switch ((string)reader.Value)
            {
                case "ready": return QuizSnapshotState.Ready;
                case "initial_locked": return QuizSnapshotState.InitialLocked;
                case "revision_open": return QuizSnapshotState.RevisionOpen;
                case "revealed": return QuizSnapshotState.Revealed;
                case "closed": return QuizSnapshotState.Closed;
                default: throw new JsonSerializationException("quizState is not recognized");
            }
        }

        public override void WriteJson(
            JsonWriter writer,
            object value,
            JsonSerializer serializer)
        {
            switch ((QuizSnapshotState)value)
            {
                case QuizSnapshotState.Ready: writer.WriteValue("ready"); return;
                case QuizSnapshotState.InitialLocked: writer.WriteValue("initial_locked"); return;
                case QuizSnapshotState.RevisionOpen: writer.WriteValue("revision_open"); return;
                case QuizSnapshotState.Revealed: writer.WriteValue("revealed"); return;
                case QuizSnapshotState.Closed: writer.WriteValue("closed"); return;
                default: throw new JsonSerializationException("quizState is not recognized");
            }
        }
    }
}
