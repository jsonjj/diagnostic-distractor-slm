# Mathbreakers: Glitch Convoy — 3D Asset Production Brief

## Purpose

This is the handoff for anyone modeling the first portfolio vertical slice. It is written so a Blender artist, technical artist, or procedural-modeling agent can build assets without inventing missing design decisions.

The target is **top-tier stylized realism in a browser**, not photorealism. Quality comes from silhouette, proportion, materials, lighting response, animation, and a coherent visual language. Do not spend geometry on tiny detail that disappears behind the gameplay camera.

The complete slice needs:

- one hero player vehicle;
- one shared enemy chassis with three misconception kits;
- one modular convoy boss;
- one shared NPC body/rig with three crew variants;
- one compact canyon relay district kit;
- a small family of props and collision meshes;
- Proof Circuit conduit pieces and VFX proxy meshes.

If only one custom asset can be made first, make the **Proofrunner R-6 player vehicle**. If a second can be made, create the **shared Glitch chassis and its three modular family kits**. Terrain, rocks, surface materials, vegetation, and minor props can be procedural or sourced from verified CC0 libraries.

---

## 1. Visual Thesis

### World

The game takes place in a sunlit desert-canyon maintenance district built around an experimental energy road. The technology is practical rather than sleek: powder-coated steel, ceramic insulators, woven straps, rubber seals, dusty glass, bolted repair plates, copper bus bars, faded safety paint, and modular components that look serviceable by hand.

### Signature element: the Proof Circuit

A cyan-and-amber energy circuit physically links the question, road, selected answer, enemy shield, and repair weapon. When a misconception wins control, the circuit takes a visibly wrong path. When the player repairs the reasoning, moving energy reconnects the correct route.

The circuit must feel like infrastructure, not a generic hologram. It sits inside ceramic channels, jumps across contact pins, pulses through braided cables, and illuminates dust close to the ground.

### Shape language

- **Trusted/player technology:** forward-leaning wedges, continuous curves, paired components, aligned seams, and protected luminous conduits.
- **Glitch technology:** interrupted arcs, offset centers, mismatched plates, visibly incorrect repetition, and mechanisms that enact the mathematical mistake.
- **World infrastructure:** large honest load-bearing shapes, exposed fasteners, thick cable runs, ceramic separators, and sun-faded paint.
- **NPC gear:** approachable rounded protection over practical angular tools.

### Palette

- Basalt structure: `#171C1E`
- Warm ceramic: `#E7E0D3`
- Oxide identification: `#B95635`
- Solar power: `#F2B84B`
- Diagnostic circuit: `#58C8D0`
- Confirmed repair: `#62A77D`

Color never carries gameplay meaning by itself. Shape, motion, icon, and label must also communicate each state.

---

## 2. Universal Technical Standard

### Coordinate system and scale

- Model in meters at real-world scale.
- World up: `+Y`.
- Asset forward: `+Z`.
- Asset right: `+X`.
- Apply object scale and rotation before delivery: scale `1,1,1`; rotation `0,0,0`.
- Place ground-contact assets at `Y = 0`.
- Put vehicle origin on the centerline at ground level, vertically below the center of mass.
- Put prop origin at the logical placement or hinge point, never at an arbitrary bounding-box corner.
- No negative scale in the delivered hierarchy.

### Source and runtime delivery

Deliver each asset as:

1. editable `.blend` source with clean named collections;
2. runtime `.glb` using glTF 2.0 metallic-roughness PBR;
3. source textures as lossless PNG or TIFF;
4. a preview turntable image;
5. a text manifest listing author, license, dimensions, triangle counts, materials, texture sizes, clips, and known restrictions.

Unity portability is achieved through GLB and clean transforms. An FBX copy is optional, not authoritative.

### Mesh rules

- Use quads in source where they improve editing and deformation; triangulate predictably on export.
- Remove hidden interior faces unless needed for an opening animation or visible silhouette.
- Use weighted normals or deliberate hard edges on manufactured forms.
- Support bevels with geometry where they affect silhouette; use normal maps for sub-centimeter detail.
- Avoid long thin triangles, non-manifold edges, lamina faces, zero-area faces, and overlapping coplanar surfaces.
- Keep mirrored UVs away from unique text, damage, and directional dirt.
- Bake small bolts, panel seams, weave, and stamped markings into normals rather than modeling each piece.
- Every moving piece must be a separate mesh or a skinned part with a documented pivot.

### Material limits

