# Dashboard Explainer

A page-by-page walkthrough of the Sonalika Demand Compass, written for someone who
needs to understand what each screen is *for* — not just what it renders.

Source of truth for the routing is `web/src/App.tsx`; each section below names the
component file so you can jump straight to the code.

---

## Running it

```bash
./startup.sh
```

Dashboard on <http://localhost:5273>, API on <http://localhost:8000>.

> The README tells you to run the API on port **8848**. That is wrong. `web/vite.config.ts`
> proxies `/api` to `127.0.0.1:8000`, and the Dockerfile serves on 8000 too. On 8848 the
> UI loads but every panel fails. `startup.sh` uses 8000.

---

## The shell — what's on every page

`web/src/App.tsx`

Four things wrap every view:

**1. Product-line switch** (`implements` / `tractors`, top right of the brand block).
This is global state, not a filter — it changes which dataset the Review and Plan pages
query. Defaults to `implements`. Switching to `tractors` raises a standing warning banner:
competitor network and agro-climatic layers are real, but Sonalika's own sales, share and
network are modelled placeholders until ITL supplies the real data.

**2. The four-stage nav.** The whole tool is organised as the client's workflow rather
than a flat pile of tabs:

| Stage | Key question | Output |
|---|---|---|
| **1 Define** | What distinct micro-markets exist, basis similarity? | Archetypes and their mapped micro-markets |
| **2 Review** | Where are we underperforming — sales issue or product issue? | Archetype trends and quantified gaps |
| **3 Plan** | Where and what should we focus on? | Forecast, priority archetypes, targets |
| **4 Act** | What do we actually do here, and how exactly do we do it? | An execution playbook across seven use cases, priced and sequenced |

Clicking a stage jumps to its first view; a sub-nav then switches between the views
inside that stage. A banner restates the stage's question and output so you never lose
the thread.

**3. Global filters** — category, product, month. They persist across views, so filtering
to *Rotavators* on one page keeps that filter when you move to another. Hidden on Summary,
Data & method and Playbooks, where they'd mean nothing.

**4. "Ask the data" button** (bottom right) — opens the chat drawer. See the last section.

### Two vocabularies you need

**The geography ladder.** State → District → Block → Village. On top of that sit two
derived groupings that most of the tool actually runs on:

- **Micro-market** — ~4–5 neighbouring villages grouped by proximity + agro-climate. The
  smallest unit that's commercially actionable.
- **Archetype** — a cross-product of *agro-climatic zone* × *TIV tier* × *HP belt*, named
  by the crop most grown in it. Micro-markets that behave alike, regardless of where they
  sit on the map. The categories are editable on **Define → Configure**.

**TIV** = Total Industry Volume, i.e. tractors in play.

**Provenance pills.** Every number carries its origin, and the colour-coded pills are used
consistently:

| Pill | Means |
|---|---|
| `real` / `IMD · DES · real` | Observed, published data |
| `modelled · ITL pending` | Placeholder until Sonalika (ITL) supplies the real figures |
| `EY primary · modelled` | Waiting on the one-time primary survey |
| `SMAM proxy` | National scheme rate standing in for a missing state rate |

This labelling is deliberate — mixing observed, estimated and simulated numbers without
saying which is which is how a tool like this loses trust.

---

## Summary

`Executive.tsx` · the landing page

Built for someone with ten seconds. Answers three questions in order:

1. **The size of the prize** — annual demand in units, market value in crore, and the
   tractor fleet an implement can attach to.
2. **How much is still unclaimed** — current penetration, unserved headroom, implements
   per tractor (the attach rate to grow), and average distance to a dealer.
3. **What to do about it** — every village lands in one of four action boxes, derived
   from two questions: *is there unserved demand here?* and *is a dealer close enough to
   capture it?* The boxes are **Convert now**, **Build access**, **Defend**, **Monitor**.

Below that: demand split by state, a donut splitting **new demand vs replacement**
(replacement is defended by service and parts coverage, not won by new selling), and a
table of the best micro-market pockets to work first.

> ⚠️ The four action cards and the pocket rows are clickable, but they navigate to a route
> (`villages`) that `App.tsx` does not handle — you land on a blank page. See *Known gaps*.

