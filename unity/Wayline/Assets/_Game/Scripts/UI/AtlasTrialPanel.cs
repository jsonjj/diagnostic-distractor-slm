using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem.UI;
using UnityEngine.UI;
using Wayline.Learning.Contracts;
using Wayline.Learning.Quiz;

namespace Wayline.UI
{
    public enum AtlasTrialView
    {
        Loading,
        Answering,
        WrongCount,
        Reviewing,
        FinalFeedback,
        Complete,
        Unavailable
    }

    public sealed class AtlasTrialPanel : MonoBehaviour
    {
        private QuizController _controller;
        private AtlasTrialSettings _settings;
        private IQuizTextToSpeech _speech;
        private CancellationTokenSource _lifetime;
        private bool _actionInFlight;
        private bool _retryInFlight;
        private bool _runtimeUnavailable;
        private int _primaryBlockedUntilFrame = -1;
        private float _textScale;
        private GameObject _unavailableRoot;
        private EventSystem _ownedEventSystem;
        private InputSystemUIInputModule _addedExternalInputModule;
        private readonly List<BaseInputModule> _disabledExternalInputModules =
            new List<BaseInputModule>();
        private Text _readAloudLabel;
        private Text _retryLabel;
        private Text _returnToMapLabel;
        private int _lastQuestionIndex = -1;
        private AtlasTrialView _lastQuestionView = AtlasTrialView.Loading;
        private float _openingStartedAt;
        private bool _openingInteractive;

        public event Action RetryRequested;

        public event Action ReturnToMapRequested;

        public AtlasTrialView View { get; private set; }

        public bool Interactive =>
            _openingInteractive &&
            !_actionInFlight &&
            !_retryInFlight &&
            !_runtimeUnavailable &&
            !_controller.HasFailure;

        public QuestionPage QuestionPage { get; private set; }

        public WrongCountPanel WrongCountPanel { get; private set; }

        public FinalFeedbackPanel FinalFeedbackPanel { get; private set; }

        public Button PrimaryButton { get; private set; }

        public Text PrimaryLabel { get; private set; }

        public Button ReadAloudButton { get; private set; }

        public Text UnavailableText { get; private set; }

        public Button RetryButton { get; private set; }

        public Button ReturnToMapButton { get; private set; }

        public static AtlasTrialPanel Create(
            QuizController controller,
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
                "Atlas Route Trial",
                typeof(RectTransform),
                typeof(Canvas),
                typeof(CanvasScaler),
                typeof(GraphicRaycaster),
                typeof(AtlasTrialPanel));
            var canvas = root.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            canvas.sortingOrder = 500;
            var scaler = root.GetComponent<CanvasScaler>();
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(1920f, 1080f);
            scaler.screenMatchMode = CanvasScaler.ScreenMatchMode.MatchWidthOrHeight;
            scaler.matchWidthOrHeight = 0.5f;

            var panel = root.GetComponent<AtlasTrialPanel>();
            panel.Initialize(controller, settings, speech);
            return panel;
        }

        public void ApplyTextScale(float scale)
        {
            if (float.IsNaN(scale) || float.IsInfinity(scale))
                throw new ArgumentOutOfRangeException(nameof(scale));
            _textScale = Mathf.Clamp(scale, 1f, 1.5f);
            QuestionPage.ApplyTextScale(_textScale);
            RenderFromController();
            Canvas.ForceUpdateCanvases();
        }

        public void ShowRuntimeUnavailable()
        {
            _runtimeUnavailable = true;
            _actionInFlight = false;
            RenderUnavailable();
        }

        public void CompleteRuntimeRetry(bool runtimeAvailable)
        {
            _retryInFlight = false;
            _runtimeUnavailable = !runtimeAvailable;
            RenderFromController();
        }

        private void Initialize(
            QuizController controller,
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
            _openingStartedAt = Time.unscaledTime;
            QuestionPage.ApplyOpeningMotion(
                AtlasMotionEvaluator.EvaluateOpening(0f, _settings.ReducedMotion));
            QuestionPage.SetInteractive(false);
            _controller.Changed += RenderFromController;
            RenderFromController();
            Canvas.ForceUpdateCanvases();
        }

