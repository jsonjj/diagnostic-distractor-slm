# Counterfeit Protocol — 3D Asset Production Brief

**Gameplay authority:**
[`superpowers/plans/2026-07-11-counterfeit-protocol-competence-engine.md`](./superpowers/plans/2026-07-11-counterfeit-protocol-competence-engine.md)

**Status:** Proposal for owner/modeler review; do not commission or generate assets until
the owner approves the visual direction.  
**Engine target:** Unity `6000.3.11f1`, Universal Render Pipeline, Apple Silicon and
Windows desktop.  
**World scale:** `1 Unity unit = 1 meter`, `+Y` up, `+Z` forward.  
**Authoritative runtime format:** triangulated FBX; `.glb` is optional archival/reference
delivery and is not a Unity runtime dependency.  
**Visual target:** grounded, cinematic science fiction with believable industrial
construction, original silhouettes, and nonlethal repair technology. The art must make
an input, a counterfeit rule, its output, and the player's repair physically legible;
it must not use spectacle as a substitute for causal readability. Avoid imitating a
specific franchise, vehicle, character, logo, or artist.

## Priority order

1. **Aegis R4 Repair Rover** — mandatory hero asset and highest-detail model.
2. **Logic Core and four Answer Conduits** — mandatory signature game-mechanic asset.
3. **Counterfeit Drone Kit** — mandatory for readable rule execution and the repaired
   companion loop; licensed placeholders are acceptable until the first two assets pass
   in-engine review.
4. **World Manipulation and Nia Interface Kit** — mandatory modular affordances needed
   to turn forecast, inspection, manipulation, and rerun into world actions; only one
   complete system family is required before wider environment production.

The first commission should be the rover. Do not spend time on a modeled human NPC,
full cockpit, open-world props, or vehicle destruction for the vertical slice.
Before any paid or generated production begins, execute the provenance/IP agreement in
the Delivery Package section; do not wait until final delivery to discover a rights gap.

## Competence-engine art contract

The game does not reward a correct answer with a decorative prize or punish a wrong
answer with a destruction animation. It lets the player cause an effect, inspect the
rule behind that effect, change the mechanism, and test it again. Every hero asset must
therefore support the same readable loop:

1. **Forecast:** inputs and possible outputs are staged neutrally. No silhouette,
   lighting, sound-emitter geometry, pose, wear, or animation may reveal which answer
   conduit is correct before commitment.
2. **Inspect:** after commitment, the selected protocol exposes a spatial trace with a
   visible input region, transformation region, and output region. The trace must remain
   understandable with emission disabled and in a still frame at the gameplay camera.
3. **Manipulate:** the mechanism reveals large, reachable probe, tether, alignment, and
   patch affordances. These are purposeful mechanical features, not generic glowing
   weak points; their location and motion must correspond to the mathematical
   relationship being repaired.
4. **Rerun:** the same input and output landmarks remain in place while the repaired
   mechanism executes again. The player must be able to compare before and after without
   relying on a text explanation.

Glitch assets additionally require three companion-compatible presentations:

- **Counterfeit active:** the incorrect procedure runs coherently and its physical tell
  is readable. The machine is not depicted as careless, stupid, or randomly broken.
- **Repair in progress:** protective parts open without exploding, the relevant trace
  stays visible, and the rover can connect to the manipulation affordances.
- **Repaired companion:** the same machine settles into a stable, visibly repaired form
  that can dock, follow, and rerun either the captured counterfeit trace or the corrected
  trace for comparison. This state must differ by shape, posture, and mechanical
  alignment as well as color.

The state change should feel like gaining control of a system, not defeating a creature.
Do not build confetti, loot drops, score plaques, badge pedestals, death poses, or
correctness trophies into these assets. Paint customization remains expression rather
than the visual language of learning progression.

---

## Asset A — Aegis R4 Repair Rover

### Creative purpose

The Aegis R4 is a compact civilian rescue and infrastructure-repair vehicle adapted to
contain and reconfigure nonhuman Glitch drones. It must read as capable and exciting
without resembling a real military vehicle. Its main tool is a **Pulse Lance** that can
send a forecast pulse, scan an exposed trace, tether a manipulation socket, project a
repair beam, and energize a repaired mechanism for its rerun. The same rover handles
driving, investigation, and containment, so its silhouette must remain recognizable
from a third-person camera at distances from `5 m` to `45 m`.

The four Pulse Lance functions must share one believable tool head rather than appearing
as unrelated weapons. Code-controlled focusing rings, shutters, cable reels, and emitter
poses should make the current verb readable: narrow forward projection for forecast,
wide fan for inspect, anchored cable tension for manipulate, and a stable closed circuit
for rerun. None of these modes may resemble ballistic ammunition or a lethal firing
cycle.

### Shape language and silhouette

- Overall form: low, wide, forward-leaning wedge with a strong protected cabin, narrow
  waist, broad rear power deck, and four articulated magnetic hover pods.
