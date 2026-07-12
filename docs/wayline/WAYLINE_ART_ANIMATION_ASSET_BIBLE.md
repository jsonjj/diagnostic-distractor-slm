# Wayline Art, Animation, UI, and 3D Asset Bible

**Engine:** Unity `6000.3.11f1`, Universal Render Pipeline  
**First target:** macOS Apple Silicon, 1920×1080, 60 fps  
**Style:** Stylized realism; believable materials and anatomy with selective silhouette exaggeration  
**Combat view:** Perspective 3D staged on a side-on 2.5D plane  

## Visual thesis

Wayline should look like a civilization built around travel, surveying, and celestial navigation—not a generic neon sci-fi arena or medieval fantasy reskin. One luminous meridian line connects every layer of the experience. It runs through bridges and route machines, appears as inlay on armor and weapons, becomes the map path, enters the quiz screen as its reading order, and reconnects after every world clear.

Spend visual boldness on that one continuous line. Keep other decoration disciplined so the signature remains memorable.

## Color and material system

| Token | Hex | Use |
| --- | --- | --- |
| Night ink | `#151B26` | Deep UI fields, night sky, silhouette separation |
| Lapis | `#253B66` | Routekeeper cloth, primary interface structures |
| Storm teal | `#2D7F83` | World machinery, secondary focus, Decimara identity |
| Oxide | `#A5432F` | Rival factions, danger accents that are not correctness states |
| Meridian gold | `#E6AF3B` | The single route line, selection focus, earned restoration |
| Limestone | `#D7D1C2` | Architecture, readable panels, daylight balance |

Materials are weathered limestone, darkened steel, brushed bronze, dyed travel cloth, translucent route glass, leather grips, and ceramic insulation. Energy is not a broad glow wash; it is a narrow emissive line with a hot core and short falloff.

Correctness never relies on green/red alone. Use icon, label, border pattern, and announced text.

## Typography

- **Big Shoulders Display:** restrained chapter/world titles and boss cards.
- **Atkinson Hyperlegible:** every paragraph, option, tutorial, and child-facing label.
- **IBM Plex Mono:** equations, confidence markers, route measurements, and audit data.
- Record exact font files, versions, and OFL licenses in `docs/ASSET_PROVENANCE.md`.
- Math body text must remain at least 32 px at 1080p; normal body text at least 28 px; allow 125% and 150% scaling without clipping.

## UI signature

The interface is an atlas, not a collection of floating generic cards.

### Fight HUD

- Health is shown by two opposed route bands engraved into a thin topographic frame.
- Focus is a three-notch atlas-bracer gauge beside the player band.
- Weapon technique icons sit on one short vertical meridian tick, not a bottom-screen mobile hotbar.
- Boss telegraph names appear near the boss, never over the player's health.
- The HUD recedes to 70% opacity outside damage/decision windows.

### Route Trial

- The frozen arena remains faintly visible behind a lowered-depth-of-field atlas overlay.
- A gold meridian arc establishes reading order from question to options to confidence.
- Answer options use quiet limestone fields with asymmetric topographic corners; no default rounded SaaS cards.
- Initial and revision states are neutral. No answer shakes, flashes red, or emits failure audio before final reveal.
- The exact wrong count occupies the center of the route arc and is the only dominant motion in that moment.
- Confidence is always written: `Certain`, `Leaning`, `Guessing`, supported by three distinct notch patterns.
- Final feedback shows `First choice`, `Review choice`, `Result`, `This answer can come from`, and `Reliable method` in that order.

### Motion

The transition into a trial is one orchestrated event: ambient arena motion slows, the bracer line lifts from the character, the line traces the screen, and the atlas surface resolves beneath it. Avoid unrelated card bounces and ambient UI loops. Reduced-motion mode crossfades the same states in 180 ms.

## Character language

- Mostly human faces remain visible and expressive.
- Base anatomy is believable: roughly 7.5 heads tall, athletic rather than superhero-proportioned.
- Faction identity comes from plate arcs, mantle direction, color blocking, and weapon silhouette.
- Routekeeper armor uses fitted travel cloth under curved plates engraved like contour lines.
- Avoid skull motifs, extreme spikes near the face, fetishized armor, sexualized silhouettes, black-shadow bodies, and impractical exposed torsos.
- Armor damage is dirt, scuffs, loosened cloth, and dimmed inlay—not blood or torn flesh.

## Exact hero asset request

This is the first bespoke model to commission or create. One delivery must support four authored appearances without four independent rigs.

### Concept

An apprentice Routekeeper in practical science-fantasy travel armor. The silhouette reads as a fast, adaptable explorer: short asymmetrical mantle over the rear shoulder, narrow waist, curved shoulder plate on the weapon-leading side, atlas-bracer on the opposite forearm, layered hip guards that clear kicks, fitted trousers, reinforced boots, and a compact back attachment for a collapsed splitstaff. Gold route inlay begins sparse and can light in world colors as the campaign progresses.

