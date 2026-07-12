using UnityEngine;

namespace Wayline.Gameplay
{
    /// <summary>
    /// Weapon families represented by the graybox rig. These are original
    /// blocked-out proxies, not final commissioned art.
    /// </summary>
    public enum WaylineWeaponKind
    {
        Splitstaff = 0,
        FoldingLance = 1,
        PivotSabers = 2,
        CounterweightChain = 3
    }

    /// <summary>
    /// A named silhouette preset. Hero is the player Routekeeper; the others are
    /// the three demo-world champions with distinct proportions and weapons.
    /// </summary>
    public enum HumanoidPreset
    {
        Hero = 0,
        SurveyorGeneral = 1,
        TideMarshal = 2,
        ChainWarden = 3
    }

    /// <summary>
    /// Builds an original low-poly humanoid at runtime from Unity primitives so
    /// the fighters read as actual characters rather than single capsules.
    /// Exposes the animation pivots the <see cref="FighterPresenter"/> drives.
    ///
    /// Built in Awake with a negative execution order so the presenter can read
    /// the pivots during its own Awake/first present.
    /// </summary>
    [DisallowMultipleComponent]
    [DefaultExecutionOrder(-200)]
    public sealed class HumanoidRig : MonoBehaviour
    {
        [SerializeField] private HumanoidPreset preset = HumanoidPreset.Hero;
        [SerializeField] private bool facingRight = true;
        [SerializeField] private Color primaryDye = new Color(0.18f, 0.31f, 0.55f);
        [SerializeField] private Color accentDye = new Color(0.90f, 0.68f, 0.23f);

        private bool _built;

        /// <summary>The travel/scale root; equals the presenter's visual root.</summary>
        public Transform Body { get; private set; }

        /// <summary>Torso pivot that leans during attacks.</summary>
        public Transform UpperBody { get; private set; }

        /// <summary>Weapon-arm pivot that swings during an attack's contact.</summary>
        public Transform WeaponArm { get; private set; }

        public Transform BackArm { get; private set; }

        public Transform FrontLeg { get; private set; }

        public Transform BackLeg { get; private set; }

        public Transform Head { get; private set; }

        public Transform Mantle { get; private set; }

        /// <summary>Rest rotation of the weapon arm, before swing is layered on.</summary>
        public Quaternion WeaponRestRotation { get; private set; }

        public void Configure(HumanoidPreset rigPreset, bool faceRight, Color primary, Color accent)
        {
            preset = rigPreset;
            facingRight = faceRight;
            primaryDye = primary;
            accentDye = accent;
        }

        public void EnsureBuilt()
        {
            if (_built)
                return;
            // Older generated scenes may contain an authored Body hierarchy.
            // Adopt it instead of creating a second runtime character.
            var existing = transform.Find("Body");
            if (existing != null)
            {
                AdoptExisting(existing);
                return;
            }
            Build();
        }

        /// <summary>
        /// Rebuilds the figure with a new champion preset (used to re-theme the
        /// opponent per world). Presentation only.
        /// </summary>
        public void Rebuild(HumanoidPreset rigPreset, bool faceRight, Color primary, Color accent)
        {
            preset = rigPreset;
            facingRight = faceRight;
            primaryDye = primary;
            accentDye = accent;
            if (Body != null)
            {
                // Hide immediately so the replacement cannot overlap the old
                // body for one rendered frame while Destroy is deferred.
                Body.gameObject.SetActive(false);
                if (Application.isPlaying)
                    Destroy(Body.gameObject);
                else
                    DestroyImmediate(Body.gameObject);
            }
            Body = null;
            UpperBody = null;
            WeaponArm = null;
            BackArm = null;
            FrontLeg = null;
            BackLeg = null;
            Head = null;
            Mantle = null;
            _built = false;
            Build();
        }

        private void AdoptExisting(Transform body)
        {
            Body = body;
            UpperBody = body.Find("UpperBody");
            WeaponArm = UpperBody != null ? UpperBody.Find("WeaponArm") : null;
            BackArm = UpperBody != null ? UpperBody.Find("BackArm") : null;
            FrontLeg = body.Find("FrontLeg");
            BackLeg = body.Find("BackLeg");
            Head = UpperBody != null ? UpperBody.Find("Head") : null;
            Mantle = UpperBody != null ? UpperBody.Find("Mantle") : null;
            WeaponRestRotation =
                WeaponArm != null ? WeaponArm.localRotation : Quaternion.identity;
            _built = true;
        }

