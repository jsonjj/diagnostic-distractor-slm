using System;
using System.Collections.Generic;
using System.Threading;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem.UI;
using UnityEngine.UI;
using Wayline.Learning.Assisted;
using Wayline.Learning.Contracts;

namespace Wayline.UI.Assisted
{
    public enum AssistedRouteView
    {
        Loading,
        WorkedExample,
        Question,
        Submitting,
        Feedback,
        Complete,
        Unavailable
    }

    public sealed class AssistedRoutePanel : MonoBehaviour
    {
        private readonly List<Selectable> _focusOrder = new List<Selectable>();
        private AssistedRouteController _controller;
        private AtlasTrialSettings _settings;
        private IQuizTextToSpeech _speech;
        private CancellationTokenSource _lifetime;
        private CanvasGroup _surfaceGroup;
        private RectTransform _routeLineRect;
        private Image _routeLine;
        private Text _routeLabel;
        private Text _readAloudLabel;
        private Text _submittingText;
        private Text _unavailableText;
        private GameObject _submittingRoot;
        private GameObject _unavailableRoot;
        private Button _retryButton;
        private Button _returnToMapButton;
        private Text _retryLabel;
        private Text _returnLabel;
        private EventSystem _ownedEventSystem;
        private InputSystemUIInputModule _addedExternalInputModule;
        private float _textScale;
        private float _motionStartedAt;
        private bool _openingComplete;
        private bool _transitioning;
        private bool _actionInFlight;
        private bool _completionRaised;

        public event Action Completed;

        public event Action ReturnToMapRequested;

        public AssistedRouteView View { get; private set; }

        public bool Interactive { get; private set; }

        public AssistedWorkedExamplePage WorkedExamplePage { get; private set; }

        public AssistedQuestionPage QuestionPage { get; private set; }

        public AssistedFeedbackPage FeedbackPage { get; private set; }

        public Button PrimaryButton { get; private set; }

        public Text PrimaryLabel { get; private set; }

        public Button ReadAloudButton { get; private set; }

        public Button RetryButton => _retryButton;

        public Button ReturnToMapButton => _returnToMapButton;

        public Text CompletionText { get; private set; }

        public IReadOnlyList<Selectable> FocusOrder => _focusOrder;

        public static AssistedRoutePanel Create(
            AssistedRouteController controller,
            AtlasTrialSettings settings,
            IQuizTextToSpeech speech)
        {
            if (controller == null)
                throw new ArgumentNullException(nameof(controller));
            if (settings == null)
                throw new ArgumentNullException(nameof(settings));
            if (speech == null)
                throw new ArgumentNullException(nameof(speech));

            var root = new GameObject(
                "Assisted Route Atlas",
                typeof(RectTransform),
                typeof(Canvas),
                typeof(CanvasScaler),
                typeof(GraphicRaycaster),
                typeof(AssistedRoutePanel));
            var canvas = root.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            canvas.sortingOrder = 510;
            var scaler = root.GetComponent<CanvasScaler>();
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(1920f, 1080f);
            scaler.screenMatchMode = CanvasScaler.ScreenMatchMode.MatchWidthOrHeight;
            scaler.matchWidthOrHeight = 0.5f;

            var panel = root.GetComponent<AssistedRoutePanel>();
            panel.Initialize(controller, settings, speech);
            return panel;
        }

        public void ApplyTextScale(float scale)
        {
            if (float.IsNaN(scale) || float.IsInfinity(scale))
                throw new ArgumentOutOfRangeException(nameof(scale));
            _textScale = Mathf.Clamp(scale, 1f, 1.5f);
            _routeLabel.fontSize = Mathf.RoundToInt(30f * _textScale);
            _readAloudLabel.fontSize = Mathf.RoundToInt(28f * _textScale);
            PrimaryLabel.fontSize = Mathf.RoundToInt(28f * _textScale);
            CompletionText.fontSize = Mathf.RoundToInt(42f * _textScale);
            _submittingText.fontSize = Mathf.RoundToInt(36f * _textScale);
            _unavailableText.fontSize = Mathf.RoundToInt(34f * _textScale);
            _retryLabel.fontSize = Mathf.RoundToInt(28f * _textScale);
            _returnLabel.fontSize = Mathf.RoundToInt(28f * _textScale);
            WorkedExamplePage.ApplyTextScale(_textScale);
            QuestionPage.ApplyTextScale(_textScale);
            FeedbackPage.ApplyTextScale(_textScale);
            RenderFromController();
            Canvas.ForceUpdateCanvases();
        }