- Approximate dimensions: `4.8 m` long, `2.35 m` wide, `1.65 m` to cabin roof, and
  `2.05 m` to the top of the stowed Pulse Lance.
- Ground clearance in neutral pose: `0.35 m` beneath the chassis.
- Cabin: two-seat impression, armored brow, and dark **opaque/dithered** electrochromic
  glazing. Do not model an interior or use a continuously transparent cabin shader for
  the first slice; subtle parallax, reflection, and dashboard emission belong in the
  Unity material.
- Front: two narrow white running lights, one central teal diagnostic scanner, two
  recessed tow/recovery hardpoints, and a replaceable lower impact skid.
- Sides: four hover pods, visible articulation joints, protected energy conduits, tool
  lockers, small handholds, and blank decal plates for player-selected markings.
- Rear: two vectoring thrusters, a vented power module, brake lights, and a readable
  center spine. Leave a blank service-panel area where a later drone bay could fit.
- Top: circular turret yaw ring and a compact Pulse Lance whose barrel cradle pitches
  independently. Keep the weapon clearly energy/tool based: broad emitter, exposed
  focusing rings, no firearm magazine, shell ejection, or realistic ammunition.
- Tool affordances: two compact tether reels with readable cable exits, one scanner fan
  aperture, and a mechanically protected repair emitter. They may share the Pulse Lance
  body but must remain readable when the energy VFX is absent.
- Visual balance: approximately 70% functional industrial surfaces, 20% protective
  shell panels, and 10% glowing technology.
- Avoid excessive greebles. Every major seam, vent, hinge, and fastener should imply a
  function and remain readable after normal-map mip reduction.

### Player-customizable zones

Use at most four Unity materials: `M_Rover_Opaque`, `M_Rover_Glass`,
`M_Rover_Emission`, and optional `M_Rover_InteriorCard`. Paint variation must use one
explicit RGBA mask rather than separate materials for every zone:

1. `Body_Primary` — dominant paint.
2. `Body_Secondary` — roof, pod shells, and turret accents.
3. `Safety_Marking` — narrow caution stripes and rescue panels.
4. `Corruption_Mask` — reserved runtime overlay regions; white means eligible.

Bare metal, glass, and emission are determined by material/texture masks, not additional
paint slots. Unity changes paint and energy through `MaterialPropertyBlock`; it must not
instantiate a unique material per rover cosmetic.

Default palette: warm off-white body, graphite frame, desaturated navy secondary,
yellow rescue markings, and teal verified-energy emission. Counterfeit corruption is a
runtime shader overlay and must not be baked into the base textures.

### Required moving parts and hierarchy

Mechanical animation will be code-driven. Use separate meshes with correctly placed
local pivots; a skeletal rig is optional if the same transform hierarchy is preserved.

```text
CP_Rover_Root
├── Chassis_Main
├── Cabin_Glass
├── Skid_Front
├── PowerDeck_Rear
├── Turret_Yaw
│   └── Turret_Pitch
│       ├── PulseLance_Body
│       ├── PulseLance_Coil_A
│       ├── PulseLance_Coil_B
│       ├── PulseLance_ModeRing
│       ├── Scanner_Shutter
│       ├── Tether_Reel_L
│       ├── Tether_Reel_R
│       └── Muzzle_Emitter
├── HoverPod_FL_Yaw
│   └── HoverPod_FL_Tilt
├── HoverPod_FR_Yaw
│   └── HoverPod_FR_Tilt
├── HoverPod_RL_Yaw
│   └── HoverPod_RL_Tilt
├── HoverPod_RR_Yaw
│   └── HoverPod_RR_Tilt
├── Thruster_Rear_L
└── Thruster_Rear_R
```

Required named attachment transforms/empties:

```text
Socket_CameraTarget
Socket_CameraLookAhead
Socket_CenterOfMass
Socket_AimReference
Socket_ShieldCenter
Socket_GroundProbe_FL
Socket_GroundProbe_FR
Socket_GroundProbe_RL
Socket_GroundProbe_RR
Socket_Muzzle
Socket_RepairBeam
Socket_Scanner
Socket_ForecastPulse
Socket_TraceProjector
Socket_Tether_L
Socket_Tether_R
Socket_ManipulationGuide
Socket_CompanionLink
Socket_Thruster_L
Socket_Thruster_R
Socket_Dust_FL
Socket_Dust_FR
Socket_Dust_RL
Socket_Dust_RR
Socket_Hit_Front
Socket_Hit_Left
Socket_Hit_Right
Socket_Hit_Rear
```

Pivot requirements:

- Root origin centered laterally, `1.85 m` behind the nose, at neutral ground level.
- Turret yaw axis vertical through the center of its ring.
- Turret pitch axis through the Pulse Lance cradle bearings.
- Hover-pod yaw axes vertical through each upper joint.
- Hover-pod tilt axes through each lateral hinge.
- Thruster pivots at their gimbal centers.
- PulseLance mode ring rotates around the local barrel axis; scanner shutter and tether
  reels open from documented neutral poses without changing the aiming reference.