- Player vehicle: maximum 4 runtime materials.
- Boss: maximum 5 runtime materials.
- Standard enemy: maximum 3 runtime materials.
- NPC variant: maximum 3 runtime materials.
- Medium environment module: maximum 2 runtime materials.
- Small prop: 1 material wherever possible.

Preferred material families:

1. painted metal and exposed metal;
2. rubber, fabric, and dark technical parts;
3. glass and emissive circuit;
4. character cloth/armor where applicable.

### Texture packing

Use:

- Base Color with no baked lighting;
- tangent-space OpenGL Normal (`+Y` green channel);
- packed ORM: red = ambient occlusion, green = roughness, blue = metallic;
- Emissive RGB;
- optional mask texture: red = paint color region, green = dirt amount, blue = wear amount, alpha = gameplay pulse mask.

Maximum source resolution:

- hero player vehicle and boss: one 2048 atlas per major material family;
- enemy kits and NPCs: 2048 for the primary atlas, 1024 for secondary gear;
- environment hero structures: 2048 trim sheet plus tileable surfaces;
- common props: 1024 atlas;
- tiny props: 512 atlas.

Runtime textures are converted to KTX2/Basis. Do not ship uncompressed 4K maps.

### UV requirements

- UV0: non-overlapping for unique baked detail, except intentional mirrored mechanical areas.
- UV1: non-overlapping lightmap UVs with at least 4 pixels of final-runtime padding.
- Target density: 512 px/m for hero vehicles and close NPC gear; 256 px/m for environment modules; 128 px/m for distant rocks.
- Straighten UV islands for pipes, cables, straps, rails, and trim-sheet elements.

### LOD policy

Every gameplay asset needs LODs authored against screen size, not arbitrary distance.

- `LOD0`: hero view and turntables.
- `LOD1`: about 50–60% of LOD0 triangles; preserve silhouette and all moving parts.
- `LOD2`: about 20–30% of LOD0; merge small forms and remove secondary cavities.
- `LOD3`: about 5–10% of LOD0 for large vehicles or repeated scenery; silhouette only.

Avoid automatic decimation on visible circular wheels, faces, hands, operation symbols, fraction plates, or silhouette-defining fins. Rebuild those areas manually.

### Collision

- Prefix gameplay collisions with `COL_`.
- Use boxes, capsules, cylinders, and small convex hull sets.
- Never use the render mesh as a moving rigid-body collider.
- Player and enemy chassis need one central body hull plus separate wheel/contact shapes managed by code.
- Static scenery may use simple compound convex collision; track borders need continuous low-detail blockers without snagging edges.

### Pivots, anchors, and sockets

Use empty transform nodes prefixed `SOCKET_`. Required examples:

- `SOCKET_Camera_Chase`
- `SOCKET_Camera_Close`
- `SOCKET_ProofCore`
- `SOCKET_Weapon_Muzzle`
- `SOCKET_Hit_Left`
- `SOCKET_Hit_Right`
- `SOCKET_Dust_FL`, `FR`, `RL`, `RR`
- `SOCKET_Hood_Module`
- `SOCKET_Roof_Module`
- `SOCKET_Side_Left`, `SOCKET_Side_Right`
- `SOCKET_Computation_Display`

Sockets use the same forward/up convention as the root and contain no render geometry.

### Naming

Use:

```text
GC_<CATEGORY>_<ASSET>_<PART>_<LOD>
```

Examples:

```text
GC_VEH_Proofrunner_Body_LOD0
GC_ENM_FractionForger_JawLeft_LOD0
GC_NPC_Mara_Helmet_LOD0
GC_ENV_RoadCurve30_A_LOD1
GC_PROP_CableSpool_A_LOD0
GC_COL_Proofrunner_Body
GC_SOCKET_Proofrunner_WeaponMuzzle
```

Animation clips use `GC_<ASSET>_<ACTION>`, such as `GC_Proofrunner_BoostCharge`.

### Scene performance target

- Normal gameplay view: under 500,000 visible rendered triangles.
- Boss view: under 750,000 visible rendered triangles.
- Normal gameplay: under 120 draw calls before UI.
- Boss scene: under 180 draw calls before UI.
- Use GPU instances for rocks, barriers, pylons, solar panels, bolts, signs, and vegetation.
- No more than two real-time shadow-casting lights.
- Transparent blended materials are reserved for glass and essential VFX; prefer alpha clip or opaque dither.

---

## 3. Asset Priority and Ownership

