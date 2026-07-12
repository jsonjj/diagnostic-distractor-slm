using System;

namespace Wayline.Core
{
    public sealed class SimulationClock
    {
        private readonly double _tickSeconds;
        private readonly int _maxCatchUpTicks;
        private double _accumulator;

        public SimulationClock(int ticksPerSecond, int maxCatchUpTicks)
        {
            if (ticksPerSecond != 60)
                throw new ArgumentOutOfRangeException(nameof(ticksPerSecond));
            if (maxCatchUpTicks < 1 || maxCatchUpTicks > 8)
                throw new ArgumentOutOfRangeException(nameof(maxCatchUpTicks));

            _tickSeconds = 1.0 / ticksPerSecond;
            _maxCatchUpTicks = maxCatchUpTicks;
        }

        public long Tick { get; private set; }

        public bool Paused { get; set; }

        public bool WasClamped { get; private set; }

        public int ConsumeFrame(double unscaledDeltaSeconds)
        {
            if (Paused)
                return 0;

            WasClamped = false;
            _accumulator += Math.Max(0.0, unscaledDeltaSeconds);

            var pending = (int)Math.Floor((_accumulator + 1e-12) / _tickSeconds);
            var emitted = Math.Min(pending, _maxCatchUpTicks);
            if (pending > _maxCatchUpTicks)
            {
                _accumulator = 0.0;
                WasClamped = true;
            }
            else
            {
                _accumulator -= emitted * _tickSeconds;
            }

            Tick += emitted;
            return emitted;
        }
    }
}