        private void Awake()
        {
            EnsureBuilt();
        }

        private void Build()
        {
            _built = true;
            var palette = PaletteFor(preset, primaryDye, accentDye);
            var profile = ProfileFor(preset);

            var shader = FindLitShader();
            var skin = MakeMaterial(shader, palette.Skin, 0.2f);
            var cloth = MakeMaterial(shader, palette.Cloth, 0.15f);
            var armor = MakeMaterial(shader, palette.Armor, 0.55f);
            var weaponMat = MakeMaterial(shader, palette.Weapon, 0.6f);
            var glow = MakeMaterial(shader, palette.Glow, 0.9f);

            var body = new GameObject("Body").transform;
            body.SetParent(transform, false);
            body.localScale = Vector3.one * profile.HeightScale;
            body.localRotation = Quaternion.Euler(0f, facingRight ? 0f : 180f, 0f);
            Body = body;

            var upper = new GameObject("UpperBody").transform;
            upper.SetParent(body, false);
            upper.localPosition = new Vector3(0f, 0.9f, 0f);
            UpperBody = upper;

            // Torso is created FIRST and is >1 world unit tall (2 * 0.55 * min
            // height scale 0.94 = 1.03) so the renderability regression test's
            // first-renderer bounds check stays valid for every preset.
            var torso = MakePart("Torso", upper, PrimitiveType.Capsule, armor,
                new Vector3(0f, 0.25f, 0f), new Vector3(0.34f, 0.55f, 0.26f), Vector3.zero);
            AddCoreGlowStripe(torso, glow);

            MakePart("Hips", body, PrimitiveType.Capsule, cloth,
                new Vector3(0f, 0.62f, 0f), new Vector3(0.32f, 0.18f, 0.26f), Vector3.zero);

            Head = MakePart("Head", upper, PrimitiveType.Sphere, skin,
                new Vector3(0f, 0.74f, 0.02f), new Vector3(0.28f, 0.32f, 0.30f), Vector3.zero);
            MakePart("Hair", upper, PrimitiveType.Sphere, cloth,
                new Vector3(0f, 0.80f, -0.03f), new Vector3(0.30f, 0.26f, 0.30f), Vector3.zero);

            // Two-joint legs rotate at the hips, keeping the foot/knee readable
            // during walk, anticipation, dodge, and impact poses.
            BackLeg = MakeLeg(body, cloth, skin, -0.12f, "BackLeg");
            FrontLeg = MakeLeg(body, cloth, skin, 0.12f, "FrontLeg");

            // Back arm (away from the viewer's read of the strike)
            var backArm = MakePart("BackArm", upper, PrimitiveType.Capsule, skin,
                new Vector3(-0.26f, 0.34f, -0.04f), new Vector3(0.11f, 0.30f, 0.11f),
                new Vector3(0f, 0f, 10f));
            BackArm = backArm;
            MakePart("BackHand", backArm, PrimitiveType.Sphere, skin,
                new Vector3(0f, -0.62f, 0f), new Vector3(0.55f, 0.5f, 0.55f), Vector3.zero);

            // Mantle across the shoulders
            Mantle = MakePart("Mantle", upper, PrimitiveType.Cube, cloth,
                new Vector3(0f, 0.5f, -0.14f), new Vector3(0.62f, 0.34f, 0.08f),
                new Vector3(14f, 0f, 0f));

            // Weapon arm + weapon (toward the opponent)
            var weaponArm = new GameObject("WeaponArm").transform;
            weaponArm.SetParent(upper, false);
            weaponArm.localPosition = new Vector3(0.26f, 0.5f, 0.06f);
            weaponArm.localRotation = Quaternion.Euler(0f, 0f, -18f);
            WeaponArm = weaponArm;
            WeaponRestRotation = weaponArm.localRotation;

            MakePart("Forearm", weaponArm, PrimitiveType.Capsule, skin,
                new Vector3(0.02f, -0.16f, 0f), new Vector3(0.11f, 0.26f, 0.11f), Vector3.zero);
            BuildWeapon(profile.Weapon, weaponArm, weaponMat, glow);

            // Route-inlay bracer on the weapon arm
            MakePart("Bracer", weaponArm, PrimitiveType.Cube, glow,
                new Vector3(0.03f, -0.30f, 0f), new Vector3(0.14f, 0.10f, 0.14f), Vector3.zero);
        }