- Yaw uses local `+Y`; positive turns forward `+Z` toward vehicle right `+X`.
- Pitch/tilt uses local `+X`; positive rover turret pitch raises the muzzle toward `+Y`.
- Hover-pod tilt positive raises the pod's forward edge; thruster horizontal gimbal uses
  local `+Y` and vertical gimbal uses local `+X` with positive exhaust deflection
  documented in the delivery sheet.
- All required transforms export with local scale `(1, 1, 1)` and neutral local rotation
  `(0, 0, 0)` unless a documented mechanical rest angle requires otherwise.
- Turret yaw supports `±160°`; turret pitch supports `-12°` to `+55°`.
- Front hover-pod yaw supports `±22°`; all pods tilt `-14°` to `+14°`; rear thrusters
  gimbal `±12°` vertically and horizontally.
- Every LOD preserves identical root, moving-part, and socket names and pivots.

### Geometry and LOD budget

- `LOD0`: `70,000–100,000` triangles, viewed at `0–12 m`.
- `LOD1`: `35,000–50,000` triangles, viewed at `12–28 m`.
- `LOD2`: `14,000–24,000` triangles, viewed at `28–60 m`.
- `LOD3`: `5,000–8,000` triangles, viewed beyond `60 m`.
- Unity LODGroup starting thresholds: LOD0 `0.55`, LOD1 `0.25`, LOD2 `0.08`,
  LOD3 `0.025`, cull `0.008`; the quarantine-scene test may lower cost but must not
  increase it without profiling.
- Preserve the cabin, turret, hover-pod, and rear-thruster silhouette at every LOD.
- Remove small fasteners, recessed seams, thin cables, and internal faces progressively.
- Use weighted/custom normals where they materially improve hard-surface shading.
- No non-manifold geometry, zero-area faces, accidental internal duplicates, or
  unapplied negative scale in exported files.

### Collision

Do not use Unreal-style `UCX_*` names. Unity gameplay owns the functional colliders and
hurtbox. Provide these hidden reference proxies with no renderer:

```text
COL_Rover_Chassis_A
COL_Rover_Chassis_B
COL_Rover_Cabin
```

Each proxy must be a closed convex mesh with at most `64` vertices. The Unity import
tool converts them into nonrendered convex MeshColliders or replaces them with compound
BoxColliders. Hover pods, turret, antennas, barrel, panels, cables, and thrusters receive
no physical collider; the rover uses one separate authored gameplay hurtbox.

### UVs and textures

- Use a consistent texel density near `512 px/m` on hero-facing exterior surfaces.
- One non-overlapping primary UV set for baked maps; overlap only deliberately hidden or
  perfectly symmetrical underside parts.
- The moving rover does not require a lightmap UV; it uses reflection and light probes.
- Author at `4096 × 4096` when useful, but target one `2048 × 2048` exterior runtime set
  plus one `1024–2048` mask/emission set unless profiling approves 4K runtime textures.
- Supply source maps and Unity-ready maps:
  - Base Color without baked lighting or ambient shadow.
  - Tangent-space Normal.
  - Metallic/Smoothness packed map: metallic in `R`, smoothness in `A`; `G/B` black.
  - Ambient Occlusion grayscale.
  - Linear grayscale Emission mask. HDR emission color and intensity are Unity shader
    properties changed through `MaterialPropertyBlock`, not baked color textures.
  - Customization RGBA mask: primary paint `R`, secondary paint `G`, safety marking
    `B`, corruption eligibility `A`.
- Base Color imports as sRGB. Normal imports as a Normal Map. Metallic/Smoothness, AO,
  Emission mask, and Customization mask import with sRGB disabled.
- Use physically plausible values: painted metal reads as dielectric paint, exposed
  frame and fasteners as metal, glass as glass, and polymer pod guards as nonmetal.
- Provide clean and lightly weathered material variants. Weathering should collect near
  lower edges, access panels, vents, and service areas rather than appear as uniform
  random grunge.

### Optional high-value details

- A looping Pulse Lance charge animation or shape-key sequence.
- A small suspension/hover idle pose demonstrating pod articulation.
- A restrained code-driven companion-link pose in which the Pulse Lance lowers and the
  repaired drone docks beside the rear service panel.
- Three decal-mask layouts for cosmetics without destructive texture edits.
- One nonfunctional service-panel seam suggesting a future repair-drone bay.

Do not create a fully modeled cockpit, six-axis mechanical repair arm, complex
destruction rig, or human driver for this milestone.

---

## Asset B — Logic Core and Answer Conduits

### Creative purpose