| Priority | Asset | Preferred source | Why |
|---|---|---|---|
| P0 | Proofrunner R-6 player vehicle | Custom model | Always on screen; defines quality bar |
| P0 | Shared Glitch chassis + 3 kits | Custom modular model | Makes the SLM's misconceptions visible |
| P0 | Proof Circuit kit | Custom procedural geometry/shader | Signature visual thesis |
| P1 | Convoy boss | Custom kitbash using Glitch modules | Portfolio climax and adaptive proof |
| P1 | Crew suit base + 3 variants | Custom/simple shared rig or carefully licensed free base | NPC identity without three separate humans |
| P1 | Relay yard hero structures | Custom modular kit | Establishes unique world identity |
| P2 | Roads, barriers, crates, cables | Procedural/custom simple meshes | Fast to build and heavily reusable |
| P2 | Canyon rocks and ground materials | CC0/procedural | Generic natural assets do not need custom sculpting |
| P2 | Vegetation and debris | CC0/procedural | Set dressing only |
| P3 | Tiny workshop clutter | CC0 or atlased simple models | Least visible gameplay value |

Do not mix unrelated “free sci-fi” packs. Generic assets may provide raw rocks, cloth scans, metal surfaces, or small workshop objects, but hero technology must share this brief's shape language and material system.

---

## 4. Hero Vehicle — Proofrunner R-6

### Role

The Proofrunner is the player's traversal vehicle, combat platform, cursor through answer lanes, and the most frequently viewed object. It must look appealing in silhouette before surface detail is added.

### Dimensions

- Length: 4.45 m.
- Width: 2.08 m excluding mirrors.
- Height: 1.72 m to roof equipment.
- Wheelbase: 2.68 m.
- Track width: 1.72 m.
- Ground clearance: 0.31 m.
- Wheel diameter: 0.86 m.
- Tire width: 0.30 m.

### Design description

Create an original electric rally-raid repair vehicle—roughly the functional category of a compact Dakar support car, without copying any real manufacturer.

The cabin occupies the front 45% of the body and has a wide protective windshield under a ceramic sun visor. The hood is short because there is no combustion engine. The rear 55% carries a visible cylindrical **Proof Core** enclosed by two arched ceramic guards. A low repair cannon folds between those guards rather than looking like a military weapon.

The main silhouette slopes forward and outward at the wheels. Fenders are broad, replaceable shells with three visible fasteners each. The sides tuck inward below the doors to expose durable suspension arms. The roof line stays low; antennas and the repair cannon provide the only thin vertical shapes.

One deliberate asymmetry makes it memorable: the left rear carries a cable reel and diagnostic arm, while the right rear carries two stacked ceramic battery modules. Balance the visual mass so the vehicle still reads stable.

Windows are dark smoked glass with a faint interior shell. The first slice does not need a modeled dashboard, seats, or visible driver. Do not waste geometry on a detailed cabin.

### Required visible components

- body shell and lower protective tub;
- four distinct wheel/tire assemblies with readable tread silhouette;
- front and rear suspension arms visible through wheel openings;
- ceramic Proof Core guards;
- cylindrical core with emissive pulse mask;
- folded repair cannon with two-axis pivot;
- left cable reel and hinged diagnostic arm;
- right battery module stack;
- front tow/repair hooks;
- underbody skid plate;
- two headlights and one continuous rear service light;
- flexible mud flaps;
- two short fabric recovery straps;
- protected cyan circuit channels running from wheels toward the Proof Core;
- blank rally number plates using a runtime decal, not baked text.

### Geometry budgets

- LOD0: 90,000–120,000 triangles.
- LOD1: 48,000–65,000 triangles.
- LOD2: 18,000–28,000 triangles.
- LOD3: 5,000–8,000 triangles.
- Collision: fewer than 300 triangles across convex hulls, excluding wheel primitives.

Allocate LOD0 approximately:

- body and fenders: 35%;
- wheels/tires: 25%;
- suspension and underbody: 12%;
- Proof Core/cannon: 18%;
- cables, straps, lights, and accessories: 10%.

### Materials

1. painted body and exposed metal;
2. rubber, suspension, underbody, and fabric;
3. ceramic, glass, and emissive Proof Circuit;
4. optional decal sheet.

Paint should be satin rather than mirror glossy. Exposed edges reveal dark primer, not bare chrome. Dust accumulates behind wheels, along lower doors, and around the rear core guards. Keep wear directional and mechanically plausible.

### Rig and hierarchy

