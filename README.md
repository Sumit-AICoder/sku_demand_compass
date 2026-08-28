# Sonalika Demand Compass

Village-level implement (SKU) demand-propensity engine and dashboard for **Punjab,
Madhya Pradesh and Maharashtra**, built to the brief in `Wireframe.xlsx`.

It answers the two lines on that workbook's *Dashboard Overview* sheet:

1. **Demand potential at implement-type level, down to village granularity** — including
   replacement demand and implement quantity scaled by field size.
2. **SKU-wise demand potential predicted from soil, application and tractor conditions.**

On top of the brief, it decomposes tractor sales with an **unobserved components model
(UCM)** to size each factor's uplift, and segments villages into **agro-mechanisation
archetypes** so district-level elasticities can be carried down to the village.

---

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pipeline.run            # full build, ~60s
.venv/bin/python -m pytest pipeline/tests   # 33 verification tests

.venv/bin/python -m uvicorn api.main:app --port 8848   # API
cd web && npm install && npm run dev                   # dashboard on :5273
```

`pipeline/run.py --stage <name>` re-runs one stage; `--from <name>` runs from there on;
`--manifest` prints what each connector actually did.

---

## What the model does

```
Addressable(v, sku) = tractors in the SKU's HP band
                    x farm-size fit x crop fit x category ceiling x gating
Headroom            = Addressable - already owned
Potential           = (Headroom x conversion + Replacement) x Propensity

Propensity(v, sku)  = sum_i w_i(sku) . F_i(v)/100
                    x soil_fit x season x compete_headwind
