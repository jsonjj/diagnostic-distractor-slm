using System.Collections;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.UI;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;
using Wayline.Flow;
using Wayline.Flow.Unity;

namespace Wayline.Tests.Flow
{
    public sealed class VerticalSlicePointerInputTests : InputTestFixture
    {
        [UnityTest]
        public IEnumerator TitleButtonRespondsToPointerInput()
        {
            var mouse = InputSystem.AddDevice<Mouse>();
            var operation = SceneManager.LoadSceneAsync(
                "Arena_Graybox",
                LoadSceneMode.Single);
            while (!operation.isDone)
                yield return null;
            yield return null;

            var slice = Object.FindFirstObjectByType<VerticalSliceRuntimeBootstrap>();
            Assert.That(slice, Is.Not.Null);
            Assert.That(slice.Flow.State, Is.EqualTo(FlowState.Title));
            Assert.That(EventSystem.current, Is.Not.Null,
                "The title screen needs an EventSystem before any UI can receive input.");
            Assert.That(
                EventSystem.current.GetComponent<InputSystemUIInputModule>(),
                Is.Not.Null,
                "Wayline uses the new Input System, so its EventSystem needs the matching UI module.");

            Canvas.ForceUpdateCanvases();
            var rect = (RectTransform)slice.EnterMapButton.transform;
            var screenPoint = RectTransformUtility.WorldToScreenPoint(
                null,
                rect.TransformPoint(rect.rect.center));
            Set(mouse.position, screenPoint);
            yield return null;
            Press(mouse.leftButton);
            yield return null;
            Release(mouse.leftButton);
            yield return null;

            Assert.That(slice.Flow.State, Is.EqualTo(FlowState.Map),
                "A real pointer click on ENTER VALUEHOLD must advance to the route map.");
        }

        [UnityTest]
        public IEnumerator TitleButtonRespondsToKeyboardSubmit()
        {
            var keyboard = InputSystem.AddDevice<Keyboard>();
            var operation = SceneManager.LoadSceneAsync(
                "Arena_Graybox",
                LoadSceneMode.Single);
            while (!operation.isDone)
                yield return null;
            yield return null;

            var slice = Object.FindFirstObjectByType<VerticalSliceRuntimeBootstrap>();
            Assert.That(slice, Is.Not.Null);
            Assert.That(slice.Flow.State, Is.EqualTo(FlowState.Title));
            Assert.That(
                EventSystem.current.currentSelectedGameObject,
                Is.EqualTo(slice.EnterMapButton.gameObject),
                "The only visible title action must receive initial keyboard and controller focus.");

            Press(keyboard.enterKey);
            yield return null;
            Release(keyboard.enterKey);
            yield return null;

            Assert.That(slice.Flow.State, Is.EqualTo(FlowState.Map),
                "Submitting the focused ENTER VALUEHOLD action must advance to the route map.");
        }
    }
}