```text
Root
├── Body
├── Wheel_FL
├── Wheel_FR
├── Wheel_RL
├── Wheel_RR
├── Steering_FL
├── Steering_FR
├── Suspension_FL
├── Suspension_FR
├── Suspension_RL
├── Suspension_RR
├── Mudflap_FL / FR / RL / RR
├── ProofCore
├── Cannon_Yaw
│   └── Cannon_Pitch
│       └── Cannon_Barrel
├── DiagnosticArm_Base
│   └── DiagnosticArm_Elbow
├── CableReel
└── Antenna_A / Antenna_B
```

Wheels rotate on local X and front steering pivots yaw on local Y. Suspension moves vertically without changing wheel scale. Cannon pivots must allow at least 110 degrees yaw and 35 degrees pitch.

### Required clips

- `Idle_Mechanical`: 3.0 s loop; slight suspension settling, core breathing pulse, antenna follow-through.
- `Startup`: 1.2 s; protective core leaves open, circuit wakes front-to-back.
- `BoostCharge`: 0.65 s; body lowers 4 cm, rear suspension compresses, core brightens.
- `BoostRelease`: 0.45 s; body pitches back then settles.
- `BrakeHard`: 0.5 s; front compression and mud-flap follow-through.
- `HitLeft` and `HitRight`: 0.4 s non-destructive body roll reactions.
- `RepairCannonDeploy`: 0.8 s; cannon unfolds with clear anticipation.
- `RepairCannonFire`: 0.6 s; short recoil, cable and antenna secondary motion.
- `VictorySettle`: 1.4 s; small controlled hop and suspension recovery, not a cartoon bounce.

Driving wheel spin and suspension travel remain procedural in code; clips provide authored body response.

### Acceptance views

The model must remain recognizable:

- as a black silhouette at 128 px wide;
- from the 6 m chase camera;
- from three-quarter front and three-quarter rear;
- with emissive maps disabled;
- in grayscale without paint differentiation.

---

## 5. Shared Glitch Chassis

### Role

All normal enemies share a compact autonomous service-drone chassis. Three modular misconception kits create distinct silhouettes without requiring three unrelated vehicles.

### Dimensions

- Core length: 2.85 m.
- Core width: 1.75 m.
- Core height: 1.15 m before family kit.
- Contact-pod diameter: 0.52 m.
- Ground clearance: 0.22 m.

### Base design

The base is a squat four-pod utility drone with a protected central processor and a blank computation display. The pods may roll or hover a few centimeters depending on animation, but they must read as mechanical load-bearing units rather than magical floating parts.

The chassis has three standardized attachment rails:

- roof spine;
- left/right side rails;
- front mechanism ring.

The body should look incomplete without a family kit. Use offset seams and exposed contacts where modules attach.

### Budget

- Base LOD0: 32,000–42,000 triangles.
- Each family kit LOD0: 18,000–30,000 triangles.
- Combined enemy LOD0: no more than 65,000 triangles.
- LOD1 combined: 28,000–35,000.
- LOD2 combined: 9,000–14,000.
- LOD3 combined: 2,500–4,000.

### Shared rig

```text
Root
├── Chassis
├── Pod_FL / FR / RL / RR
├── Core
├── Display
├── Module_Roof
├── Module_Front
├── Module_Left
└── Module_Right
```

Family kits may add bones below the corresponding module root. Keep shared bone names unchanged.

### Shared clips

- `SpawnUnfold`: 0.9 s.
- `IdleScan`: 2.5 s loop.
- `Advance`: 1.0 s loop layered with procedural movement.
- `AttackAnticipation`: 0.45 s.
- `Stagger`: 0.5 s.
- `RepairBreak`: 1.0 s; panels release into safe suspended parts and retract with repair tape/light, not explosive destruction.

---

## 6. Glitch Kit A — Fraction Forger

### Mathematical tell

Combines fraction parts without first making equal-sized pieces, or manipulates numerator and denominator as unrelated whole numbers.

### Silhouette

This is the widest enemy. Two segmented shield jaws project from the front and sides, creating a broad crab-like outline. The upper jaw carries numerator tiles and the lower jaw carries denominator divisions. Do not bake fixed numbers into geometry; use blank plates with runtime decals.

### Required parts

- left and right three-segment front jaws;
- upper numerator rail with two sliding blank tiles;
- lower denominator drum with 4, 6, 8, and 12 physical segment grooves but no printed values;
- two unequal side shield plates that visibly fail to line up;
- central “equalizer” hinge exposed during repair;
- three sockets for dynamic fraction-strip VFX.

### Motion

