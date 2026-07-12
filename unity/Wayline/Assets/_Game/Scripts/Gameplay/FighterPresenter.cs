using UnityEngine;
using Wayline.Combat.Data;
using Wayline.Combat.Presentation;
using Wayline.Combat.Simulation;

namespace Wayline.Gameplay
{
    [DisallowMultipleComponent]
    public sealed class FighterPresenter : MonoBehaviour
    {
        [SerializeField] private FighterSide side;
        [SerializeField] private Transform visualRoot;
        [SerializeField] private HumanoidRig rig;

        private Vector3 _baseVisualPosition;
        private Vector3 _baseVisualScale;
        private bool _basePoseCaptured;

        private Transform _upperBody;
        private Transform _weaponArm;
        private Transform _backArm;
        private Transform _frontLeg;
        private Transform _backLeg;
        private Transform _head;
        private Transform _mantle;
        private Quaternion _baseRootRotation;
        private Quaternion _baseUpperRotation;
        private Quaternion _weaponRestRotation;
        private Quaternion _backArmRestRotation;
        private Quaternion _frontLegRestRotation;
        private Quaternion _backLegRestRotation;
        private Quaternion _headRestRotation;
        private Quaternion _mantleRestRotation;
        private bool _rigResolved;

        private Renderer[] _renderers;
        private Color[] _rendererBaseColors;
        private int _flashFrames;
        private const int FlashFrameCount = 6;

        // Smoothing for readable, non-jittery presentation between sim ticks.
        private float _leanDegrees;
        private float _swingDegrees;
        private float _backArmDegrees;
        private float _frontLegDegrees;
        private float _backLegDegrees;
        private float _headDegrees;
        private float _mantleDegrees;
        private float _impactStrength;
        private int _lastXMillimeters;
        private bool _positionInitialized;

        public FighterSide Side => side;

        public ActionPhase LastPhase { get; private set; } = ActionPhase.Rest;

        public void Configure(FighterSide fighterSide, Transform visual)
        {
            side = fighterSide;
            visualRoot = visual;
            CaptureBasePose();
        }

        public void ConfigureRig(FighterSide fighterSide, HumanoidRig humanoidRig)
        {
            side = fighterSide;
            rig = humanoidRig;
            _rigResolved = false;
            // Scene-authoring calls ConfigureRig in EditMode. Do not build the
            // runtime hierarchy there or it will be serialized and duplicated
            // by HumanoidRig.Awake in the player.
            if (Application.isPlaying)
                CaptureBasePose();
        }

        /// <summary>Re-theme this fighter's rig to a new champion preset at runtime.</summary>
        public void Retheme(HumanoidPreset preset, bool facingRight, Color primary, Color accent)
        {
            if (rig == null)
                return;
            rig.Rebuild(preset, facingRight, primary, accent);
            _rigResolved = false;
            _basePoseCaptured = false;
            _upperBody = null;
            _weaponArm = null;
            _backArm = null;
            _frontLeg = null;
            _backLeg = null;
            _head = null;
            _mantle = null;
            _leanDegrees = 0f;
            _swingDegrees = 0f;
            _backArmDegrees = 0f;
            _frontLegDegrees = 0f;
            _backLegDegrees = 0f;
            _headDegrees = 0f;
            _mantleDegrees = 0f;
            ResolveRig();
        }

        public void Present(FighterState state)
        {
            Present(state, 0);
        }