```

`F_1..F_10` are the ten factor groups from the Excel *Factors Listings* sheet, each a
0–100 index built from named sub-factors.

**The weights are the point.** Where the UCM produced a significant, correctly-signed
coefficient from a model that beat seasonal-naive out of sample, the weight is
*empirical*. Otherwise it falls back to a judgmental prior. Every weight in the UI
carries an origin badge, and the split is never hidden — currently **148 of 370**
SKU-factor pairs are empirical.

Only four factor groups (farm economics, mechanisation, policy, monsoon) have a
time-varying driver a monthly model can identify. The other six are structural — they
barely move month to month — so they stay judgmental **by necessity, not oversight**.

---

## The UCM

Fitted per district on monthly tractor registrations:

```
log y_t = mu_t + gamma_t + psi_t + sum_j beta_j x_j,t + eps_t
```

local linear trend, stochastic 12-period seasonal, damped cycle, and 11 standardised
agri drivers (rainfall departure, reservoir, NDVI, mandi prices, MSP, credit, subsidy
intensity, rural wages, diesel, fertilizer offtake, PMFBY claims). Because `y` is logged
and `x` standardised, each beta reads as *"a 1-sd move in this driver shifts sales by
beta x 100 %"*.

Results on the current build: **114/114 districts converged, 114/114 beat seasonal-naive**
(median backtest MAPE 13.1 % vs 52.1 %), median R²-like 0.98.

It also produces the headline the dashboard leads with — year-on-year uplift attribution:

> Sangrur, −22.5 % YoY: NDVI −10.9 pp, diesel −10.6 pp, mandi prices −10.5 pp,
> offset by +10.6 pp structural trend.

The estimated seasonal `gamma_t` **replaces** the hand-coded seasonality table where the
data supports it: it peaks in October (1.46x) and troughs in July (0.79x), which is the
real Indian tractor buying rhythm rather than an assumption.

---

## Honest data provenance

Every table carries a `provenance` column, enforced at write time; the UI badges it.

| | |
|---|---|
| **real** | District boundaries (open GADM-derived geojson), district names and counts (PB 23, MP 55, MH 36), village counts anchored to Census 2011, SKU taxonomy and HP bands |
| **allocated** | District statistics downscaled to villages, and everything derived from the UCM |
| **simulated** | Layers with no public source: OEM implement sales, dealer network, competitor share, CHC density, finance penetration — all generated from documented parameters in `sim_params.yaml` |

**Why so much is simulated.** LGD's village master sits behind a session/CSRF wall with
no REST surface; Vahan is a JSF dashboard with no scriptable endpoint; data.gov.in needs
an API key. Each connector implements both `fetch_real()` and `synthesize()`, tries the
real path first, and records which ran in `data/raw/_manifest.json`. A dead endpoint
degrades the data and labels it — it never crashes the pipeline and never silently
passes simulation off as measurement.

### The limit worth knowing

**No public series of implement sales exists.** Implement elasticities are therefore
*derived* from tractor elasticities via HP band and crop fit, not directly estimated.
If Sonalika can supply dealer secondary-sales history, the same UCM machinery applies to
it directly — that is the single largest accuracy upgrade available to this model.

---

## Executive KPI framework

Six tiers, served by `GET /api/kpis` in one call:

| Tier | KPI | Question it answers |
|---|---|---|
| **1 Size** | demand units/yr · market value ₹cr · addressable tractors · villages covered | How big is this? |
| **2 Headroom** | penetration % · unserved units · implements per tractor · attach gap vs peers | How much is still unclaimed? |
| **3 Mix** | new vs replacement split · top category · top SKU · seasonal index | What kind of demand is it? |
| **4 Route to market** | avg km to dealer · % villages within 15 km · service & spares index · finance approval rate | Can we actually capture it? |
| **5 Priority** | Convert now / Build access / Defend / Monitor — villages and units each | What do we do Monday? |
| **6 Risk** | monsoon sensitivity (% per 1-sd rainfall) · competitive headwind · model confidence | What could move it? |

Tier 2 is the one most demand dashboards miss: market size without penetration tells you
where demand *is*, not where it is *available*.

## Verification (126 tests, all passing)

The one that gates everything: **`test_ucm_parameter_recovery`**. The simulated series is
built from coefficients known by construction, and the estimator must recover them. It
does — pooled betas land within 0.01 of truth across all 11 drivers.

`test_ucm_recovers_district_heterogeneity` goes further: the DGP gives rainfed districts a
much larger rainfall elasticity than irrigated ones, and the estimator recovers that
*variation* at r = 0.98 (rainfed 0.269 vs assured-irrigation 0.059). Without it, a drought
scenario would report the same impact everywhere — the most misleading thing this
dashboard could do.

Others cover: Census-anchored counts within 1 %, exact preservation of district totals
under downscaling, decomposition additivity, sign audit against the Excel's stated impact
directions, cluster bootstrap stability (ARI 0.98) and spatial coherence (0.97), level
reconciliation, and **face validity** — the model must reproduce what the Excel already
knows:

| SKU | Leader | Why |
|---|---|---|
| Super seeder, happy seeder, baler, straw reaper | Punjab | paddy-residue policy belt |
| Orchard sprayer | Maharashtra | grapes, orange, banana |
| Laser leveler | Punjab | irrigated north |
| Trolley | highest volume overall | "often bundled with tractor ownership" |

A full clean rebuild is **bit-for-bit reproducible** (max diff 0.0 across 3.9 M scores).

---

## Dashboard

Split into two groups, because there are two audiences.

**Business** — Summary (the ten-second answer: size of the prize, what is unclaimed,
what to do Monday), **Village finder**, Products, **Competition**, **Map explorer**.

**Analysis** — Demand map, What drives sales (UCM), Village types, Demand factors,
What-if, Data & method.

Every view carries a **plain-English briefing** at the top, and an **Ask the data**
chat is available from anywhere.

### The map

A real drill-down choropleth, **India → state → district → block → village**, where the
map *is* the navigation rather than a picture beside it.

Geometry uses **current Indian administrative boundaries** (36 states/UTs, post-2019
reorganisation), which depict **Jammu & Kashmir and Ladakh at their full official
extent** — including Gilgit-Baltistan and Aksai Chin. This is the depiction required of
a map published in India. An older GADM file is kept only as a fallback for the handful
of districts created after the primary source was published.

Each zoom level gets its own Ramer-Douglas-Peucker budget (the India view needs only
enough detail to read as India; a single district can afford a much finer edge),
bringing the whole set to ~900 KB across 118 files.

Two details worth knowing:

- **Geometry and value arrive in the same request.** The number colouring a shape is
  joined server-side, so a shape can never display a figure computed under a different
  filter — the commonest way a choropleth quietly misleads.
- **The colour scale adapts to the distribution.** Quantile bins spread a skewed
  distribution well but exaggerate trivial gaps when there are few shapes — three states
  within 5% of each other rendered lightest-to-darkest reads as a large difference that
  is not there. Below eight shapes, or a spread under 40%, the scale switches to linear
  anchored at zero.
- **Blocks have no real boundaries** (they are constructed in `geo_spine`), so rather
  than invent polygons the map draws Voronoi cells around block centroids and clips them
  to the district's *true* outline with an SVG `clipPath`: real edge, derived interior.
  Districts that share a historical parent polygon are flagged `shared` and say so on
  hover, instead of implying a boundary the data does not have.

### Competition and cannibalisation

Static market shares say who is winning but cannot say what happens if anyone *changes*
something, because a share is an outcome, not a mechanism. So share is computed from a
**multinomial-logit choice model** over things a business can actually move:

```
utility(brand) = brand equity + fit
               + price_sensitivity  x (price - market)
               + distance_sensitivity x (km beyond that brand's reach)
share(brand)   = softmax(utility)
```

Because shares are a softmax they sum to one, so a gain for one brand is necessarily a
loss for the others — which is what makes switching *calculable* rather than asserted.
A scenario slider then answers "Shaktiman expands its dealer network 40% and cuts price
5%" with a number and the categories it lands in.

**External** — Sonalika sits at ~8% share, so the framing is a challenger's. Contests are
graded **Leading / Winnable / Stretch / Out of reach** by how close the nearest rival is,
because a small gap is a sales problem and a large one is a structural one; treating them
alike wastes the field team's week. Local fabricators hold the most volume (139k units) but
lose it wherever a dealer is close — they compete on price and cannot be travelled to.

**Internal** — Sonalika SKUs that compete with each other, gated on doing the **same job**
rather than sharing a catalogue category. A super seeder and a seed drill both put wheat in
the ground and genuinely compete (overlap 0.68); a trolley competes with nothing. Net
displacement is ~6.6% of gross demand, capped per SKU per village — a product with three
in-house substitutes cannot lose three times 45% of itself.

### Product imagery

Openly-licensed photographs from Wikimedia Commons (22 of 37 SKUs), downloaded once and
served locally rather than hotlinked. Each carries its licence and author in the UI,
because most of those licences require attribution. Where no suitable photograph exists
the UI draws a category icon instead of showing a loosely related picture — a photograph
of the wrong machine is worse than none, because it is read as fact.

### Village-level operational layer

The archetype layer answers *what kind of village is this* — a strategy answer that says
nothing about which of 10,000 villages to visit. On top of it sits the operational layer:

- **24 micro-segments** — each archetype sub-clustered on opportunity dimensions
  (attach rate, dealer access, credit, replacement pressure), so "High-Mech Irrigated
  Wheat-Paddy" resolves into four pockets with different recommended actions rather than
  one undifferentiated mass of 9,639 villages.
- **Four action segments**, from two questions a sales head actually acts on:

  |  | dealer near | dealer far |
  |---|---|---|
  | **headroom high** | Convert now — 25,603 villages, 123k units | Build access — 27,020 villages, 92k units |
  | **headroom low** | Defend — 27,020 villages | Monitor — 25,603 villages |

- **Peer gap** — attach rate against the village's *own micro-segment*, not a district
  average. Same type, soil, crop and farm size: if one village buys less iron than its
  twins, that difference is addressable.
- **A headline per village**, generated from that village's own numbers:
  > *#1 of 603 in Barnala on opportunity. About 81 implements of unserved demand, a
  > dealer 3 km away, and an attach rate 0.20 below comparable villages.*

### Narratives and chat

Both are grounded the same way, and the rule is strict: **the model never supplies
facts.** Deterministic Python computes a fact pack from DuckDB; the model re-writes that
pack into prose, or picks which query to run and reads the result back.

**Providers.** `api/llm.py` auto-detects **Azure OpenAI (GPT-4.1)** or **Anthropic
(Claude)** and falls back to deterministic output when neither is configured. Tools are
declared once in Anthropic shape and translated to OpenAI function schema, so adding a
tool never means maintaining two definitions. Configure via `.env` (see `.env.example`);
`AZURE_OPENAI_DEPLOYMENT`, `DEPLOYMENT` and `MODEL_NAME` are all accepted.

- **Narratives** — every view has a `facts_*()` function and a template. With no
  credential the template renders and is badged "auto-generated"; with one, Claude
  rewrites the same pack and it is badged "AI written". A test asserts every figure in a
  narrative traces to its own fact pack.
- **Chat** — the model gets parameterised **query tools**, not free SQL and not the
  data. It cannot answer from memory, no arbitrary SQL reaches DuckDB, and the UI shows
  which queries ran. Without a credential the same tools are reachable through a keyword
  router, so the chat box still answers the common questions.
- **Answers render as tables and charts.** A `present` tool lets the model choose the
  form — table, bar, line, pie or scatter — from the shape of the question, and it can
  only point at rows a query actually returned, so it cannot fabricate what it draws.
  Prompting alone proved unreliable (the model renders when asked directly for a table
  and intermittently forgets when a question merely implies one), so a server-side
  fallback attaches a table whenever an answer is list-shaped and no visual was chosen.
  Tables copy to CSV. A `competition` tool covers rivals, brand positioning, head-to-head
  and cannibalisation — without it the model answered "which rivals hold volume?" with
  product data, because it had no better tool to reach for.

Field names in tool output carry their units (`unserved_implements`, not `unserved`)
because a bare count is otherwise easy to read as a percentage — a real failure observed
in testing before the rename. Categories are a closed enum and products resolve from
plain words (`"orchard sprayer"` → `ORCHARD_SPRAYER`), so the model never has to guess
an internal id; an invalid value is rejected with the valid ones attached rather than
returning an empty result it cannot diagnose.

### Chat memory

Three things persist, with deliberately different lifetimes:

| | What | Lifetime |
|---|---|---|
| **Turns** | the conversation, so `"what about Maharashtra?"` resolves against the previous question | bounded to 12 exchanges in the prompt, 60 kept for the transcript |
| **Facts** | durable statements about the *user* — territory, current priorities — injected into every later prompt | until forgotten |
| **Context** | the view, product and filters they are looking at right now | the request |

The model writes facts by calling a `remember` tool bound to the session, so it can
never address another user's memory, and it is never asked to infer them silently. State
is JSON on disk (atomic writes, TTL eviction, thread-safe), so **a server restart does
not lose the thread**. "New chat" clears the conversation but keeps what is known about
the person — clearing a thread and forgetting someone are different actions, and get
different buttons.

Worked example, with nothing but the first line establishing scope:

> **"I look after Punjab and we're pushing residue equipment this quarter."** → calls `remember`
> **"Where should I focus?"** → Barnala, Patiala, Fatehgarh Sahib… (Punjab, residue, unprompted)
> **"What about Maharashtra instead?"** → same question, new state, product retained

Scenarios are quantitative rather than directional. A 1.5-sd monsoon shortfall propagates
through each district's *own* elasticity:

| | |
|---|---|
| Maharashtra | −28.9 % |
| Madhya Pradesh | −24.6 % |
| Punjab | **−11.5 %** (assured irrigation buffers it) |

with a 90 % confidence band from the estimated standard errors.

---

## Layout

```
pipeline/
  config/     sources, districts, sku_catalog, factors, weights, sim_params, ucm
  transform/  geo_spine        State -> District -> Block -> Village
  ingest/     village_layers, district_series (UCM target + regressors)
  simulate/   assets, competition, sku_history
  features/   engineered village features incl. spatial lag
  ucm/        structural time-series decomposition
  cluster/    archetype segmentation + village micro-segments & insights
  score/      factors, propensity, competition & cannibalisation
  export/     API-ready marts + simplified map geometry
api/          FastAPI over DuckDB/Parquet + narrative & chat (grounded LLM)
web/          React + TypeScript + Vite dashboard
```

Scale: 105,246 villages x 37 SKUs = 3.9 M scored rows, 114 district UCMs, 6 archetypes,
24 micro-segments, ~60 s full build.