- Idle: jaws test-fit and fail by a small offset.
- Attack: unequal plates slam together, leaving a visible gap; anticipation 180 ms, close 220 ms, settle 300 ms.
- Reveal: numerator tiles slide together while denominator drum rotates incorrectly.
- Repair: both side plates divide into matching segment widths, align, and lock with one satisfying centered motion.

### Kit budget

- LOD0: 24,000–30,000 triangles.
- Four moving jaw sections must retain shape through LOD1.
- Use geometry for major plate divisions; use normal/emissive textures for minor tick marks.

---

## 7. Glitch Kit B — Decimal Drifter

### Mathematical tell

Misaligns place values, drops a decimal, or shifts the decimal the wrong number of places.

### Silhouette

This is the lowest and longest enemy. Three lateral digit carriages sit on parallel rails, producing an unmistakable offset silhouette. Wheel/contact pods lean outward like a vehicle drifting sideways.

### Required parts

- three blank digit carriages with runtime display surfaces;
- one illuminated decimal-marker bead that can travel across five detents;
- two telescoping lateral rails;
- left/right stabilizer fins that counter-slide;
- narrow front sensor bar;
- place-value baseline projector socket.

### Motion

- Idle: carriages settle into alignment, then one slips half a place.
- Attack: the decimal bead accelerates past the correct detent while the body drifts laterally.
- Reveal: each carriage steps sideways in sequence so the mistaken alignment is readable.
- Repair: a projected baseline appears first; carriages snap to it from largest place to smallest, followed by the decimal bead.

### Kit budget

- LOD0: 18,000–24,000 triangles.
- Rails need enough radial segments to hold highlights but no hidden internal screw detail.
- Sliding carriages must remain separate nodes through LOD2.

---

## 8. Glitch Kit C — Operation Swapper

### Mathematical tell

Substitutes addition, subtraction, multiplication, or division for the operation described by the problem.

### Silhouette

This is the tallest normal enemy. A four-sided operation carousel rises above the chassis like a mechanical route signal. Each face has a physically cut symbol plate: `+`, `−`, `×`, or `÷`.

### Required parts

- four-face rotating carousel;
- four separate high-contrast symbol plates;
- locking pawl and visible index ring;
- two side actuator arms;
- front story-action indicator socket;
- folding protective hoop that frames the selected symbol.

### Motion

- Idle: carousel nudges between detents but the locking pawl catches it.
- Attack: one side actuator pulls the lock, the carousel overshoots to the wrong operation, then the hoop clamps.
- Reveal: carousel repeats the swap slowly once while the computation display updates.
- Repair: the player-selected story action illuminates; the carousel reverses to the matching operation and the lock seats visibly.

### Kit budget

- LOD0: 20,000–26,000 triangles.
- Symbols must be silhouette geometry, not texture-only, so they remain legible under glare and for color-impaired players.
- Carousel is one rigid animated node; symbol plates are separate replaceable children.

---

## 9. Convoy Boss — The Counterfeit Hauler

### Role

The boss demonstrates adaptation by mounting modules from Glitch families observed earlier in the run. It is a moving repair convoy corrupted into a modular challenge—not a tank and not a lethal war machine.

### Dimensions

- Length: 8.8 m.
- Width: 2.75 m.
- Height: 3.15 m with highest module deployed.
- Eight wheels, each 0.95 m diameter.
- Ground clearance: 0.38 m.

### Design

Use a long articulated utility carrier with a front cab, center Proof Core cage, and three rear module docks. The boss must look complete with any combination of the three Glitch kits. Each dock has identical mounting geometry and a unique cable route so active modules illuminate one at a time.

The cab is unmanned and has a wide amber sensor visor. The central cage exposes a large ceramic-ringed core. The rear silhouette forms a stepped convoy profile rather than a military turret.

### Required parts

- front four-wheel cab unit;
- rear four-wheel carrier unit;
- articulated yaw joint with limited pitch;
- central core cage with three breakable-but-repairable ceramic rings;
- three universal module docks;
- left/right cable trunks;
- retractable stabilizers for arena phases;
- computation billboard socket;
- repair tape dispensers used in defeat animation.

### Budget

- LOD0 base without modules: 85,000–110,000 triangles.
- LOD1: 48,000–60,000.
- LOD2: 20,000–28,000.
- LOD3: 6,000–9,000.
- Reuse normal-enemy module meshes; do not build separate boss copies.
- Combined boss plus three modules: maximum 190,000 LOD0 triangles.

### Rig and clips

- eight wheel nodes;
- front/rear chassis roots;
- articulation joint;
- three dock roots;
- core ring A/B/C;
- stabilizer nodes;
- cable trunk bones for secondary motion.

