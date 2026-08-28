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
| **4 Develop** | What strategy will unlock growth? | Archetype playbooks |

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
- **Archetype** — a cross-product of *NARP agro-climatic sub-zone* × *TIV tier* × *HP belt*.
  Micro-markets that behave alike, regardless of where they sit on the map.

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

### Map Explorer — `MapExplorer.tsx`
Micro-markets plotted as points on a district map. Pick state → district → a colouring
metric (TIV, Sonalika share, or demand potential). Click any point and the right panel
shows its profile: archetype, HP belt, mean HP, TIV, share, annual demand, rainfall and
temperature, plus every member village with its own demand and tractor count.

This is where you *see* what a micro-market is.

### District profile — `DistrictProfile.tsx`
One sortable row per district. Real agro-climate (temperature, rainfall, NARP sub-zone
with length-of-growing-period, and a stacked crop-mix bar for wheat/rice/cotton/soybean/
sugarcane) sitting next to the modelled market signals (TIV, Sonalika share, demand).

A `~` after a temperature means it was filled from the nearest station rather than
measured in that district.

### Archetypes — `Archetypes.tsx`
The segmentation itself. KPIs for archetype count, micro-market count, total TIV and
TIV-weighted average share. Then three tables: the **NARP sub-zones** (the real
agro-climatic axis), the **HP belts** (the third axis), and the full archetype list
ranked by demand — each named by dominant crop + TIV, split geographically by sub-zone.

### Configure — `Configure.tsx`
Carve out a **new archetype from a rule**. Set thresholds — sub-zone, TIV tier (top/bottom
third), HP belt, dominant crop, irrigation — name it, and hit *Create & re-cluster*. Every
matching micro-market is pulled out of its current archetype into the new one and all
summaries recompute.

Deterministic and transparent, so the client can react to a concrete preview rather than a
black box. Changes are saved **server-side** and show up on Archetypes, Map Explorer and
District profile too, until you hit *Reset to base*. If a rule matches nothing, the page
tells you the rule was too narrow instead of failing silently.

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

### Micro-market details — `MicromarketDetails.tsx`
Every operational signal at micro-market level, mapped and colourable by Sonalika sales,
market share, TIV, demand potential or activities. Click a point for sales, share, TIV,
demand, conversion rate, product fit, the **activities → enquiries → deliveries funnel**
as bars, and dealer accessibility.

### Archetype details — `ArchetypeDetails.tsx`
The same signals rolled up to archetype level, with the diagnosis attached and *explained*
in plain English. Sortable table of every archetype (share, product fit, % of its
micro-markets cracked, sales); click one to see its full profile and a map of its
micro-markets coloured by sales.

### Network coverage — `NetworkCoverage.tsx`
Sonalika vs rival OEMs across the three states, mapped onto archetypes. Toggle between
**sales** coverage (real, from the dealer locator) and **service** coverage (dummy — the
ITL service master is pending).

The actionable output is the highlighted rows: **sales-issue archetypes with under-50%
coverage**. Product is proven, the network just isn't there. That's the fastest lever —
expand coverage, don't change the product.

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

## Stage 4 · Develop

### Playbooks — `Playbooks.tsx`
*Build differentiated playbooks*

Pick an archetype, get a commercial plan: root cause, what customers say, primary enabler,
recommended action, plus four strategy cells (network, subsidy focus, engagement/beat plan,
content strategy).

Be clear-eyed about this page's status. The root causes come from a **primary survey that
hasn't been run yet**, so they're placeholders drawn from a hardcoded four-item list in the
component and rotate by archetype index. They're badged `EY primary · modelled` and will
hot-swap when the study lands. The enablers it recommends (network, subsidy) do draw on
real data.

Of the four stages, this is the least built out — one view against Define's four.

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
