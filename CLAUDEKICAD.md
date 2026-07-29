# CLAUDEKICAD.md — Production PCB Design Flow for Claude + KiCad

Operating manual for an AI agent taking a PCB from concept to fabrication-ready output using
**KiCad 10**, **Konnect** (write side) and **kicad-happy** (verify side).

---

## §0 How to use this file

Load this file **once** at the start of a design session. Do not re-read it. Do not quote it back.

Two invariants govern everything below:

1. **The phase order is fixed.** Phases run 1 → 10. No phase begins until the previous phase's
   exit gate has passed. No phase is skipped, merged, or reordered.
2. **Artifacts on disk are the source of truth — not conversation history.** Every phase writes
   its result to a file. Every later phase reads that file. This is what makes it safe to
   `/clear` the context at each gate, and `/clear` at each gate is the *expected* operating mode,
   not an exception.

The flow:

| # | Phase | Writes | Nature |
|---|---|---|---|
| 1 | Function definition + mechanical | `docs/00-function.md`, `docs/01-mechanical.md` | human-led |
| 2 | Parts definition | `docs/02-parts.md` | agent + human approval |
| 3 | Datasheet gathering → `.md` | `datasheets/md/<MPN>.md` | agent |
| 4 | Requirements from datasheets | `docs/04-requirements-electrical.md` | agent |
| 5 | Requirements from guidance / best practice | `docs/05-requirements-layout.md` | agent |
| 6 | Schematic | `<name>.kicad_sch` | agent (Konnect) |
| 7 | Check — schematic | `docs/07-schematic-review.md` | agent (kicad-happy) |
| 8 | PCB | `<name>.kicad_pcb` | agent (Konnect) |
| 9 | Check — PCB | `docs/09-pcb-review.md` | agent (kicad-happy) |
| 10 | Production files | `production/`, `docs/10-release.md` | agent (Konnect) + verify |

---

## §1 Toolchain and authority

Two toolchains, one rule: **Konnect writes, kicad-happy verifies.** They overlap in three places
and the overlap must be resolved the same way every time.

### 1.1 Authority table