        private void EnsureEventSystem()
        {
            if (EventSystem.current != null)
            {
                if (EventSystem.current.GetComponent<InputSystemUIInputModule>() != null)
                    return;
                foreach (var module in EventSystem.current.GetComponents<BaseInputModule>())
                {
                    if (!module.enabled)
                        continue;
                    module.enabled = false;
                    _disabledExternalInputModules.Add(module);
                }
                _addedExternalInputModule =
                    EventSystem.current.gameObject.AddComponent<InputSystemUIInputModule>();
                return;
            }
            var eventObject = new GameObject(
                "Atlas Event System",
                typeof(EventSystem),
                typeof(InputSystemUIInputModule));
            eventObject.transform.SetParent(transform, false);
            _ownedEventSystem = eventObject.GetComponent<EventSystem>();
        }

        private void BuildVisualTree()
        {
            var background = AtlasUiFactory.Image(
                transform,
                "Night ink veil",
                new Color(
                    AtlasPalette.NightInk.r,
                    AtlasPalette.NightInk.g,
                    AtlasPalette.NightInk.b,
                    0.96f));
            background.raycastTarget = true;

            var structuralBand = AtlasUiFactory.Image(
                transform,
                "Atlas structural band",
                new Color(
                    AtlasPalette.Lapis.r,
                    AtlasPalette.Lapis.g,
                    AtlasPalette.Lapis.b,
                    0.66f));
            var bandRect = (RectTransform)structuralBand.transform;
            bandRect.anchorMin = new Vector2(0f, 0.13f);
            bandRect.anchorMax = new Vector2(1f, 0.87f);
            bandRect.offsetMin = new Vector2(36f, 0f);
            bandRect.offsetMax = new Vector2(-36f, 0f);

            var questionObject = new GameObject(
                "Question page",
                typeof(RectTransform),
                typeof(QuestionPage));
            questionObject.transform.SetParent(transform, false);
            QuestionPage = questionObject.GetComponent<QuestionPage>();
            QuestionPage.Initialize(
                _settings.QuestionHeader,
                SelectOption,
                SelectConfidence);

            var wrongObject = new GameObject(
                "Wrong count panel",
                typeof(RectTransform),
                typeof(WrongCountPanel));
            wrongObject.transform.SetParent(transform, false);
            WrongCountPanel = wrongObject.GetComponent<WrongCountPanel>();
            WrongCountPanel.Initialize();

            var finalObject = new GameObject(
                "Final feedback panel",
                typeof(RectTransform),
                typeof(FinalFeedbackPanel));
            finalObject.transform.SetParent(transform, false);
            FinalFeedbackPanel = finalObject.GetComponent<FinalFeedbackPanel>();
            FinalFeedbackPanel.Initialize();

            BuildUnavailableView();

            ReadAloudButton = AtlasUiFactory.AtlasButton(
                transform,
                "Read aloud",
                "Read aloud",
                out _readAloudLabel,
                out _);
            _readAloudLabel.alignment = TextAnchor.MiddleCenter;
            ((RectTransform)_readAloudLabel.transform).offsetMin = new Vector2(12f, 6f);
            ((RectTransform)_readAloudLabel.transform).offsetMax = new Vector2(-12f, -6f);
            PlaceButton(
                ReadAloudButton,
                Vector2.one,
                new Vector2(-76f, -62f),
                new Vector2(250f, 56f),
                new Vector2(1f, 0.5f));
            ReadAloudButton.onClick.AddListener(ReadCurrentAloud);

            PrimaryButton = AtlasUiFactory.AtlasButton(
                transform,
                "Primary action",
                "Continue",
                out var primaryLabel,
                out _);
            PrimaryLabel = primaryLabel;
            PrimaryLabel.alignment = TextAnchor.MiddleCenter;
            var primaryLabelRect = (RectTransform)PrimaryLabel.transform;
            primaryLabelRect.offsetMin = new Vector2(18f, 8f);
            primaryLabelRect.offsetMax = new Vector2(-18f, -8f);
            PlaceButton(
                PrimaryButton,
                new Vector2(1f, 0f),
                new Vector2(-76f, 58f),
                new Vector2(330f, 72f),
                new Vector2(1f, 0.5f));
            PrimaryButton.onClick.AddListener(OnPrimaryAction);

            QuestionPage.ConfigureFocusOrder(ReadAloudButton, PrimaryButton);
        }