        private static Transform MakeLeg(
            Transform parent,
            Material cloth,
            Material skin,
            float x,
            string name)
        {
            var hip = new GameObject(name).transform;
            hip.SetParent(parent, false);
            hip.localPosition = new Vector3(x, 0.64f, 0f);

            MakePart("Thigh", hip, PrimitiveType.Capsule, cloth,
                new Vector3(0f, -0.18f, 0f),
                new Vector3(0.14f, 0.25f, 0.14f),
                Vector3.zero);

            var knee = new GameObject("Knee").transform;
            knee.SetParent(hip, false);
            knee.localPosition = new Vector3(0f, -0.38f, 0f);
            MakePart("Shin", knee, PrimitiveType.Capsule, cloth,
                new Vector3(0f, -0.17f, 0f),
                new Vector3(0.12f, 0.24f, 0.12f),
                Vector3.zero);
            MakePart("Foot", knee, PrimitiveType.Cube, skin,
                new Vector3(0f, -0.42f, 0.12f),
                new Vector3(0.20f, 0.10f, 0.38f),
                Vector3.zero);
            return hip;
        }

        private void BuildWeapon(WaylineWeaponKind kind, Transform arm, Material weaponMat, Material glow)
        {
            switch (kind)
            {
                case WaylineWeaponKind.FoldingLance:
                    var lance = MakePart("Lance", arm, PrimitiveType.Capsule, weaponMat,
                        new Vector3(0.55f, -0.18f, 0f), new Vector3(0.05f, 1.05f, 0.05f),
                        new Vector3(0f, 0f, 90f));
                    MakePart("LanceHead", lance, PrimitiveType.Capsule, glow,
                        new Vector3(0f, 0.9f, 0f), new Vector3(1.3f, 0.16f, 1.3f), Vector3.zero);
                    break;
                case WaylineWeaponKind.PivotSabers:
                    MakePart("SaberA", arm, PrimitiveType.Capsule, weaponMat,
                        new Vector3(0.16f, -0.34f, 0f), new Vector3(0.05f, 0.5f, 0.05f),
                        new Vector3(0f, 0f, 24f));
                    var saberB = MakePart("SaberB", arm, PrimitiveType.Capsule, weaponMat,
                        new Vector3(0.28f, -0.30f, 0f), new Vector3(0.05f, 0.5f, 0.05f),
                        new Vector3(0f, 0f, 52f));
                    MakePart("SaberEdge", saberB, PrimitiveType.Capsule, glow,
                        new Vector3(0f, 0.5f, 0f), new Vector3(0.5f, 0.4f, 0.5f), Vector3.zero);
                    break;
                case WaylineWeaponKind.CounterweightChain:
                    var haft = MakePart("Crescent", arm, PrimitiveType.Capsule, weaponMat,
                        new Vector3(0.34f, -0.20f, 0f), new Vector3(0.09f, 0.34f, 0.09f),
                        new Vector3(0f, 0f, 70f));
                    MakePart("Counterweight", haft, PrimitiveType.Sphere, glow,
                        new Vector3(0f, 0.7f, 0f), new Vector3(1.6f, 1.6f, 1.6f), Vector3.zero);
                    break;
                default: // Splitstaff
                    var staff = MakePart("Splitstaff", arm, PrimitiveType.Capsule, weaponMat,
                        new Vector3(0.16f, -0.10f, 0f), new Vector3(0.05f, 0.92f, 0.05f),
                        new Vector3(0f, 0f, 8f));
                    MakePart("StaffGlow", staff, PrimitiveType.Capsule, glow,
                        new Vector3(0f, 0f, 0f), new Vector3(1.25f, 0.28f, 1.25f), Vector3.zero);
                    break;
            }
        }

        private static void AddCoreGlowStripe(Transform torso, Material glow)
        {
            MakePart("RouteStripe", torso, PrimitiveType.Cube, glow,
                new Vector3(0f, 0.05f, 0.5f), new Vector3(0.18f, 1.0f, 0.12f), Vector3.zero);
        }

