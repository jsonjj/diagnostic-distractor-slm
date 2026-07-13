using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.UI;
using UnityEngine.UI;
using Wayline.Campaign;
using Wayline.Characters;
using Wayline.Combat.Simulation;
using Wayline.Flow.Authority;
using Wayline.Flow.Runtime;
using Wayline.Gameplay;
using Wayline.Learning.Assisted;
using Wayline.Learning.Client;
using Wayline.Learning.Contracts;
using Wayline.Learning.Quiz;
using Wayline.Save;
using Wayline.UI;
using Wayline.UI.Assisted;
using CampaignBattleTier = Wayline.Campaign.BattleTier;
using LearningBattleTier = Wayline.Learning.Contracts.BattleTier;

namespace Wayline.Flow.Unity
{
    [DisallowMultipleComponent]
    public sealed class VerticalSliceRuntimeBootstrap : MonoBehaviour,
        ICombatFlowPort,
        ITrialFlowPort,
        IRuntimeFlowPresentation
    {
        private const string SessionId = "acceptance-session-001";

        [SerializeField] private CombatWorldRunner runner;
        [SerializeField] private bool useDeterministicAcceptanceData;
        [SerializeField] private bool reducedMotion;
        [SerializeField, Range(1f, 1.5f)] private float textScale = 1f;

        private CancellationTokenSource _lifetime;
        private RuntimeCampaignFlowAdapter _campaignPort;
        private CampaignControllerMutations _campaignMutations;
        private RuntimeSessionPersistence _persistence;
        private IWaylineForgeClient _quizClient;
        private GameObject _shellRoot;
        private Text _shellTitle;
        private Text _shellBody;
        private Text _startBattleButtonLabel;
        private readonly Image[] _atlasNodes = new Image[3];
        private int _activeWorldNode;
        private WorldDefinition[] _demoWorlds;
        private int _worldIndex;

        private GameObject _openingRoot;
        private Text _openingCaption;
        private Text _openingSkipHint;
        private Image _openingSweep;
        private float _openingElapsed;
        private bool _openingActive;

        // Skippable, captioned in-engine opening. Locked narration from the
        // Higgsfield brief plus two establishing beats. Times are cumulative
        // seconds; the final beat holds until dismissed.
        private static readonly float[] OpeningBeatTimes = { 0f, 2.4f, 4.8f, 7.2f, 9.6f };
        private static readonly string[] OpeningBeatLines =
        {
            "A single line once connected every floating territory.",
            "When the Meridian broke, every territory sealed its route.",
            "To reach each seal, a Routekeeper must earn its champion's trust.",
            "Win the duel. Read the route. Repair the world.",
            "WAYLINE\nTHE BROKEN MERIDIAN"
        };
        private bool _combatResolved;
        private bool _trialCommitted;
        private AuthoritativeTrialCompletion _pendingAuthorityCompletion;
        private int _sealAttempt;
        private bool _ownsAcceptanceSave;
        private string _runtimeSessionPathOverride;

        public VerticalSliceFlowController Flow { get; private set; }

        public Text AcceptanceDataLabel { get; private set; }

        public Button EnterMapButton { get; private set; }

        public Button StartBattleButton { get; private set; }

        public Button RewardButton { get; private set; }

        public CombatWorldRunner Runner => runner;

        public AtlasTrialPanel TrialPanel { get; private set; }

        public QuizController TrialController { get; private set; }

        public AssistedRoutePanel AssistedPanel { get; private set; }

        public AssistedRouteController AssistedController { get; private set; }

        public FlowTrialStage? ActiveTrialStage { get; private set; }

        public ProfileDataV1 Profile { get; private set; }

        public CampaignController Campaign { get; private set; }

        public FlowBattle Battle { get; private set; }

        public void Configure(
            CombatWorldRunner combatRunner,
            bool deterministicAcceptanceData,
            string runtimeSessionPath = null)
        {
            if (Flow != null)
                throw new InvalidOperationException("Runtime configuration is already active.");
            if (runtimeSessionPath != null && string.IsNullOrWhiteSpace(runtimeSessionPath))
                throw new ArgumentException(
                    "A runtime session override cannot be empty.",
                    nameof(runtimeSessionPath));

            runner = combatRunner ?? throw new ArgumentNullException(nameof(combatRunner));
            useDeterministicAcceptanceData = deterministicAcceptanceData;
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            _runtimeSessionPathOverride = runtimeSessionPath;
#else
            if (runtimeSessionPath != null)
            {
                throw new InvalidOperationException(
                    "Runtime session path injection is restricted to development acceptance runs.");
            }
#endif
        }

        private void Awake()
        {
            _lifetime = new CancellationTokenSource();
            if (runner == null)
                runner = FindFirstObjectByType<CombatWorldRunner>();
            if (runner == null)
                throw new InvalidOperationException("The vertical slice requires a CombatWorldRunner.");

            var worlds = CreateDemoWorlds();
            var world = worlds[0];
            _demoWorlds = worlds;
            var store = new RuntimeSessionStore(ResolveSessionPath());
            FlowCheckpoint restoredCheckpoint;
            if (File.Exists(store.PrimaryPath) || File.Exists(store.BackupPath))
            {
                var restored = store.Load(snapshot =>
                    IsRuntimeSessionCoherent(worlds, snapshot));
                Profile = restored.Profile;
                restoredCheckpoint = restored.Checkpoint;
                // Migrate saves written by the first three-world build, which
                // persisted the next world's stable checkpoint before updating
                // activeWorldId. Coherence has already proven this is a valid
                // sequential advance through completed prior routes.
                if (restoredCheckpoint.Battle != null &&
                    !string.Equals(
                        Profile.ActiveWorldId,
                        restoredCheckpoint.Battle.WorldId,
                        StringComparison.Ordinal))
                {
                    Profile.ActivateWorld(restoredCheckpoint.Battle.WorldId);
                    store.Save(Profile, restoredCheckpoint);
                }
            }
            else
            {
                Profile = CreateProfile();
                restoredCheckpoint = new FlowCheckpoint(
                    FlowState.Title,
                    null,
                    combatVictoryPreserved: false,
                    committedTrialIds: Array.Empty<string>(),
                    committedRewardIds: Array.Empty<string>(),
                    rewardSourceCompletionId: null,
                    rewardAuthorityReceiptId: null);
                store.Save(Profile, restoredCheckpoint);
            }

            Campaign = new CampaignController(
                worlds,
                Profile,
                new RewardController(maxFocusPerTrial: 3));
            ValidateRestoredBattle(worlds, restoredCheckpoint);
            Battle = restoredCheckpoint.Battle ??
                     new FlowBattle(world.Id, world.LeadInBattles[0].Id);
            _campaignMutations = new CampaignControllerMutations(Campaign);
            _persistence = new RuntimeSessionPersistence(store, Profile);
            _campaignPort = new RuntimeCampaignFlowAdapter(
                _campaignMutations,
                this,
                _persistence,
                restoredCheckpoint,
                RestoredVictories(worlds, Profile));
            Flow = new VerticalSliceFlowController(this, this, _campaignPort);
            _sealAttempt = restoredCheckpoint.CommittedTrialIds.Count(id =>
                id.StartsWith("complete-seal-", StringComparison.Ordinal));

            BuildShell();
            ConfigureQuizBoundary();
            runner.RunAutomatically = false;
            runner.Hud.enabled = false;
            Flow.Restore(restoredCheckpoint);
            if (restoredCheckpoint.StableState == FlowState.Title)
            {
                ShowTitle();
                BuildOpening();
            }
        }

        private void Update()
        {
            if (Flow == null)
                return;

            UpdateOpening();

            if (Flow.State == FlowState.Combat &&
                !_combatResolved &&
                runner.State != null &&
                runner.State.Result != CombatResult.InProgress)
            {
                if (Flow.ResolveCombat(
                    runner.State.Result == CombatResult.PlayerWon
                        ? FlowCombatOutcome.Victory
                        : FlowCombatOutcome.Defeat))
                {
                    _combatResolved = true;
                }
                return;
            }

            if ((Flow.State == FlowState.NormalTrial ||
                 Flow.State == FlowState.SealTrial) &&
                !_trialCommitted &&
                TrialController != null &&
                TrialController.State == QuizState.Complete)
            {
                CommitStandardTrialAuthority();
                return;
            }

            if (Flow.State == FlowState.LossTrial &&
                !_trialCommitted &&
                TrialController != null &&
                TrialController.State == QuizState.Complete)
            {
                _trialCommitted = Flow.CompleteLossTrial();
                return;
            }

            if (Flow.State == FlowState.AssistedRoute &&
                !_trialCommitted &&
                AssistedController?.FinalResult != null)
            {
                CompleteAssistedAuthority();
            }
        }

        public void PresentCombat(FlowBattle battle)
        {
            if (battle == null)
                throw new ArgumentNullException(nameof(battle));
            HideShell();
            CleanupTrialPanels();
            _combatResolved = false;
            _trialCommitted = false;
            _pendingAuthorityCompletion = null;
            ApplyWorldTheme(DemoWorldIndexFor(battle.WorldId));
            runner.Hud.enabled = true;
            runner.RestartCombat();
            runner.RunAutomatically = true;
        }

        public void PresentNormalTrial(FlowBattle battle)
        {
            BeginStandardTrial(
                battle,
                FlowTrialStage.Normal,
                LearningBattleTier.Route1,
                AtlasTrialPurpose.RouteProgression);
        }

        public void PresentLossTrial(FlowBattle battle)
        {
            BeginStandardTrial(
                battle,
                null,
                LearningBattleTier.Route1,
                AtlasTrialPurpose.DefeatRecovery);
        }

        public void PresentSealTrial(FlowBattle battle)
        {
            BeginStandardTrial(
                battle,
                FlowTrialStage.Seal,
                LearningBattleTier.SealTrial,
                AtlasTrialPurpose.RouteProgression);
        }

        public void PresentAssistedRoute(FlowBattle battle)
        {
            if (battle == null)
                throw new ArgumentNullException(nameof(battle));
            HideShell();
            CleanupTrialPanels();
            runner.RunAutomatically = false;
            runner.Hud.enabled = false;
            ActiveTrialStage = FlowTrialStage.Assisted;
            _trialCommitted = false;
            _pendingAuthorityCompletion = null;
            AssistedController = new AssistedRouteController(
                _quizClient,
                NextRequestId);
            AssistedPanel = AssistedRoutePanel.Create(
                AssistedController,
                new AtlasTrialSettings(TrialTitleFor(battle), textScale, reducedMotion),
                new SilentQuizSpeech());
            AssistedPanel.Completed += CompleteAssistedAuthority;
            AssistedPanel.ReturnToMapRequested += ReturnFromUnavailable;
            _ = AssistedController.PrepareAsync(
                battle.WorldId,
                new AssistedRoutePrepare(
                    "wayline.v1",
                    NextRequestId(),
                    SessionId),
                _lifetime.Token);
        }

        public void PresentMap()
        {
            runner.RunAutomatically = false;
            runner.Hud.enabled = false;
            CleanupTrialPanels();
            _shellRoot.SetActive(true);

            var cleared = ClearedScoutCount();
            var worldCount = _demoWorlds?.Length ?? 1;
            var allComplete = _demoWorlds != null && cleared >= worldCount;
            _worldIndex = _demoWorlds == null ? 0 : Mathf.Clamp(cleared, 0, worldCount - 1);
            SetActiveWorldNode(_worldIndex);

            if (Flow.HasPendingTrial)
            {
                var pendingWorld = WorldForBattle(Battle);
                _shellTitle.text = (pendingWorld?.DisplayName ?? "Valuehold Reach").ToUpperInvariant();
                _shellBody.text =
                    "Your combat victory is preserved. Resume the route trial to continue.";
                _startBattleButtonLabel.text = "RESUME ROUTE TRIAL";
                SetOnlyButton(StartBattleButton);
                return;
            }

            if (allComplete)
            {
                _shellTitle.text = "MERIDIAN RESTORED";
                _shellBody.text =
                    "All three routes are reconnected. The Broken Meridian holds — the demo campaign is complete.";
                _startBattleButtonLabel.text = "ROUTE COMPLETE";
                SetOnlyButton(StartBattleButton);
                return;
            }

            var world = _demoWorlds[_worldIndex];
            Battle = new FlowBattle(world.Id, world.Id + "-scout");
            _shellTitle.text = world.DisplayName.ToUpperInvariant();
            _shellBody.text =
                $"Route {_worldIndex + 1} of {worldCount}. Face {ChampionName(_worldIndex)}, " +
                "then secure the route through a short trial.";
            _startBattleButtonLabel.text = "FACE " + ChampionName(_worldIndex).ToUpperInvariant();
            SetOnlyButton(StartBattleButton);
        }

        public void PresentReward(
            FlowBattle battle,
            string sourceCompletionId,
            string authorityReceiptId)
        {
            runner.RunAutomatically = false;
            runner.Hud.enabled = false;
            CleanupTrialPanels();
            _shellRoot.SetActive(true);

            var finalRoute = _demoWorlds != null && ClearedScoutCount() >= _demoWorlds.Length;
            var world = WorldForBattle(battle);
            if (finalRoute)
            {
                _shellTitle.text = "MERIDIAN RESTORED";
                _shellBody.text =
                    "Every route is reconnected. The Broken Meridian holds.\n" +
                    $"{Profile.RouteMarks} Route Marks  /  {Profile.Focus} Focus";
                for (var i = 0; i < _atlasNodes.Length; i++)
                {
                    if (_atlasNodes[i] != null)
                        _atlasNodes[i].color = new Color32(230, 175, 59, 255);
                }
            }
            else
            {
                _shellTitle.text = $"ROUTE SECURED — {(world?.DisplayName ?? "Valuehold Reach").ToUpperInvariant()}";
                _shellBody.text =
                    $"Combat preserved • Trial committed\n{Profile.RouteMarks} Route Marks  /  {Profile.Focus} Focus";
            }
            SetOnlyButton(RewardButton);
        }

        private void ShowTitle()
        {
            _shellRoot.SetActive(true);
            _shellTitle.text = "WAYLINE\nTHE BROKEN MERIDIAN";
            _shellBody.text =
                "Win the duel. Read the route. Repair the world.\nA playable internal Valuehold slice.";
            SetOnlyButton(EnterMapButton);
        }

        private void EnterMap()
        {
            if (Flow.State == FlowState.Title)
                Flow.EnterMap();
        }

        private void StartBattle()
        {
            if (Flow.State != FlowState.Map)
                return;
            if (Flow.HasPendingTrial)
            {
                Flow.ResumePending();
                return;
            }
            if (_demoWorlds != null && ClearedScoutCount() >= _demoWorlds.Length)
                return; // campaign complete: no more routes to start
            Flow.StartCombat(Battle);
        }

        private int ClearedScoutCount()
        {
            if (_demoWorlds == null)
                return 0;
            var count = 0;
            foreach (var world in _demoWorlds)
            {
                if (Profile.IsBattleCompleted(world.Id + "-scout"))
                    count++;
            }
            return count;
        }

        private WorldDefinition WorldForBattle(FlowBattle battle)
        {
            if (battle == null || _demoWorlds == null)
                return null;
            foreach (var world in _demoWorlds)
            {
                if (string.Equals(world.Id, battle.WorldId, StringComparison.Ordinal))
                    return world;
            }
            return null;
        }

        private string TrialTitleFor(FlowBattle battle)
        {
            var world = WorldForBattle(battle);
            return (world?.DisplayName ?? "Valuehold Reach").ToUpperInvariant();
        }

        private int DemoWorldIndexFor(string worldId)
        {
            if (_demoWorlds == null)
                return 0;
            for (var i = 0; i < _demoWorlds.Length; i++)
            {
                if (string.Equals(_demoWorlds[i].Id, worldId, StringComparison.Ordinal))
                    return i;
            }
            return 0;
        }

        private static string ChampionName(int worldIndex)
        {
            switch (worldIndex)
            {
                case 1:
                    return "the Tide Marshal";
                case 2:
                    return "the Chain Warden";
                default:
                    return "the Surveyor";
            }
        }

        private void ApplyWorldTheme(int worldIndex)
        {
            if (runner == null)
                return;
            var enemyPreset = worldIndex == 1
                ? HumanoidPreset.TideMarshal
                : worldIndex == 2
                    ? HumanoidPreset.ChainWarden
                    : HumanoidPreset.SurveyorGeneral;
            runner.EnemyPresenter?.Retheme(
                enemyPreset,
                facingRight: false,
                new Color(0.6f, 0.6f, 0.65f),
                new Color(0.9f, 0.68f, 0.23f));
            runner.Hud?.SetOpponentName(
                worldIndex == 1
                    ? "Tide Marshal"
                    : worldIndex == 2
                        ? "Chain Warden"
                        : "Surveyor-General");

            Color background;
            Color floorColor;
            switch (worldIndex)
            {
                case 1:
                    background = new Color32(14, 30, 38, 255);
                    floorColor = new Color32(45, 78, 80, 255);
                    break;
                case 2:
                    background = new Color32(32, 20, 20, 255);
                    floorColor = new Color32(96, 62, 52, 255);
                    break;
                default:
                    background = new Color32(21, 27, 38, 255);
                    floorColor = new Color32(90, 94, 100, 255);
                    break;
            }

            var camera = runner.FightCamera != null ? runner.FightCamera.Camera : null;
            if (camera != null)
                camera.backgroundColor = background;
            var floor = GameObject.Find("Combat Floor");
            if (floor != null)
            {
                var floorRenderer = floor.GetComponent<Renderer>();
                if (floorRenderer != null)
                    floorRenderer.material.color = floorColor;
            }
        }

        private void AcknowledgeReward()
        {
            var completedBattle = Battle;
            if (!Flow.CompleteReward())
                return;

            var completedIndex = completedBattle == null
                ? -1
                : DemoWorldIndexFor(completedBattle.WorldId);
            var nextIndex = completedIndex + 1;
            if (_demoWorlds != null &&
                completedIndex >= 0 &&
                nextIndex < _demoWorlds.Length)
            {
                Profile.ActivateWorld(_demoWorlds[nextIndex].Id);
                // Flow already persisted the Map checkpoint before presenting
                // it; persist once more with the new active-world identity.
                _persistence?.Store(Flow.LastCheckpoint);
            }
        }

        private void BeginStandardTrial(
            FlowBattle battle,
            FlowTrialStage? stage,
            LearningBattleTier tier,
            AtlasTrialPurpose purpose)
        {
            if (battle == null)
                throw new ArgumentNullException(nameof(battle));
            HideShell();
            CleanupTrialPanels();
            runner.RunAutomatically = false;
            runner.Hud.enabled = false;
            ActiveTrialStage = stage;
            _trialCommitted = false;
            _pendingAuthorityCompletion = null;
            TrialController = new QuizController(_quizClient, NextRequestId);
            TrialPanel = AtlasTrialPanel.Create(
                TrialController,
                new AtlasTrialSettings(
                    TrialTitleFor(battle),
                    textScale,
                    reducedMotion,
                    purpose),
                new SilentQuizSpeech());
            TrialPanel.RetryRequested += RetryStandardTrial;
            TrialPanel.ReturnToMapRequested += ReturnFromUnavailable;
            _ = TrialController.PrepareAsync(
                new BattleQuizRequest(
                    "wayline.v1",
                    NextRequestId(),
                    SessionId,
                    battle.BattleId,
                    battle.WorldId,
                    tier),
                _lifetime.Token);
        }

        private async void RetryStandardTrial()
        {
            if (TrialController == null ||
                (ActiveTrialStage == null && Flow.State != FlowState.LossTrial))
                return;
            try
            {
                await TrialController.PrepareAsync(
                    new BattleQuizRequest(
                        "wayline.v1",
                        NextRequestId(),
                        SessionId,
                        Battle.BattleId,
                        Battle.WorldId,
                        ActiveTrialStage == FlowTrialStage.Seal
                            ? LearningBattleTier.SealTrial
                            : LearningBattleTier.Route1),
                    _lifetime.Token);
            }
            catch (OperationCanceledException)
            {
                return;
            }

            if (TrialPanel != null)
                TrialPanel.CompleteRuntimeRetry(!TrialController.HasFailure);
        }

        private void ReturnFromUnavailable()
        {
            if (Flow.State != FlowState.Unavailable)
                Flow.SuspendTrial("runtime_unavailable");
            Flow.ReturnToMapFromUnavailable();
        }

        private void CommitStandardTrialAuthority()
        {
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            var authority =
                _quizClient as DevelopmentDeterministicAcceptanceQuizClient;
            if (authority == null &&
                _quizClient is DevelopmentLiveAcceptanceQuizClient liveClient)
            {
                authority = liveClient.Authority;
            }
            if (authority == null)
            {
                SuspendForMissingAuthority();
                return;
            }

            var completion = _pendingAuthorityCompletion;
            if (completion == null)
            {
                var result = TrialController.FinalResult;
                var requestId = ActiveTrialStage == FlowTrialStage.Seal
                    ? CreateGloballyUniqueRequestId("complete-seal")
                    : CreateGloballyUniqueRequestId("complete-battle");
                if (ActiveTrialStage == FlowTrialStage.Seal)
                {
                    var attemptNumber = ++_sealAttempt;
                    var command = new SealTrialComplete(
                        "wayline.v1",
                        requestId,
                        SessionId);
                    var server = authority.AuthorizeSeal(
                        command.RequestId,
                        Battle,
                        result,
                        attemptNumber);
                    completion = AuthoritativeProgressionMapper.FromSeal(
                        Battle,
                        attemptNumber,
                        result.BatchId,
                        command,
                        server);
                    _campaignMutations.RegisterAuthoritativeTrial(
                        completion.CompletionId,
                        Battle,
                        campaign => campaign.ApplySealTrial(new SealTrialResolution(
                            server.WorldId,
                            server.AttemptNumber,
                            server.Passed,
                            server.WorldCleared,
                            server.AssistedRouteUnlocked)));
                }
                else
                {
                    var definition = Campaign.TrialFor(Battle.WorldId, Battle.BattleId);
                    var boss = definition.Tier == CampaignBattleTier.Boss ||
                               definition.Tier == CampaignBattleTier.CampaignFinale;
                    var command = new BattleComplete(
                        "wayline.v1",
                        requestId,
                        SessionId,
                        true);
                    var server = authority.AuthorizeBattle(
                        command.RequestId,
                        Battle,
                        result,
                        boss,
                        worldCleared: boss,
                        sealTrialRequired: false);
                    completion = AuthoritativeProgressionMapper.FromBattle(
                        Battle,
                        result.BatchId,
                        command,
                        server);
                    var performance = new TrialPerformance(
                        result.ItemCount - result.FirstPassWrongCount,
                        result.Items.Count(item => item.SelfCorrected),
                        result.ItemCount);
                    _campaignMutations.RegisterAuthoritativeTrial(
                        completion.CompletionId,
                        Battle,
                        campaign =>
                        {
                            if (server.BossBattle)
                            {
                                campaign.ApplyBossTrial(new BossTrialResolution(
                                    server.WorldId,
                                    server.BattleId,
                                    server.FinalCorrect,
                                    server.ItemCount,
                                    server.WorldCleared,
                                    server.SealTrialRequired));
                            }
                            else
                            {
                                campaign.CompleteStandardBattle(
                                    server.WorldId,
                                    server.BattleId,
                                    performance);
                            }
                        });
                }

                _pendingAuthorityCompletion = completion;
            }

            if (Flow.CompleteTrial(completion))
            {
                _trialCommitted = ShouldCloseTrialCommitGuard(Flow.State);
                _pendingAuthorityCompletion = null;
            }
#else
            SuspendForMissingAuthority();
#endif
        }

        private void CompleteAssistedAuthority()
        {
            if (_trialCommitted || AssistedController?.FinalResult == null)
                return;
            var completion = _pendingAuthorityCompletion;
            if (completion == null)
            {
                var server = AssistedController.FinalResult;
                var command = AssistedController.CompletionRequest;
                if (command == null || AssistedController.Batch == null)
                {
                    SuspendForMissingAuthority();
                    return;
                }
                completion = AuthoritativeProgressionMapper.FromAssisted(
                    Battle,
                    AssistedController.Batch.RouteId,
                    command,
                    server);
                _campaignMutations.RegisterAuthoritativeTrial(
                    completion.CompletionId,
                    Battle,
                    campaign => campaign.ApplyAssistedRoute(new AssistedRouteResolution(
                        server.WorldId,
                        server.FinalCorrect,
                        server.SupportedMcqCount,
                        server.WorldCleared)));
                _pendingAuthorityCompletion = completion;
            }
            if (Flow.CompleteTrial(completion))
            {
                _trialCommitted = ShouldCloseTrialCommitGuard(Flow.State);
                _pendingAuthorityCompletion = null;
            }
        }

        private static bool ShouldCloseTrialCommitGuard(FlowState resultingState)
        {
            return resultingState == FlowState.Reward;
        }

        private void SuspendForMissingAuthority()
        {
            if (Flow.State != FlowState.Unavailable)
                Flow.SuspendTrial("authoritative_progression_unavailable");
            TrialPanel?.ShowRuntimeUnavailable();
        }

        private void ConfigureQuizBoundary()
        {
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            if (useDeterministicAcceptanceData && DeterministicAcceptanceGate.IsAvailable)
            {
                var liveBridge = Environment.GetEnvironmentVariable(
                    "WAYLINE_LIVE_BRIDGE");
                if (Uri.TryCreate(liveBridge, UriKind.Absolute, out var bridgeUri) &&
                    bridgeUri.IsLoopback &&
                    bridgeUri.Scheme == Uri.UriSchemeHttp)
                {
                    _quizClient = new DevelopmentLiveAcceptanceQuizClient(bridgeUri);
                    AcceptanceDataLabel.text =
                        DevelopmentLiveAcceptanceQuizClient.VisibleLabel;
                }
                else
                {
                    _quizClient = new DevelopmentDeterministicAcceptanceQuizClient();
                    AcceptanceDataLabel.text =
                        DevelopmentDeterministicAcceptanceQuizClient.VisibleLabel;
                }
                AcceptanceDataLabel.gameObject.SetActive(true);
                return;
            }
#else
            ConfigureFailClosedQuizBoundary();
            return;
#endif
            ConfigureFailClosedQuizBoundary();
        }

        private void ConfigureFailClosedQuizBoundary()
        {
            _quizClient = new FailClosedWaylineClient();
            AcceptanceDataLabel.text = "LIVE WAYLINE FORGE REQUIRED";
            AcceptanceDataLabel.gameObject.SetActive(true);
        }

        private void BuildShell()
        {
            EnsureEventSystem();
            var root = new GameObject(
                "Wayline Vertical Slice Shell",
                typeof(RectTransform),
                typeof(Canvas),
                typeof(CanvasScaler),
                typeof(GraphicRaycaster));
            root.transform.SetParent(transform, false);
            var canvas = root.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            canvas.sortingOrder = 300;
            var scaler = root.GetComponent<CanvasScaler>();
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(1920f, 1080f);
            scaler.matchWidthOrHeight = 0.5f;
            _shellRoot = root;

            var veil = AddImage(root.transform, "Night ink field", new Color32(21, 27, 38, 246));
            Stretch(veil.rectTransform);
            var meridian = AddImage(root.transform, "Meridian line", new Color32(230, 175, 59, 255));
            SetRect(meridian.rectTransform, new Vector2(0.08f, 0.52f), new Vector2(0.92f, 0.52f),
                new Vector2(0f, -2f), new Vector2(0f, 2f));

            BuildAtlasNodes(root.transform);

            _shellTitle = AddText(root.transform, "Slice title", 64, FontStyle.Bold,
                new Color32(215, 209, 194, 255));
            SetRect((RectTransform)_shellTitle.transform, new Vector2(0.12f, 0.52f),
                new Vector2(0.88f, 0.86f), Vector2.zero, Vector2.zero);
            _shellBody = AddText(root.transform, "Slice body", 30, FontStyle.Normal,
                new Color32(215, 209, 194, 255));
            SetRect((RectTransform)_shellBody.transform, new Vector2(0.18f, 0.27f),
                new Vector2(0.82f, 0.48f), Vector2.zero, Vector2.zero);

            EnterMapButton = AddButton(root.transform, "Enter Valuehold", "ENTER VALUEHOLD");
            StartBattleButton = AddButton(root.transform, "Start scout duel", "FACE THE SURVEYOR");
            _startBattleButtonLabel =
                StartBattleButton.transform.Find("Label").GetComponent<Text>();
            RewardButton = AddButton(root.transform, "Return to route map", "RETURN TO MAP");
            EnterMapButton.onClick.AddListener(EnterMap);
            StartBattleButton.onClick.AddListener(StartBattle);
            RewardButton.onClick.AddListener(AcknowledgeReward);

            var labelRoot = new GameObject(
                "Acceptance Boundary Label",
                typeof(RectTransform),
                typeof(Canvas),
                typeof(CanvasScaler),
                typeof(GraphicRaycaster));
            labelRoot.transform.SetParent(transform, false);
            var labelCanvas = labelRoot.GetComponent<Canvas>();
            labelCanvas.renderMode = RenderMode.ScreenSpaceOverlay;
            labelCanvas.sortingOrder = 900;
            var labelScaler = labelRoot.GetComponent<CanvasScaler>();
            labelScaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            labelScaler.referenceResolution = new Vector2(1920f, 1080f);
            AcceptanceDataLabel = AddText(
                labelRoot.transform,
                "Acceptance data warning",
                20,
                FontStyle.Bold,
                new Color32(230, 175, 59, 255));
            AcceptanceDataLabel.alignment = TextAnchor.UpperLeft;
            SetRect(
                (RectTransform)AcceptanceDataLabel.transform,
                new Vector2(0f, 1f),
                new Vector2(0.72f, 1f),
                new Vector2(24f, -52f),
                new Vector2(-12f, -12f));
        }

        private void EnsureEventSystem()
        {
            if (EventSystem.current != null)
            {
                if (EventSystem.current.GetComponent<InputSystemUIInputModule>() == null)
                    EventSystem.current.gameObject.AddComponent<InputSystemUIInputModule>();
                return;
            }

            var eventObject = new GameObject(
                "Wayline Event System",
                typeof(EventSystem),
                typeof(InputSystemUIInputModule));
            eventObject.transform.SetParent(transform, false);
        }

        private void BuildAtlasNodes(Transform parent)
        {
            var labels = new[] { "VALUEHOLD", "DECIMARA", "FRACTURE" };
            var anchors = new[] { 0.27f, 0.5f, 0.73f };
            for (var i = 0; i < 3; i++)
            {
                var node = AddImage(parent, "Atlas Node " + labels[i], new Color32(21, 27, 38, 255));
                SetRect(node.rectTransform,
                    new Vector2(anchors[i], 0.52f), new Vector2(anchors[i], 0.52f),
                    new Vector2(-11f, -11f), new Vector2(11f, 11f));
                _atlasNodes[i] = node;

                var ring = AddImage(node.transform, "Ring", new Color32(230, 175, 59, 255));
                Stretch(ring.rectTransform, new Vector2(-3f, -3f), new Vector2(3f, 3f));
                ring.transform.SetSiblingIndex(0);

                var caption = AddText(parent, "Atlas Label " + labels[i], 16, FontStyle.Bold,
                    new Color32(215, 209, 194, 255));
                SetRect((RectTransform)caption.transform,
                    new Vector2(anchors[i] - 0.1f, 0.55f), new Vector2(anchors[i] + 0.1f, 0.60f),
                    Vector2.zero, Vector2.zero);
                caption.text = labels[i];
            }
            SetActiveWorldNode(0);
        }

        private void SetActiveWorldNode(int index)
        {
            _activeWorldNode = index;
            for (var i = 0; i < _atlasNodes.Length; i++)
            {
                if (_atlasNodes[i] == null)
                    continue;
                var active = i == index;
                _atlasNodes[i].color = active
                    ? new Color32(230, 175, 59, 255)
                    : new Color32(45, 55, 72, 255);
                var scale = active ? 1.35f : 1f;
                _atlasNodes[i].rectTransform.localScale = new Vector3(scale, scale, 1f);
            }
        }

        private void BuildOpening()
        {
            EnsureEventSystem();
            // No GraphicRaycaster: the opening is a visual overlay only. Skip is
            // handled by direct input polling so a pointer click still reaches the
            // title buttons underneath (preserves the pointer-input contract).
            var root = new GameObject(
                "Wayline Opening",
                typeof(RectTransform),
                typeof(Canvas),
                typeof(CanvasScaler));
            root.transform.SetParent(transform, false);
            var canvas = root.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            canvas.sortingOrder = 600;
            var scaler = root.GetComponent<CanvasScaler>();
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(1920f, 1080f);
            _openingRoot = root;

            var veil = AddImage(root.transform, "Opening veil", new Color32(10, 13, 19, 255));
            Stretch(veil.rectTransform);
            veil.raycastTarget = false;

            _openingSweep = AddImage(root.transform, "Opening route", new Color32(230, 175, 59, 255));
            SetRect(_openingSweep.rectTransform, new Vector2(0.1f, 0.5f), new Vector2(0.1f, 0.5f),
                new Vector2(0f, -2f), new Vector2(0f, 2f));
            _openingSweep.raycastTarget = false;

            _openingCaption = AddText(root.transform, "Opening caption", 40, FontStyle.Bold,
                new Color32(230, 224, 208, 255));
            SetRect((RectTransform)_openingCaption.transform,
                new Vector2(0.12f, 0.30f), new Vector2(0.88f, 0.46f), Vector2.zero, Vector2.zero);
            _openingCaption.raycastTarget = false;

            _openingSkipHint = AddText(root.transform, "Opening skip", 20, FontStyle.Normal,
                new Color32(150, 160, 176, 255));
            SetRect((RectTransform)_openingSkipHint.transform,
                new Vector2(0.5f, 0.08f), new Vector2(0.5f, 0.12f), Vector2.zero, Vector2.zero);
            _openingSkipHint.text = "Press any key to skip";
            _openingSkipHint.raycastTarget = false;

            _openingElapsed = 0f;
            _openingActive = true;
            if (_openingCaption != null)
                _openingCaption.text = OpeningBeatLines[0];
        }

        private void UpdateOpening()
        {
            if (!_openingActive || _openingRoot == null)
                return;

            if (Flow != null && Flow.State != FlowState.Title)
            {
                DismissOpening();
                return;
            }

            _openingElapsed += Time.unscaledDeltaTime;
            if (SkipRequested())
            {
                DismissOpening();
                return;
            }

            var total = OpeningBeatTimes[OpeningBeatTimes.Length - 1] + 2.6f;
            if (reducedMotion)
                total = 1.2f;
            if (_openingElapsed >= total)
            {
                DismissOpening();
                return;
            }

            var beat = reducedMotion ? OpeningBeatLines.Length - 1 : CurrentBeat(_openingElapsed);
            if (_openingCaption != null)
                _openingCaption.text = OpeningBeatLines[beat];

            if (_openingSweep != null)
            {
                var reveal = reducedMotion ? 1f : Mathf.Clamp01(_openingElapsed / total);
                var min = _openingSweep.rectTransform.anchorMin;
                var max = _openingSweep.rectTransform.anchorMax;
                min.x = 0.1f;
                max.x = Mathf.Lerp(0.1f, 0.9f, reveal);
                _openingSweep.rectTransform.anchorMin = min;
                _openingSweep.rectTransform.anchorMax = max;
            }
        }

        private static int CurrentBeat(float elapsed)
        {
            var beat = 0;
            for (var i = 0; i < OpeningBeatTimes.Length; i++)
            {
                if (elapsed >= OpeningBeatTimes[i])
                    beat = i;
            }
            return beat;
        }

        private static bool SkipRequested()
        {
            try
            {
                var keyboard = Keyboard.current;
                if (keyboard != null &&
                    (keyboard.spaceKey.wasPressedThisFrame ||
                     keyboard.enterKey.wasPressedThisFrame ||
                     keyboard.escapeKey.wasPressedThisFrame))
                {
                    return true;
                }
                var mouse = Mouse.current;
                if (mouse != null && mouse.leftButton.wasPressedThisFrame)
                    return true;
                var gamepad = Gamepad.current;
                if (gamepad != null &&
                    (gamepad.buttonSouth.wasPressedThisFrame || gamepad.startButton.wasPressedThisFrame))
                {
                    return true;
                }
            }
            catch (System.Exception)
            {
                // Input device state can be momentarily unreadable in test
                // harnesses; treat as "no skip" rather than surfacing an error.
                return false;
            }
            return false;
        }

        private void DismissOpening()
        {
            _openingActive = false;
            if (_openingRoot != null)
                _openingRoot.SetActive(false);
        }

        private void HideShell()
        {
            _shellRoot.SetActive(false);
        }

        private void SetOnlyButton(Button active)
        {
            foreach (var button in new[] { EnterMapButton, StartBattleButton, RewardButton })
                button.gameObject.SetActive(button == active);
            var rect = (RectTransform)active.transform;
            rect.anchorMin = rect.anchorMax = new Vector2(0.5f, 0.17f);
            rect.pivot = new Vector2(0.5f, 0.5f);
            rect.anchoredPosition = Vector2.zero;
            rect.sizeDelta = new Vector2(420f, 82f);
            EventSystem.current?.SetSelectedGameObject(active.gameObject);
        }

        private void CleanupTrialPanels()
        {
            if (TrialPanel != null)
            {
                TrialPanel.RetryRequested -= RetryStandardTrial;
                TrialPanel.ReturnToMapRequested -= ReturnFromUnavailable;
                Destroy(TrialPanel.gameObject);
                TrialPanel = null;
            }
            if (AssistedPanel != null)
            {
                AssistedPanel.Completed -= CompleteAssistedAuthority;
                AssistedPanel.ReturnToMapRequested -= ReturnFromUnavailable;
                Destroy(AssistedPanel.gameObject);
                AssistedPanel = null;
            }
            ActiveTrialStage = null;
        }

        private string NextRequestId()
        {
            return CreateGloballyUniqueRequestId("acceptance-request");
        }

        private static string CreateGloballyUniqueRequestId(string prefix)
        {
            return prefix + "-" + Guid.NewGuid().ToString("N");
        }

        private string ResolveSessionPath()
        {
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            if (_runtimeSessionPathOverride != null)
            {
                _ownsAcceptanceSave = true;
                return _runtimeSessionPathOverride;
            }
            if (useDeterministicAcceptanceData && IsAutomatedTestRun())
            {
                _ownsAcceptanceSave = true;
                return Path.Combine(
                    Application.temporaryCachePath,
                    "wayline-vertical-slice-acceptance-" +
                    Guid.NewGuid().ToString("N") +
                    ".json");
            }
#endif
            return Path.Combine(
                Application.persistentDataPath,
                "wayline-runtime-session-v1.json");
        }

        private static bool IsAutomatedTestRun()
        {
            return Environment.GetCommandLineArgs().Any(argument =>
                string.Equals(argument, "-runTests", StringComparison.OrdinalIgnoreCase));
        }

        private static bool IsRuntimeSessionCoherent(
            WorldDefinition[] worlds,
            RuntimeSessionSnapshot snapshot)
        {
            if (worlds == null || worlds.Length == 0 || snapshot == null)
                return false;

            var worldById = new Dictionary<string, WorldDefinition>(StringComparer.Ordinal);
            var battleById = new Dictionary<string, BattleDefinition>(StringComparer.Ordinal);
            var battleWorldById = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (var world in worlds)
            {
                if (world == null || !worldById.TryAdd(world.Id, world))
                    return false;
                foreach (var battle in world.Battles)
                {
                    if (battle == null || !battleById.TryAdd(battle.Id, battle))
                        return false;
                    battleWorldById.Add(battle.Id, world.Id);
                }
            }

            var profile = snapshot.Profile;
            var checkpoint = snapshot.Checkpoint;
            if (!worldById.ContainsKey(profile.ActiveWorldId) ||
                (profile.PendingWorldId != null &&
                 !worldById.ContainsKey(profile.PendingWorldId)) ||
                profile.ClearedWorldIds.Any(id => !worldById.ContainsKey(id)))
            {
                return false;
            }

            if (profile.CombatVictoryBattleIds.Any(id => !battleById.ContainsKey(id)) ||
                profile.CompletedBattleIds.Any(id => !battleById.ContainsKey(id)) ||
                profile.RewardedBattleIds.Any(id => !battleById.ContainsKey(id)))
            {
                return false;
            }

            if (profile.CompletedBattleIds.Any(id =>
                    !profile.HasCombatVictory(id) ||
                    !profile.HasRewardedBattle(id)) ||
                profile.RewardedBattleIds.Any(id => !profile.HasCombatVictory(id)))
            {
                return false;
            }

            foreach (var clearedWorldId in profile.ClearedWorldIds)
            {
                var bossId = worldById[clearedWorldId].BossBattle.Id;
                if (!profile.HasCombatVictory(bossId) ||
                    !profile.IsBattleCompleted(bossId) ||
                    !profile.HasRewardedBattle(bossId))
                {
                    return false;
                }
            }

            BattleDefinition checkpointBattle = null;
            string checkpointWorldId = null;
            if (checkpoint.Battle != null)
            {
                if (!battleById.TryGetValue(
                        checkpoint.Battle.BattleId,
                        out checkpointBattle) ||
                    !battleWorldById.TryGetValue(
                        checkpoint.Battle.BattleId,
                        out checkpointWorldId) ||
                    !string.Equals(
                        checkpointWorldId,
                        checkpoint.Battle.WorldId,
                        StringComparison.Ordinal))
                {
                    return false;
                }
                if (!string.Equals(
                        checkpointWorldId,
                        profile.ActiveWorldId,
                        StringComparison.Ordinal) &&
                    !CanMigrateActiveWorld(
                        worlds,
                        profile,
                        checkpointWorldId))
                {
                    return false;
                }
            }

            var postCombat = checkpoint.StableState == FlowState.NormalTrial ||
                             checkpoint.StableState == FlowState.SealTrial ||
                             checkpoint.StableState == FlowState.AssistedRoute ||
                             checkpoint.StableState == FlowState.Reward;
            if (postCombat &&
                (checkpoint.Battle == null ||
                 !profile.HasCombatVictory(checkpoint.Battle.BattleId)))
            {
                return false;
            }

            var sealOrAssisted = checkpoint.StableState == FlowState.SealTrial ||
                                 checkpoint.StableState == FlowState.AssistedRoute;
            var unfinishedRewards = profile.RewardedBattleIds
                .Where(id => !profile.IsBattleCompleted(id))
                .ToArray();
            if (unfinishedRewards.Length > 0)
            {
                var matchingPendingBoss = unfinishedRewards.Length == 1 &&
                                          sealOrAssisted &&
                                          checkpoint.Battle != null &&
                                          string.Equals(
                                              unfinishedRewards[0],
                                              checkpoint.Battle.BattleId,
                                              StringComparison.Ordinal) &&
                                          checkpointBattle != null &&
                                          (checkpointBattle.Tier == CampaignBattleTier.Boss ||
                                           checkpointBattle.Tier == CampaignBattleTier.CampaignFinale);
                if (!matchingPendingBoss)
                    return false;
            }

            if (sealOrAssisted)
            {
                var boss = checkpointBattle != null &&
                           (checkpointBattle.Tier == CampaignBattleTier.Boss ||
                            checkpointBattle.Tier == CampaignBattleTier.CampaignFinale);
                if (!boss ||
                    !string.Equals(
                        profile.PendingStep,
                        checkpoint.StableState.ToString(),
                        StringComparison.Ordinal) ||
                    !string.Equals(
                        profile.PendingWorldId,
                        checkpointWorldId,
                        StringComparison.Ordinal) ||
                    !profile.HasRewardedBattle(checkpoint.Battle.BattleId) ||
                    profile.IsWorldCleared(checkpointWorldId))
                {
                    return false;
                }
            }
            else if (profile.PendingStep != null || profile.PendingWorldId != null)
            {
                return false;
            }

            if (checkpoint.StableState == FlowState.Reward)
            {
                var battleId = checkpoint.Battle.BattleId;
                if (!profile.IsBattleCompleted(battleId) ||
                    !profile.HasRewardedBattle(battleId))
                {
                    return false;
                }

                var boss = checkpointBattle.Tier == CampaignBattleTier.Boss ||
                           checkpointBattle.Tier == CampaignBattleTier.CampaignFinale;
                if (boss && !profile.IsWorldCleared(checkpointWorldId))
                    return false;
            }

            return true;
        }

        private static bool CanMigrateActiveWorld(
            WorldDefinition[] worlds,
            ProfileDataV1 profile,
            string checkpointWorldId)
        {
            var activeIndex = -1;
            var checkpointIndex = -1;
            for (var index = 0; index < worlds.Length; index++)
            {
                if (string.Equals(
                        worlds[index].Id,
                        profile.ActiveWorldId,
                        StringComparison.Ordinal))
                {
                    activeIndex = index;
                }
                if (string.Equals(
                        worlds[index].Id,
                        checkpointWorldId,
                        StringComparison.Ordinal))
                {
                    checkpointIndex = index;
                }
            }

            if (activeIndex < 0 || checkpointIndex <= activeIndex)
                return false;

            // Every world crossed by the migration must have its demo scout
            // victory, trial completion, and reward committed. This preserves
            // the original cross-world tamper guard while accepting the one
            // save shape written by the first three-world build.
            for (var index = activeIndex; index < checkpointIndex; index++)
            {
                var scoutId = worlds[index].LeadInBattles[0].Id;
                if (!profile.HasCombatVictory(scoutId) ||
                    !profile.IsBattleCompleted(scoutId) ||
                    !profile.HasRewardedBattle(scoutId))
                {
                    return false;
                }
            }
            return true;
        }

        private static void ValidateRestoredBattle(
            WorldDefinition[] worlds,
            FlowCheckpoint checkpoint)
        {
            if (checkpoint.Battle == null)
                return;
            var world = worlds.FirstOrDefault(candidate =>
                string.Equals(candidate.Id, checkpoint.Battle.WorldId, StringComparison.Ordinal));
            if (world == null)
            {
                throw new InvalidDataException(
                    "The runtime checkpoint belongs to an unavailable world.");
            }

            world.Battle(checkpoint.Battle.BattleId);
            if (checkpoint.CombatVictoryPreserved &&
                !checkpoint.Battle.BattleId.StartsWith(world.Id + "-", StringComparison.Ordinal) &&
                !checkpoint.Battle.BattleId.StartsWith(world.Id + "_", StringComparison.Ordinal))
            {
                throw new InvalidDataException(
                    "The preserved combat victory does not belong to its world.");
            }
        }

        private static FlowBattle[] RestoredVictories(
            WorldDefinition[] worlds,
            ProfileDataV1 profile)
        {
            var battleWorld = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (var world in worlds)
            {
                foreach (var definition in world.Battles)
                    battleWorld[definition.Id] = world.Id;
            }
            if (profile.CombatVictoryBattleIds.Any(id => !battleWorld.ContainsKey(id)))
            {
                throw new InvalidDataException(
                    "The runtime profile contains an unknown combat victory.");
            }

            return profile.CombatVictoryBattleIds
                .Select(id => new FlowBattle(battleWorld[id], id))
                .ToArray();
        }

        private static ProfileDataV1 CreateProfile()
        {
            return ProfileDataV1.CreateNew(
                "profile-local-acceptance-001",
                "valuehold",
                "splitstaff",
                new HeroAppearanceSelection(
                    "routekeeper-amber",
                    "hair-braid",
                    "mantle-scout",
                    "dye-lapis",
                    "dye-oxide",
                    "inlay-gold"));
        }

        private static WorldDefinition[] CreateDemoWorlds()
        {
            return new[]
            {
                DemoWorld(
                    "valuehold", "Valuehold Reach",
                    new[] { "place_value", "mental_add_sub" },
                    "arena-valuehold-graybox", "surveyors", "surveyor-general",
                    "folding-lance", "#E6AF3B"),
                DemoWorld(
                    "decimara", "Decimara Basin",
                    new[] { "decimal_add_sub" },
                    "arena-decimara-graybox", "tides", "tide-marshal",
                    "pivot-sabers", "#2D7F83"),
                DemoWorld(
                    "fracture", "Fracture Isles",
                    new[] { "fraction_add_sub" },
                    "arena-fracture-graybox", "dividers", "chain-warden",
                    "counterweight-chain", "#A5432F")
            };
        }

        private static WorldDefinition DemoWorld(
            string id,
            string displayName,
            string[] skills,
            string arenaId,
            string factionId,
            string bossId,
            string weaponId,
            string colorHex)
        {
            return new WorldDefinition(
                id, displayName, skills, arenaId, factionId, bossId, weaponId, colorHex,
                new[]
                {
                    new BattleDefinition(id + "-scout", CampaignBattleTier.Scout,
                        "ai-scout", id + "-opp-scout", 10, "card-scout"),
                    new BattleDefinition(id + "-rival", CampaignBattleTier.Rival,
                        "ai-rival", id + "-opp-rival", 12, "card-rival"),
                    new BattleDefinition(id + "-warden", CampaignBattleTier.Warden,
                        "ai-warden", id + "-opp-warden", 14, "card-warden"),
                    new BattleDefinition(id + "-lieutenant", CampaignBattleTier.Lieutenant,
                        "ai-lieutenant", id + "-opp-lieutenant", 16, "card-lieutenant"),
                    new BattleDefinition(id + "-boss", CampaignBattleTier.Boss,
                        "ai-boss", bossId, 25, "card-boss")
                });
        }

        private static Image AddImage(Transform parent, string name, Color color)
        {
            var gameObject = new GameObject(name, typeof(RectTransform), typeof(Image));
            gameObject.transform.SetParent(parent, false);
            var image = gameObject.GetComponent<Image>();
            image.color = color;
            return image;
        }

        private static Text AddText(
            Transform parent,
            string name,
            int size,
            FontStyle style,
            Color color)
        {
            var gameObject = new GameObject(name, typeof(RectTransform), typeof(Text));
            gameObject.transform.SetParent(parent, false);
            var text = gameObject.GetComponent<Text>();
            text.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            text.fontSize = size;
            text.fontStyle = style;
            text.color = color;
            text.alignment = TextAnchor.MiddleCenter;
            text.horizontalOverflow = HorizontalWrapMode.Wrap;
            text.verticalOverflow = VerticalWrapMode.Overflow;
            return text;
        }

        private static Button AddButton(Transform parent, string name, string label)
        {
            var image = AddImage(parent, name, new Color32(37, 59, 102, 255));
            var button = image.gameObject.AddComponent<Button>();
            var text = AddText(image.transform, "Label", 28, FontStyle.Bold,
                new Color32(230, 175, 59, 255));
            text.text = label;
            Stretch((RectTransform)text.transform, new Vector2(24f, 12f), new Vector2(-24f, -12f));
            return button;
        }

        private static void Stretch(
            RectTransform rect,
            Vector2? offsetMin = null,
            Vector2? offsetMax = null)
        {
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.one;
            rect.offsetMin = offsetMin ?? Vector2.zero;
            rect.offsetMax = offsetMax ?? Vector2.zero;
        }

        private static void SetRect(
            RectTransform rect,
            Vector2 anchorMin,
            Vector2 anchorMax,
            Vector2 offsetMin,
            Vector2 offsetMax)
        {
            rect.anchorMin = anchorMin;
            rect.anchorMax = anchorMax;
            rect.offsetMin = offsetMin;
            rect.offsetMax = offsetMax;
        }

        private void OnDestroy()
        {
            EnterMapButton?.onClick.RemoveListener(EnterMap);
            StartBattleButton?.onClick.RemoveListener(StartBattle);
            RewardButton?.onClick.RemoveListener(AcknowledgeReward);
            CleanupTrialPanels();
            _lifetime?.Cancel();
            _lifetime?.Dispose();
            _lifetime = null;
            if (_quizClient is IDisposable disposableQuizClient)
                disposableQuizClient.Dispose();
            if (_ownsAcceptanceSave)
            {
                try
                {
                    _persistence?.Delete();
                }
                catch (IOException)
                {
                }
                catch (UnauthorizedAccessException)
                {
                }
            }
        }

        private sealed class SilentQuizSpeech : IQuizTextToSpeech
        {
            public void Speak(string text)
            {
            }
        }

        private sealed class RuntimeSessionPersistence : IRuntimeFlowPersistence
        {
            private readonly RuntimeSessionStore _store;
            private readonly ProfileDataV1 _profile;

            public RuntimeSessionPersistence(
                RuntimeSessionStore store,
                ProfileDataV1 profile)
            {
                _store = store ?? throw new ArgumentNullException(nameof(store));
                _profile = profile ?? throw new ArgumentNullException(nameof(profile));
            }

            public FlowCheckpoint LastCheckpoint { get; private set; }

            public void Store(FlowCheckpoint checkpoint)
            {
                _store.Save(_profile, checkpoint);
                LastCheckpoint = checkpoint;
            }

            public void Delete() => _store.Delete();
        }
    }
}
