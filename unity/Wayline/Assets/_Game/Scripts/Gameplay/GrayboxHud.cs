using UnityEngine;
using Wayline.Combat.Simulation;

namespace Wayline.Gameplay
{
    [DisallowMultipleComponent]
    public sealed class GrayboxHud : MonoBehaviour
    {
        [SerializeField] private CombatWorldRunner runner;

        private const float MaximumHealth = 100f;
        private const float MaximumGuard = 100f;

        private int _playerHealth = 100;
        private int _enemyHealth = 100;
        private int _playerGuard = 100;
        private int _enemyGuard = 100;
        private float _displayPlayerHealth = 100f;
        private float _displayEnemyHealth = 100f;
        private float _trailPlayerHealth = 100f;
        private float _trailEnemyHealth = 100f;
        private bool _initialized;
        private string _opponentName = "SURVEYOR";

        private Texture2D _pixel;
        private GUIStyle _nameStyle;
        private GUIStyle _numberStyle;
        private GUIStyle _statusStyle;
        private GUIStyle _knockoutStyle;
        private GUIStyle _buttonStyle;

        public string PlayerSummary { get; private set; } = string.Empty;

        public string EnemySummary { get; private set; } = string.Empty;

        public string StatusText { get; private set; } = string.Empty;

        public bool IsRestartVisible { get; private set; }

        public void Configure(CombatWorldRunner worldRunner)
        {
            runner = worldRunner;
        }

        public void SetOpponentName(string value)
        {
            _opponentName = string.IsNullOrWhiteSpace(value)
                ? "CHAMPION"
                : value.Trim().ToUpperInvariant();
        }

        public void Present(CombatWorldState state)
        {
            if (state == null)
                return;
            _playerHealth = state.Player.Health;
            _enemyHealth = state.Enemy.Health;
            _playerGuard = state.Player.Guard;
            _enemyGuard = state.Enemy.Guard;
            if (!_initialized)
            {
                _displayPlayerHealth = _trailPlayerHealth = _playerHealth;
                _displayEnemyHealth = _trailEnemyHealth = _enemyHealth;
                _initialized = true;
            }
            PlayerSummary =
                $"ROUTEKEEPER  HP {state.Player.Health:000}  GUARD {state.Player.Guard:000}";
            EnemySummary =
                $"{_opponentName}  HP {state.Enemy.Health:000}  GUARD {state.Enemy.Guard:000}";
            IsRestartVisible = state.Result != CombatResult.InProgress;
            StatusText = state.Result == CombatResult.InProgress
                ? "A/D MOVE     J LIGHT     K HEAVY     L PARRY     SHIFT GUARD     SPACE DODGE"
                : "KNOCKOUT — Press R to restart";
        }

        private void Update()
        {
            if (!_initialized)
                return;
            var dt = Mathf.Min(0.05f, Time.unscaledDeltaTime);
            _displayPlayerHealth = Mathf.MoveTowards(
                _displayPlayerHealth,
                _playerHealth,
                150f * dt);
            _displayEnemyHealth = Mathf.MoveTowards(
                _displayEnemyHealth,
                _enemyHealth,
                150f * dt);
            // Delayed gold chip clearly shows recent damage without a number hunt.
            _trailPlayerHealth = Mathf.MoveTowards(
                _trailPlayerHealth,
                _displayPlayerHealth,
                34f * dt);
            _trailEnemyHealth = Mathf.MoveTowards(
                _trailEnemyHealth,
                _displayEnemyHealth,
                34f * dt);
        }

        private void OnGUI()
        {
            EnsureStyles();
            var scale = Mathf.Clamp(
                Mathf.Min(Screen.width / 1920f, Screen.height / 1080f),
                0.72f,
                1.35f);
            var panelWidth = Mathf.Min(610f * scale, Screen.width * 0.42f);
            var panelHeight = 82f * scale;
            var top = 22f * scale;
            var margin = 28f * scale;

            DrawFighterPanel(
                new Rect(margin, top, panelWidth, panelHeight),
                "ROUTEKEEPER",
                _displayPlayerHealth,
                _trailPlayerHealth,
                _playerGuard,
                mirrored: false,
                new Color32(45, 127, 131, 255));
            DrawFighterPanel(
                new Rect(Screen.width - margin - panelWidth, top, panelWidth, panelHeight),
                _opponentName,
                _displayEnemyHealth,
                _trailEnemyHealth,
                _enemyGuard,
                mirrored: true,
                new Color32(165, 67, 47, 255));

            var statusWidth = Mathf.Min(820f * scale, Screen.width - 80f);
            var statusRect = new Rect(
                (Screen.width - statusWidth) * 0.5f,
                Screen.height - 54f * scale,
                statusWidth,
                32f * scale);
            DrawRect(statusRect, new Color(0.035f, 0.045f, 0.065f, 0.84f));
            GUI.Label(statusRect, StatusText, _statusStyle);

            if (IsRestartVisible)
            {
                DrawRect(
                    new Rect(0f, 0f, Screen.width, Screen.height),
                    new Color(0.02f, 0.025f, 0.04f, 0.42f));
                var titleRect = new Rect(
                    Screen.width * 0.25f,
                    Screen.height * 0.38f,
                    Screen.width * 0.5f,
                    72f * scale);
                GUI.Label(titleRect, "ROUTE DUEL COMPLETE", _knockoutStyle);
                var buttonRect = new Rect(
                    Screen.width * 0.5f - 110f * scale,
                    Screen.height * 0.52f,
                    220f * scale,
                    52f * scale);
                if (GUI.Button(buttonRect, "RESTART DUEL", _buttonStyle))
                    runner?.RestartCombat();
            }
        }