Clips:

- `ArenaEntry`: 2.5 s.
- `DeployStabilizers`: 1.0 s.
- `ActivateDockA/B/C`: 0.8 s each.
- `CoreExpose`: 1.2 s.
- `StaggerLeft/Right`: 0.6 s.
- `RepairedShutdown`: 2.4 s; modules fold, ceramic rings realign, service lights turn green, and repair tape secures loose panels.

---

## 10. NPC Crew System

### Scope decision

Do not build three photoreal human faces. That would consume more time than the hero vehicles and still look weaker. Build one shared, helmeted field-crew body with an opaque expressive visor, then create three silhouette/gear variants. The visor uses simple emissive brow and eye marks; dialogue carries personality without lip sync.

### Shared body

- Adult field mechanic proportions, about 1.72 m nominal height.
- Practical coverall, knee/elbow protection, boots, gloves, soft hood, hard helmet shell, opaque visor.
- Friendly rounded torso protection; tools use angular shapes.
- No exposed hair, face, or skin required.
- Hands need mitten-like palm geometry with grouped index, thumb, and remaining fingers for readable pointing.

### Shared budget

- Body LOD0: 28,000–36,000 triangles.
- Gear per variant: 6,000–12,000 triangles.
- Combined NPC LOD0: maximum 45,000.
- LOD1: 20,000–24,000.
- LOD2: 7,000–10,000.
- Two core materials plus one visor/tool material.

### Rig

Use a humanoid rig with:

- root and pelvis;
- three-spine chain;
- neck and head;
- clavicle, upper arm, forearm, hand per side;
- thumb, index, and grouped-finger controls;
- thigh, calf, foot, and toe per side;
- two visor-brow bones;
- two chest-strap bones;
- gear attachment sockets at back, chest, hips, wrists, and helmet.

Keep bone count below 70. Provide an A-pose bind pose and in-place animation clips.

### NPC A — Mara, relay engineer

- Height target: 1.66 m through proportional scaling before bind.
- Broad grounded silhouette with a short utility jacket over the shared suit.
- Left-hip cable spool, right-wrist diagnostic pad, rounded backpack with two ceramic insulators.
- Oxide identification panels and warm ceramic helmet shell.
- Personality through pose: weight forward, hands active, direct pointing.

### NPC B — Ivo, route scout

- Height target: 1.80 m.
- Tall narrow silhouette with a weather hood over the helmet and a folded route-scanner mast on the backpack.
- Slim chest harness, binocular scanner, small pennant strip that reacts to wind.
- Basalt and muted amber identification.
- Personality through pose: looks into distance, gestures broad route arcs.

### NPC C — Jun, systems mechanic

- Height target: 1.72 m.
- Medium silhouette with asymmetric tool apron and a compact powered service gauntlet on the right arm.
- Two magnetic tool blocks on the thighs and a rectangular back battery.
- Ceramic and diagnostic cyan identification.
- Personality through pose: checks devices, precise small gestures, reserved celebration.

### Required shared animation clips

- neutral idle A/B/C, 4 s loops;
- radio listen, 2 s loop;
- radio speak with restrained hand movement, 3 s loop;
- point left, right, and forward, 1.1 s each;
- present tablet, 1.0 s in and 1.0 s out;
- crouch repair, 2.5 s loop;
- concerned recoil, 0.6 s;
- relieved exhale/shoulder settle, 1.2 s;
- cheer small and cheer large, 1.4 s each;
- wave greeting, 1.6 s;
- walk, 1.0 s loop, in place.

Visor brows may animate with the clips, but avoid emoji faces and exaggerated cartoon expressions.

---

## 11. Canyon Relay District Kit

### Level footprint

The authored slice occupies approximately 240 m by 180 m with a 600–750 m drivable loop. It contains:

- garage/start court;
- NPC relay yard;
- canyon transit section;
- one bridge decision gate;
- one repair arena;
- one 45 m boss basin;
- connecting roads and sightline blockers.

Do not model the district as one mesh. It must be assembled from repeatable modules and engine-generated terrain.

### Road kit

Road width is 7.0 m, with a 5.5 m clear driving surface and 0.75 m service shoulders.

Create:

- 10 m and 20 m straight modules;
- 15-degree, 30-degree, 45-degree, and 90-degree curves at a 14 m center radius;
- matching curves at a 24 m center radius;
- 10 m transition from level road to 8-degree grade;
- 20 m ramp at 8 degrees;
- 12 m four-answer gate platform with four 2.6 m visual channels that merge into the drivable road;
- 18 m bridge span;
- T junction and Y fork;
- road end cap;
- separate left/right safety rail modules;
- separate ceramic Proof Circuit trench and cover modules.

