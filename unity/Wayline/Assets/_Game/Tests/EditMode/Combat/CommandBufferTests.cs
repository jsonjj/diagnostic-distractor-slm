using NUnit.Framework;
using Wayline.Combat.Simulation;

namespace Wayline.Tests.Combat
{
    public sealed class CommandBufferTests
    {
        [Test]
        public void BufferIsBoundedOrderedAndAllowsOneCommandPerTick()
        {
            var buffer = new CommandBuffer(2);

            Assert.That(buffer.TryEnqueue(10, CombatCommand.LightAttack), Is.True);
            Assert.That(buffer.TryEnqueue(10, CombatCommand.HeavyAttack), Is.False);
            Assert.That(buffer.TryEnqueue(11, CombatCommand.Guard), Is.True);
            Assert.That(buffer.TryEnqueue(12, CombatCommand.Parry), Is.False);

            Assert.That(buffer.TryDequeue(10, out var first), Is.True);
            Assert.That(first.Action, Is.EqualTo(CombatAction.LightAttack));
            Assert.That(buffer.TryDequeue(11, out var second), Is.True);
            Assert.That(second.IsGuarding, Is.True);
            Assert.That(buffer.Count, Is.Zero);
        }
    }
}