        private void Initialize(
            AssistedRouteController controller,
            AtlasTrialSettings settings,
            IQuizTextToSpeech speech)
        {
            _controller = controller;
            _settings = settings;
            _speech = speech;
            _textScale = settings.TextScale;
            _lifetime = new CancellationTokenSource();
            EnsureEventSystem();
            BuildVisualTree();
            _controller.Changed += OnControllerChanged;
            RenderFromController();
            ApplyTextScale(_textScale);
            _motionStartedAt = Time.unscaledTime;
            ApplyMotion(AssistedRouteMotionEvaluator.EvaluateOpening(
                0f,
                _settings.ReducedMotion));
        }

        private void EnsureEventSystem()
        {
            var eventSystem = EventSystem.current;
            if (eventSystem == null)
            {
                var eventObject = new GameObject(
                    "Assisted Atlas Event System",
                    typeof(EventSystem),
                    typeof(InputSystemUIInputModule));
                eventObject.transform.SetParent(transform, false);
                _ownedEventSystem = eventObject.GetComponent<EventSystem>();
                return;
            }
            if (eventSystem.GetComponent<InputSystemUIInputModule>() == null)
            {
                _addedExternalInputModule =
                    eventSystem.gameObject.AddComponent<InputSystemUIInputModule>();
            }
        }

        private void BuildVisualTree()
        {
            var background = AssistedUiFactory.Image(
                transform,
                "Night ink veil",
                new Color(
                    AssistedPalette.NightInk.r,
                    AssistedPalette.NightInk.g,
                    AssistedPalette.NightInk.b,
                    0.92f));
            background.raycastTarget = true;

            var surface = new GameObject(
                "Assisted atlas surface",
                typeof(RectTransform),
                typeof(CanvasGroup));
            surface.transform.SetParent(transform, false);
            var surfaceRect = (RectTransform)surface.transform;
            surfaceRect.anchorMin = Vector2.zero;
            surfaceRect.anchorMax = Vector2.one;
            surfaceRect.offsetMin = Vector2.zero;
            surfaceRect.offsetMax = Vector2.zero;
            _surfaceGroup = surface.GetComponent<CanvasGroup>();

            var band = new GameObject(
                "Topographic assisted field",
                typeof(RectTransform),
                typeof(AsymmetricAtlasGraphic));
            band.transform.SetParent(surface.transform, false);
            var bandRect = (RectTransform)band.transform;
            bandRect.anchorMin = new Vector2(0.025f, 0.08f);
            bandRect.anchorMax = new Vector2(0.975f, 0.92f);
            bandRect.offsetMin = Vector2.zero;
            bandRect.offsetMax = Vector2.zero;
            var bandGraphic = band.GetComponent<AsymmetricAtlasGraphic>();
            bandGraphic.color = new Color(
                AssistedPalette.Lapis.r,
                AssistedPalette.Lapis.g,
                AssistedPalette.Lapis.b,
                0.48f);
            bandGraphic.CutSize = 34f;
            bandGraphic.raycastTarget = false;

            _routeLabel = AssistedUiFactory.Text(
                surface.transform,
                "Assisted route label",
                _settings.WorldLabel + " / ASSISTED ROUTE",
                30,
                AssistedPalette.MeridianGold,
                TextAnchor.MiddleLeft,
                FontStyle.Bold);
            AssistedUiFactory.SetFixedRect(
                (RectTransform)_routeLabel.transform,
                new Vector2(0f, 1f),
                new Vector2(0f, 0.5f),
                new Vector2(74f, -60f),
                new Vector2(850f, 60f));

            _routeLine = AssistedUiFactory.Image(
                surface.transform,
                "Meridian assisted route",
                AssistedPalette.MeridianGold);
            _routeLineRect = (RectTransform)_routeLine.transform;
            _routeLineRect.anchorMin = new Vector2(0.06f, 1f);
            _routeLineRect.anchorMax = new Vector2(0.94f, 1f);
            _routeLineRect.pivot = new Vector2(0f, 0.5f);
            _routeLineRect.anchoredPosition = new Vector2(0f, -108f);
            _routeLineRect.sizeDelta = new Vector2(0f, 3f);

            WorkedExamplePage = AddPage<AssistedWorkedExamplePage>(
                surface.transform,
                "Worked example page");
            WorkedExamplePage.Initialize();
            QuestionPage = AddPage<AssistedQuestionPage>(
                surface.transform,
                "Supported question page");
            QuestionPage.Initialize(SelectOption, SelectConfidence);
            FeedbackPage = AddPage<AssistedFeedbackPage>(
                surface.transform,
                "Supported feedback page");
            FeedbackPage.Initialize();

            _submittingRoot = AssistedUiFactory.Rect(
                surface.transform,
                "Submitting assisted route",
                Vector2.zero,
                Vector2.one,
                Vector2.zero,
                Vector2.zero).gameObject;
            _submittingText = AssistedUiFactory.Text(
                _submittingRoot.transform,
                "Submitting message",
                "Securing the supported route…",
                36,
                AssistedPalette.Limestone,
                TextAnchor.MiddleCenter,
                FontStyle.Bold);

            CompletionText = AssistedUiFactory.Text(
                surface.transform,
                "Assisted completion",
                "ASSISTED ROUTE COMPLETE\nWorld route cleared.",
                42,
                AssistedPalette.Limestone,
                TextAnchor.MiddleCenter,
                FontStyle.Bold);

            BuildUnavailable(surface.transform);

            ReadAloudButton = AssistedUiFactory.AtlasButton(
                surface.transform,
                "Read assisted page aloud",
                "Read aloud",
                out _readAloudLabel,
                out _);
            _readAloudLabel.alignment = TextAnchor.MiddleCenter;
            var readAloudLabelRect = (RectTransform)_readAloudLabel.transform;
            readAloudLabelRect.offsetMin = new Vector2(20f, 10f);
            readAloudLabelRect.offsetMax = new Vector2(-20f, -10f);
            AssistedUiFactory.SetFixedRect(
                (RectTransform)ReadAloudButton.transform,
                Vector2.one,
                new Vector2(1f, 0.5f),
                new Vector2(-74f, -60f),
                new Vector2(290f, 76f));
            ReadAloudButton.onClick.AddListener(ReadCurrentAloud);

            PrimaryButton = AssistedUiFactory.AtlasButton(
                surface.transform,
                "Assisted primary action",
                "Try supported questions",
                out var primaryLabel,
                out _);
            PrimaryLabel = primaryLabel;
            PrimaryLabel.alignment = TextAnchor.MiddleCenter;
            var primaryLabelRect = (RectTransform)PrimaryLabel.transform;
            primaryLabelRect.offsetMin = new Vector2(20f, 10f);
            primaryLabelRect.offsetMax = new Vector2(-20f, -10f);
            AssistedUiFactory.SetFixedRect(
                (RectTransform)PrimaryButton.transform,
                new Vector2(1f, 0f),
                new Vector2(1f, 0.5f),
                new Vector2(-74f, 58f),
                new Vector2(360f, 76f));
            PrimaryButton.onClick.AddListener(OnPrimaryAction);
        }