---

## Stage 1 · Define
*Configure micro-markets & archetypes*

### Micro-market & district profile — `MapExplorer.tsx`
One map that zooms: **India → state → district**. The three pilot states are the only ones
you can click; the rest of the country is drawn as context because the model does not cover
it. Inside a district the layer toggle switches between the district boundary and the
**micro-markets inside it**, drawn as bubbles sized by TIV.

Clicking anything — a district polygon, a micro-market bubble, or a row in the table below —
opens the same profile panel:

| | District | Micro-market |
|---|---|---|
| Villages, TIV, mean HP, HP belt, TIV tier | its own | its own |
| Sonalika share | — | its own |
| Dealers by line, OEMs present | real, district-geocoded | inherited, plus **distance to the nearest dealer** |
| Rainfall, temperature, irrigation | real (IMD) | inherited, and the panel says so |
| Soil type, climate, growing period | real (ICAR AESR) | inherited |
| Crop mix, most-grown crop | real (DES) | its own most-grown |
| Fleet by HP band | rolled up | its own |
| Zone, sub-zone, archetype | | |

This replaces the old two-tab split, where *Map Explorer* was a dot cloud on an empty
canvas and *District profile* was a table with no map. The district table is still there
under the map, and it drives the same selection.

Dealer counts are real but geocoded to the district, so a micro-market shows distance
rather than an invented count — and the implements dealer file has no Punjab rows, so a
zero there means *no data*, not *no dealers*.

### Archetypes — `Archetypes.tsx`
The segmentation itself: **zone × TIV tier × HP belt**, 46 of them, ranked by fleet.
Each row carries the zone and its member sub-zones, the HP belt, the TIV tier, the crop
most grown there, micro-market and village counts, TIV, Sonalika share, and the **top
branded rival**.

Two notes on what is *not* here:

- **No demand column.** Define describes the market; demand is the number Plan ranks with,
  and it lives there. (It is load-bearing everywhere else — `sales = share × demand`
  exactly — so it was removed from these tables only.)
- **"Top branded rival", not "dominant competitor".** The actual leader is the unbranded
  *Local* segment in all 46 archetypes, so a leader column would say the same thing 46
  times. Excluding Local and Sonalika gives four real names — Landforce, KhedutAgro,
  Mahindra, Fieldking — which is what separates Punjab from MP and Maharashtra.

The archetype is named by its **own most-grown crop**, so the name and the Most-grown
column are the same fact. Zone is the key rather than sub-zone because the client thinks in
zones; the sub-zones are still shown, and micro-market clustering still happens within them.

### Configure — `Configure.tsx`
The **taxonomy editor** — the categories every archetype is built from:

- **TIV tiers** — create, rename, delete, and move the quantile cuts (the shipped three are
  Low/Medium/High at even thirds).
- **HP belts** — same, with HP bounds; the top belt is left open-ended.
- **Dominant crop** — the vocabulary archetypes are named from. Each category lists the raw
  crops it *covers*, so putting wheat, rice and maize in one row merges them into a single
  "Cereals" category, and deleting a row stops that crop naming anything (those archetypes
  fall through to their next-biggest crop). A crop belongs to at most one category.

**Zones are shown but not editable.** They are the published ICAR agro-climatic scheme, and
the soil, climate and growing-season figures on the profile panel are measured against
those boundaries — a redrawn zone would carry data that no longer describes it. The API
pins them, so a PUT that redraws one is ignored rather than silently accepted.

**Save re-labels** all 23,389 micro-markets against the new definition in about a second,
and the archetype table on the previous tab follows — as do Review, Plan and Act, which
re-roll their archetype-grain numbers from micro-market grain using the pipeline's own
rollup function. What it does *not* do is regroup villages: which villages form a
micro-market is fixed by the pipeline.

Two things stay keyed to the shipped clustering until the pipeline is re-run: the
archetype-level **UCM panels** and the **cluster profiles** (the "defining features"
sentence). An archetype whose id the pipeline has never seen shows those two fields blank
rather than borrowing another archetype's.

*Reset to shipped* restores `pipeline/config/taxonomy.yaml`.

---

## Stage 2 · Review
*Diagnose performance gaps*