| Job | Use | Never use |
|---|---|---|
| Create / edit schematic | Konnect `sch_*` toolsets | anything else |
| Create / edit PCB | Konnect `pcb_*` toolsets | anything else |
| Design review, audit, sanity | kicad-happy `kicad`, `emc`, `spice` skills + `analyze_thermal.py` | Konnect `design_review` toolset; Konnect's bundled `kicad-review` skill; Konnect's `kicad-design-review-agent` |
| Part search, pricing, lifecycle | kicad-happy `digikey` / `mouser` / `lcsc` / `element14` skills | Konnect `integration` JLCPCB DB tools (`download_jlcpcb_database` currently returns HTTP 404 — upstream issue #97) |
| Datasheet retrieval + extraction | kicad-happy `datasheets` skill | Konnect `enrich_datasheets` / `get_datasheet_url` |
| Authoritative DRC | Konnect `verification` → `run_drc` (calls KiCad's real DRC engine) | kicad-happy findings *as a substitute* — they are complementary, not equivalent |
| Authoritative ERC | `kicad-cli sch erc` | — |
| Fab-house rule + BOM/CPL format check | kicad-happy `jlcpcb` / `pcbway` skills | — |
| Manufacturing export | Konnect `pcb_export` + `manufacturing` | — |
| Any file modification | **Konnect only** | kicad-happy is read-only by design; it never writes design files |

**Disable Konnect's competing review assets before starting.** Konnect bundles 6 skills and
2 agents. Keep `kicad-schematic`, `kicad-pcb`, `kicad-library`, `kicad-manufacture`, `konnect`
and `kicad-schematic-build-agent`. Remove or disable `kicad-review` and
`kicad-design-review-agent` — they will compete with kicad-happy for triggering and produce a
second, shallower verdict that contradicts the first.

### 1.2 Preflight (run once per project, ~6 commands, then never again)

```bash
kicad-cli version                  # must report 10.x
which ngspice || which xyce        # optional; absent → SPICE degrades to analytical models
python3 --version                  # must be ≥ 3.10
ls ~/.claude/skills/kicad          # kicad-happy installed
```

Then in-session: call Konnect's `list_toolboxes` **once** to confirm the MCP server is reachable.
Do not call it again — the phase→toolset table in §4.1 is authoritative.

Optional distributor API keys (absent → skills fall back to web search, slower and lower
confidence): `DIGIKEY_CLIENT_ID`, `DIGIKEY_CLIENT_SECRET`, `MOUSER_SEARCH_API_KEY`,
`ELEMENT14_API_KEY`. LCSC needs none.

---

## §2 Non-negotiable safety rules

These four rules exist because violating them destroys work. They are repeated at the phase where
each one bites.

**R1 — Commit before every write session.**
`git add -A && git commit` before any phase that invokes a Konnect write tool (6, 8, 10).
Konnect is beta software with a history of geometry miscalculation and file corruption from
formatting assumptions. Git is the only undo for direct schematic file writes.

**R2 — The save barrier.**
Konnect's PCB edits go through the IPC API into KiCad's **in-memory** board. kicad-happy reads
**files on disk**. Therefore: **save the board in KiCad (Ctrl+S) before every analysis pass.**
An unsaved board means phase 9 reviews a stale layout and reports findings that do not exist.
If an analysis result contradicts a change you just made, the cause is almost always a missing
save — check that before anything else.

**R3 — The schematic-editor rule.**
Konnect writes `.kicad_sch` **directly on disk**, bypassing KiCad. If KiCad's schematic editor
has that file open, it holds a stale in-memory copy that will overwrite Konnect's work the moment
anyone saves. **Close the KiCad schematic editor for the whole of phase 6.** Reopen it (or use
File → Revert) only after phase 6's exit gate.

**R4 — Phase order is not negotiable.**
No phase starts before the previous gate passes. If a later phase reveals that an earlier phase
was wrong, return to that phase, fix it, re-pass its gate, and re-run every phase in between.
Do not patch forward.

---

## §3 Project layout

Fixed directory contract. No phase should ever have to search for a file.

```
<project>/
  <name>.kicad_pro
  <name>.kicad_sch                  ← phase 6
  <name>.kicad_pcb                  ← phase 8
  STATE.md                          ← phase ledger; the resume point after /clear

  docs/
    00-function.md                  ← phase 1
    01-mechanical.md                ← phase 1
    02-parts.md                     ← phase 2
    04-requirements-electrical.md   ← phase 4  (REQ-E)
    05-requirements-layout.md       ← phase 5  (REQ-L)
    07-schematic-review.md          ← phase 7
    09-pcb-review.md                ← phase 9
    10-release.md                   ← phase 10
    waivers.md                      ← any phase

  datasheets/
    <MPN>.pdf                       ← phase 3, source PDFs
    extracted/<MPN>.json            ← phase 3, kicad-happy extraction cache
    md/<MPN>.md                     ← phase 3, THE GROUNDING CORPUS

  analysis/                         ← kicad-happy cache, timestamped runs + manifest.json
  production/
    gerbers/  drill/  bom/  cpl/  3d/  pdf/
```

`.kicad-happy.json` at project root:

```json
{ "analysis": { "output_dir": "analysis", "retention": 5, "auto_diff": true,
                "track_in_git": false, "diff_threshold": "major" } }
```

`.gitignore`: `analysis/`, `datasheets/*.pdf`. **Commit** `datasheets/md/`,
`datasheets/extracted/`, all of `docs/`, `STATE.md`, and `production/` at release.

### 3.1 `STATE.md` format

Rewritten at every gate. This file — and only this file — is what a fresh context reads first.
Keep it under 40 lines.

```markdown
# STATE
project: <name>
current_phase: 7
last_gate_passed: 6
last_commit: <sha>

## Gate status
1 function+mechanical  PASS  2026-07-29
2 parts                PASS  2026-07-29
3 datasheets           PASS  2026-07-30   (24/24 MPN, min quality 0.72)
4 REQ-E                PASS  2026-07-30   (61 requirements)
5 REQ-L                PASS  2026-07-30   (38 requirements)
6 schematic            PASS  2026-07-31
7 check-schematic      IN PROGRESS        (2 open errors)

## Open items
- REQ-E-017 FAIL: U3 VDDIO decoupling 2x100nF, datasheet requires 3
- ERC: 1 unconnected pin U5.14

## Next action
Fix REQ-E-017 via Konnect sch_components, re-run analyze_schematic.py, re-verify.
```

---

## §4 Token discipline

The flow is long. It is only affordable if context stays small. These rules are mandatory.

### 4.1 Konnect toolset loading

Konnect exposes 185 tools across 18 toolsets but preloads only the starter kit (`project`,
`config`). Loading everything would consume the context budget before design starts.

**Load exactly the toolsets listed for the current phase, one at a time, and
`unload_toolset(name)` the moment that step is done.**

| Phase | Toolsets permitted | Order |
|---|---|---|
| 1 | none | — |
| 2 | none | — |
| 3 | none | — |
| 4 | none | — |
| 5 | none | — |
| 6 | `config`, `templates`, `sch_components`, `sch_wiring`, `sch_hierarchy`, `sch_batch` | load/unload in this order |
| 7 | `sch_analysis` *(only to resolve a specific discrepancy)*, `sch_export` | on demand |
| 8 | `pcb_board`, `pcb_components`, `pcb_routing`, `library`, `verification` | load/unload in this order |
| 9 | `verification`, `pcb_export` (`refill_zones`, `get_drc_violations` only) | on demand |
| 10 | `pcb_export`, `manufacturing` | load/unload in this order |

Never load `design_review` or `integration` at any phase (see §1.1).

### 4.2 Never read analyzer JSON

kicad-happy analyzer output is **60–300 KB per file**. Reading one into context can cost more
than an entire phase. It is a machine artifact, not a document.

| Need | Do this | Not this |
|---|---|---|
| Overall findings | `python3 <skill>/scripts/summarize_findings.py analysis/ --top 15 --severity high` | `Read analysis/.../analysis.json` |
| One net's connectivity | extract the single field with `python3 -c` / `jq` | read the file |
| Compare two runs | `diff_analysis.py base.json head.json --text` | read both |
| Value change impact | `what_if.py analysis.json R5=4.7k --text` | read + reason manually |

Escalate severity filters progressively: `--severity high` first, then `medium` only after every
high is closed. Do not pull `info`-level findings until the gate is otherwise clean.

### 4.3 Analyzer schema field names

Getting these wrong causes a failed extraction, a retry, and wasted tokens. Memorize:

- `nets[<name>].pins[]` fields are `.component`, `.pin_number`, `.pin_name`, `.pin_type` —
  **not** `.ref`, **not** `.pin`
- `ic_pin_analysis[]` is a **list**, not a dict keyed by reference
- `transistor_pin_analysis[]` is **separate** from `ic_pin_analysis[]` (MOSFET/BJT/FET only)
- `pcb.zones[].net` is an **integer** net ID; layers field is `.layers` (plural)
- `pcb.footprints[].pad_nets` is a **dict** keyed by pad number
- `pcb.tracks` is a **dict envelope** (`{segment_count, arc_count, layer_distribution}`) —
  `.segments[]` and `.arcs[]` exist only with `--full`
- `pcb.power_net_routing` is a **list**, not a dict keyed by net
- `connectivity_graph` keys are net names directly: `cg['GND']`, `cg['+3V3']`

### 4.4 Batch everything

Konnect provides batch variants. One batch call per logical group — never one call per wire.

`batch_add_wire`, `batch_add_junction`, `batch_get_schematic_pin_locations`,
`batch_rotate_labels`, `batch_delete_schematic_wire`, `batch_delete_no_connect`, plus the whole
`sch_batch` toolset. For PCB: `place_component_array`, `align_components`, `duplicate_component`.

Wiring a 20-pin connector with 20 `add_wire` calls instead of 1 `batch_add_wire` costs roughly
20× the tokens for identical output.

### 4.5 Do not re-run unchanged analysis

Check `analysis/manifest.json` before invoking any analyzer. If the input file's mtime and hash
are unchanged since the last run, reuse the cached result. With `auto_diff: true` the delta from
the previous run is produced automatically — read the delta, not the full result.

### 4.6 Read the `.md`, never the PDF

After phase 3, `datasheets/md/<MPN>.md` is the *only* datasheet source any phase reads. Never
re-open a PDF. Never re-run extraction on an MPN that already has an `.md`. If an `.md` is
missing a fact you need, add that fact to the `.md` (with page citation) and move on — that way
the next phase gets it for free.

### 4.7 Reporting discipline

- Write results to the phase artifact. Report to the user in **≤10 lines**: gate status, counts,
  blocking items, next action.
- Never paste an artifact's content back into chat. Reference it by path.
- Never echo a datasheet table, a netlist, or a findings list into chat unless the user asks.
- At each gate, output the gate verdict and stop. Let the user `/clear`.

---

## §5 Requirement register

Phases 4 and 5 produce the register. Phases 7 and 9 verify against it. It is the spine of
"production-ready" — a board is production-ready when every requirement is traced to evidence.

### 5.1 ID scheme

| Prefix | Phase | Source | Strength |
|---|---|---|---|
| `REQ-M-nnn` | 1 | Mechanical envelope, enclosure, connector positions | **must** |
| `REQ-E-nnn` | 4 | Datasheet tables: abs max, recommended operating, electrical characteristics | **must** — violating one is a hard failure |
| `REQ-L-nnn` | 5 | Datasheet layout/application guidance + general practice | **should** — deviation requires a waiver with rationale |

IDs are permanent. Never renumber. A deleted requirement is marked `WITHDRAWN`, not removed.

### 5.2 Row format

One line per requirement. Keep it short — this table is read in full at each check phase.

```markdown
| ID | Requirement | Source | Verify by | Phase | Status |
|---|---|---|---|---|---|
| REQ-E-017 | U3 VDDIO: 3× 100nF X7R within 3mm of pins 12,24,36 | LAN9360.md §Decoupling p.44 | analyze_schematic decoupling_analysis | 7 | PASS |
| REQ-L-008 | MDI pairs 100Ω differential ±10%, length-matched ±0.5mm | LAN9360.md §Layout p.51 | analyze_pcb --full impedance | 9 | OPEN |
```

`Verify by` must name the concrete check that will close it — an analyzer field, a script, a DRC
rule, or `manual`. A requirement with no verification method is not a requirement; make it
verifiable or drop it.

### 5.3 Status values

`OPEN` → not yet checked · `PASS` → evidence recorded · `FAIL` → blocking ·
`WAIVED` → see §16 · `WITHDRAWN` → no longer applies, reason recorded.

---

## §6 Phase 1 — Function definition and mechanical

**Purpose.** Fix what the board does and the physical envelope it must fit, before any part is
chosen. Everything downstream is derived from this; changing it later invalidates phases 2–10.

**Entry gate.** None — this is the start.

**Inputs.** The user's intent. Nothing else exists yet.

**Actions.** No tools. This is a human-led phase; the agent's job is to interrogate and record,
not to decide.

Write `docs/00-function.md`:
- One-paragraph purpose statement
- Functional block diagram (ASCII), one box per function, arrows labelled with the interface
- External interfaces table: connector, signal type, voltage/current, protocol, rate
- Power budget table: rail, voltage, tolerance, max current per consumer, total, source
- Operating environment: temperature range, humidity, ingress, shock/vibration if relevant
- Compliance targets, if any (they drive phase 5 layout requirements)
- Explicit non-goals

Write `docs/01-mechanical.md`:
- Board outline with dimensions, or the enclosure it must fit
- Mounting hole positions, diameter, keep-out radius
- Connector positions and orientations that are fixed by the enclosure
- Component height limits, per region if not uniform
- Intended layer count and stackup, with the reason (this is an input to phase 5, not a
  conclusion of phase 8)
- Thermal strategy: convection, conduction to chassis, heatsink, airflow direction

Assign `REQ-M-nnn` for every constraint that a later phase must honour.

**Output.** `docs/00-function.md`, `docs/01-mechanical.md`, `STATE.md`.

**Exit gate.** User explicitly signs off both documents. Power budget totals are computed and
consistent. Every block in the diagram has at least one interface. No `TBD` remains in the
mechanical constraints.

**Token note.** Zero tool calls. If the user's description is thin, ask the questions in one
batch, not one at a time.

---

## §7 Phase 2 — Parts definition

**Purpose.** Select a specific MPN for every functional block. Everything from here is grounded
in real parts with real datasheets — no generic placeholders.

**Entry gate.** Phase 1 signed off.

**Inputs.** `docs/00-function.md` (blocks and power budget), `docs/01-mechanical.md`
(package/height constraints, temperature range).

**Actions.** kicad-happy distributor skills only. No Konnect toolsets are loaded in this phase.

1. For each block, search with the `digikey` skill (fall back: `mouser` → `lcsc` →
   `element14`). Prefer `lcsc` when the board is destined for JLCPCB assembly, since basic-parts
   availability changes the BOM cost materially.
2. For each candidate, record: MPN, manufacturer, package, key spec vs the block's requirement,
   operating temperature range, price at build quantity, stock, lifecycle status.
3. **Lifecycle and temperature screening.** `lifecycle_audit.py` operates on analyzer JSON, which
   does not exist yet — so at this phase, query lifecycle status per MPN through the distributor
   skills directly. Re-run the proper `lifecycle_audit.py` sweep in phase 7 once a schematic
   exists, as a backstop.
4. Reject any part whose operating temperature range does not fully cover the phase-1
   environment. Reject NRND/EOL parts unless the user explicitly accepts, recorded as a waiver.
5. Identify a second source for every part that is single-sourced, sole-manufacturer, or on
   allocation. Where none exists, say so explicitly — that is a supply risk to record, not to
   hide.
6. Passives get classes, not individual MPNs, at this stage: value, tolerance, dielectric
   (X7R/C0G — never Y5V for anything that matters), voltage rating with derating, package size.

Write `docs/02-parts.md`: block → part table with the columns above, plus a rejected-alternatives
list with one-line reasons (this prevents re-litigating the same choice in a later session).

**Output.** `docs/02-parts.md`, updated `STATE.md`.

**Exit gate.** Every functional block from `00-function.md` maps to at least one MPN. No unapproved
NRND/EOL part. Every part's temperature range covers the environment. Power budget re-checked
against the actual parts' quiescent and peak currents. User approves the list.

**Token note.** Search once per block, not once per candidate. Record results to the file
immediately — do not hold a comparison table in context across multiple blocks.

---

## §8 Phase 3 — Datasheet gathering and conversion to `.md`

**Purpose.** Build the grounding corpus. From this point forward, every claim about a part traces
to a `.md` file with a page citation. This phase is what makes the review in phases 7 and 9
evidence-based rather than plausible-sounding.

**Entry gate.** Phase 2 approved.

**Inputs.** The MPN list in `docs/02-parts.md`.

**Actions.**

1. **Fetch PDFs.** kicad-happy's `sync_datasheets_digikey.py` takes a `.kicad_sch` as input —
   which does not exist yet. So drive retrieval by MPN through the `digikey` skill (fallbacks:
   `lcsc`, `element14`, `mouser`) into `datasheets/<MPN>.pdf`. In phase 7, run
   `sync_datasheets_digikey.py <name>.kicad_sch` as a backstop to catch anything added during
   schematic capture.
2. **Extract.** Use the `datasheets` skill to produce `datasheets/extracted/<MPN>.json`. Record
   the extraction quality score it returns.
3. **Convert to `.md`.** For each MPN write `datasheets/md/<MPN>.md` using the template below.
   Where the extraction is thin or the quality score is low, read the specific PDF pages needed
   to fill the gaps — then never open that PDF again.

**Fixed template — every `<MPN>.md` has exactly these sections, in this order:**

```markdown
# <MPN> — <one-line description>
manufacturer: · package: · datasheet_rev: · extraction_quality: · pdf: datasheets/<MPN>.pdf

## 1. Identity and package
Pin count, package, thermal pad y/n, pitch, footprint name if a KiCad library match exists.

## 2. Pinout
| Pin | Name | Type | Function | Notes |     ← every pin, no ellipsis

## 3. Absolute maximum ratings          (p.NN)
| Parameter | Min | Max | Unit |        ← these become hard REQ-E limits

## 4. Recommended operating conditions  (p.NN)
| Parameter | Min | Typ | Max | Unit |

## 5. Key electrical characteristics    (p.NN)
Only what the design depends on: supply currents, logic thresholds, timing minima,
drive strength, leakage, reference accuracy.

## 6. Thermal                           (p.NN)
θJA, θJC, max junction temp, derating curve, required copper area or via count.

## 7. Required external components       (p.NN)
Decoupling (value, count, placement distance), bulk caps, pull-ups/downs with ranges,
crystal load caps, feedback network, mandatory series/termination resistors,
reference/bypass caps. Each with the datasheet's stated value or range.

## 8. Configuration and strapping        (p.NN)
Boot straps, address pins, mode pins, reset behaviour, power sequencing requirements.

## 9. Layout and application guidance    (p.NN)  ← QUOTE, do not paraphrase
Verbatim excerpts of every layout instruction the datasheet or its application note gives.
This section is the input to phase 5. Paraphrasing here loses the specificity that makes a
layout requirement checkable.

## 10. Open questions
Anything the datasheet does not answer that the design depends on.
```

**Output.** `datasheets/md/<MPN>.md` for every MPN, `datasheets/extracted/<MPN>.json`,
updated `STATE.md` with the count and the minimum extraction quality score.

**Exit gate.** Every MPN in `docs/02-parts.md` has an `.md`. Every `.md` has all ten sections
populated or an explicit "not applicable — reason". Sections 3, 4, 7 and 9 are non-empty for
every active component. Any extraction quality score below the skill's own threshold has been
manually verified against the PDF and marked as such.

**Token note.** This is the most token-expensive phase, and it is a one-time cost that pays for
itself across phases 4, 5, 7 and 9. Do it properly. Process one MPN at a time and write the file
before starting the next — never hold multiple datasheets in context simultaneously. Passives
that are value-classes rather than specific MPNs do not need an `.md` unless their dielectric or
derating behaviour matters to a requirement.

---

## §9 Phase 4 — Requirements from datasheets

**Purpose.** Convert datasheet facts into checkable, traceable `REQ-E` requirements. This is
mechanical translation, not design judgement — judgement belongs to phase 5.

**Entry gate.** Phase 3 complete.

**Inputs.** `datasheets/md/*.md` sections 3, 4, 5, 6, 7, 8. `docs/00-function.md` for the
operating conditions the requirements are evaluated against.

**Actions.** No tools. For each `.md`, walk sections 3→8 and emit one `REQ-E` per constraint:

- **From §3 Absolute maximum** — every rail, input voltage and current the design could approach.
  State the limit and the design margin required.
- **From §4 Recommended operating** — supply ranges, input voltage ranges, clock frequency
  limits, logic level compatibility across every interface between two different parts.
  Cross-domain interfaces get an explicit requirement on *both* sides.
- **From §5 Electrical characteristics** — pull-up/pull-down ranges derived from leakage and
  drive strength, timing minima that constrain series resistance, reference accuracy where it
  sets an output.
- **From §6 Thermal** — max junction temperature at the phase-1 ambient, given the part's
  computed dissipation. Required copper area or thermal via count becomes a `REQ-E`, since it is
  a hard limit, not guidance.
- **From §7 Required external components** — decoupling value/count/placement, bulk capacitance,
  crystal load caps, feedback dividers, mandatory terminations. One requirement per component
  group, with the value or range as stated.
- **From §8 Strapping** — every strap pin's required state, every power-sequencing constraint.

Rules:
- Every requirement cites `<MPN>.md §section p.NN`. **An uncited requirement is invalid** —
  delete it or find its source.
- Every requirement names a `Verify by` method that phase 7 can actually run.
- Quantify. "Adequate decoupling" is not a requirement; "3× 100nF X7R within 3mm of pins
  12, 24, 36" is.
- Where the datasheet gives a range, the requirement states the range and the chosen value.

Write `docs/04-requirements-electrical.md`, grouped by functional block, with a summary count.

**Output.** `docs/04-requirements-electrical.md`, updated `STATE.md`.

**Exit gate.** Every active component has ≥1 `REQ-E`. Every rail in the power budget has a
`REQ-E`. Every interface between two components has a level-compatibility `REQ-E`. Zero uncited
requirements. Zero requirements without a verification method.

**Token note.** Read each `.md` once, emit its requirements, move on. Do not hold the whole corpus
in context. Write the output file incrementally, block by block.

---

## §10 Phase 5 — Requirements from guidance and best practice

**Purpose.** Convert the *layout* guidance — datasheet section 9 plus general engineering
practice — into `REQ-L` requirements that phase 8 designs against and phase 9 verifies. Phase 4
captured what the silicon requires; phase 5 captures what the physics requires.

**Entry gate.** Phase 4 complete.

**Inputs.** `datasheets/md/*.md` §9 (verbatim guidance), `docs/01-mechanical.md` (stackup,
envelope), `docs/00-function.md` (interfaces, rates, compliance targets).

**Actions.** No tools. Two sources, clearly labelled in the register:

**(a) Datasheet-derived** — from each `.md` §9. Typical yield: decoupling placement distance and
loop path, ground/thermal pad via count and pattern, crystal placement and guard ring, star vs
plane grounding for a specific part, sensitive-node keepouts, feedback trace routing for
regulators, exposed-pad connection, recommended footprint deviations.

**(b) Practice-derived** — general rules the datasheets do not state. Apply these systematically
rather than ad hoc:

| Domain | Requirement to emit |
|---|---|
| Every switching regulator | Input loop area bounded; input cap adjacent to the switch node; feedback trace away from the inductor; switch-node copper minimized |
| Every differential pair | Target impedance ± tolerance, intra-pair length match, inter-pair spacing, reference-plane continuity for the whole run |
| Every clock net | Length ceiling, no layer changes without a return via, guard/keepout, no parallel run with a sensitive net beyond a stated length |
| Every plane transition | Return path continuity — a stitching via within a stated distance of every signal via crossing reference planes |
| Every high-current path | Trace width from IPC-2152 for the current and allowable rise, at the phase-1 ambient |
| Every connector | ESD path length, chassis/shield strategy, mating-cycle mechanical support |
| Every sensitive analog node | Keepout, guard, separation from switching and digital domains |
| Board edge | Clearance for every copper feature and every component body |
| Assembly | Fiducial count and placement, test point coverage, courtyard clearance, tombstoning-risk mitigation for fine-pitch passives, silkscreen never over pads |
| Stackup | Layer assignment with reference plane for each signal layer; dielectric thicknesses that make the impedance targets achievable |

Rules:
- Every requirement is **measurable by a phase-9 check** — a distance, a count, a tolerance, a
  ratio. "Keep it short" is not a requirement; "≤ 15 mm, no layer change" is.
- Datasheet-derived requirements cite the source. Practice-derived requirements state their
  **rationale** — the failure mode being prevented. The rationale is what justifies a waiver
  decision later.
- Do not restate a `REQ-E` here. If it came from a table, it is a `REQ-E`; if it came from
  guidance, it is a `REQ-L`.

Write `docs/05-requirements-layout.md`, grouped by net class / functional block.

**Output.** `docs/05-requirements-layout.md`, updated `STATE.md`.

**Exit gate.** Every high-speed net has a `REQ-L`. Every switching regulator has a `REQ-L`. Every
sensitive analog node has a `REQ-L`. Every `.md` §9 instruction is either converted to a `REQ-L`
or explicitly marked not-applicable with a reason. Stackup is now fully specified — layer count,
assignment, dielectric thicknesses, impedance targets — and phase 8 will not revisit it.

**Token note.** Read `.md` §9 only, not whole files. The practice-derived table above is a
checklist — walk it once against the design and emit; do not re-derive it per part.

---

## §11 Phase 6 — Schematic

**Purpose.** Build a schematic that satisfies every `REQ-E`. Not a first draft — a schematic
whose every connection traces to a requirement or to the function block diagram.

**Entry gate.** Phases 4 and 5 complete. **R1: `git commit` now.**
**R3: close the KiCad schematic editor and keep it closed for this entire phase.**

**Inputs.** `docs/00-function.md`, `docs/02-parts.md`,
`docs/04-requirements-electrical.md`, `datasheets/md/*.md` §2 (pinout) and §7.

**Actions.** Konnect only. Load and unload toolsets in this order:

1. **Plan the netlist before touching a tool.** From the requirement register and block diagram,
   write the intended net list — net name, member pins, net class — as a working note. Capture
   from a netlist is dramatically cheaper than capture-then-discover.
2. `load_toolset("templates")` — `search_templates` / `list_template_categories` for any stock
   block in the design (USB-C, LDO, buck, STM32 minimal, I²C pull-ups, LED). `apply_template`
   where one fits, then verify the applied values against the relevant `REQ-E` — a template's
   defaults are not your requirements. `unload_toolset("templates")`.
3. `load_toolset("sch_components")` — `create_schematic` if new. Place all components with
   `add_schematic_component`, working block by block in the block-diagram order. Use
   `batch_get_schematic_pin_locations` once per block to get coordinates for the wiring step —
   never one `get_schematic_pin_locations` call per pin. Keep it loaded through step 4 if
   convenient, but do not load wiring tools yet.
4. `load_toolset("sch_wiring")` — wire block by block:
   - `add_power_symbol` for every rail first
   - `batch_add_wire` per block, one call per block
   - `add_schematic_net_label` for every named net; `connect_to_net` / `connect_pins` for
     point-to-point
   - `batch_add_junction` for all junctions in the block, one call
   - `add_no_connect` on every intentionally unconnected pin — this is not cosmetic; it is what
     makes ERC meaningful in phase 7
5. `load_toolset("sch_hierarchy")` if the design is hierarchical — sheets, hierarchical labels,
   sheet pins. Unload when done.
6. `load_toolset("sch_batch")` for any bulk edit passes (mass value changes, mass property
   edits).
7. Back in `sch_components`: `annotate_schematic` **last**, after all placement is final.
   Annotating early means every later reference in your notes is wrong.
8. Set component fields that phases 7 and 10 depend on: MPN, manufacturer, datasheet URL,
   footprint, DNP flags. A missing MPN field breaks BOM export and datasheet sync.
9. `unload_toolset` everything.

**Do not** run any review in this phase. Building and checking are separate phases for a reason —
mixing them doubles the token cost and produces neither a complete schematic nor a complete
review.

**Output.** `<name>.kicad_sch`, updated `STATE.md`, `git commit`.

**Exit gate.** Every component in `docs/02-parts.md` is placed. Every net in the planned netlist
exists. Every pin is connected or has a no-connect. All components annotated. MPN and footprint
fields populated. Schematic saved to disk (it already is — Konnect writes directly).

**Token note.** Batch calls are the single largest lever in this phase (§4.4). Plan the netlist
first — iterative discovery through repeated `sch_analysis` queries costs far more than thinking
before acting. Do not call `get_schematic_view` repeatedly to "look at" the schematic; you have
the netlist plan.

---

## §12 Phase 7 — Check: schematic

**Purpose.** Prove the schematic satisfies every `REQ-E`, with evidence. Nothing proceeds to
layout on an unverified schematic.

**Entry gate.** Phase 6 exit gate passed. The `.kicad_sch` is on disk (it is — Konnect wrote it
directly; but if the KiCad editor was opened during phase 6 in violation of R3, verify the file
content is what you expect before analyzing).

**Inputs.** `<name>.kicad_sch`, `docs/04-requirements-electrical.md`, `datasheets/md/*.md`.

**Actions.** kicad-happy first, Konnect only to fix.

1. **Datasheet backstop.**
   `python3 <digikey-skill>/scripts/sync_datasheets_digikey.py <name>.kicad_sch` — catches any
   part added during capture that skipped phase 3. Any new part gets a full phase-3 `.md` and
   phase-4 requirements before continuing. **R4 applies: go back, do not patch forward.**
2. **Structural analysis.**
   `python3 <kicad-skill>/scripts/analyze_schematic.py <name>.kicad_sch --analysis-dir analysis/`
3. **Read findings, not JSON** (§4.2):
   `python3 <kicad-skill>/scripts/summarize_findings.py analysis/ --top 15 --severity high`
4. **Authoritative ERC.** `kicad-cli sch erc --exit-code-violations <name>.kicad_sch`
   ERC errors are blocking. ERC warnings are triaged: fixed, or waived with reason.
5. **SPICE.** If a simulator is present, run the `spice` skill against the detected subcircuits —
   filters, dividers, feedback networks, opamp stages. Monte Carlo on any network whose tolerance
   stack affects a `REQ-E`. Without a simulator, the skill falls back to analytical models; note
   the reduced confidence in the review.
6. **Lifecycle backstop.**
   `python3 <kicad-skill>/scripts/lifecycle_audit.py analysis/<run>/analysis.json --temp-range <industrial|commercial|automotive>`
   This is the proper run that phase 2 could not do.
7. **Requirement traceability — the core of this phase.** Walk
   `docs/04-requirements-electrical.md` top to bottom. For each `REQ-E`, run its stated
   `Verify by` method and set the status with evidence:
   - decoupling requirements → the analyzer's decoupling analysis
   - pull-up/divider/filter values → `what_if.py` or the analyzer's computed fields
   - level compatibility → the analyzer's net + pin-type data, checked against `.md` §4
   - strapping → net membership per strap pin
   - thermal ceilings → deferred to phase 9 (needs the PCB), marked `OPEN — phase 9`
   Cross-check every claim against `datasheets/md/<MPN>.md`, **not** against the KiCad library
   symbol. Library symbols are frequently wrong; a symbol matching the schematic matching the
   analyzer proves internal consistency, not physical correctness.
8. **Fix loop.** For each `FAIL`: load the minimal Konnect toolset, fix, unload, re-run only the
   affected analysis. Use `what_if.py` to find corrected component values before editing —
   including inverse solve (`--fix <target> --target <value>`) with E-series snapping. Repeat
   from step 2 until the gate passes. Use `diff_analysis.py` between runs to confirm the fix
   landed and nothing else moved.
9. **False-positive triage.** Every finding the analyzer raises that you dismiss must be recorded
   with the reason. An undocumented dismissal is indistinguishable from an oversight.

Write `docs/07-schematic-review.md`: findings by severity, the full `REQ-E` traceability matrix
with evidence, SPICE results, false-positive triage, and an explicit list of what was *not*
checked and why.

**Output.** `docs/07-schematic-review.md`, updated register statuses, updated `STATE.md`,
`git commit`.

**Exit gate.** Zero ERC errors. Zero unwaived high-severity findings. **100 % of `REQ-E` are
`PASS`, `WAIVED`, or explicitly deferred to phase 9 with a reason.** Every SPICE-checked network
meets its requirement. Review document committed.

**Token note.** Never read analyzer JSON (§4.2, §4.3). The traceability matrix is read once, in
full, at the start of step 7 — that read is unavoidable and is the reason register rows are kept
to one line. On each fix iteration, re-run only the affected analysis and read the diff, not the
whole result.

---

## §13 Phase 8 — PCB

**Purpose.** Produce a layout that satisfies every `REQ-L`, from a verified schematic.

**Entry gate.** Phase 7 gate passed. **R1: `git commit` now.**
**KiCad 10 must be running with the board open** — Konnect's PCB tools work over the IPC API
against the live application, unlike the schematic tools.

**Inputs.** `<name>.kicad_sch`, `docs/05-requirements-layout.md`, `docs/01-mechanical.md`.

**Actions.** Konnect, in this order. **R2 applies throughout: save in KiCad after each numbered
step.**

1. `load_toolset("verification")` — `check_kicad_ui` to confirm the IPC connection;
   `launch_kicad_ui` if not running. Then `set_design_rules` from the phase-5 stackup and
   impedance targets, and `set_layer_constraints`. **Design rules before layout, not after** —
   rules set afterwards find violations you then have to undo work to fix.
2. `load_toolset("pcb_board")` — `set_board_size`, `add_board_outline` from
   `docs/01-mechanical.md`, `add_mounting_hole` per `REQ-M`, `add_layer` / stackup per phase 5.
   Save. `unload_toolset`.
3. `load_toolset("library")` if any footprint needs creating or verifying against `.md` §1.
   Verify every non-trivial footprint's pad geometry against the datasheet package drawing —
   a wrong footprint is discovered at assembly, which is the most expensive place to discover it.
   Unload.
4. `load_toolset("pcb_components")` — placement, which is where board quality is decided:
   - Fixed-position parts first: connectors, mounting-constrained parts, per `REQ-M`
   - Then power stages, honouring the loop-area `REQ-L`s
   - Then each functional block as a cluster, in block-diagram order
   - Then decoupling — every cap placed to its `REQ-E` distance, not "near the part"
   - `place_component_array` and `align_components` for regular groups (§4.4)
   - `get_board_2d_view` sparingly — once per major placement milestone, not continuously
   Save. Unload.
5. `load_toolset("pcb_routing")` — routing in strict priority order:
   - `create_netclass` + `assign_net_to_class` for every class in phase 5, **before** routing
   - Critical nets by hand first: `route_differential_pair` for pairs, `route_trace` for clocks
     and sensitive analog, `add_via` with return-path stitching per `REQ-L`
   - High-current paths at the widths computed in phase 5
   - Then bulk signal routing
   - `add_copper_pour` for planes; `add_zone` from `pcb_board` if a split or keepout is required
   Save. Unload.
6. `load_toolset("verification")` — `run_drc` and `check_clearance` as you go, not only at the
   end. Fix as found. Unload.
7. **Save the board.** R2. Phase 9 reads the disk.

On autorouting: Konnect exposes `autoroute` / `check_freerouting` in the `integration` toolset,
which §4.1 forbids loading. If the user explicitly requests autorouting, load `integration` for
that single call and unload immediately — and route every `REQ-L`-constrained net by hand first,
before the autorouter touches the board. An autorouter has no knowledge of your requirement
register.

**Output.** `<name>.kicad_pcb` saved to disk, updated `STATE.md`, `git commit`.

**Exit gate.** All components placed inside the outline. All nets routed. All zones poured.
`run_drc` returns zero errors. Board saved to disk.

**Token note.** Set design rules first so the tool enforces constraints instead of you checking
them by hand. Do not call `get_board_2d_view` or `query_traces` in a loop — one view per
milestone. Route in class order so `create_netclass` does the constraint work once for a whole
group.

---

## §14 Phase 9 — Check: PCB

**Purpose.** Prove the layout satisfies every `REQ-L`, close the `REQ-E`s deferred from phase 7,
and confirm the board is manufacturable.

**Entry gate.** Phase 8 gate passed. **R2: confirm the board is saved in KiCad before doing
anything else in this phase.** If any finding below looks impossible, re-check the save first.

**Inputs.** `<name>.kicad_pcb` (on disk), `<name>.kicad_sch`,
`docs/05-requirements-layout.md`, `docs/04-requirements-electrical.md` (deferred rows),
`datasheets/md/*.md` §6 and §9.

**Actions.**

1. **Authoritative DRC.** Konnect `verification` → `run_drc`; `pcb_export` → `refill_zones` then
   `get_drc_violations`. Zero errors is a hard requirement — DRC errors block everything.
   Unload both.
2. **Layout analysis.**
   `python3 <kicad-skill>/scripts/analyze_pcb.py <name>.kicad_pcb --analysis-dir analysis/ --full --proximity`
   `--full` is required — it is what produces per-segment impedance from the stackup, pad-to-pad
   routed distances, and the `connectivity_graph` for island analysis. `--proximity` adds
   crosstalk/trace-proximity analysis.
3. **Cross-domain consistency.**
   `python3 <kicad-skill>/scripts/cross_analysis.py --schematic analysis/<run>/schematic.json --pcb analysis/<run>/pcb.json --analysis-dir analysis/`
   Catches schematic↔PCB divergence: component count parity, net name consistency, pin-to-net
   alignment, footprint cross-reference, DNP propagation. Any divergence here means the two
   halves of the design disagree — treat as blocking.
4. **Thermal.**
   `python3 <kicad-skill>/scripts/analyze_thermal.py -s analysis/<run>/schematic.json -p analysis/<run>/pcb.json --ambient <phase-1 max ambient> --analysis-dir analysis/`
   This closes the `REQ-E` thermal rows deferred from phase 7. Check junction temperatures
   against `.md` §6, not against a default assumption.
5. **EMC pre-compliance.** Run the `emc` skill — 44 rules covering radiated-emission risk, PDN
   impedance, differential-pair skew, ESD paths, ground-plane voids, loop areas. Weight the
   results by the phase-1 compliance targets.
6. **Findings.** `summarize_findings.py analysis/ --top 15 --severity high`, then `medium` once
   the highs are closed.
7. **Requirement traceability.** Walk `docs/05-requirements-layout.md` and set each `REQ-L` to
   `PASS` / `FAIL` / `WAIVED` with evidence from the analyses above. Also close every `REQ-E` row
   marked "deferred to phase 9". Impedance, length-match, clearance, via-count and loop-area
   requirements are verified from the `--full` analyzer output; anything the analyzer cannot
   measure is verified `manual` and the method recorded.
8. **Fix loop.** Return to phase 8 for each `FAIL`, fix, **save**, re-run only the affected
   analysis. `diff_analysis.py` between runs to confirm the fix and detect collateral change.
9. **False-positive triage.** Same rule as phase 7 — every dismissed finding gets a recorded
   reason.

Write `docs/09-pcb-review.md`: DRC result, findings by severity, full `REQ-L` traceability matrix,
closed `REQ-E` deferrals, thermal results, EMC assessment, triage, and what was not checked.

**Output.** `docs/09-pcb-review.md`, updated register, updated `STATE.md`, `git commit`.

**Exit gate.** Zero DRC errors. Zero cross-analysis divergences. Zero unwaived high-severity
findings. Every junction temperature within its `.md` §6 limit at the phase-1 ambient.
**100 % of `REQ-L` are `PASS` or `WAIVED`. 100 % of `REQ-E` are now closed.** Review committed.

**Token note.** `--full --proximity` produces the largest artifact in the flow (up to 300 KB).
Never read it (§4.2). Every fact you need is reachable through `summarize_findings.py` or a
single-field extraction — and the field names in §4.3 are what make those extractions work first
time.

---

## §15 Phase 10 — Production files

**Purpose.** Generate the fabrication and assembly package, **verify the generated output**, and
produce a release record. Exporting is not the deliverable; a verified, reproducible package is.

**Entry gate.** Phase 9 gate passed. **R1: `git commit` now** — the release must reference an
exact commit.

**Inputs.** `<name>.kicad_pcb`, `<name>.kicad_sch`, both review documents, `docs/02-parts.md`.

**Actions.**

1. `load_toolset("pcb_export")` — `refill_zones` first (never export unfilled zones), then:
   `export_gerber` → `production/gerbers/`, `export_position_file` → `production/cpl/`,
   `export_bom` → `production/bom/`, `export_3d` → `production/3d/`, `export_pdf` →
   `production/pdf/`. Add `export_ipc2581` or `export_odb` if the fab house prefers it —
   IPC-2581/ODB++ carry netlist and stackup intent that Gerbers do not.
   Drill files come with the Gerber export; confirm they landed in `production/drill/`.
2. `load_toolset("manufacturing")` — `validate_for_manufacturing`, then
   `export_manufacturing_package` for the combined deliverable, and `estimate_cost` for the
   record. Unload both toolsets.
3. `load_toolset("sch_export")` — schematic PDF and netlist into `production/pdf/`. Unload.
4. **Verify what was actually generated** — this is the step that distinguishes a release from an
   export:
   `python3 <kicad-skill>/scripts/analyze_gerbers.py production/gerbers/ --analysis-dir analysis/ --full`
   Checks layer completeness (GR-001), layer alignment (GR-002), drill integrity (GR-003), paste
   aperture mismatch (GR-004), open board outline (GR-005). **Any GR finding is blocking** — it
   means the files you would send to the fab do not describe the board you designed.
5. **Fab-house validation.** Run the `jlcpcb` or `pcbway` skill (whichever matches the target):
   design-rule conformance against that house's capabilities, BOM and CPL format validation,
   assembly-readiness. Fix format issues by re-exporting, not by hand-editing generated files.
6. **BOM finalization.** Use the `bom` skill: source every line, confirm stock at build quantity,
   confirm lifecycle status has not changed since phase 2, price the build. Cross-check the
   exported BOM's part count against the schematic component count.
7. Write `docs/10-release.md`.

**Release manifest — `docs/10-release.md` contains:**

```markdown
# Release <version> — <date>
git commit: <full sha>          KiCad: 10.x.x          board rev: <rev>

## Files            (path · sha256 · purpose)
## Gates            (phase · date · result · review doc)
## Requirements     (REQ-E: n PASS / n WAIVED · REQ-L: n PASS / n WAIVED)
## Waivers          (ID · rationale · accepted by · risk if wrong)
## Known deviations from fab-house rules, with the house's confirmation
## BOM summary      (line count · unit cost · build cost at qty · single-sourced parts)
## Not verified     (anything untested, and why)
```

**Output.** `production/**`, `docs/10-release.md`, `git commit` + `git tag`, and **push**.

**Exit gate.** Gerber analysis returns zero GR findings. Fab-house validation passes. Every
exported file is listed with a checksum in the manifest. Every waiver is recorded with an
accepting party. The release commit is tagged and pushed.

**Token note.** Export, then verify — do not read the exported files. `analyze_gerbers.py` output
is ~10 KB, the smallest artifact in the flow; even so, reach it through `summarize_findings.py`
for consistency.

---

## §16 Waivers and the definition of production-ready

### 16.1 Waiver policy

A `FAIL` may be closed as `WAIVED` only with all four of these recorded in `docs/waivers.md`:

1. **What** — the requirement ID and what the design does instead
2. **Why** — why the requirement does not apply, or why the deviation is acceptable here
3. **Who** — the human who accepted it. **An agent cannot waive a requirement.**
4. **Risk** — what fails, and how it manifests, if the waiver judgement is wrong

A `REQ-E` waiver is a strong signal something is wrong: `REQ-E` comes from datasheet hard limits.
Waiving one usually means the requirement was mis-transcribed in phase 4, or the part is being
operated out of spec. Check the first before accepting the second.

`REQ-L` waivers are routine — layout guidance frequently conflicts with mechanical reality. The
rationale recorded in phase 5 is what makes the trade-off assessable.

### 16.2 Definition of production-ready

All of the following. No partial credit.

- [ ] Phase 1 documents signed off by the user
- [ ] Every MPN lifecycle-screened; no unapproved NRND/EOL part
- [ ] Every MPN has a `datasheets/md/<MPN>.md` with sections 3, 4, 7, 9 populated
- [ ] Every `REQ-E` cited to a datasheet section and page
- [ ] Every `REQ-L` measurable, and either datasheet-cited or rationale-stated
- [ ] Zero ERC errors; zero DRC errors
- [ ] Zero cross-analysis schematic↔PCB divergences
- [ ] Zero unwaived high-severity findings in phases 7 and 9
- [ ] Every junction temperature within limit at the specified maximum ambient
- [ ] 100 % of `REQ-E` and `REQ-L` closed as `PASS` or `WAIVED`
- [ ] Every waiver has rationale, a named human acceptor, and a stated risk
- [ ] Gerber analysis clean; fab-house validation passed
- [ ] BOM fully sourced with stock confirmed at build quantity
- [ ] Release manifest committed, tagged, pushed

If any box is unchecked, the board is not production-ready. Say so plainly rather than
qualifying it.

---

## §17 Recovery

**Konnect reports success but nothing changed.** A known failure mode — some tools have
historically returned success without completing the action. Do not retry blindly. Query the
actual state (`get_schematic_component`, `find_component`, `query_traces`), confirm, then decide.
If it genuinely did not apply, `git diff` to see what the tool did do before retrying.

**Konnect IPC disconnects mid-phase-8.** PCB tools need KiCad running with the board open.
`check_kicad_ui` to confirm; `launch_kicad_ui` to restart. **Anything unsaved in KiCad at
disconnect is lost** — this is why R2 says save after each numbered step, not only at the end.

**A write corrupted a file.** `git checkout -- <file>`, then redo the step in smaller increments.
Report the corrupting call to the user; it is worth an upstream issue.

**Orphan `konnect` server processes accumulate.** Known upstream issue (#103) — the PID file
tracks only the last instance. Kill strays manually if the MCP server becomes unresponsive.

**JLCPCB parts database returns 404.** Expected (issue #97). §1.1 already routes sourcing through
kicad-happy's distributor skills. Do not attempt `download_jlcpcb_database`.

**Analyzer field-name error.** Re-read §4.3 rather than guessing a variant. Guessing costs a
retry each time.

**A finding contradicts a change you just made.** Check R2 — the board was almost certainly not
saved. Second most likely: you read a cached analysis run. Check `analysis/manifest.json`.

**Resume after `/clear`.** Read, in this order and nothing else:
1. `STATE.md` — current phase, gate status, open items, next action
2. This file's phase section for the current phase
3. The specific artifact named in `STATE.md`'s next action

Do not re-read earlier phases' artifacts. Do not re-run passed analyses. If `STATE.md` is stale
or missing, rebuild it from the `docs/` files present and the last commit — then treat that as
the resume point.

---

## §18 Quick reference

**kicad-happy scripts** (all under the relevant skill's `scripts/`, all read-only):

| Script | Purpose | Phase |
|---|---|---|
| `sync_datasheets_digikey.py <sch>` | Fetch datasheets for parts in a schematic | 7 (backstop) |
| `analyze_schematic.py <sch> --analysis-dir analysis/` | Schematic structure, nets, subcircuits, ERC audit | 7 |
| `analyze_pcb.py <pcb> --analysis-dir analysis/ --full --proximity` | Layout, impedance, DFM, connectivity graph | 9 |
| `cross_analysis.py --schematic <json> --pcb <json>` | Schematic↔PCB divergence | 9 |
| `analyze_thermal.py -s <json> -p <json> --ambient N` | Junction temperatures | 9 |
| `analyze_gerbers.py <dir> --full` | Verify generated fab output | 10 |
| `summarize_findings.py analysis/ --top N --severity high` | **The way to read findings** | 7, 9, 10 |
| `what_if.py <json> R5=4.7k --text` | Value sweep / inverse solve with E-series snapping | 7 |
| `diff_analysis.py base.json head.json --text` | Confirm a fix landed, detect collateral change | 7, 9 |
| `lifecycle_audit.py <json> --temp-range <range>` | NRND/EOL + temperature coverage | 7 |
| `sexp_parser.py` | Manual S-expression fallback when a script fails | any |

**kicad-happy skills:** `kicad` `spice` `emc` `datasheets` `bom` `digikey` `mouser` `lcsc`
`element14` `jlcpcb` `pcbway`

**Konnect toolsets** (18; starter kit `project` + `config` preloaded; `load_toolset` /
`unload_toolset` for the rest):
`sch_components` `sch_wiring` `sch_analysis` `sch_batch` `sch_export` `sch_hierarchy`
`pcb_board` `pcb_components` `pcb_routing` `pcb_export` `library` `verification`
`templates` `manufacturing` — plus `design_review` and `integration`, **both forbidden** (§1.1).

**kicad-cli:** `kicad-cli sch erc <sch>` · `kicad-cli pcb drc <pcb>` · `kicad-cli version`

**The four rules:** R1 commit before writes · R2 save before analysis · R3 schematic editor
closed during phase 6 · R4 phase order is fixed.