The Logic Core is the visual signature of the game. It is an industrial relay reactor
corrupted by false mathematical routes. When exposed, it opens into four equal answer
conduits without making any conduit look more correct than another. Commitment selects
one forecast to run. The core then becomes a causal work surface: a correct protocol
reconnects the reactor, while a counterfeit protocol remains coherent long enough to
expose its input, transformation, and output before it is transferred to a Glitch drone
or companion test unit.

The core must support four authored presentations with one shared geometry set:

- **Neutral forecast:** all conduits closed to the same degree, equally lit, and equally
  reachable.
- **Trace exposed:** the selected conduit opens its access panel and reveals a continuous
  mechanical path from input anchors through an operator fixture to an output cradle.
- **Manipulation:** two rover-scale handles or cable points become reachable without
  hiding the trace; moving the relevant part visibly changes the relationship rather
  than merely filling a progress ring.
- **Rerun/repaired:** the altered route remains in its new position while energy passes
  through the same landmarks and the repaired world machine begins operating.

### Dimensions and composition

- Central closed core: approximately `3.2 m` diameter and `5.5 m` tall.
- Four freestanding conduits: approximately `1.2 m` wide, `0.8 m` deep, and `2.6 m`
  tall, positioned on a `7–9 m` radius around the core by Unity.
- Core silhouette: grounded cylindrical base, three rotating diagnostic rings, protected
  transparent energy chamber, four radial cable sockets, and an upper antenna crown.
- Each conduit has the same geometry and material. Runtime text, symbol, lane marker,
  and energy animation distinguish options without correctness cues.
- Provide a rectangular recessed screen area at least `0.75 m × 0.42 m` on each conduit
  for a Unity world-space answer display. The screen surface must be flat and separate.
- Conduits require a wide lower target plate readable from a rover camera and a protected
  emitter that can receive pulse/repair VFX.

### Moving parts and hierarchy

```text
CP_LogicCore_Root
├── Base
├── Chamber_Glass
├── EnergyColumn
├── Shell_Petal_A
├── Shell_Petal_B
├── Shell_Petal_C
├── Shell_Petal_D
├── Lock_A
├── Lock_B
├── Lock_C
├── Lock_D
├── Ring_Outer_Yaw
├── Ring_Middle_Pitch
├── Ring_Inner_Roll
├── Crown
├── Port_Conduit_A
├── Port_Conduit_B
├── Port_Conduit_C
└── Port_Conduit_D

CP_AnswerConduit_Root
├── Housing
├── Screen_Surface
├── Target_Plate
├── Emitter
├── Energy_Path
├── Access_Panel
├── Trace_Input_A
├── Trace_Input_B
├── Trace_Transform
├── Trace_Output
├── Manipulator_Primary
└── Manipulator_Secondary
```

Required sockets: `Socket_Screen`, `Socket_Target`, `Socket_Impact`,
`Socket_EnergyIn`, `Socket_EnergyOut`, `Socket_TraceInput_A`,
`Socket_TraceInput_B`, `Socket_TraceTransform`, `Socket_TraceOutput`,
`Socket_Probe`, `Socket_Tether_Primary`, `Socket_Tether_Secondary`, and
`Socket_RepairBurst` on each conduit. The core additionally requires
`Socket_CameraFocus`, `Socket_StateChangeBurst`, `Socket_CompanionDock`,
`Socket_RerunPulse`, `Socket_BossAnchor`, `Socket_RepairFinale`, and four radial cable
sockets.

Name the cable sockets exactly `Socket_Conduit_A`, `Socket_Conduit_B`,
`Socket_Conduit_C`, and `Socket_Conduit_D` clockwise when viewed from local `+Y`, starting
with A on local `+Z`.

The core is code-driven, not clip-driven. Petals hinge outward `72°`, locks retract
`0.22 m` radially, outer ring yaws continuously, middle ring pitches `±28°`, and inner
ring rolls continuously. Every moving transform exports at identity local rotation in
the closed state. Socket forward is local `+Z`; socket up is local `+Y`.
Petals and middle-ring pitch rotate about local `+X`, positive opening away from the
EnergyColumn; outer-ring yaw and inner-ring roll use local `+Y`, positive from local
`+Z` toward `+X` when viewed from above.

Provide hidden reference proxies named `COL_Core_Base`, `COL_Core_Chamber`,
`COL_Conduit_Housing`, and `COL_Conduit_Target`. Unity owns functional colliders:
physical blockers are separate from the answer hit target, and the target collider is
disabled outside Focus Mode.

### Budget and materials

- Logic Core `LOD0`: `40,000–70,000` triangles; conduit `LOD0`: `8,000–15,000`.
- Provide two additional LODs at roughly 50% and 20% of LOD0.
- Initial Unity LODGroup thresholds: core `0.48/0.18/0.045`, conduit
  `0.42/0.15/0.035`; cull below `0.008`.
- Author one 4K core set and one 2K conduit set, but ship 2K/1K respectively unless the
  combined-scene profile approves higher resolution.
