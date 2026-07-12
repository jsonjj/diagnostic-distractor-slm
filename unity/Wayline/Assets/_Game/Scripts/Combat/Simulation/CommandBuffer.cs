using System;

namespace Wayline.Combat.Simulation
{
    public sealed class CommandBuffer
    {
        private readonly long[] _ticks;
        private readonly CombatCommand[] _commands;
        private int _head;
        private int _count;

        public CommandBuffer(int capacity)
        {
            if (capacity < 1 || capacity > 8)
                throw new ArgumentOutOfRangeException(nameof(capacity));

            _ticks = new long[capacity];
            _commands = new CombatCommand[capacity];
        }

        public int Count => _count;

        public bool TryEnqueue(long tick, CombatCommand command)
        {
            if (tick < 0)
                throw new ArgumentOutOfRangeException(nameof(tick));
            if (_count == _ticks.Length)
                return false;
            if (_count > 0)
            {
                var lastIndex = (_head + _count - 1) % _ticks.Length;
                if (tick <= _ticks[lastIndex])
                    return false;
            }

            var index = (_head + _count) % _ticks.Length;
            _ticks[index] = tick;
            _commands[index] = command;
            _count++;
            return true;
        }

        public bool TryDequeue(long tick, out CombatCommand command)
        {
            if (tick < 0)
                throw new ArgumentOutOfRangeException(nameof(tick));
            if (_count == 0 || _ticks[_head] != tick)
            {
                command = CombatCommand.None;
                return false;
            }

            command = _commands[_head];
            _head = (_head + 1) % _ticks.Length;
            _count--;
            return true;
        }
    }
}
