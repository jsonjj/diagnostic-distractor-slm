using NUnit.Framework;
using Wayline.Core;

namespace Wayline.Tests.Core
{
    public sealed class SimulationClockTests
    {
        [Test]
        public void SixtyNormalFramesProduceSixtyTicks()
        {
            var clock = new SimulationClock(60, 4);
            var emitted = 0;
            for (var i = 0; i < 60; i++)
                emitted += clock.ConsumeFrame(1.0 / 60.0);
            Assert.That(clock.Tick, Is.EqualTo(60));
            Assert.That(emitted, Is.EqualTo(60));
        }

        [Test]
        public void HitchIsCappedAndDropped()
        {
            var clock = new SimulationClock(60, 4);
            Assert.That(clock.ConsumeFrame(1.0), Is.EqualTo(4));
            Assert.That(clock.WasClamped, Is.True);
            Assert.That(clock.ConsumeFrame(0.0), Is.Zero);
        }
    }
}