- Use at most three materials: opaque industrial, opaque/dithered chamber glass, and
  emission. Follow the same Metallic/Smoothness, AO, Emission, and sRGB rules as the
  rover. Provide nonoverlapping lightmap UVs for these static structures.
- Before commitment, all four conduits use identical neutral white-blue energy intensity,
  animation, material, and audio. Teal verified and amber/coral counterfeit states occur
  only after the server resolves a committed answer.
- After commitment, color may support the state but cannot carry it. Panel position,
  trace continuity, cable tension, and the alignment of the input/transform/output parts
  must make counterfeit execution, manipulation, and repair distinguishable in
  grayscale and with emission disabled.
- Provide sufficient clearance around the manipulation sockets for the rover nose and
  two tether lines. Unity places the conduits, but the asset must tolerate a clean
  approach arc without the core petals or cable ports obstructing the interaction.
- Supply the closed geometry and documented code-driven limits above; do not deliver
  conflicting opening/overload animation clips.

---

## Asset C — Modular Counterfeit Drone Kit

### Creative purpose

One modular hovering machine must support two ordinary counterfeit agents, repaired
companions, and one convoy composition. Misconception families should change the drone
through attachments, shader patterns, shield geometry, trace motion, and behavior—not
through nine unrelated character models. Each family module is a visible miniature
procedure: quantities enter, a specific transformation occurs, and the claimed result
leaves. The free-form SLM label may inform copy, but only the verified authored module
determines this physical execution.

### Base design

- Approximate ordinary size: `1.3 m` wide, `0.9 m` tall, `1.1 m` deep.
- Central armored computation core with readable front-facing optic.
- Four articulated vectoring fins or thruster pods forming a strong diamond silhouette.
- One modular lower tool/weapon socket and two side utility sockets.
- Floating outer shield segments that can rotate, separate, and reassemble.
- A protected trace bay that can open toward the gameplay camera without detaching from
  the drone, plus large rover-facing manipulation hardware.
- A stable companion silhouette using the same chassis: shields nested rather than
  aggressive, optic level, modules mechanically aligned, and one visible repaired seam
  or brace. Do not communicate allegiance through hue alone.
- No face, gore, biological anatomy, or realistic firearm.

### Shared hierarchy and roadmap modules

```text
CP_Drone_Root
├── Core_Base
├── Optic_Yaw
│   └── Optic_Pitch
├── Fin_FL
├── Fin_FR
├── Fin_RL
├── Fin_RR
├── ShieldPivot_A
│   └── Shield_Segment_A
├── ShieldPivot_B
│   └── Shield_Segment_B
├── ShieldPivot_C
│   └── Shield_Segment_C
├── ShieldPivot_D
│   └── Shield_Segment_D
├── Socket_Module_L
├── Socket_Module_R
├── Socket_Module_Center
├── Socket_BossShield_E
├── Socket_BossShield_F
├── Socket_BossShield_G
├── Socket_BossShield_H
├── TraceBay_Door
├── TraceBay_Input_A
├── TraceBay_Input_B
├── TraceBay_Transform
├── TraceBay_Output
├── Manipulator_Primary
├── Manipulator_Secondary
└── Socket_Weapon

Module_Pulse
Module_Beam
Module_DecimalDrifter
Module_FractionForger
Module_SignFlipper
```

Only `Module_FractionForger` is required for the first owner showcase. Preserve the
shared sockets and naming contract for `Module_DecimalDrifter` and `Module_SignFlipper`,
but do not take those two modules beyond blockout until the fraction loop passes the
competence playtest gate and receives its own expansion plan.

Every module's root transform must attach to its socket at local position `(0, 0, 0)`,
rotation `(0, 0, 0)`, and scale `(1, 1, 1)`. Module forward is `+Z`, up is `+Y`.
Fins pitch `-20°` to `+35°`; shield pivots orbit around the core and can flare outward
`30°`; optic yaw is unrestricted and optic pitch is `±50°`. Yaw/orbit uses local `+Y`,
positive from local `+Z` toward `+X`; pitch/flaring uses local `+X`, with positive optic
pitch raising its look direction toward `+Y`. The Sign Flipper module contains named
children `PolarityVane_Positive` and `PolarityVane_Negative`, both rotating around local
`+Z` in opposite signed directions.

The three misconception modules need different silhouettes:

- **Decimal Drifter:** offset sliding rails and a laterally displaced optic aperture.
  Individual digit carriers must be able to slide against fixed place-value notches. In
  repair, the rover anchors the notches and moves the carriers into one shared column;
  in companion form, the rails can rerun both the captured drift and the aligned trace.
- **Fraction Forger:** segmented numerator/denominator plates separated by a bright bar.
  The plates must accept instanced or projected cell divisions so unlike piece sizes can
  be compared. In repair, the rover connects the denominator lattice and slides both
  quantities onto equal-size cells; the repaired plates stay aligned for rerun.