        public void Present(FighterState state, long worldTick)
        {
            if (state == null)
                return;
            var targetPosition = new Vector3(state.XMillimeters / 1000f, 0f, 0f);
            if (!_positionInitialized)
            {
                transform.position = targetPosition;
                _positionInitialized = true;
            }
            else
            {
                transform.position = Vector3.Lerp(
                    transform.position,
                    targetPosition,
                    0.68f);
            }
            ResolveRig();
            UpdateFlash();
            if (visualRoot == null)
                return;
            if (!_basePoseCaptured)
                CaptureBasePose();
            if (!_basePoseCaptured)
                return;

            var moving = Mathf.Abs(state.XMillimeters - _lastXMillimeters) > 1;
            _lastXMillimeters = state.XMillimeters;
            var impact = _impactStrength;
            _impactStrength = Mathf.Max(0f, _impactStrength - 0.16f);

            if (state.Health <= 0)
            {
                ApplyKnockout(state);
                LastPhase = ActionPhase.Rest;
                return;
            }

            if (state.CurrentAction == CombatAction.None)
            {
                ApplyIdle(state, worldTick, moving, impact);
                LastPhase = ActionPhase.Rest;
                return;
            }

            var pose = ProceduralActionPoseEvaluator.Evaluate(
                DefinitionFor(state.CurrentAction),
                state.ActionTick,
                state.Facing);

            // Body travel keeps its facing sign (root-space); rotations are applied
            // in the rig's local space where facing is already baked into the yaw.
            var travel = pose.BodyTravelMillimeters / 1000f;
            var widen = 1f + pose.CompressionPermille / 2000f;
            var shorten = 1f - pose.CompressionPermille / 2000f;
            visualRoot.localPosition =
                _baseVisualPosition +
                Vector3.right * (travel - state.Facing * 0.16f * impact) +
                Vector3.up * (0.025f * impact);
            visualRoot.localScale = new Vector3(
                _baseVisualScale.x * widen,
                _baseVisualScale.y * shorten,
                _baseVisualScale.z * widen);

            var targetLean =
                -pose.TorsoLeanMillidegrees * state.Facing / 1000f;
            var targetSwing = WeaponAngleFor(state.CurrentAction, pose);
            var targetBackArm = BackArmAngleFor(state.CurrentAction, pose);
            var targetFrontLeg = FrontLegAngleFor(state.CurrentAction, pose);
            var targetBackLeg = -targetFrontLeg * 0.75f;

            if (state.StunTicksRemaining > 0)
            {
                targetLean -= 14f;
                targetSwing += 28f;
                targetBackArm -= 32f;
                targetFrontLeg -= 10f;
                targetBackLeg += 14f;
            }
            if (impact > 0f)
            {
                targetLean -= 20f * impact;
                targetSwing += 36f * impact;
                targetBackArm -= 46f * impact;
                targetFrontLeg -= 15f * impact;
                targetBackLeg += 18f * impact;
            }

            if (_upperBody != null || _weaponArm != null)
            {
                // Primary action resolves first; off-hand, head, legs, and
                // mantle settle with lower response factors for follow-through.
                _leanDegrees = Mathf.Lerp(_leanDegrees, targetLean, 0.58f);
                _swingDegrees = Mathf.Lerp(_swingDegrees, targetSwing, 0.72f);
                _backArmDegrees = Mathf.Lerp(
                    _backArmDegrees,
                    targetBackArm,
                    0.46f);
                _frontLegDegrees = Mathf.Lerp(
                    _frontLegDegrees,
                    targetFrontLeg,
                    0.52f);
                _backLegDegrees = Mathf.Lerp(
                    _backLegDegrees,
                    targetBackLeg,
                    0.42f);
                _headDegrees = Mathf.Lerp(
                    _headDegrees,
                    -targetLean * 0.28f,
                    0.35f);
                _mantleDegrees = Mathf.Lerp(
                    _mantleDegrees,
                    -targetLean * 0.55f - targetSwing * 0.035f,
                    0.24f);
                ApplyRigRotations();
                visualRoot.localRotation = _baseRootRotation;
            }
            else
            {
                // Fallback capsule path: lean the single visual root.
                visualRoot.localRotation = Quaternion.Euler(0f, 0f, -pose.TorsoLeanMillidegrees / 1000f);
            }

            LastPhase = pose.Phase;
        }

        private void ApplyIdle(
            FighterState state,
            long worldTick,
            bool moving,
            float impact)
        {
            var time = worldTick / 60f;
            var breath = Mathf.Sin(time * 2.2f + (side == FighterSide.Player ? 0f : 1.1f));
            var stride = Mathf.Sin(time * 9.5f);
            var bob = moving ? Mathf.Abs(stride) * 0.035f : breath * 0.012f;
            visualRoot.localPosition =
                _baseVisualPosition +
                Vector3.up * (bob + 0.025f * impact) +
                Vector3.right * (-state.Facing * 0.16f * impact);
            visualRoot.localScale = _baseVisualScale;
            if (_upperBody != null || _weaponArm != null)
            {
                var guard = state.GuardActive;
                var idleLean = guard ? -6f : breath * 1.2f;
                var weapon = guard ? -52f : breath * 2.5f;
                var offHand = guard ? 62f : -breath * 2f;
                var frontLeg = moving ? stride * 24f : guard ? -8f : 0f;
                var backLeg = moving ? -stride * 24f : guard ? 10f : 0f;
                if (state.StunTicksRemaining > 0)
                {
                    idleLean = -13f;
                    weapon = 22f;
                    offHand = -28f;
                }
                if (impact > 0f)
                {
                    idleLean -= 20f * impact;
                    weapon += 36f * impact;
                    offHand -= 46f * impact;
                    frontLeg -= 15f * impact;
                    backLeg += 18f * impact;
                }

                _leanDegrees = Mathf.Lerp(_leanDegrees, idleLean, 0.22f);
                _swingDegrees = Mathf.Lerp(_swingDegrees, weapon, 0.24f);
                _backArmDegrees = Mathf.Lerp(_backArmDegrees, offHand, 0.20f);
                _frontLegDegrees = Mathf.Lerp(
                    _frontLegDegrees,
                    frontLeg,
                    moving ? 0.5f : 0.2f);
                _backLegDegrees = Mathf.Lerp(
                    _backLegDegrees,
                    backLeg,
                    moving ? 0.5f : 0.2f);
                _headDegrees = Mathf.Lerp(
                    _headDegrees,
                    -idleLean * 0.22f,
                    0.18f);
                _mantleDegrees = Mathf.Lerp(
                    _mantleDegrees,
                    -idleLean * 0.5f - (moving ? stride * 5f : breath * 2f),
                    0.12f);
                ApplyRigRotations();
                visualRoot.localRotation = _baseRootRotation;
            }
            else
            {
                visualRoot.localRotation = Quaternion.identity;
            }
        }