Road modules use a shared trim sheet and tileable surface. Do not bake directional arrows or fixed answers; those are runtime decals.

### Cliff kit

Create or source CC0 rock forms, then retopologize/atlas them into:

- 10 × 6 m cliff face A/B/C;
- 20 × 10 m cliff face A/B;
- convex corner 45 and 90 degrees;
- concave corner 45 and 90 degrees;
- cliff top cap;
- talus slope;
- natural arch large enough for the vehicle;
- six instanced boulders from 0.5 to 4 m.

Cliffs use vertex-color material blending between rock, dust, and dark crevice. Collisions are separate low-resolution hulls.

### Hero structures

#### Garage facade

- Footprint: 18 × 12 m; height 6.5 m.
- Exterior and shallow 3 m interior only.
- Two large service doors, one player bay, suspended cable rack, solar canopy, and roof signal.
- No full interior workshop required.

#### Relay tower

- Height: 16 m.
- Three ceramic energy rings around a triangular steel mast.
- Large silhouette visible from most of the loop.
- Central pulse travels upward when the district is repaired.

#### NPC service bay

- Footprint: 10 × 7 m.
- Raised 0.2 m platform, fabric shade, waist-high console, tool wall, two battery racks, and NPC anchor markers.

#### Decision gate

- Four heavy ceramic contact arches integrated into the road.
- Each arch has a blank answer display surface and a physical circuit contact.
- Gates remain visually neutral before commitment; no unique material or wear reveals the correct option.

#### Boss basin

- 45 m clear circular driving area.
- Three large service pylons around the edge, each corresponding to a possible Glitch module.
- Low cover elements that do not snag vehicle collision.
- One overlook for NPC radio projection.

### Environment module budgets

- Hero structure LOD0: 30,000–70,000 triangles.
- Medium module LOD0: 4,000–18,000.
- Small repeated module LOD0: 300–3,000.
- Each repeated object needs at least LOD1 and LOD2.
- Use trim sheets and atlases so the district needs no more than eight environment material families.

---

## 12. Prop List

### Required gameplay-readable props

- ceramic circuit junction box;
- repair battery tall and short;
- cable spool large and small;
- collapsible road barrier;
- service pylon;
- solar panel single and 2 × 3 array;
- tool console;
- portable floodlight;
- blank rally sign in three proportions;
- bridge actuator;
- repair tape dispenser;
- fraction-strip projector base;
- place-value baseline projector;
- operation-symbol lock.

### Set dressing

- three crate sizes;
- two fabric-covered cargo bundles;
- four pipe elbows and two straight pipe lengths;
- cable bundle straight, curved, and hanging;
- two traffic cone forms;
- wheel chock;
- water tank;
- compressed-air cylinder rack;
- dust tarp;
- six debris clusters;
- four dry grass clumps;
- three low desert shrubs.

Repeated props share atlases and color masks. Avoid small readable English text; use simple original symbols and runtime labels.

---

## 13. Proof Circuit and VFX Geometry

Most effects are procedural in Babylon.js. The modeler supplies reusable profiles and anchors, not a baked effect for every scene.

### Required meshes

- straight ceramic conduit, 1 m;
- inside/outside 15-degree conduit bend;
- 45-degree and 90-degree bends;
- T and four-way junctions;
- circular contact pad;
- gate contact tongue;
- cable plug male/female;
- repair-beam core cylinder;
- three concentric beam rings;
- shield plate hex and quarter-arc;
- dust-card quad with soft geometry curl;
- spark shard A/B/C;
- repair tape strip with 6-bone chain;
- computation display plane with beveled protective frame.

### Vertex data

- Vertex color red: pulse phase offset.
- Green: emissive intensity mask.
- Blue: repair-state blend mask.
- Alpha: edge fade or dissolve mask.

### Motion requirements

- Energy pulse follows spline direction and never flickers randomly.
- Glitch routes visibly branch at a junction before an enemy attack.
- Repair beam travels from player to faulty mechanism, not simply screen center.
- Settling pieces follow the primary repaired mechanism with 80–160 ms delay.
- Reduced-motion mode disables camera shake, long travel, and loose-parts follow-through while preserving state changes.

---

## 14. Free and Procedural Sourcing Rules

### Safe candidates for free sourcing