- **Sign Flipper:** two opposed polarity vanes that visibly rotate through each other.
  Direction arrows and a fixed zero/reference notch must remain readable without text.
  In repair, the rover holds the reference while rotating the active vane; companion
  form parks both vanes in a stable, comparable orientation.

Other misconception families can initially reuse these modules with distinct patterns
and behavior only when the shared geometry still presents the verified input,
transformation, output, and manipulation relationship honestly. Pattern swaps alone do
not constitute a readable counterfeit rule.

### Counterfeit, repair, and companion states

The kit must transition among these states through code-driven parts and materials:

1. **Counterfeit active:** shields protect the trace bay while the family module repeats
   its internally coherent transformation. Input and output landmarks remain visible
   enough for forecast and pursuit.
2. **Repair in progress:** shields flare but remain attached, the trace-bay door opens,
   the drone holds a stable interaction pose, and the primary/secondary manipulation
   hardware faces the rover. Nothing explodes, drops as loot, or becomes a corpse.
3. **Repaired companion:** shield segments re-nest, module parts settle into corrected
   alignment, a mechanical repair brace remains visible, and the drone exposes a dock
   and projector so it can follow the rover and replay before/after traces at a
   workbench.

The repaired companion must preserve the recognizable module silhouette. Repair should
not erase the misconception evidence; it should make comparison possible.

### Technical budget

- Base plus one module `LOD0`: `20,000–35,000` triangles.
- `LOD1`: `10,000–17,000`; `LOD2`: `4,000–7,000`; `LOD3`: under `2,500`.
- Author at 4K if needed, but ship one 2K atlas for the complete kit plus one 1K
  emissive/corruption-pattern mask.
- Separate pivots for every fin, shield segment, optic, and module.
- Every module supplies renderer children for the same LOD levels as the base drone.
- Sockets: `Socket_Target`, `Socket_Weapon`, `Socket_Module_L`, `Socket_Module_R`,
  `Socket_Shield_A` through `D`, `Socket_Hit_Core`, `Socket_TraceInput_A`,
  `Socket_TraceInput_B`, `Socket_TraceTransform`, `Socket_TraceOutput`,
  `Socket_Probe`, `Socket_Tether_Primary`, `Socket_Tether_Secondary`,
  `Socket_CompanionDock`, `Socket_CompanionProjector`, and
  `Socket_StateChangeBurst`.
- Provide hidden convex reference proxies `COL_Drone_Core` and `COL_Drone_Shield`; Unity
  authors one core hurtbox, one optional shield trigger, and no fin/module colliders.
- Use at most three materials: opaque body, opaque/dithered shield, and emission. Supply
  the same RGBA paint/corruption mask and import rules used by the rover.
- Code drives hover, anticipation, probe reaction, shield opening, repair hold,
  reassembly, companion follow, and trace replay from the documented pivots; do not
  supply competing animation clips.

The finale is a Unity-authored diagnostic convoy, not a fourth monster or health-bar
boss. Compose it from three standard drone instances, each exposing one previously
observed protocol through its own input/transform/output trace. In the fraction-first
showcase all three may use `Module_FractionForger` with different plate arrangements and
code-driven traces; later expansions may substitute Decimal or Sign modules. Do not
require a larger boss mesh, eight extra shields, or all three subject families before
the core loop is validated. Gameplay owns convoy colliders and behavior. The art kit
must tolerate identity module attachment, shield opening, and a non-destructive
companion/repaired resolution without corrective rotations.

---

## Asset D — World Manipulation and Nia Interface Kit

### Creative purpose

The relay station is a competence engine, not scenery around a quiz panel. Its machines
must let the rover forecast an output, inspect the resulting trace, physically alter the
relationship, and rerun the same system. Repairs then change traversal or operation in
the world: a bridge rotates into place, a crane resumes carrying cargo, a coolant loop
opens a safe drive line, or a companion dock comes online. A color swap or celebratory
particle burst alone is not a repaired world state.

The kit should remain modular and achievable within the zero-budget environment scope.
It does not require three bespoke buildings. Shared industrial frames, cable channels,
rails, cradles, clamps, pylons, and service panels may be recombined into three readable
system families:

- **Place-value alignment system:** fixed column notches, laterally movable digit/load
  carriers, and a visible input-to-output rail. Misalignment changes where the carrier
  travels; repair anchors a shared place-value reference.
- **Equal-part lattice system:** scalable or instanced cell trays, two quantity cradles,
  and a common-size alignment frame. Unlike pieces visibly fail to share the same rail;
  repaired pieces remain aligned during rerun.
- **Signed-direction system:** a central zero/reference pylon, opposed directional rails,
  and reversible polarity vanes. The sign transformation changes physical direction;
  repair preserves the reference while the active direction is changed.

Every system family needs a quiet workbench presentation in addition to its combat or
mission presentation. After repair, the player must be able to return, select bounded
inputs through authored controls, and rerun the mechanism without another reward screen.

### Shared interaction affordances