### Appearance modules

- Four complete head/face/hair sets representing varied skin tones and facial structures.
- Heads share neck seam placement, eye height, jaw clearance, blendshape names, and skeleton.
- At least two short, one tied-back, and one textured/curly hair silhouette. Hair must stay clear of weapon arcs and support simple secondary bones.
- Cosmetic slots: `Head`, `Hair`, `TorsoCloth`, `ChestPlate`, `Mantle`, `Bracer`, `HipGuards`, `Boots`.
- Two player-controlled armor-dye masks plus one fixed meridian-emission mask.
- No body slider system in v1. Every module is validated against the same animation/contact set.

### Mesh budgets

| Part | LOD0 triangles | LOD1 | LOD2 |
| --- | ---: | ---: | ---: |
| Body plus armor | 70k maximum | 38k | 18k |
| Head and eyes | 22k maximum | 12k | 6k |
| Hair | 18k maximum | 9k | 4k |
| Mantle and accessories | 14k maximum | 7k | 3k |

The complete hero at LOD0 remains under 120k visible triangles with one head and one hair set. Use clean deformation topology at shoulders, elbows, wrists, hips, knees, ankles, neck, and mouth. No nonmanifold geometry, internal duplicate faces, unapplied negative scale, or automatic decimation at LOD0.

### Textures and materials

- One 4K body/armor texture set and one 2K head/hair set per appearance for LOD0; provide 2K/1K downscaled versions.
- Unity URP metallic workflow: Base Color, Metallic/Smoothness, Normal, Ambient Occlusion, Emission Mask.
- At most five runtime materials on a complete character: skin/eyes, hair, cloth, armor, route glass/emission.
- Skin color is not placed in a player dye mask.
- Mantle uses bones or a conservative cloth setup with collision capsules; it may not lead the torso or obscure weapon contacts.

### Face

- Required blendshapes: blink left/right, brow up/down, eyes wide/squint, smile, frown, jaw open, mouth narrow/wide, and four phoneme-group shapes for short nonverbal exertions.
- Neutral, focused, surprised, relieved, and determined expressions must read at the side-on gameplay camera.

## Shared rig and export contract

- Unity Humanoid-compatible skeleton, one meter = one Unity unit, `+Y` up, `+Z` forward.
- Root at world origin with feet on `Y=0`, character facing `+Z` in the source bind pose.
- Separate `RootMotion` and `Hips` bones. Gameplay locomotion does not consume clip root motion, but authored root curves are retained for reference and cinematic playback.
- Twist support at upper/lower arms and upper legs; at least three spine joints, neck, head, clavicles, full fingers, ball/toe bones.
- Weapon sockets: `Socket_R_Hand`, `Socket_L_Hand`, `Socket_Back`, `Socket_Hip_L`, `Socket_Hip_R`, `Socket_Bracer`.
- VFX sockets: weapon tip/base, each palm, each foot, chest center, head, bracer emitter.
- Provide hidden reference transforms for chest/hip/head hit regions; Unity owns functional colliders and hitboxes.
- Source `.blend` is authoritative. Deliver clean triangulated FBX files for Unity; do not add a runtime glTF dependency.
- FBX root imports at position `(0,0,0)`, rotation `(0,0,0)`, scale `(1,1,1)`.
- Bake at 60 fps, preserve event pose frames, strip control-rig helpers from runtime exports, and never duplicate skeletons per LOD.

## First three bosses

All three bosses share the humanoid skeleton conventions but have unique silhouette modules and animation attitude.

### Valuehold Surveyor-General

- Tall, centered, disciplined silhouette; long curved shoulder plates echo contour arcs.
- Telescoping survey spear with a bronze measurement spine and narrow gold route line.
- Fighting personality: controls distance, plants the spear to define safe/unsafe zones, uses deliberate symmetrical guards that break into fast thrusts.
- Palette: limestone, lapis, sun-faded bronze, restrained gold.
- Defeat: spear telescopes shut, boss kneels briefly, then offers the weapon grip-first.

### Decimara Tide Marshal

- Lower, forward-leaning stance; two asymmetric forearm plates and a split mantle like converging channels.
- Twin pivot sabers that can lock into a short glaive. The connection must be mechanically believable and visible in close-up.
- Fighting personality: flowing redirects, delayed second blade, stance changes timed to water-gate surges.
- Palette: storm teal, deep blue, wet steel, pale route glass.
- Defeat: catches both blades point-down, unlocks the hilt, and marks the hero's bracer.

### Fracture Isles Chain Warden