This is the stage that answers the client's real question: **is a weak archetype a sales
problem or a product problem?** Four diagnoses are used consistently across all five views:

| Diagnosis | Meaning | So what |
|---|---|---|
| **Defend** | Already winning here | Protect the share |
| **Sales issue** | Product is proven (good fit) but share is low | Execution / coverage / effort gap — selling can close it |
| **Product issue** | Low product fit, can't crack it anywhere | Needs an adapted or new product, *not* more selling |
| **Monitor** | Too little demand to prioritise | Leave it |

### Market explorer — `MarketExplorer.tsx`
The same India → state → district drill as Define's first tab, asking the next question.
Click any district or micro-market and the panel gives three buckets:

- **What we sold** — sales, demand, market share, TIV, product fit, unserved demand, and the
  **activities → enquiries → deliveries** funnel with its two conversion rates. Three bars,
  not four: `deliveries_yr` and `sonalika_sales_units` are the same column, so drawing both
  would invent a stage converting at 100%.
- **Who farms here** — rural population, households, holdings, average holding size,
  small-and-marginal share, farm income per holding, tractor density, fleet age and loan
  approval. Badged **allocated**: published state totals (Census 2011 population, the
  state × tier holding mix) split down to villages by model and summed back up.
- **What grows here** — rainfall, temperature, irrigation, soil, climate and growing period
  from ICAR, plus the foodgrain area mix.

**The two crop facts are deliberately kept apart.** The DES source behind the crop table is a
**foodgrain-only** extract, so cotton, soybean, sugarcane and groundnut are zero on all 114
districts however much of them is grown. The *most-grown* line underneath comes from the
modelled village crop mix, which does cover them. Merging the two into one chart would say
cotton isn't grown in Punjab; showing them separately, each with its own badge, says what
each source actually knows. Crops the source reports as zero for a district are dropped
rather than drawn as an empty bar.

Bubble size is any of six metrics; bubble colour is the diagnosis, so a district of amber
dots is a product problem and a district of blue ones is a selling problem.

### Archetype details — `ArchetypeDetails.tsx`
The same signals rolled up to archetype level with the diagnosis attached, and the diagnosis
*explained*: hover any of the three cards for the rule that produced it.

- **Monitor** means the whole archetype earns less demand than its micro-market count would
  need to clear the bar — the 20th-percentile micro-market's demand times how many it has.
  Not a product problem and not a selling problem; too small to plan against yet.
- **There is no Defend card**, and the screen says so: Defend needs a 10% share and the
  strongest archetype holds 8.9%. Plan's Defend bucket reads strength *relative* to the set,
  which is why Plan shows one and this table does not.

The map sits directly under the diagnosis cards. Green is a micro-market we have **won** —
10% share or better, the same bar the **% of MM won** column counts — so the green fraction
of the map *is* that column.

### Network coverage — `NetworkCoverage.tsx`
Where the dealers are against where the demand is, with a map above the tables: one bubble
per district, coloured by the toggled coverage index and sized by demand, so a large red
bubble is the gap worth closing.

**Two kinds of number, and the screen no longer conflates them.** Dealer counts and the OEM
table are **real**, from the locator. The coverage indices are **modelled** — sales coverage
is a distance decay off a *simulated* dealer network, and service coverage is that discounted
and noised until ITL ships its service master. This screen used to badge the whole sales view
"real · dealer locator", which read as a claim about the coverage bars.

Districts the dealer file does not cover — **every Punjab district, for implements** — are
drawn grey rather than at 0%. The count is unknown there, not zero, and colouring it as an
absence would claim a gap we cannot see.

The table adds a **major competitor** column: the strongest *branded* rival, excluding the
unbranded local-fabricator segment that leads all 46 archetypes. Same source and same
exclusion as the Define archetype table, so the two screens never name different competitors
for the same archetype.

### Competition — `Competition.tsx`
Sonalika is a challenger in implements (~8% share), so this page deliberately doesn't ask
"what is our share". It asks four sharper questions:

- **Where we stand, contest by contest** — every contest labelled *Leading*, *Winnable*,
  *Stretch* or *Out of reach*. A challenger has to know what's winnable and what's fantasy.
