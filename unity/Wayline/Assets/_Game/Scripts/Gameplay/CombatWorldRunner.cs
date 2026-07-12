using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.InputSystem;
using Wayline.Combat.Simulation;
using Wayline.Core;

namespace Wayline.Gameplay
{
    [DisallowMultipleComponent]
    public sealed class CombatWorldRunner : MonoBehaviour
    {
        [SerializeField] private bool runAutomatically = true;
        [SerializeField] private FighterPresenter playerPresenter;
        [SerializeField] private FighterPresenter enemyPresenter;
        [SerializeField] private FightCameraController fightCamera;
        [SerializeField] private GrayboxHud hud;

        private SimulationClock _clock;
        private CombatWorld _world;
        private ICombatCommandSource _playerCommands;
        private ICombatCommandSource _enemyCommands;
        private IReadOnlyList<CombatEvent> _lastEvents = Array.Empty<CombatEvent>();

        public bool RunAutomatically
        {
            get => runAutomatically;
            set => runAutomatically = value;
        }

        public CombatWorldState State => _world?.State;

        public FighterPresenter PlayerPresenter => playerPresenter;

        public FighterPresenter EnemyPresenter => enemyPresenter;

        public FightCameraController FightCamera => fightCamera;

        public GrayboxHud Hud => hud;

        public IReadOnlyList<CombatEvent> LastEvents => _lastEvents;

        public void ConfigurePresentation(
            FighterPresenter player,
            FighterPresenter enemy,
            FightCameraController cameraController,
            GrayboxHud grayboxHud)
        {
            playerPresenter = player;
            enemyPresenter = enemy;
            fightCamera = cameraController;
            hud = grayboxHud;
        }

        public void SetCommandSources(
            ICombatCommandSource playerSource,
            ICombatCommandSource enemySource)
        {
            _playerCommands = playerSource ?? throw new ArgumentNullException(nameof(playerSource));
            _enemyCommands = enemySource ?? throw new ArgumentNullException(nameof(enemySource));
        }

        public int AdvanceFrame(double unscaledDeltaSeconds)
        {
            EnsureInitialized();
            var emitted = _clock.ConsumeFrame(unscaledDeltaSeconds);
            for (var index = 0; index < emitted; index++)
            {
                var state = _world.State;
                var playerCommand = _playerCommands.NextCommand(state, FighterSide.Player);
                var enemyCommand = _enemyCommands.NextCommand(state, FighterSide.Enemy);
                _lastEvents = _world.Step(playerCommand, enemyCommand);
                ReactToEvents(_lastEvents);
            }

            Present();
            return emitted;
        }

        public void RestartCombat()
        {
            _clock = new SimulationClock(60, 4);
            _world = CombatWorld.CreateGraybox(100, 100, -800, 800);
            _lastEvents = Array.Empty<CombatEvent>();
            if (_playerCommands == null)
                _playerCommands = new PlayerCombatInput();
            if (_enemyCommands == null)
                _enemyCommands = new DeterministicFighterAi(7411, 12);
            Present();
        }

        public byte[] SerializeSnapshot()
        {
            EnsureInitialized();
            return _world.SerializeSnapshot();
        }

        private void Awake()
        {
            RestartCombat();
        }

        private void Update()
        {
            if (_world != null &&
                _world.State.Result != CombatResult.InProgress &&
                Keyboard.current != null &&
                Keyboard.current.rKey.wasPressedThisFrame)
            {
                RestartCombat();
                return;
            }
            if (runAutomatically)
                AdvanceFrame(Time.unscaledDeltaTime);
        }

        private void EnsureInitialized()
        {
            if (_world == null || _clock == null)
                RestartCombat();
        }

        private void ReactToEvents(IReadOnlyList<CombatEvent> events)
        {
            if (events == null)
                return;
            for (var index = 0; index < events.Count; index++)
            {
                var combatEvent = events[index];
                switch (combatEvent.Kind)
                {
                    case CombatEventKind.Knockout:
                        fightCamera?.AddImpulse(0.3f);
                        FlashTarget(combatEvent.Target);
                        break;
                    case CombatEventKind.Hit:
                        fightCamera?.AddImpulse(0.12f);
                        FlashTarget(combatEvent.Target);
                        break;
                    case CombatEventKind.GuardBreak:
                        fightCamera?.AddImpulse(0.08f);
                        break;
                }
            }
        }

        private void FlashTarget(FighterSide target)
        {
            var presenter = target == FighterSide.Player ? playerPresenter : enemyPresenter;
            presenter?.FlashHit();
        }

        private void Present()
        {
            if (_world == null)
                return;
            var state = _world.State;
            playerPresenter?.Present(state.Player, state.Tick);
            enemyPresenter?.Present(state.Enemy, state.Tick);
            fightCamera?.Present(state);
            hud?.Present(state);
        }
    }
}