        private void BuildUnavailable(Transform parent)
        {
            _unavailableRoot = AssistedUiFactory.Rect(
                parent,
                "Assisted route unavailable",
                Vector2.zero,
                Vector2.one,
                Vector2.zero,
                Vector2.zero).gameObject;
            _unavailableText = AssistedUiFactory.Text(
                _unavailableRoot.transform,
                "Unavailable message",
                "The assisted route is unavailable. Your progress is safe.",
                34,
                AssistedPalette.Limestone,
                TextAnchor.MiddleCenter,
                FontStyle.Bold);
            AssistedUiFactory.SetFixedRect(
                (RectTransform)_unavailableText.transform,
                new Vector2(0.5f, 0.5f),
                new Vector2(0.5f, 0.5f),
                new Vector2(0f, 110f),
                new Vector2(1320f, 130f));
            _retryButton = AssistedUiFactory.AtlasButton(
                _unavailableRoot.transform,
                "Retry assisted route",
                "Retry",
                out _retryLabel,
                out _);
            _retryLabel.alignment = TextAnchor.MiddleCenter;
            AssistedUiFactory.SetFixedRect(
                (RectTransform)_retryButton.transform,
                new Vector2(0.5f, 0.5f),
                new Vector2(0.5f, 0.5f),
                new Vector2(-190f, -55f),
                new Vector2(320f, 76f));
            _retryButton.onClick.AddListener(RetryAsync);
            _returnToMapButton = AssistedUiFactory.AtlasButton(
                _unavailableRoot.transform,
                "Return from assisted route",
                "Return to map",
                out _returnLabel,
                out _);
            _returnLabel.alignment = TextAnchor.MiddleCenter;
            AssistedUiFactory.SetFixedRect(
                (RectTransform)_returnToMapButton.transform,
                new Vector2(0.5f, 0.5f),
                new Vector2(0.5f, 0.5f),
                new Vector2(190f, -55f),
                new Vector2(320f, 76f));
            _returnToMapButton.onClick.AddListener(() =>
            {
                if (Interactive)
                    ReturnToMapRequested?.Invoke();
            });
        }