        private void DrawFighterPanel(
            Rect panel,
            string label,
            float health,
            float trailHealth,
            float guard,
            bool mirrored,
            Color healthColor)
        {
            DrawRect(panel, new Color(0.035f, 0.045f, 0.065f, 0.92f));
            var inset = 13f;
            var nameRect = new Rect(
                panel.x + inset,
                panel.y + 5f,
                panel.width - inset * 2f,
                24f);
            _nameStyle.alignment = mirrored
                ? TextAnchor.MiddleRight
                : TextAnchor.MiddleLeft;
            GUI.Label(nameRect, label, _nameStyle);

            var hpRect = new Rect(
                panel.x + inset,
                panel.y + 31f,
                panel.width - inset * 2f,
                22f);
            DrawRect(hpRect, new Color(0.08f, 0.09f, 0.12f, 1f));
            DrawBar(
                hpRect,
                Mathf.Clamp01(trailHealth / MaximumHealth),
                new Color32(230, 175, 59, 255),
                mirrored);
            DrawBar(
                hpRect,
                Mathf.Clamp01(health / MaximumHealth),
                healthColor,
                mirrored);
            GUI.Label(
                hpRect,
                $"{Mathf.RoundToInt(health):000}",
                _numberStyle);

            var guardRect = new Rect(
                panel.x + inset,
                panel.y + 61f,
                panel.width - inset * 2f,
                7f);
            DrawRect(guardRect, new Color(0.08f, 0.09f, 0.12f, 1f));
            DrawBar(
                guardRect,
                Mathf.Clamp01(guard / MaximumGuard),
                new Color32(123, 175, 233, 255),
                mirrored);
        }

        private void DrawBar(Rect bounds, float normalized, Color color, bool mirrored)
        {
            var width = bounds.width * normalized;
            var fill = mirrored
                ? new Rect(bounds.xMax - width, bounds.y, width, bounds.height)
                : new Rect(bounds.x, bounds.y, width, bounds.height);
            DrawRect(fill, color);
        }

        private void DrawRect(Rect rect, Color color)
        {
            var previous = GUI.color;
            GUI.color = color;
            GUI.DrawTexture(rect, _pixel);
            GUI.color = previous;
        }

        private void EnsureStyles()
        {
            if (_pixel != null)
                return;
            _pixel = new Texture2D(1, 1, TextureFormat.RGBA32, false)
            {
                name = "Wayline HUD Pixel",
                hideFlags = HideFlags.HideAndDontSave,
            };
            _pixel.SetPixel(0, 0, Color.white);
            _pixel.Apply();

            _nameStyle = new GUIStyle(GUI.skin.label)
            {
                fontSize = 18,
                fontStyle = FontStyle.Bold,
                normal = { textColor = new Color32(230, 224, 208, 255) },
            };
            _numberStyle = new GUIStyle(GUI.skin.label)
            {
                fontSize = 15,
                fontStyle = FontStyle.Bold,
                alignment = TextAnchor.MiddleCenter,
                normal = { textColor = Color.white },
            };
            _statusStyle = new GUIStyle(GUI.skin.label)
            {
                fontSize = 14,
                fontStyle = FontStyle.Bold,
                alignment = TextAnchor.MiddleCenter,
                normal = { textColor = new Color32(215, 209, 194, 255) },
            };
            _knockoutStyle = new GUIStyle(GUI.skin.label)
            {
                fontSize = 36,
                fontStyle = FontStyle.Bold,
                alignment = TextAnchor.MiddleCenter,
                normal = { textColor = new Color32(230, 175, 59, 255) },
            };
            _buttonStyle = new GUIStyle(GUI.skin.button)
            {
                fontSize = 18,
                fontStyle = FontStyle.Bold,
                normal = { textColor = new Color32(230, 224, 208, 255) },
            };
        }

        private void OnDestroy()
        {
            if (_pixel != null)
                Destroy(_pixel);
        }
    }
}