- Compact triangular silhouette, layered hip armor, reinforced bracer, short rear mantle.
- Counterweight chain weapon with a blunt crescent blade and a non-spiked weight. Chain length is visually managed by a segmented energy tether to avoid unsafe noisy simulation.
- Fighting personality: readable large arcs, pulls, stance traps, and counterweight feints.
- Palette: oxide, charcoal steel, limestone dust, turquoise fracture glass.
- Defeat: drops the weapon into a safe coil and steadies a collapsing bridge instead of receiving a finishing blow.

## Weapon kit specifications

- Each family needs idle, locomotion, guard, parry, light chain, heavy, dodge-compatible, hit-react, victory, defeat, equip, and unequip coverage.
- Weapon geometry must support left/right mirroring only when physically valid; asymmetrical weapons receive dedicated facing poses.
- All blades are stylized but blunt enough for the age rating. Impact VFX use sparks, dust, route-light fractures, and cloth displacement.
- Contact points and authored reach are recorded in meters; visual trails never extend meaningful hit range.

### Starting splitstaff

- Two 0.85 m sections connected by a locking 0.30 m center sleeve.
- Collapsed back length under 0.95 m.
- Primary silhouette is a clean crescent at each butt cap, not spikes.
- Two-handed staff form for reach; split form for faster technique animation.

### Folding lance

- Extended length 2.15 m; collapsed length 1.05 m.
- Three clearly nested mechanical sections; no impossible telescoping volume.
- A narrow route-glass sight near the grip supports telegraph readability.

### Pivot sabers

- Each blade 0.72 m, with offset circular pivots that join into a 1.55 m glaive.
- Locked/unlocked states use separate collision and trail definitions.

### Counterweight chain

- Maximum authored attack radius 2.4 m.
- Use an animation-driven spline with 12–16 rendered segments, not unconstrained rigidbody chain simulation.
- Hand, blade, and weight paths are deterministic from the action phase; the tether settles after the primary body mass.

## Arena kits

### Shared requirements

- Combat floor: flat authored 16 m × 4 m gameplay strip, with invisible bounds and no geometry that changes collision readability.
- Background extends at least 60 m for parallax and cinematic angles.
- Foreground elements never cover fighters during commitment/contact frames.
- Three lighting states per arena: arrival, duel, restored.
- Modular architecture uses 2 m grid increments and metric pivots.
- Hero and boss must retain at least 4.5:1 luminance contrast from the immediate background at the gameplay camera.

### Valuehold Reach

- Wind-carved limestone terraces, suspended brass survey bridge, large nonliteral coordinate discs, high blue sky.
- The meridian line runs broken beneath the floor and reconnects behind the boss after victory.
- Required modules: floor slabs, parapets, bridge truss, survey arch, beacon, banners, distant terraces, clouds, dust wisps.

### Decimara Basin

- Reflective shallow channels outside the combat strip, stepped tide gates, storm light, wet stone.
- Reflections are screen-space/probe controlled; no full real-time planar reflection requirement.
- Required modules: dry fight platform, channel edge, gate, pipe/sluice forms, route glass, distant basin walls, rain cards, mist.

### Fracture Isles

- Fragmented floating masonry, restrained turquoise glass seams, deep cloud void, visibly stabilized combat platform.
- Background islands drift slowly; the gameplay floor never moves.
- Required modules: platform slabs, broken arches, bridge segments, floating rocks, glass seams, distant islands, cloud layers, dust trails.

## Animation system

### Engineering rule

Simulation state is deterministic at 60 Hz. Presentation evaluates animation from explicit action phase, normalized phase time, fighter state, velocity, facing, and stable action seed. Particles, camera, cloth, and weapon overlap derive from those semantic events. They never decide hit validity.

### Event phases

Every attack defines:

```text
rest/setup -> anticipation -> commitment -> contact -> follow-through -> recovery
```

Large actions may add anticipation hold, passing pose, overshoot, or settle. One generic easing curve may not drive the whole body, weapon, camera, VFX, and cloth.

### Baseline timing at 60 fps

| Action | Anticipation | Commit | Contact | Follow-through | Recovery | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Light opener | 6 f | 3 f | 2 f | 5 f | 9 f | 25 f |
| Light finisher | 8 f | 4 f | 2 f | 7 f | 11 f | 32 f |
| Heavy | 13 f + 2 f hold | 5 f | 3 f | 9 f | 15 f | 47 f |
| Parry | 3 f | 2 f | 6 f active | 4 f recoil | 7 f | 22 f |
| Dodge | 4 f | 12 f travel | — | 3 f overshoot | 8 f | 27 f |

These are the first-feel constants and must be tuned only through recorded playtest evidence. Faster weapons may redistribute frames but cannot remove readable anticipation from high-damage moves.