        private static T AddPage<T>(Transform parent, string name) where T : MonoBehaviour
        {
            var gameObject = new GameObject(name, typeof(RectTransform), typeof(T));
            gameObject.transform.SetParent(parent, false);
            return gameObject.GetComponent<T>();
        }

        private void SelectOption(int optionIndex)
        {
            if (!Interactive || !_controller.AnswersRemainEditable || _controller.Batch == null)
                return;
            var item = _controller.Batch.Items[_controller.CurrentItemIndex];
            if (optionIndex < 0 || optionIndex >= item.Options.Count)
                return;
            _controller.SelectOption(item.ItemId, item.Options[optionIndex].OptionId);
        }

        private void SelectConfidence(Confidence confidence)
        {
            if (!Interactive || !_controller.AnswersRemainEditable || _controller.Batch == null)
                return;
            var item = _controller.Batch.Items[_controller.CurrentItemIndex];
            _controller.SelectConfidence(item.ItemId, confidence);
        }

        private void OnPrimaryAction()
        {
            if (!Interactive || _actionInFlight)
                return;
            switch (View)
            {
                case AssistedRouteView.WorkedExample:
                    BeginAdvance();
                    _controller.AcknowledgeWorkedExample();
                    break;
                case AssistedRouteView.Question:
                    if (!_controller.CanContinueCurrent)
                        return;
                    if (_controller.CurrentItemIndex < _controller.Batch.Items.Count - 1)
                    {
                        BeginAdvance();
                        _controller.GoToItem(_controller.CurrentItemIndex + 1);
                    }
                    else
                    {
                        SubmitAsync();
                    }
                    break;
                case AssistedRouteView.Feedback:
                    BeginAdvance();
                    _controller.AdvanceFeedback();
                    break;
            }
        }

        private async void SubmitAsync()
        {
            if (_actionInFlight)
                return;
            _actionInFlight = true;
            BeginAdvance();
            UpdateInteractivity();
            try
            {
                await _controller.SubmitAsync(_lifetime.Token);
            }
            catch (OperationCanceledException)
            {
                return;
            }
            finally
            {
                _actionInFlight = false;
            }
            if (this != null)
            {
                RenderFromController();
                UpdateInteractivity();
            }
        }

        private async void RetryAsync()
        {
            if (!Interactive || _actionInFlight)
                return;
            var retryCompletion = _controller.CanRetryCompletion;
            var recoverPreparation =
                !retryCompletion && _controller.CanRecoverPreparation;
            if (!retryCompletion && !recoverPreparation)
                return;

            _actionInFlight = true;
            BeginAdvance();
            UpdateInteractivity();
            try
            {
                if (retryCompletion)
                    await _controller.RetryCompletionAsync(_lifetime.Token);
                else
                    await _controller.RecoverPreparationAsync(_lifetime.Token);
            }
            catch (OperationCanceledException)
            {
                return;
            }
            finally
            {
                _actionInFlight = false;
            }
            if (this != null)
            {
                RenderFromController();
                UpdateInteractivity();
            }
        }

        private void ReadCurrentAloud()
        {
            if (!Interactive)
                return;
            string text;
            switch (View)
            {
                case AssistedRouteView.WorkedExample:
                    text = WorkedExamplePage.BuildSpeechText();
                    break;
                case AssistedRouteView.Question:
                    text = QuestionPage.BuildSpeechText();
                    break;
                case AssistedRouteView.Feedback:
                    text = FeedbackPage.BuildSpeechText();
                    break;
                default:
                    return;
            }
            if (!string.IsNullOrWhiteSpace(text))
                _speech.Speak(text);
        }

        private void OnControllerChanged()
        {
            var previous = View;
            RenderFromController();
            if (_openingComplete &&
                previous != View &&
                (previous == AssistedRouteView.Submitting ||
                 View == AssistedRouteView.Unavailable))
            {
                BeginAdvance();
            }
        }

