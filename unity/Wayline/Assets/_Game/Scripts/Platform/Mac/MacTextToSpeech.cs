using System;
using System.Runtime.InteropServices;
using Wayline.UI;

namespace Wayline.Platform.Mac
{
    public sealed class MacTextToSpeech : IQuizTextToSpeech
    {
#if UNITY_EDITOR_OSX || UNITY_STANDALONE_OSX
        [DllImport("WaylineTextToSpeech", EntryPoint = "WaylineSpeakUtf8")]
        private static extern void WaylineSpeakUtf8(
            [MarshalAs(UnmanagedType.LPUTF8Str)] string text);
#endif

        public void Speak(string text)
        {
            if (string.IsNullOrWhiteSpace(text))
                return;
#if UNITY_EDITOR_OSX || UNITY_STANDALONE_OSX
            try
            {
                WaylineSpeakUtf8(text);
            }
            catch (DllNotFoundException)
            {
                // Accessibility must fail closed without logging learner content.
            }
            catch (EntryPointNotFoundException)
            {
                // A damaged plug-in never exposes or substitutes quiz content.
            }
#endif
        }
    }
}