        private void BuildUnavailableView()
        {
            var root = AtlasUiFactory.Rect(
                transform,
                "Unavailable view",
                Vector2.zero,
                Vector2.one,
                Vector2.zero,
                Vector2.zero);
            _unavailableRoot = root.gameObject;
            UnavailableText = AtlasUiFactory.Text(
                root,
                "Unavailable explanation",
                _settings.UnavailableMessage,
                36,
                AtlasPalette.Limestone,
                TextAnchor.MiddleCenter,
                FontStyle.Bold);
            var unavailableRect = (RectTransform)UnavailableText.transform;
            unavailableRect.anchorMin = unavailableRect.anchorMax = new Vector2(0.5f, 0.5f);
            unavailableRect.pivot = new Vector2(0.5f, 0.5f);
            unavailableRect.anchoredPosition = new Vector2(0f, 105f);
            unavailableRect.sizeDelta = new Vector2(1320f, 150f);

            RetryButton = AtlasUiFactory.AtlasButton(
                root,
                "Retry runtime",
                "Retry",
                out _retryLabel,
                out _);
            _retryLabel.alignment = TextAnchor.MiddleCenter;
            ((RectTransform)_retryLabel.transform).offsetMin = new Vector2(18f, 8f);
            ((RectTransform)_retryLabel.transform).offsetMax = new Vector2(-18f, -8f);
            PlaceButton(
                RetryButton,
                new Vector2(0.5f, 0.5f),
                new Vector2(-190f, -65f),
                new Vector2(320f, 76f),
                new Vector2(0.5f, 0.5f));
            RetryButton.onClick.AddListener(RetryRuntimeOrSnapshot);

            ReturnToMapButton = AtlasUiFactory.AtlasButton(
                root,
                "Return to map",
                "Return to map",
                out _returnToMapLabel,
                out _);
            _returnToMapLabel.alignment = TextAnchor.MiddleCenter;
            ((RectTransform)_returnToMapLabel.transform).offsetMin = new Vector2(18f, 8f);
            ((RectTransform)_returnToMapLabel.transform).offsetMax = new Vector2(-18f, -8f);
            PlaceButton(
                ReturnToMapButton,
                new Vector2(0.5f, 0.5f),
                new Vector2(190f, -65f),
                new Vector2(320f, 76f),
                new Vector2(0.5f, 0.5f));
            ReturnToMapButton.onClick.AddListener(() => ReturnToMapRequested?.Invoke());
            ConfigureUnavailableNavigation();
        }

        private void SelectOption(int optionIndex)
        {
            if (_actionInFlight || !_controller.AnswersRemainEditable || _controller.Batch == null)
                return;
            var item = _controller.Batch.Items[_controller.CurrentItemIndex];
            if (optionIndex < 0 || optionIndex >= item.Options.Count)
                return;
            _controller.SelectOption(item.ItemId, item.Options[optionIndex].OptionId);
        }

        private void SelectConfidence(Confidence confidence)
        {
            if (_actionInFlight || !_controller.AnswersRemainEditable || _controller.Batch == null)
                return;
            var item = _controller.Batch.Items[_controller.CurrentItemIndex];
            _controller.SelectConfidence(item.ItemId, confidence);
        }

        private void OnPrimaryAction()
        {
            if (_actionInFlight || _runtimeUnavailable ||
                Time.frameCount < _primaryBlockedUntilFrame)
                return;
            if (_controller.IsCountMomentVisible)
            {
                GatePrimaryTransition();
                _controller.AcknowledgeWrongCount();
                return;
            }
            if (_controller.State == QuizState.Revealed)
            {
                GatePrimaryTransition();
                _controller.AdvanceFinalFeedback();
                return;
            }
            if (_controller.State != QuizState.Answering &&
                _controller.State != QuizState.Reviewing)
            {
                return;
            }
            if (!_controller.CanContinueCurrent)
                return;

            if (_controller.CurrentItemIndex < _controller.Batch.Items.Count - 1)
            {
                GatePrimaryTransition();
                _controller.GoToItem(_controller.CurrentItemIndex + 1);
                return;
            }
            GatePrimaryTransition();
            SubmitCurrentPassAsync();
        }

        private async void RetryRuntimeOrSnapshot()
        {
            if (_retryInFlight)
                return;
            _retryInFlight = true;
            RenderUnavailable();
            if (!_runtimeUnavailable && _controller.CanRecover &&
                _controller.Batch != null)
            {
                try
                {
                    await _controller.RecoverAsync(_lifetime.Token);
                }
                catch (OperationCanceledException)
                {
                    return;
                }
                finally
                {
                    _retryInFlight = false;
                }
                if (this != null)
                    RenderFromController();
                return;
            }

            if (RetryRequested != null)
            {
                RetryRequested.Invoke();
                return;
            }
            _retryInFlight = false;
            RenderUnavailable();
        }

