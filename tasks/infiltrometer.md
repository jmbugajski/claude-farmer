# Single-Ring Infiltrometer

**Type:** Manual, seasonal (run in spring before planting)
**Purpose:** Measure how fast the raised bed actually absorbs water, in inches per
hour, and compare it to the 6.6 in/hr the emitters deliver.
**Time:** ~20 min per spot, plus pre-wetting soak time. Budget 1.5 hr for three spots.

---

## Why this test exists

`config.json` asserts `bed.infiltration.raised_bed_mix_estimate_in_per_hr = [1.0, 3.0]`.
**That number has never been measured.** It is a textbook range for "raised bed mix,"
and the entire over-application thesis — the claim that a 6.6 in/hr application rate
ponds, runs to the edges and channels down preferential paths instead of wetting the
profile — rests on it.

This test replaces the assumption with a measurement. It also settles a second
question the dashboard cannot answer: whether the bed absorbs water *evenly*, or
whether some parts of it take water far faster than others.

Two opposite problems look identical from above, and both leave the profile
under-wetted:

- **High infiltration** — water passes through fast, carrying dissolved nutrients
  below the root zone. Supports the "doesn't hold water, feeding washes out" theory.
- **Low infiltration** — water ponds on the surface and runs to the bed edges
  without soaking in. Same symptom, opposite fix.

Rebuilding the bed without knowing which one you have is guesswork.

---

## Materials

| Item | Notes |
|---|---|
| Ring | Large coffee can with **both** ends removed, or a 6 in length of 4–6 in PVC |
| Mallet | Rubber or dead-blow |
| Wood block | Scrap 2×4 — strike this, never the rim |
| Ruler | Steel, marked in 1/8 in or mm |
| Water | ~2 gal per spot including pre-wet |
| Stopwatch | Phone is fine |
| Notebook | Or print the table below |

A steel can beats PVC — the thin wall cuts in rather than compressing the soil
around it. Compressing the soil at the rim is the single most common way to get a
falsely low reading.

---

## Procedure

1. **Pick the spots.** Three minimum. Deliberately include **one directly under a
   bubbler** and **one as far from any emitter as the bed allows.** The difference
   between those two is a large part of the point.

2. **Check bed moisture.** Test at normal working moisture, not bone dry and not
   just-irrigated. The morning after a normal run is about right.

3. **Drive the ring in 3–4 inches,** vertically. Wood block on top, strike the
   block. Shallower than 3 in and water escapes sideways under the rim; deeper than
   about 4 in and you are fighting the can. Stop if it visibly tilts — pull it and
   restart a few inches away.

4. **Pre-wet.** Fill the ring, let it drain completely, then fill and drain a second
   time. **Do not skip this.** Dry soil pulls water by capillary suction, which is
   not infiltration, and an un-pre-wetted first reading runs 3–5× too high. This step
   is the difference between a real number and a useless one.

5. **Run the test.** Fill to a marked depth — 4 in is convenient. Start the
   stopwatch and record the water level at intervals:

   - every 1 min for the first 5 min
   - every 5 min thereafter, until the rate stops changing

6. **Stop** when three successive intervals give roughly the same rate. That plateau
   is the **steady infiltration rate** and is the number you want. The fast early
   readings are always discarded.

7. **Repeat** at the other spots. Same ring, same fill depth, same pre-wet.

---

## Recording table

Fill depth: ______ in   ·   Spot: ______   ·   Date: __________
Distance to nearest emitter: ______ in   ·   Bed moisture at start: ______

| Elapsed (min) | Level (in) | Drop since last (in) | Interval (min) | Rate (in/hr) |
|---|---|---|---|---|
| 0 | | — | — | — |
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |
| 4 | | | | |
| 5 | | | | |
| 10 | | | | |
| 15 | | | | |
| 20 | | | | |
| 30 | | | | |

`Rate (in/hr) = drop_in ÷ interval_min × 60`

**Steady rate for this spot: ______ in/hr**

Top the ring up if the level approaches the soil surface mid-test, and note the refill
— never let it run dry, or the next interval measures suction again.

---

## Interpreting the number

| Steady rate | Reading |
|---|---|
| < 0.5 in/hr | Compacted or structurally poor. Water ponds and runs off. |
| 0.5 – 1.0 | Slow. Application must be heavily cycled. |
| 1.0 – 3.0 | Normal amended raised-bed mix — the range `config.json` assumes. |
| > 3.0 | Coarse, fast-draining, low water holding. Supports the leaching theory. |

**Spread between spots matters as much as the average.** If the fastest spot is more
than ~2× the slowest, preferential flow is confirmed and no single irrigation
schedule will wet the bed evenly — that becomes a bed-rebuild or emitter-layout
problem, not a timer problem.

### Known bias

A single ring **overestimates**, because water spreads laterally below the ring as
well as downward. A double-ring setup (an outer buffer ring kept at the same level)
corrects this and is worth building only if the first result lands ambiguously near
6.6 in/hr.

For the decision at hand the bias is harmless and runs in the useful direction: **if
even the inflated single-ring number comes in below 6.6 in/hr, the over-application
case is proven** and better precision would not change the conclusion.

---

## What to do with the result

### 1. Set the cycle duty

The measurement directly sets the duty cycle that `plan.proposed_plan.cycle_mode`
has been carrying as a guess since 2026-08-03:

```
duty % = measured_infiltration_in_per_hr ÷ 6.6 × 100
```

| Measured | Duty | Example on/off (180 s cycle) |
|---|---|---|
| 1.0 in/hr | 15% | 27 s on / 153 s off |
| 1.7 in/hr | 26% | 45 s on / 135 s off |
| 3.0 in/hr | 45% | 81 s on / 99 s off |

The 45/135 pair already in `config.json` assumes 1.7 in/hr. If the measurement lands
elsewhere, update `bed.infiltration.raised_bed_mix_estimate_in_per_hr` and
`target_effective_rate_in_per_hr`, then recompute from the formula above.

### 2. Update config, not code

Write the measured value into `bed.infiltration`, replace the
`raised_bed_mix_estimate_in_per_hr` comment with the measurement date and the
per-spot results, and re-run `.venv/bin/python lib/build_dashboard.py`.

---

## Bed reference numbers

From `config.json`, for converting between litres, inches and run time:

| Quantity | Value |
|---|---|
| Bed area | 48 ft² (12 × 4 ft) |
| Soil depth | 10 in |
| Water per inch over the bed | 113.3 L |
| Measured line flow | 12.4 L/min = 744 L/hr |
| Application rate | 744 ÷ 113.3 = **6.6 in/hr** |
| One 90 s run (current plan) | ~19.7 L metered (`plan.metered_daily` 39.4 ÷ 2) = 0.174 in; 18.6 L at the nominal 12.4 L/min |
| Assumed AWC | 0.12–0.20 in/in → 1.2–2.0 in available in the profile |

That last row is the other unmeasured assumption in the file, and it is why a
10-inch bed gives only two or three days of buffer at summer demand. A soil lab test
(organic matter, CEC, pH, salts) measures it properly and is the natural companion to
this test — but run this one first, since it costs nothing but an afternoon.

---

## Common errors

- **Skipping the pre-wet.** Produces a number 3–5× too high. The most common failure.
- **Striking the rim** instead of a wood block. Deforms the ring, breaks the seal.
- **Driving too shallow.** Under 3 in, water escapes under the edge and the rate
  reads high.
- **Compacting the soil inside the ring** while seating it. Reads low.
- **Reading the early minutes as the answer.** Always use the plateau.
- **One spot only.** The variation between spots is half the information.