        private void RenderFromController()
        {
            switch (_controller.State)
            {
                case AssistedRouteState.WorkedExample:
                    RenderWorkedExample();
                    break;
                case AssistedRouteState.Answering:
                    RenderQuestion();
                    break;
                case AssistedRouteState.Submitting:
                    RenderSubmitting();
                    break;
                case AssistedRouteState.Revealed:
                    RenderFeedback();
                    break;
                case AssistedRouteState.Complete:
                    RenderComplete();
                    break;
                case AssistedRouteState.Failed:
                    RenderUnavailable();
                    break;
                default:
                    RenderLoading();
                    break;
            }
            ConfigureFocusOrder();
            UpdateInteractivity();
        }

        private void RenderWorkedExample()
        {
            View = AssistedRouteView.WorkedExample;
            SetSections(true, false, false, false, false, false);
            WorkedExamplePage.Bind(_controller.Batch.WorkedExample);
            PrimaryLabel.text = "Try supported questions";
        }

        private void RenderQuestion()
        {
            View = AssistedRouteView.Question;
            SetSections(false, true, false, false, false, false);
            var index = _controller.CurrentItemIndex;
            QuestionPage.Bind(
                _controller.Batch.Items[index],
                _controller.Answers[index],
                index,
                _controller.Batch.Items.Count);
            PrimaryLabel.text = index == _controller.Batch.Items.Count - 1
                ? "Submit assisted route"
                : "Continue";
        }

        private void RenderSubmitting()
        {
            View = AssistedRouteView.Submitting;
            SetSections(false, false, false, true, false, false);
            _submittingText.text = "Securing the supported route…";
        }

        private void RenderFeedback()
        {
            var feedback = _controller.CurrentFeedback;
            if (feedback == null)
            {
                RenderComplete();
                return;
            }
            View = AssistedRouteView.Feedback;
            SetSections(false, false, true, false, false, false);
            var index = FindFeedbackIndex(feedback);
            FeedbackPage.Bind(feedback, index, _controller.FinalResult.Items.Count);
            PrimaryLabel.text = index == _controller.FinalResult.Items.Count - 1
                ? "Complete assisted route"
                : "Next supported method";
        }

        private void RenderComplete()
        {
            View = AssistedRouteView.Complete;
            SetSections(false, false, false, false, true, false);
            if (!_completionRaised)
            {
                _completionRaised = true;
                Completed?.Invoke();
            }
        }

        private void RenderUnavailable()
        {
            View = AssistedRouteView.Unavailable;
            SetSections(false, false, false, false, false, true);
            _retryButton.gameObject.SetActive(
                _controller.CanRecoverPreparation || _controller.CanRetryCompletion);
            _returnToMapButton.gameObject.SetActive(true);
        }

        private void RenderLoading()
        {
            View = AssistedRouteView.Loading;
            SetSections(false, false, false, true, false, false);
            _submittingText.text = "Preparing the assisted route…";
        }

        private void SetSections(
            bool worked,
            bool question,
            bool feedback,
            bool submitting,
            bool complete,
            bool unavailable)
        {
            WorkedExamplePage.gameObject.SetActive(worked);
            QuestionPage.gameObject.SetActive(question);
            FeedbackPage.gameObject.SetActive(feedback);
            _submittingRoot.SetActive(submitting);
            CompletionText.gameObject.SetActive(complete);
            _unavailableRoot.SetActive(unavailable);
            ReadAloudButton.gameObject.SetActive(worked || question || feedback);
            PrimaryButton.gameObject.SetActive(worked || question || feedback);
        }

        private int FindFeedbackIndex(AssistedItemResult result)
        {
            for (var index = 0; index < _controller.FinalResult.Items.Count; index++)
            {
                if (ReferenceEquals(_controller.FinalResult.Items[index], result) ||
                    _controller.FinalResult.Items[index].ItemId == result.ItemId)
                {
                    return index;
                }
            }
            return 0;
        }