- **Who holds the volume** — click a rival for the head-to-head, which shows what slice of
  each category's rival volume is actually takeable by us.
- **Price and reach** — a bubble chart positioning every brand by price and reach, bubble
  size = share.
- **Cannibalisation** — our own products competing with each other. If two Sonalika SKUs
  both count the same demand, adding both at full value double-counts. This quantifies the
  share of each product's demand that is *not* incremental.

At the bottom, a **"what if the rival moves?"** simulator: slide their dealer network
(−50% to +100%) and price (±20%), and see the effect on our volume. Because shares come
from a choice model, one brand's move necessarily shifts everyone else's. The result is
honest about scope — it only covers the contests where that rival is our nearest
competitor, not the whole business.

### What drives sales — `WhatDrivesSales.tsx`
A per-archetype **Unobserved Components Model** decomposing a daily sales series.

Read the badge carefully: Sonalika has no real daily sales feed, so this panel runs on
**simulated illustrative daily history**, built so the UCM can cleanly recover known
effects. It demonstrates the machinery and proves the estimator works; it is not a
statement about real trading.

The model is fit in **levels**, not logs, so every uplift is additive and the identity
holds exactly by construction:

```
Baseline (trend + weekly + annual seasonal)  +  factor uplifts  +  residual  =  Actual
```

Panels: actual vs predicted vs baseline; the additive stacked contribution; uplift
attribution for a trailing vs prior window (90/180/365 days); elasticities in this
archetype with 90% intervals; pooled elasticities across all archetypes against the known
true β (the parameter-recovery check); and model quality per archetype vs seasonal-naive.

---

## Stage 3 · Plan
*Prioritise opportunities & set targets*

### Prioritise & subsidy — `Priorities.tsx`
Focus products ranked by demand, shown against the **real subsidy lever**. Punjab and
Maharashtra use state-specific rates; Madhya Pradesh falls back to the national SMAM 40%
rate, badged as a proxy. Products that are both high-demand and high-subsidy get a
**"push now"** flag — that's the page's actual recommendation.

