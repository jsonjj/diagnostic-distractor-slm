using UnityEngine;
using Wayline.Combat.Simulation;

namespace Wayline.Gameplay
{
    [DisallowMultipleComponent]
    public sealed class FightCameraController : MonoBehaviour
    {
        [SerializeField] private Camera fightCamera;

        private float _impulse;
        private int _impulseSeed;

        public Camera Camera => fightCamera;

        public void Configure(Camera cameraComponent)
        {
            fightCamera = cameraComponent;
        }

        /// <summary>
        /// Adds a short decaying impact shake. Presentation only; it never feeds
        /// back into the deterministic simulation.
        /// </summary>
        public void AddImpulse(float magnitude)
        {
            _impulse = Mathf.Clamp(_impulse + magnitude, 0f, 0.35f);
            _impulseSeed++;
        }

        public void Present(CombatWorldState state)
        {
            if (fightCamera == null || state == null)
                return;

            var midpoint = (state.Player.XMillimeters + state.Enemy.XMillimeters) / 2000f;
            var separation = (state.Enemy.XMillimeters - state.Player.XMillimeters) / 1000f;
            var distance = Mathf.Clamp(7.5f + separation * 0.65f, 8f, 12f);

            var shake = Vector3.zero;
            if (_impulse > 0.0001f)
            {
                // Deterministic, seed-driven offset so identical event streams
                // produce identical camera motion.
                var sx = Mathf.Sin(_impulseSeed * 12.9898f) * _impulse * 0.5f;
                var sy = Mathf.Cos(_impulseSeed * 78.233f) * _impulse * 0.5f;
                shake = new Vector3(sx, sy, 0f);
                _impulse *= 0.72f;
                if (_impulse < 0.0005f)
                    _impulse = 0f;
            }

            fightCamera.transform.position =
                new Vector3(midpoint + shake.x, 2.6f + shake.y, -distance);
            fightCamera.transform.LookAt(new Vector3(midpoint, 1f, 0f));
            fightCamera.fieldOfView = 42f;
        }
    }
}