        private void ApplyRigRotations()
        {
            if (_upperBody != null)
                _upperBody.localRotation =
                    _baseUpperRotation * Quaternion.Euler(0f, 0f, _leanDegrees);
            if (_weaponArm != null)
                _weaponArm.localRotation =
                    _weaponRestRotation * Quaternion.Euler(0f, 0f, _swingDegrees);
            if (_backArm != null)
                _backArm.localRotation =
                    _backArmRestRotation * Quaternion.Euler(0f, 0f, _backArmDegrees);
            if (_frontLeg != null)
                _frontLeg.localRotation =
                    _frontLegRestRotation * Quaternion.Euler(0f, 0f, _frontLegDegrees);
            if (_backLeg != null)
                _backLeg.localRotation =
                    _backLegRestRotation * Quaternion.Euler(0f, 0f, _backLegDegrees);
            if (_head != null)
                _head.localRotation =
                    _headRestRotation * Quaternion.Euler(0f, 0f, _headDegrees);
            if (_mantle != null)
                _mantle.localRotation =
                    _mantleRestRotation * Quaternion.Euler(_mantleDegrees, 0f, 0f);
        }

        private void ApplyKnockout(FighterState state)
        {
            var fall = side == FighterSide.Player ? 72f : -72f;
            _leanDegrees = Mathf.Lerp(_leanDegrees, fall, 0.18f);
            _swingDegrees = Mathf.Lerp(_swingDegrees, 55f, 0.12f);
            _backArmDegrees = Mathf.Lerp(_backArmDegrees, -45f, 0.12f);
            _frontLegDegrees = Mathf.Lerp(_frontLegDegrees, -22f, 0.12f);
            _backLegDegrees = Mathf.Lerp(_backLegDegrees, 18f, 0.12f);
            ApplyRigRotations();
            visualRoot.localPosition = Vector3.Lerp(
                visualRoot.localPosition,
                _baseVisualPosition + Vector3.down * 0.25f,
                0.15f);
            visualRoot.localRotation =
                _baseRootRotation * Quaternion.Euler(0f, 0f, _leanDegrees * 0.35f);
        }

        /// <summary>Briefly flash the fighter white on taking a hit. Presentation only.</summary>
        public void FlashHit()
        {
            _flashFrames = FlashFrameCount;
            _impactStrength = 1f;
        }

        private void CacheRenderers()
        {
            if (visualRoot == null)
                return;
            _renderers = visualRoot.GetComponentsInChildren<Renderer>();
            _rendererBaseColors = new Color[_renderers.Length];
            for (var i = 0; i < _renderers.Length; i++)
                _rendererBaseColors[i] = _renderers[i].material.color;
        }

        private void UpdateFlash()
        {
            if (_renderers == null || _rendererBaseColors == null)
                return;
            if (_flashFrames <= 0)
                return;
            var t = (float)_flashFrames / FlashFrameCount;
            var mix = 0.65f * t;
            for (var i = 0; i < _renderers.Length; i++)
            {
                if (_renderers[i] == null)
                    continue;
                var baseColor = _rendererBaseColors[i];
                _renderers[i].material.color = _flashFrames == 1
                    ? baseColor
                    : Color.Lerp(baseColor, Color.white, mix);
            }
            _flashFrames--;
        }

        private void Awake()
        {
            CaptureBasePose();
        }