Alongside it, district priorities anchored to **real DES cropland**, so under-penetration
shows relative to actual farmland (demand per '000 ha) rather than raw volume.

### Demand map — `Overview.tsx`
The geographic demand view: a drillable India map, demand by state, top districts, top
SKUs split into new vs replacement, and a seasonality strip.

The seasonality point is worth understanding: the monthly shape is **estimated** from the
tractor registration series by the UCM's stochastic seasonal component — not asserted. It
peaks post-kharif and through the festive window and troughs at monsoon onset. Each SKU's
own agronomic window is layered on top.

> ⚠️ Clicking a district row navigates to a `drill` route that `App.tsx` doesn't handle.
> Blank page. See *Known gaps*.

### Focus products — `SkuView.tsx`
The full SKU table: HP band, units/year, new vs replacement, headroom, penetration, value,
maturity — scopeable to a state or district. Click any SKU to filter every other view to it.

Two charts below: **new vs replacement** stacked (replacement-heavy SKUs are defended,
new-heavy ones are contested), and **weight composition**, which shows how much of a given
SKU's score comes from UCM-estimated weights versus judgmental priors.

That second chart is the honesty mechanism. Currently **148 of 370** SKU-factor pairs are
empirical; the rest are priors, and the chart colour-codes every one.

### What-if & forecast — `ScenarioView.tsx`
Two sets of sliders:

- **Driver shocks**, in standard deviations from normal — monsoon rainfall, reservoir
  storage, NDVI, mandi prices, rural credit, subsidy intensity, diesel, rural wages.
- **Factor weight overrides** for the ten factor groups (leave at 0 to keep the model's
  own weight).

Hit *Run scenario* for baseline vs scenario volume, the % change with a 90% confidence
interval, per-state impact with error bars, and a table of every shock showing pooled β,
the β range across districts, and whether the estimate is usable.

The design point: shocks propagate through **each district's own estimated elasticity**,
so a drought hits a rainfed district far harder than an assured-irrigation one. The spread
across states *is* the insight, not a rounding artefact.

---

## Stage 4 · Act

### Playbook — `ActPlaybook.tsx`, `api/playbook.py`
*Build the archetype playbook, then work it*

One page. It answers the question the client actually asked — *"for this archetype, these
are the 10 things I want to do"* — organised as the seven use cases the business team
defined: **Network Expansion & Development, Customer growth, Product development,
Inventory, Activity Plan, Sales planning, Incentives & Consumer Schemes**.

**Scope.** Pick a bucket, then an archetype, then optionally narrow to one of its districts
and then to a single micro-market. Everything below recomputes for what you select — the
funnel, the survey, the rivals, and every play. Bucket, leader and rank stay archetype
properties and the page says so, because narrowing does not re-run the bucket rule.

**What customers here are telling us.** The primary study has not run, so this layer is
modelled from `village_factors`, whose 44 sub-factors are percentile-ranked 0–100 across
every village in the country. A score of 72 means the scope sits at the 72nd percentile
nationally — a statement about real data rather than an invented survey response. It
produces purchase drivers, a happy/unhappy split with the top praise and top complaint,
buying behaviour (own vs hire, credit-led, scheme-led, peak month, who they listen to),
channel mix, switching triggers and unmet product needs. **Every line names the sub-factor
and percentile it came from**, on hover. Badged `EY primary · modelled`; it hot-swaps at
one function when the study lands.

The loudest complaint sets the `top_barrier`, and that re-orders the seven cards so the one
answering it leads. This is the client's own worked example running end to end: where the
data says running cost bites, Activity Plan leads and its themes become fuel-economy demos;
where it says service and distance, Network Expansion leads. **Ordering never changes a
units figure** — a dropdown lets you override the barrier and the numbers stay put.

**The seven cards.** Each renders the slide's own bullets as sections — real dealer counts
and whitespace for network, segments and stage-wise messages for customer growth, value
propositions and feature gaps for product, stock norms and demo placement for inventory,
themes and a 12-month beat plan for activity, back-solved targets and a dealer scorecard
for sales, incentive triggers and scheme simulation for incentives.

Cards also carry the **priced plays**, which are the same six mechanism plays as before —
reach, approval, effort, execution, price, policy — each re-homed under the card that owns
its mechanism. Every play appears in exactly one card, so the card totals still add to the
growth total. Cards 2 and 4 carry no addend at all: they allocate and aim volume the other
cards create. Each play now expands to an execution spec — objective, evidence, numbered
steps with timing, the named micro-markets to do it in, cadence, owner and the KPI to
watch.

**Track playbook performance** turns the plays into a baseline → target table per use case,
computed from the same numbers the plays are priced on. The actuals column stays empty
until ITL supplies two years of activity, enquiry and delivery history.

**The 10 things to do** stitches the cards into one sequence, ordered by when the work has
to start rather than by what it is worth.

Three honesty guards the page holds to: service coverage is a modelled index (ITL's service
master will replace it); `demo_activity` is a marketing-effort index, not a fleet roster, so
demo placement is a recommendation badged `ITL pending` and only shows on the tractor line;
and a district absent from the dealer file reads `no data`, never "zero dealers".

> The old **Archetype summary** view (`ActSummary.tsx`) and the original **Develop**
> playbooks page are hidden rather than deleted — both are one `true` away from returning
> in `App.tsx`'s `STAGES`, and `/api/act/summary` still serves.

---

## Data & method

`DataView.tsx` · the utility tab, always reachable

The page that makes the rest trustworthy. Three things:

**What is real, what is estimated, what is simulated.**
- `real` — district boundaries and names, district/village counts (Census 2011), SKU
  taxonomy and HP bands.
- `allocated` — district statistics downscaled to villages, and everything from the UCM.
  Most open Indian agri data publishes at district level; village figures are district
  signal apportioned by real village-level modifiers.
- `simulated` — layers with no public source at all: OEM implement sales, dealer network,
  competitor share, CHC density, finance penetration. Generated from documented parameters
  in `sim_params.yaml`, not invented ad hoc.

**Model quality.** UCM districts fitted and how many beat seasonal-naive, backtest MAPE vs
naive, median R²-like, residual autocorrelation. The empirical-vs-prior weight split.
Clustering stability (bootstrap ARI, spatial coherence).

**The source fetch manifest** — what each connector actually did on the last run, including
*why* anything isn't live.

**The limit worth knowing** (quoted, because it's the honest headline): no public series of
implement sales exists, so implement elasticities are *derived* from tractor elasticities
via HP band and crop fit — not directly estimated. If Sonalika supplies real dealer
secondary-sales history, the same machinery applies directly, and that is the single
largest accuracy upgrade available to this model.

---

## Two AI features

### "What this means" briefings — `Narrative.tsx`
The boxed summary at the top of Summary, Demand map and Focus products. The text is always
generated from a **fact pack computed server-side**, so the numbers in the prose are the
same numbers in the charts below it. When an Azure OpenAI or Anthropic credential is
configured, the same fact pack is rewritten by the model; without one, a deterministic
template writes it.

The badge tells you which, and *show numbers* dumps the raw fact pack. A reader deserves to
know whether they're reading a template or a model.

### Ask the data — `Chat.tsx`, `api/chat.py`
A chat drawer over the same data. The model **cannot** see the database directly — it may
only call one of a set of whitelisted query tools (`top_geographies`, `top_products`,
`village_detail`, `find_villages`, `village_segments`, `sales_drivers`, `data_sources`,
`competition`, `compare`, plus `present` for rendering and `remember` for memory).

No free-form SQL ever reaches the database, and every answer shows a *"how I got this"*
trace of the queries that actually ran.

It has memory: the conversation persists (so follow-ups resolve and a reload resumes
mid-thread), and durable facts about you — territory, priorities — persist separately.
Clearing the thread and forgetting the person are deliberately separate actions.

---

## Known gaps

Found while reading the code. None of these are hard to fix; you should just know before
demoing.

**1. Three clicks lead to blank pages.** `Executive.tsx` (the four action cards and the
pocket table rows) navigates to `villages`, and `Overview.tsx` (the top-districts rows)
navigates to `drill`. `App.tsx` renders neither route, so `<main>` comes up empty. Nothing
crashes — it just silently goes nowhere, which is worse in a demo.

**2. Five components are dead code.** `Villages.tsx` (303 lines), `Drill.tsx` (206),
`IndiaMap`-consuming `Explore.tsx` (142), `ClusterView.tsx` (125) and `FactorView.tsx` (95)
are imported by nothing. Two of them are almost certainly the missing routes above — the
restructure into four stages re-homed the views and these got orphaned rather than deleted.

**3. The README's port is wrong.** Documented 8848 vs the actual 8000 in both
`vite.config.ts` and the Dockerfile.

**4. `SkuView.tsx` has a dead fetch.** `const geo = useAsync(() => api.geo('village',
{ parent: 'x' }), [])` — commented `// unused, keeps shape`. It fires a real request on
every render of the weight-mix panel and throws the result away.

Say the word and I'll fix 1–4; the first is a ten-line change.

---

## How data flows

```
sources  ->  pipeline.run (21 stages)  ->  data/marts/*.parquet  ->  FastAPI + DuckDB  ->  React view
```

Census 2011 geography, IMD weather, DES cropland, tractor registrations, dealer
locators, state subsidy rates and the SKU catalogue go in. `python -m pipeline.run`
walks 21 dependency-ordered stages (`geo → ingest → assets → competition → sku →
features → agroclimate → ucm → cluster → factors → score → export → compete →
micromarkets → operations → archetype_sales → archetype_ucm → insights → shapes →
dealers → subsidy`) and writes parquet into `data/marts`. `api/main.py` is a thin
DuckDB read layer over those files; each view fetches only the endpoints it needs.

Two consequences: every screen is a **read** of a pre-computed mart — the maths is in
the pipeline, not the browser, so a wrong number is a pipeline question. And any stage
re-runs alone once its inputs exist (`--stage ucm`, `--from features`), so you don't
pay the full ~60s rebuild to iterate.

The one exception is **Configure** (Stage 1), which writes an edited taxonomy
server-side and re-labels every micro-market live, without a pipeline run.

**5. `startup.sh` reload loop — fixed.** `uvicorn --reload` was unscoped, so the watcher
watched `.venv` and reloaded forever; the API never stayed up long enough to answer.
Now scoped with `--reload-dir api --reload-dir pipeline`.