Use a small reusable language of rover-scale parts:

- A probe plate that accepts the Pulse Lance forecast pulse.
- Two visually distinct tether anchors for holding a reference and moving an active
  part. Shape and placement, not color alone, distinguish their roles.
- A guided slider, hinge, or rotary manipulation part with obvious mechanical limits.
- Persistent input, transformation, and output landmarks that occupy the same positions
  before and after repair.
- A companion dock with clearance for one repaired drone and a nearby trace-projector
  surface.
- A rerun actuator that is mechanically separate from answer commitment so testing the
  repaired system feels like operating it, not submitting another quiz response.

Representative environment prefabs should expose consistently named anchors such as
`Socket_Probe`, `Socket_Tether_Reference`, `Socket_Tether_Active`,
`Socket_TraceInput_A`, `Socket_TraceInput_B`, `Socket_TraceTransform`,
`Socket_TraceOutput`, `Socket_Rerun`, `Socket_CompanionDock`, and
`Socket_CameraFocus`. Final collider size and interaction tolerance remain gameplay
owned, but these landmarks must sit on unobstructed exterior faces reachable by the
rover and remain visible from the Focus camera.

### Persistent world states

Each mission-critical machine requires a documented state sheet and matching geometry
or transform plan:

1. **Before:** the system is usable enough to run a forecast, but one route is visibly
   unstable or incomplete. It must not identify the correct answer in advance.
2. **Repair in progress:** panels open, manipulation parts gain clearance, and the full
   causal path remains visible while the rover acts.
3. **Repaired:** parts settle into a mechanically credible new alignment, world motion
   resumes, and a traversable or operable affordance becomes available. Retain one
   restrained repair brace, seal, or alignment mark so the player's intervention remains
   legible after leaving and returning.

Do not make the repaired state simply cleaner, brighter, or more expensive-looking.
Its changed function must be understandable in a silent before/after comparison.

### Nia presentation

Nia remains a radio/hologram systems engineer; no realistic human model, lip sync, or
facial rig is required. Provide one compact industrial communication terminal or rover
dashboard projection frame with:

- A flat separate message surface for captions and authored portrait/diagram cards.
- A world-focus pointer that can aim toward the input, transformation, or output part
  Nia is discussing.
- Restrained neutral, observing, trace-available, and rerun-ready poses or light-panel
  configurations. Do not include cheering, star ratings, score readouts, streak meters,
  diagnosis labels, or a generic success celebration.
- Clear space for the player's choices to request **Show the trace**, **Run it again**,
  **Change one thing**, or **Give me a stranger case**. These are interface affordances,
  not baked English texture text.

Nia's terminal should look like another service tool in the environment. It supports
focal attention and player choice; it does not compete with the mechanism for visual
priority.

---

## Combined-scene performance budget

The owner target is an Apple M4 with `16 GB` unified memory shared by Unity and the local
Q4 model. Asset acceptance therefore occurs in the complete representative scene, not
from isolated turntables.

- Target no more than `1.5 million` visible triangles in the normal
  driving/containment camera and
  `2.0 million` during the close Logic Core Focus shot.
- Target at most `140` visible renderer draw calls, `24` transparent/dithered draws, and
  `80` realtime shadow-casting renderers at the medium preset.
- Keep runtime-resident project textures within `600 MB` at the medium preset before
  model/runtime memory. Start hero textures at 2K and increase only after profiling.
- Limit full-screen transparent fog/energy layers; use depth-aware particles and opaque
  or dithered glass where possible.
- Only the rover, nearest two drones, Logic Core, and immediate arena props receive
  highest-detail shadows simultaneously.
- A combined Unity-plus-inference soak must maintain the product plan's frame-time and
  memory-pressure gates before higher LODs or texture sizes are approved.

---

## Authoritative Blender-to-Unity export contract

- FBX is the runtime master. Export only selected delivery objects from Blender with
  unit scale `1.0`, Apply Unit enabled, Apply Transform enabled, Forward `-Z`, Up `Y`,
  leaf bones disabled, object types limited to Mesh/Armature/Empty, and no exported
  cameras or lights.
- Unity import must produce root position `(0,0,0)`, rotation `(0,0,0)`, scale `(1,1,1)`,
  `+Z` gameplay forward, and `+Y` up without a corrective prefab parent.
- Deliver one FBX per asset family—one rover FBX, one core/conduit FBX, one base-drone
  FBX, one FBX per module, and one FBX per independently reusable environment or Nia
  interface module. Each FBX contains exactly one shared logical transform/socket
  hierarchy. Every moving part owns renderer children suffixed `_LOD0`, `_LOD1`,
  `_LOD2`, `_LOD3`; sockets and pivots exist once above those renderer children and are
  never duplicated per LOD. Unity tooling builds each LODGroup from the per-part
  renderer children. Every module supplies matching LOD renderer levels.