        private async void SubmitCurrentPassAsync()
        {
            if (_actionInFlight)
                return;
            _actionInFlight = true;
            RenderFromController();
            try
            {
                if (_controller.State == QuizState.Answering)
                    await _controller.SubmitInitialAsync(_lifetime.Token);
                else if (_controller.State == QuizState.Reviewing)
                    await _controller.SubmitRevisionAsync(_lifetime.Token);
            }
            catch (OperationCanceledException)
            {
                return;
            }
            catch (Exception)
            {
                ShowRuntimeUnavailable();
                return;
            }
            finally
            {
                _actionInFlight = false;
            }
            if (this != null)
                RenderFromController();
        }

        private void ReadCurrentAloud()
        {
            string text;
            switch (View)
            {
                case AtlasTrialView.Answering:
                case AtlasTrialView.Reviewing:
                    text = QuestionPage.BuildSpeechText();
                    break;
                case AtlasTrialView.FinalFeedback:
                    text = FinalFeedbackPanel.BuildSpeechText();
                    break;
                default:
                    return;
            }
            if (!string.IsNullOrWhiteSpace(text))
                _speech.Speak(text);
        }

        private void RenderFromController()
        {
            if (_runtimeUnavailable)
            {
                RenderUnavailable();
                return;
            }
            if (_controller.HasFailure)
            {
                RenderUnavailable();
                return;
            }
            if (_controller.Batch == null)
            {
                RenderLoading();
                return;
            }
            if (_controller.IsCountMomentVisible)
            {
                if (!_controller.WrongCount.HasValue ||
                    _controller.WrongCount.Value < 0 ||
                    _controller.WrongCount.Value > _controller.Batch.ItemCount)
                {
                    RenderUnavailable();
                    return;
                }
                RenderWrongCount();
                return;
            }

            switch (_controller.State)
            {
                case QuizState.Answering:
                case QuizState.SubmittingInitial:
                    RenderQuestion(false);
                    break;
                case QuizState.Reviewing:
                case QuizState.SubmittingRevision:
                    RenderQuestion(true);
                    break;
                case QuizState.Revealed:
                    RenderFinalFeedback();
                    break;
                case QuizState.Complete:
                    RenderComplete();
                    break;
                default:
                    RenderLoading();
                    break;
            }
        }

        private void RenderQuestion(bool reviewing)
        {
            var targetView = reviewing
                ? AtlasTrialView.Reviewing
                : AtlasTrialView.Answering;
            var itemIndex = _controller.CurrentItemIndex;
            var shouldMoveFocus =
                targetView != _lastQuestionView || itemIndex != _lastQuestionIndex;
            View = targetView;
            SetMainSections(true, false, false, false);
            QuestionPage.Bind(
                _controller.Batch.Items[itemIndex],
                _controller.Answers[itemIndex],
                itemIndex,
                _controller.Batch.ItemCount,
                reviewing);
            QuestionPage.ApplyTextScale(_textScale);
            QuestionPage.SetInteractive(_openingInteractive);
            QuestionPage.ConfigureFocusOrder(ReadAloudButton, PrimaryButton);
            ScaleGlobalActionLabels();
            ReadAloudButton.gameObject.SetActive(true);
            AtlasUiFactory.SetInteractable(ReadAloudButton, _openingInteractive);
            PrimaryButton.gameObject.SetActive(true);
            PrimaryLabel.text = PrimaryActionText(reviewing);
            PrimaryLabel.fontSize = Mathf.RoundToInt(28f * _textScale);
            AtlasUiFactory.SetInteractable(
                PrimaryButton,
                _openingInteractive && !_actionInFlight && _controller.CanContinueCurrent);
            _lastQuestionView = targetView;
            _lastQuestionIndex = itemIndex;
            if (shouldMoveFocus && _openingInteractive && EventSystem.current != null)
            {
                EventSystem.current.SetSelectedGameObject(
                    QuestionPage.OptionButtons[0].gameObject);
            }
        }

        private string PrimaryActionText(bool reviewing)
        {
            var isLast = _controller.CurrentItemIndex == _controller.Batch.ItemCount - 1;
            if (reviewing)
                return isLast ? "Finish review" : "Continue review";
            return isLast ? "Submit answers" : "Continue";
        }