        private static Transform MakePart(
            string name,
            Transform parent,
            PrimitiveType primitive,
            Material material,
            Vector3 localPosition,
            Vector3 localScale,
            Vector3 localEuler)
        {
            var go = GameObject.CreatePrimitive(primitive);
            go.name = name;
            var collider = go.GetComponent<Collider>();
            if (collider != null)
                DestroyImmediateSafe(collider);
            go.transform.SetParent(parent, false);
            go.transform.localPosition = localPosition;
            go.transform.localScale = localScale;
            go.transform.localRotation = Quaternion.Euler(localEuler);
            var renderer = go.GetComponent<Renderer>();
            renderer.sharedMaterial = material;
            renderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.On;
            return go.transform;
        }

        private static void DestroyImmediateSafe(Object target)
        {
            if (Application.isPlaying)
                Destroy(target);
            else
                DestroyImmediate(target);
        }

        private static Shader FindLitShader()
        {
            var shader = Shader.Find("Universal Render Pipeline/Lit");
            if (shader == null)
                shader = Shader.Find("Standard");
            return shader;
        }

        private static Material MakeMaterial(Shader shader, Color color, float smoothness)
        {
            var material = new Material(shader) { color = color };
            if (material.HasProperty("_BaseColor"))
                material.SetColor("_BaseColor", color);
            if (material.HasProperty("_Smoothness"))
                material.SetFloat("_Smoothness", smoothness);
            return material;
        }

        private readonly struct Palette
        {
            public Palette(Color skin, Color cloth, Color armor, Color weapon, Color glow)
            {
                Skin = skin;
                Cloth = cloth;
                Armor = armor;
                Weapon = weapon;
                Glow = glow;
            }

            public Color Skin { get; }
            public Color Cloth { get; }
            public Color Armor { get; }
            public Color Weapon { get; }
            public Color Glow { get; }
        }

        private readonly struct Profile
        {
            public Profile(float heightScale, WaylineWeaponKind weapon)
            {
                HeightScale = heightScale;
                Weapon = weapon;
            }

            public float HeightScale { get; }
            public WaylineWeaponKind Weapon { get; }
        }

        private static Profile ProfileFor(HumanoidPreset preset)
        {
            switch (preset)
            {
                case HumanoidPreset.SurveyorGeneral:
                    return new Profile(1.12f, WaylineWeaponKind.FoldingLance);
                case HumanoidPreset.TideMarshal:
                    return new Profile(1.02f, WaylineWeaponKind.PivotSabers);
                case HumanoidPreset.ChainWarden:
                    return new Profile(0.94f, WaylineWeaponKind.CounterweightChain);
                default:
                    return new Profile(1.0f, WaylineWeaponKind.Splitstaff);
            }
        }

        private static Palette PaletteFor(HumanoidPreset preset, Color primaryDye, Color accentDye)
        {
            var skin = new Color(0.82f, 0.66f, 0.54f);
            switch (preset)
            {
                case HumanoidPreset.SurveyorGeneral:
                    return new Palette(skin,
                        new Color(0.84f, 0.82f, 0.76f),
                        new Color(0.65f, 0.26f, 0.18f),
                        new Color(0.70f, 0.55f, 0.28f),
                        new Color(0.90f, 0.68f, 0.23f));
                case HumanoidPreset.TideMarshal:
                    return new Palette(skin,
                        new Color(0.20f, 0.42f, 0.44f),
                        new Color(0.16f, 0.29f, 0.40f),
                        new Color(0.55f, 0.72f, 0.74f),
                        new Color(0.30f, 0.85f, 0.82f));
                case HumanoidPreset.ChainWarden:
                    return new Palette(skin,
                        new Color(0.30f, 0.24f, 0.28f),
                        new Color(0.65f, 0.26f, 0.18f),
                        new Color(0.55f, 0.55f, 0.60f),
                        new Color(0.71f, 0.56f, 0.68f));
                default:
                    return new Palette(skin,
                        primaryDye,
                        new Color(0.15f, 0.24f, 0.40f),
                        new Color(0.72f, 0.72f, 0.78f),
                        accentDye);
            }
        }
    }
}