        private void ConfigureFocusOrder()
        {
            _focusOrder.Clear();
            switch (View)
            {
                case AssistedRouteView.WorkedExample:
                    _focusOrder.Add(ReadAloudButton);
                    _focusOrder.Add(PrimaryButton);
                    break;
                case AssistedRouteView.Question:
                    _focusOrder.AddRange(QuestionPage.OptionButtons);
                    _focusOrder.AddRange(QuestionPage.ConfidenceControl.Buttons);
                    _focusOrder.Add(ReadAloudButton);
                    _focusOrder.Add(PrimaryButton);
                    break;
                case AssistedRouteView.Feedback:
                    _focusOrder.Add(ReadAloudButton);
                    _focusOrder.Add(PrimaryButton);
                    break;
                case AssistedRouteView.Unavailable:
                    if (_retryButton.gameObject.activeSelf)
                        _focusOrder.Add(_retryButton);
                    _focusOrder.Add(_returnToMapButton);
                    break;
            }
            for (var index = 0; index < _focusOrder.Count; index++)
            {
                var previous = _focusOrder[(index - 1 + _focusOrder.Count) % _focusOrder.Count];
                var next = _focusOrder[(index + 1) % _focusOrder.Count];
                _focusOrder[index].navigation = new Navigation
                {
                    mode = Navigation.Mode.Explicit,
                    selectOnUp = previous,
                    selectOnDown = next,
                    selectOnLeft = previous,
                    selectOnRight = next
                };
            }
        }

        private void UpdateInteractivity()
        {
            Interactive = _openingComplete &&
                          !_transitioning &&
                          !_actionInFlight &&
                          View != AssistedRouteView.Loading &&
                          View != AssistedRouteView.Submitting &&
                          View != AssistedRouteView.Complete;
            _surfaceGroup.interactable = Interactive;
            _surfaceGroup.blocksRaycasts = Interactive;
            ReadAloudButton.interactable = Interactive;
            QuestionPage.SetInteractable(
                Interactive &&
                View == AssistedRouteView.Question &&
                _controller.AnswersRemainEditable);
            PrimaryButton.interactable = Interactive &&
                (View != AssistedRouteView.Question || _controller.CanContinueCurrent);
            _retryButton.interactable =
                Interactive && _retryButton.gameObject.activeSelf;
            _returnToMapButton.interactable =
                Interactive && _returnToMapButton.gameObject.activeSelf;
        }

        private void BeginAdvance()
        {
            if (!_openingComplete)
                return;
            _transitioning = true;
            _motionStartedAt = Time.unscaledTime;
            ApplyMotion(AssistedRouteMotionEvaluator.EvaluateAdvance(
                0f,
                _settings.ReducedMotion));
            UpdateInteractivity();
        }

        private void Update()
        {
            if (!_openingComplete)
            {
                var state = AssistedRouteMotionEvaluator.EvaluateOpening(
                    Time.unscaledTime - _motionStartedAt,
                    _settings.ReducedMotion);
                ApplyMotion(state);
                if (state.Interactive)
                {
                    _openingComplete = true;
                    UpdateInteractivity();
                    FocusCurrentView();
                }
                return;
            }
            if (!_transitioning)
                return;
            var transition = AssistedRouteMotionEvaluator.EvaluateAdvance(
                Time.unscaledTime - _motionStartedAt,
                _settings.ReducedMotion);
            ApplyMotion(transition);
            if (transition.Interactive)
            {
                _transitioning = false;
                UpdateInteractivity();
                FocusCurrentView();
            }
        }

        private void ApplyMotion(AssistedRouteMotionState state)
        {
            _routeLineRect.localScale = new Vector3(state.RouteProgress, 1f, 1f);
            var color = AssistedPalette.MeridianGold;
            color.a = state.LineOpacity;
            _routeLine.color = color;
            _surfaceGroup.alpha = state.SurfaceOpacity;
        }

        private void FocusCurrentView()
        {
            if (!Interactive || _focusOrder.Count == 0 || EventSystem.current == null)
                return;
            var target = _focusOrder[0];
            if (target != null && target.gameObject.activeInHierarchy)
                EventSystem.current.SetSelectedGameObject(target.gameObject);
        }

        private void OnDestroy()
        {
            if (_controller != null)
                _controller.Changed -= OnControllerChanged;
            if (_lifetime != null)
            {
                _lifetime.Cancel();
                _lifetime.Dispose();
                _lifetime = null;
            }
            if (_ownedEventSystem != null && _ownedEventSystem.gameObject != null)
                Destroy(_ownedEventSystem.gameObject);
            if (_addedExternalInputModule != null)
            {
                _addedExternalInputModule.enabled = false;
                Destroy(_addedExternalInputModule);
                _addedExternalInputModule = null;
            }
        }
    }
}