        private void RenderWrongCount()
        {
            View = AtlasTrialView.WrongCount;
            SetMainSections(false, true, false, false);
            WrongCountPanel.Bind(
                _controller.WrongCount.Value,
                _controller.Batch.ItemCount,
                _textScale,
                _settings.ReducedMotion);
            ReadAloudButton.gameObject.SetActive(false);
            PrimaryButton.gameObject.SetActive(true);
            var zeroWrong = _controller.WrongCount.Value == 0;
            PrimaryLabel.text = zeroWrong ? "See trusted methods" : "Review answers";
            PrimaryLabel.fontSize = Mathf.RoundToInt(28f * _textScale);
            AtlasUiFactory.SetInteractable(PrimaryButton, !_actionInFlight);
            MoveFocusTo(PrimaryButton);
        }

        private void RenderFinalFeedback()
        {
            var result = _controller.CurrentFeedback;
            if (result == null)
            {
                RenderComplete();
                return;
            }
            View = AtlasTrialView.FinalFeedback;
            SetMainSections(false, false, true, false);
            var resultIndex = FindResultIndex(result);
            var item = _controller.Batch.Items.First(value => value.ItemId == result.ItemId);
            FinalFeedbackPanel.Bind(
                result,
                item,
                resultIndex,
                _controller.FinalResult.Items.Count,
                _textScale);
            ConfigureFinalNavigation();
            ScaleGlobalActionLabels();
            ReadAloudButton.gameObject.SetActive(true);
            AtlasUiFactory.SetInteractable(ReadAloudButton, true);
            PrimaryButton.gameObject.SetActive(true);
            PrimaryLabel.text =
                _settings.RequiresCompletionBeforeMap &&
                _controller.FinalActionLabel == "Complete route trial"
                    ? "Return to map"
                    : _controller.FinalActionLabel;
            PrimaryLabel.fontSize = Mathf.RoundToInt(28f * _textScale);
            AtlasUiFactory.SetInteractable(PrimaryButton, !_actionInFlight);
            MoveFocusTo(PrimaryButton);
        }

        private void RenderComplete()
        {
            View = AtlasTrialView.Complete;
            SetMainSections(false, false, true, false);
            FinalFeedbackPanel.ShowComplete(_textScale, _settings.CompletionTitle);
            ReadAloudButton.gameObject.SetActive(false);
            PrimaryButton.gameObject.SetActive(false);
        }

        private void RenderLoading()
        {
            View = AtlasTrialView.Loading;
            SetMainSections(false, false, false, true);
            UnavailableText.text = _settings.LoadingMessage;
            UnavailableText.fontSize = Mathf.RoundToInt(36f * _textScale);
            ScaleGlobalActionLabels();
            RetryButton.gameObject.SetActive(false);
            ReturnToMapButton.gameObject.SetActive(false);
            ReadAloudButton.gameObject.SetActive(false);
            PrimaryButton.gameObject.SetActive(false);
        }

        private void RenderUnavailable()
        {
            View = AtlasTrialView.Unavailable;
            SetMainSections(false, false, false, true);
            UnavailableText.text = _settings.UnavailableMessage;
            UnavailableText.fontSize = Mathf.RoundToInt(36f * _textScale);
            ScaleGlobalActionLabels();
            RetryButton.gameObject.SetActive(true);
            ReturnToMapButton.gameObject.SetActive(
                !_settings.RequiresCompletionBeforeMap);
            AtlasUiFactory.SetInteractable(RetryButton, !_retryInFlight);
            AtlasUiFactory.SetInteractable(
                ReturnToMapButton,
                !_settings.RequiresCompletionBeforeMap);
            ReadAloudButton.gameObject.SetActive(false);
            PrimaryButton.gameObject.SetActive(false);
            MoveFocusTo(
                _retryInFlight && !_settings.RequiresCompletionBeforeMap
                    ? ReturnToMapButton
                    : RetryButton);
        }

        private int FindResultIndex(FinalQuizItemResult result)
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

        private void SetMainSections(
            bool question,
            bool wrongCount,
            bool finalFeedback,
            bool unavailable)
        {
            QuestionPage.gameObject.SetActive(question);
            WrongCountPanel.gameObject.SetActive(wrongCount);
            FinalFeedbackPanel.gameObject.SetActive(finalFeedback);
            _unavailableRoot.SetActive(unavailable);
            if (!unavailable)
            {
                RetryButton.gameObject.SetActive(false);
                ReturnToMapButton.gameObject.SetActive(false);
            }
        }