- Unity ModelImporter settings: file scale honored at `1.0`, mesh compression Off for
  LOD0 and Low for lower LODs, Read/Write disabled after import validation, Normals
  Import, Tangents Calculate Mikktspace, cameras/lights/visibility disabled, and
  animation disabled for these code-driven assets.
- Triangulation occurs before final tangent bake so the FBX, normal map, and Unity mesh
  use the same topology.
- `.glb` may accompany the source for inspection but the project will not add a glTF
  runtime/import package merely to consume these assets.

---

## Delivery package

Before work begins, the artist/model provider must sign or explicitly accept a written
agreement requiring complete provenance disclosure, ownership/license warranties, and
the commercial/source-sharing rights below. Final delivery then includes the completed
itemized provenance record; it is not the first time those terms are introduced.

For each asset, provide:

- Original `.blend` source with modifiers and high-poly/bake sources organized into
  named collections.
- Clean triangulated `.fbx` exports tested at Unity meter scale; optional `.glb` reference
  exports may accompany them.
- LOD meshes and collision meshes.
- PNG or lossless TGA textures plus editable source files where available.
- A neutral-light turntable render, wireframe render, material-channel sheet, and scale
  comparison beside a `1.8 m` human reference.
- A silent state sheet or short neutral-camera capture showing forecast/before,
  counterfeit execution where applicable, repair-in-progress, rerun, and repaired or
  companion presentation with emission both enabled and disabled.
- A text file listing scale, forward axis, material slots, texture channel packing,
  hierarchy, pivot purpose, sockets, triangle counts, state-driving parts, manipulation
  clearances, and known limitations.
- Written provenance disclosing every kitbash source, texture library, generator, and AI
  tool. The agreement must confirm the creator owns or validly licenses each component
  and grants rights to modify it, commercially distribute it in compiled builds, retain
  editable source, and share source with project contractors under confidentiality.

Large `.blend`, high-poly, and bake-source files live in a private owner-controlled art
archive, not the public Git repository. Runtime FBX/textures enter the project only after
license and quarantine-scene approval. Bespoke art, third-party art, and project-authored
materials use separate `Bespoke/`, `ThirdParty/`, and `ProjectMaterials/` directories.

## Production approval gates

1. Approve three original silhouette thumbnails and one annotated material sheet.
2. Approve a meter-scale Unity blockout with all required pivots, sockets, and identity
   attachments before high-poly modeling.
3. Demonstrate the complete forecast → inspect → manipulate → rerun blockout with the
   rover, one conduit, one misconception module, and one world machine. Require the
   tether and probe sockets to remain reachable from the production camera and vehicle
   approach.
4. Demonstrate counterfeit-active, repair-in-progress, and repaired-companion states in
   silhouette and grayscale before approving detailed drone production.
5. Test rover, trace, and manipulation readability at `5 m`, `15 m`, and `45 m` using
   the production camera.
6. Approve the final LOD0 low-poly mesh and weighted normals in Unity URP before baking.
7. Approve the RGBA customization mask and one representative textured section.
8. Deliver remaining textures, LODs, reference collision proxies, and state sheets.
9. Run the combined mission scene for triangle, draw-call, texture, shadow, collision,
   frame-time, and memory budgets.
10. Accept final delivery only after the Unity quarantine-scene checklist passes.

## Acceptance checklist

- Imports into Unity at correct meter scale and faces `+Z` without corrective parent
  rotation or negative scale.
- No missing textures, pink materials, inverted normals, broken tangents, or unexpected
  alpha sorting.
- All named pivots rotate around the intended mechanical joint.
- Every module attaches at identity and every socket faces `+Z` with `+Y` up.
- Material customization changes paint without affecting glass, metal, or emission.
- LOD transitions preserve silhouette and do not visibly explode or shift.
- Collision is stable at rover combat speeds and contains no tiny snagging triangles.
- World-space answer panels remain flat, equal in size, and equally visible.
- No answer conduit or forecast state leaks correctness through geometry, pose,
  animation readiness, wear, lighting fixtures, or socket exposure.
- Every supported Glitch shows a coherent input → transformation → output trace in a
  still frame with emission disabled.
- Probe, tether, manipulation, repair, rerun, and companion sockets remain reachable,
  unobstructed, and correctly aligned throughout their required states.
- Counterfeit-active, repair-in-progress, and repaired-companion states differ through
  silhouette and mechanical alignment rather than color alone; no state requires a
  destruction or death presentation.
- The same input, transform, and output landmarks persist across repair so a silent
  before/after rerun is directly comparable.
- Each environment machine's repaired state creates a visible functional change and can
  be rerun from its authored workbench controls.
- The Nia interface supports focal pointers and player-requested trace/rerun options
  without baked praise, scoring, diagnosis, or language-specific UI text.
- The rover, drones, causal traces, and manipulation affordances are readable at the
  gameplay camera distance.
- Visuals remain original, age-appropriate, non-graphic, and commercially licensable.
