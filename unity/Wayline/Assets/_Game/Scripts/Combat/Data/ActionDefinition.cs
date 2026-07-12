using System;

namespace Wayline.Combat.Data
{
    public enum ActionPhase
    {
        Rest = 0,
        Anticipation = 1,
        Commitment = 2,
        Contact = 3,
        FollowThrough = 4,
        Recovery = 5
    }

    public readonly struct TickRange
    {
        public TickRange(int start, int end)
        {
            if (start < 0)
                throw new ArgumentOutOfRangeException(nameof(start));
            if (end < start - 1)
                throw new ArgumentOutOfRangeException(nameof(end));

            Start = start;
            End = end;
        }

        public int Start { get; }

        public int End { get; }

        public bool IsEmpty => End < Start;

        public bool Contains(int tick)
        {
            return !IsEmpty && tick >= Start && tick <= End;
        }
    }

    public sealed class ActionDefinition
    {
        public ActionDefinition(
            string id,
            int totalTicks,
            TickRange anticipation,
            TickRange commitment,
            TickRange contact,
            TickRange followThrough,
            TickRange recovery,
            int damage,
            int guardDamage,
            int reachMillimeters,
            int hitStopTicks,
            TickRange invulnerability)
        {
            if (string.IsNullOrWhiteSpace(id))
                throw new ArgumentException("An action ID is required.", nameof(id));
            if (totalTicks < 1)
                throw new ArgumentOutOfRangeException(nameof(totalTicks));
            if (anticipation.IsEmpty || anticipation.Start != 0)
                throw new ArgumentException("Anticipation must begin at tick zero.", nameof(anticipation));
            if (commitment.IsEmpty || commitment.Start != anticipation.End + 1)
                throw new ArgumentException("Commitment must follow anticipation.", nameof(commitment));
            if (!contact.IsEmpty && contact.Start != commitment.End + 1)
                throw new ArgumentException("Contact must immediately follow commitment.", nameof(contact));

            var contactOrCommitmentEnd = contact.IsEmpty ? commitment.End : contact.End;
            if (followThrough.IsEmpty || followThrough.Start != contactOrCommitmentEnd + 1)
                throw new ArgumentException("Follow-through must follow contact or commitment.", nameof(followThrough));
            if (recovery.IsEmpty || recovery.Start != followThrough.End + 1)
                throw new ArgumentException("Recovery must follow follow-through.", nameof(recovery));
            if (recovery.End != totalTicks - 1)
                throw new ArgumentException("Recovery must end on the final action tick.", nameof(recovery));
            if (damage < 0)
                throw new ArgumentOutOfRangeException(nameof(damage));
            if (guardDamage < 0)
                throw new ArgumentOutOfRangeException(nameof(guardDamage));
            if (reachMillimeters < 0)
                throw new ArgumentOutOfRangeException(nameof(reachMillimeters));
            if (hitStopTicks < 0 || hitStopTicks > 4)
                throw new ArgumentOutOfRangeException(nameof(hitStopTicks));
            if (!invulnerability.IsEmpty &&
                (invulnerability.Start < 0 || invulnerability.End >= totalTicks))
                throw new ArgumentException("Invulnerability must remain inside the action.", nameof(invulnerability));

            Id = id;
            TotalTicks = totalTicks;
            Anticipation = anticipation;
            Commitment = commitment;
            Contact = contact;
            FollowThrough = followThrough;
            Recovery = recovery;
            Damage = damage;
            GuardDamage = guardDamage;
            ReachMillimeters = reachMillimeters;
            HitStopTicks = hitStopTicks;
            Invulnerability = invulnerability;
        }

        public string Id { get; }

        public int TotalTicks { get; }

        public TickRange Anticipation { get; }

        public TickRange Commitment { get; }

        public TickRange Contact { get; }

        public TickRange FollowThrough { get; }

        public TickRange Recovery { get; }

        public int Damage { get; }

        public int GuardDamage { get; }

        public int ReachMillimeters { get; }

        public int HitStopTicks { get; }

        public TickRange Invulnerability { get; }

        public ActionPhase PhaseAt(int tick)
        {
            if (tick < 0 || tick >= TotalTicks)
                throw new ArgumentOutOfRangeException(nameof(tick));
            if (Anticipation.Contains(tick))
                return ActionPhase.Anticipation;
            if (Commitment.Contains(tick))
                return ActionPhase.Commitment;
            if (Contact.Contains(tick))
                return ActionPhase.Contact;
            if (FollowThrough.Contains(tick))
                return ActionPhase.FollowThrough;
            if (Recovery.Contains(tick))
                return ActionPhase.Recovery;
            throw new InvalidOperationException("Action phases must cover every tick.");
        }
    }
}