### Motion hierarchy

1. Preserve gameplay bounds, foot contacts, weapon reach, and facing.
2. Make the torso/hips path and key silhouette readable with untextured models.
3. Add pose change and compression around force and contact.
4. Add hand/weapon alignment and contact IK.
5. Add mantle, hair, chain, and plate overlap with delayed settle.
6. Add VFX, trails, hit-stop, audio, and camera impulse last.

### Selected animation principles

- **Anticipation:** every dangerous action shows direction and force before commitment.
- **Staging:** background and secondary movement quiet during critical poses.
- **Pose to pose:** launch, passing, contact, overshoot, and recovery frames are authored and reviewable.
- **Follow-through:** mantle, hair, weapon tether, and free hand reverse after the torso—not before it.
- **Arcs:** weapon tips and organic joints follow deliberate arcs; thrusts are straight only by intent.
- **Timing/spacing:** heavy attacks use longer preparation and faster spacing into contact; recovery communicates mass.
- **Exaggeration:** one readable variable per action—pose, spacing, hold, or recoil—not every channel.
- **Solid drawing:** center of mass stays over support during idle/guard; feet do not drift under deformation.

### Contact and hit readability

- A light hit may use 2 frames of hit-stop; heavy and boss signature hits use at most 4. Reduced-hit-stop mode uses 0–1.
- Camera impulse scales from verified impact force and is clamped; it cannot obscure the next telegraph.
- The attacker weapon and defender contact region must align within 8 cm at the authored contact frame for showcase attacks.
- Foot lock error remains under 3 cm during anticipation holds and under 6 cm during recovery pivots.
- Test important still frames without trails, particles, motion blur, or sound. If the action is unclear, fix posing before effects.

## Minimum animation library for the internal slice

### Shared humanoid

- Locomotion: idle, breathe variants, walk forward/back, dash, crouch, turn, guard locomotion.
- Defense: high/low block, parry left/right, dodge forward/back, guard break, knockdown, rise, surrender.
- Reactions: light high/mid/low, heavy recoil, stagger, wallless fall, defeat kneel.
- Social: intro stance, bracer inspect, weapon receive, route repair, three victory poses.

### Per weapon family

- Equip/unequip.
- Three-hit light chain.
- Two heavies with distinct height/reach.
- Crouching attack.
- Dash attack.
- Parry counter.
- One Focus technique.
- Boss-only signature and safe defeat handoff.

The internal slice ships the splitstaff and folding lance completely before adding pivot sabers and the chain. No bespoke arena art begins until two graybox fighters can complete a satisfying three-minute duel with these animations.

## VFX and audio

- Weapon trails are narrow route ribbons visible only above a speed threshold and clipped before recovery.
- Hit accents: small spark/dust for light, brief route-glass fracture for heavy, ground dust for knockdown.
- No blood substitute that visually reads as blood.
- Each weapon family has a different transient and material tail: staff wood/metal, lance telescoping ring, sabers bright paired cut, chain segmented whip.
- Boss telegraphs combine silhouette, a unique 250–500 ms sound, and a localized meridian pulse.
- Quiz UI uses quiet mechanical tracing, paper-glass movement, and confirmation tones; wrong-count feedback has no alarm or failure sting.

## Asset provenance and originality

- Record creator, source URL, purchase/download date, license text, allowed uses, modifications, and shipped file paths.
- No ripped assets, copied move data, traced characters, recreated UI, or generated prompt that names a copyrighted game or living artist.
- Free assets may fill rocks, foliage, sky, and background props only after palette/material normalization.
- Bespoke hero, bosses, signature weapons, meridian machinery, HUD, and quiz atlas define the game's identity.

## Performance budgets

- 60 fps target; 16.67 ms frame budget during combat.
- CPU main thread p95 under 8 ms; GPU p95 under 13 ms on the owner Mac at 1080p Medium.
- Maximum 1.8 million visible triangles in a gameplay shot; maximum 150 draw calls after SRP batching/instancing.
- No model inference during active combat. Arena VFX and audio remain stable while the local model is idle.
- LOD transitions are tested at gameplay and cinematic cameras; no visible skeleton pop or material reassignment.

## Asset acceptance checklist

- All four hero appearances animate on the same avatar without retarget warnings.
- Every armor combination clears every weapon arc and crouch/dodge pose.
- Key silhouettes read in grayscale at gameplay distance.
- Required contact and foot-lock tolerances pass recorded frame tests.
- Materials use the Wayline palette and do not resemble unmodified marketplace packs.
- All files have valid provenance and license records.
- FBX scale, axes, pivots, sockets, LODs, texture channels, and naming pass an automated Unity import audit.
- The scene remains readable with VFX disabled and with reduced motion/flashes enabled.

