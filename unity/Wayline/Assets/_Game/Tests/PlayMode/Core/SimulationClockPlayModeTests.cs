using NUnit.Framework;
using Wayline.Core;

namespace Wayline.Tests.Core
{
    public sealed class SimulationClockPlayModeTests
    {
        [Test]
        public void ClockConsumesUnscaledPresentationFramesWithoutEngineTime()
        {
            var clock = new SimulationClock(60, 4);

            Assert.That(clock.ConsumeFrame(1.0 / 30.0), Is.EqualTo(2));
            Assert.That(clock.Tick, Is.EqualTo(2));
        }
    }
}