- HDR environments;
- rock scans and tileable ground;
- generic fabric, rubber, metal, and dust materials;
- workshop hand tools;
- minor vegetation;
- generic audio and particle textures;
- base humanoid motions if redistribution terms allow derived animation data.

Prefer CC0. Permissive attribution licenses may be used only if the attribution requirement is recorded and compatible with repository redistribution.

### Must remain original or substantially redesigned

- player vehicle silhouette;
- Glitch family silhouettes and mechanisms;
- boss assembly;
- Proof Circuit channel language;
- operation/fraction/decimal gameplay mechanisms;
- named NPC gear combinations;
- logos, symbols, rally markings, and interface graphics.

### Reject an asset when

- “free” means noncommercial only;
- the license forbids redistribution in a game build;
- authorship or provenance is unclear;
- it copies a recognizable vehicle, game, film, or character design;
- it requires hotlinking or a runtime account;
- it has no editable source and cannot meet performance/UV requirements;
- its art style cannot be reconciled with the shared material and shape language.

Record source URL, author, license text, download date, original filename, modifications, and final runtime paths in `game3d/ASSET_LICENSES.md`.

---

## 15. Delivery Folder

```text
art-source/
├── vehicles/proofrunner/
│   ├── GC_VEH_Proofrunner.blend
│   ├── textures/source/
│   └── previews/
├── enemies/glitch_chassis/
├── enemies/fraction_forger/
├── enemies/decimal_drifter/
├── enemies/operation_swapper/
├── boss/counterfeit_hauler/
├── characters/crew_base/
├── characters/mara/
├── characters/ivo/
├── characters/jun/
├── environment/road_kit/
├── environment/cliff_kit/
├── environment/relay_yard/
├── props/
└── vfx/proof_circuit/

game3d/public/assets/
├── vehicles/
├── enemies/
├── characters/
├── environment/
├── props/
├── vfx/
└── manifest.json
```

Do not commit raw downloaded archives. Keep only licensed source needed for modification, optimized runtime exports, previews, and the license record.

---

## 16. Modeler Acceptance Checklist

An asset is not accepted until all applicable items pass.

### Form

- silhouette matches its role at 128 px;
- no copied brand or recognizable franchise design;
- dimensions and ground contact match the brief;
- major moving parts are readable from the chase camera;
- family mechanic remains understandable without color or emissive effects.

### Geometry

- budgets met at every LOD;
- clean normals and intentional hard edges;
- no non-manifold, duplicate, hidden, or zero-area geometry;
- no negative scales;
- moving pivots tested;
- collision meshes supplied and visibly simpler than render geometry.

### Materials

- material slot limit met;
- base color contains no baked lighting;
- normal map uses OpenGL orientation;
- ORM channels verified;
- emissive is masked and does not wash out in daylight;
- wear and dust follow physical exposure.

### UV and export

- UV0 and UV1 meet requirements;
- GLB opens with correct scale and orientation;
- texture paths are embedded or relative and complete;
- LOD names, sockets, clips, and custom properties survive export;
- no unsupported Blender-only material nodes determine the final look.

### Animation

- clips are in place unless root motion is explicitly requested;
- loops have no visible pop;
- vehicle wheels and steering use correct axes;
- secondary motion follows, rather than precedes, the primary action;
- repair/deactivation reads as safe mechanical resolution, not injury or destruction.

### Performance

- asset renders in the target scene within draw-call and triangle budgets;
- KTX2 conversion succeeds;
- LOD transitions preserve silhouette;
- repeated props instance correctly;
- transparent overdraw is bounded.

---

## 17. Recommended Modeling Order

1. Block out Proofrunner proportions and chase-camera silhouette.
2. Block out shared Glitch chassis beside it for scale.
3. Build crude Fraction, Decimal, and Operation modules; test whether each mistake reads in motion.
4. Graybox the road, gate, relay yard, and boss basin.
5. Finish Proofrunner LOD0, rig, materials, and animations.
6. Finish shared chassis and all three module kits.
7. Build Proof Circuit conduit kit and runtime material masks.
8. Kitbash the boss from validated module interfaces.
9. Build one crew body and Mara variant; validate scale and gestures.
10. Finish Ivo and Jun gear variants.
11. Replace graybox hero structures and roads with final modules.
12. Add sourced CC0 rocks/materials, then props and vegetation.
13. Author LODs, collisions, KTX2 textures, and final GLBs.
14. Run silhouette, animation, accessibility, license, and performance acceptance.

This order prevents weeks of environment polishing before the vehicle, Glitches, and mathematical mechanics—the assets that make the project unique—are proven.