        private static void PlaceButton(
            Button button,
            Vector2 anchor,
            Vector2 position,
            Vector2 size,
            Vector2 pivot)
        {
            var rect = (RectTransform)button.transform;
            rect.anchorMin = rect.anchorMax = anchor;
            rect.pivot = pivot;
            rect.anchoredPosition = position;
            rect.sizeDelta = size;
        }

        private void ConfigureUnavailableNavigation()
        {
            if (_settings.RequiresCompletionBeforeMap)
            {
                RetryButton.navigation = new Navigation
                {
                    mode = Navigation.Mode.Explicit,
                    selectOnLeft = RetryButton,
                    selectOnRight = RetryButton,
                    selectOnUp = RetryButton,
                    selectOnDown = RetryButton
                };
                return;
            }

            var retryNavigation = new Navigation
            {
                mode = Navigation.Mode.Explicit,
                selectOnLeft = ReturnToMapButton,
                selectOnRight = ReturnToMapButton,
                selectOnUp = ReturnToMapButton,
                selectOnDown = ReturnToMapButton
            };
            RetryButton.navigation = retryNavigation;
            var returnNavigation = new Navigation
            {
                mode = Navigation.Mode.Explicit,
                selectOnLeft = RetryButton,
                selectOnRight = RetryButton,
                selectOnUp = RetryButton,
                selectOnDown = RetryButton
            };
            ReturnToMapButton.navigation = returnNavigation;
        }

        private void ConfigureFinalNavigation()
        {
            var activeScroll = FinalFeedbackPanel.ScrollButtons
                .Where(button => button.gameObject.activeSelf)
                .Cast<Selectable>()
                .ToList();
            var order = new List<Selectable>();
            order.AddRange(activeScroll);
            order.Add(ReadAloudButton);
            order.Add(PrimaryButton);
            for (var index = 0; index < order.Count; index++)
            {
                order[index].navigation = new Navigation
                {
                    mode = Navigation.Mode.Explicit,
                    selectOnUp = order[(index - 1 + order.Count) % order.Count],
                    selectOnDown = order[(index + 1) % order.Count],
                    selectOnLeft = order[(index - 1 + order.Count) % order.Count],
                    selectOnRight = order[(index + 1) % order.Count]
                };
            }
        }

        private void ScaleGlobalActionLabels()
        {
            var size = Mathf.RoundToInt(28f * _textScale);
            if (_readAloudLabel != null)
                _readAloudLabel.fontSize = size;
            if (_retryLabel != null)
                _retryLabel.fontSize = size;
            if (_returnToMapLabel != null)
                _returnToMapLabel.fontSize = size;
            if (PrimaryLabel != null)
                PrimaryLabel.fontSize = size;
        }

        private void GatePrimaryTransition()
        {
            _primaryBlockedUntilFrame = Time.frameCount + 1;
        }

        private static void MoveFocusTo(Selectable selectable)
        {
            if (EventSystem.current == null || selectable == null ||
                !selectable.gameObject.activeInHierarchy)
            {
                return;
            }
            if (EventSystem.current.currentSelectedGameObject != selectable.gameObject)
                EventSystem.current.SetSelectedGameObject(selectable.gameObject);
        }

        private void Update()
        {
            if (_openingInteractive || QuestionPage == null)
                return;
            var motion = AtlasMotionEvaluator.EvaluateOpening(
                Time.unscaledTime - _openingStartedAt,
                _settings.ReducedMotion);
            QuestionPage.ApplyOpeningMotion(motion);
            if (!motion.Interactive)
                return;
            _openingInteractive = true;
            QuestionPage.SetInteractive(true);
            RenderFromController();
            if ((View == AtlasTrialView.Answering || View == AtlasTrialView.Reviewing) &&
                QuestionPage.OptionButtons.Count > 0)
            {
                MoveFocusTo(QuestionPage.OptionButtons[0]);
            }
        }

        private void OnDestroy()
        {
            if (_controller != null)
                _controller.Changed -= RenderFromController;
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
            }
            foreach (var module in _disabledExternalInputModules)
            {
                if (module != null)
                    module.enabled = true;
            }
        }
    }
}