        private void ResolveRig()
        {
            if (_rigResolved || rig == null)
                return;
            rig.EnsureBuilt();
            if (rig.Body == null)
                return;
            visualRoot = rig.Body;
            _upperBody = rig.UpperBody;
            _weaponArm = rig.WeaponArm;
            _backArm = rig.BackArm;
            _frontLeg = rig.FrontLeg;
            _backLeg = rig.BackLeg;
            _head = rig.Head;
            _mantle = rig.Mantle;
            _baseRootRotation = visualRoot.localRotation;
            _baseUpperRotation = _upperBody != null ? _upperBody.localRotation : Quaternion.identity;
            _weaponRestRotation = rig.WeaponRestRotation;
            _backArmRestRotation =
                _backArm != null ? _backArm.localRotation : Quaternion.identity;
            _frontLegRestRotation =
                _frontLeg != null ? _frontLeg.localRotation : Quaternion.identity;
            _backLegRestRotation =
                _backLeg != null ? _backLeg.localRotation : Quaternion.identity;
            _headRestRotation =
                _head != null ? _head.localRotation : Quaternion.identity;
            _mantleRestRotation =
                _mantle != null ? _mantle.localRotation : Quaternion.identity;
            CacheRenderers();
            _rigResolved = true;
            _baseVisualPosition = visualRoot.localPosition;
            _baseVisualScale = visualRoot.localScale;
            _basePoseCaptured = true;
        }

        private static float WeaponAngleFor(
            CombatAction action,
            ProceduralActionPose pose)
        {
            if (action == CombatAction.Parry)
            {
                switch (pose.Phase)
                {
                    case ActionPhase.Anticipation: return -18f;
                    case ActionPhase.Commitment: return -62f;
                    case ActionPhase.Contact: return -78f;
                    case ActionPhase.FollowThrough: return -58f;
                    default: return -42f * pose.PrimarySettlePermille / 1000f;
                }
            }
            if (action == CombatAction.Dodge)
                return 18f;

            var heavy = action == CombatAction.HeavyAttack;
            switch (pose.Phase)
            {
                case ActionPhase.Anticipation:
                    return Mathf.Lerp(
                        0f,
                        heavy ? 78f : 45f,
                        Remap(pose.WeaponProgressPermille, 0, 120));
                case ActionPhase.Commitment:
                    return Mathf.Lerp(
                        heavy ? 78f : 45f,
                        heavy ? -118f : -92f,
                        Remap(pose.WeaponProgressPermille, 120, 650));
                case ActionPhase.Contact:
                    return Mathf.Lerp(
                        heavy ? -118f : -92f,
                        heavy ? -170f : -132f,
                        Remap(pose.WeaponProgressPermille, 650, 800));
                case ActionPhase.FollowThrough:
                    return Mathf.Lerp(
                        heavy ? -170f : -132f,
                        heavy ? -198f : -158f,
                        Remap(pose.WeaponProgressPermille, 800, 1000));
                default:
                    return (heavy ? -198f : -158f) *
                           pose.PrimarySettlePermille / 1000f;
            }
        }

        private static float BackArmAngleFor(
            CombatAction action,
            ProceduralActionPose pose)
        {
            if (action == CombatAction.Parry)
                return pose.Phase == ActionPhase.Contact ? 72f : 45f;
            if (action == CombatAction.Dodge)
                return -30f;
            var heavy = action == CombatAction.HeavyAttack;
            var swing = WeaponAngleFor(action, pose);
            return -swing * (heavy ? 0.42f : 0.28f);
        }

        private static float FrontLegAngleFor(
            CombatAction action,
            ProceduralActionPose pose)
        {
            if (action == CombatAction.Dodge)
                return pose.Phase == ActionPhase.Commitment ? -32f : -16f;
            if (action == CombatAction.Parry)
                return -12f;
            switch (pose.Phase)
            {
                case ActionPhase.Anticipation: return -18f;
                case ActionPhase.Commitment: return 10f;
                case ActionPhase.Contact: return 28f;
                case ActionPhase.FollowThrough: return 22f;
                default: return 12f * pose.PrimarySettlePermille / 1000f;
            }
        }

        private static float Remap(int value, int minimum, int maximum)
        {
            if (maximum <= minimum)
                return 1f;
            return Mathf.Clamp01((value - minimum) / (float)(maximum - minimum));
        }

        private void CaptureBasePose()
        {
            if (rig != null && !_rigResolved)
            {
                ResolveRig();
                return;
            }
            if (visualRoot == null)
                return;
            _baseVisualPosition = visualRoot.localPosition;
            _baseVisualScale = visualRoot.localScale;
            _basePoseCaptured = true;
        }

        private static ActionDefinition DefinitionFor(CombatAction action)
        {
            switch (action)
            {
                case CombatAction.LightAttack:
                    return GrayboxCombatCatalog.SplitstaffLight;
                case CombatAction.HeavyAttack:
                    return GrayboxCombatCatalog.SplitstaffHeavy;
                case CombatAction.Parry:
                    return GrayboxCombatCatalog.Parry;
                case CombatAction.Dodge:
                    return GrayboxCombatCatalog.Dodge;
                default:
                    throw new System.ArgumentOutOfRangeException(nameof(action));
            }
        }
    }
}
