import React, { useState } from 'react'
import { useStore, ProductLine } from './lib/store'
import { api, MONTHS } from './lib/api'
import { useAsync, Info } from './components/common'
import Executive from './components/Executive'
import Chat from './components/Chat'
import Overview from './components/Overview'
import SkuView from './components/SkuView'
import WhatDrivesSales from './components/WhatDrivesSales'
import ScenarioView from './components/ScenarioView'
import DataView from './components/DataView'
import Competition from './components/Competition'
import NetworkCoverage from './components/NetworkCoverage'
import Priorities from './components/Priorities'
import Playbooks from './components/Playbooks'
import MapExplorer from './components/MapExplorer'
import DistrictProfile from './components/DistrictProfile'
import Archetypes from './components/Archetypes'
import Configure from './components/Configure'
import MicromarketDetails from './components/MicromarketDetails'
import MarketExplorer from './components/MarketExplorer'
import ArchetypeDetails from './components/ArchetypeDetails'
import WhereToPlay from './components/WhereToPlay'
import Forecast from './components/Forecast'
import Targets from './components/Targets'
import ActSummary from './components/ActSummary'
import ActPlaybook from './components/ActPlaybook'

/**
 * The tool is organised as the client's four-stage workflow — Define, Review, Plan,
 * Act — run for either product line. Every good view we already had is re-homed
 * under the stage where it belongs, so the app reads as one storyline instead of a
 * flat pile of tabs. Summary is the ten-second landing; Data & method is a utility.
 */
interface Stage {
  key: string
  n: number
  name: string
  tagline: string
  question: string
  output: string
  // [view key, label, hidden?] -- a hidden view keeps its route and its component, it just
  // leaves the sub-nav. Drop the `true` to bring a tab back; nothing else has to change.
  views: Array<[string, string] | [string, string, boolean]>
}

const STAGES: Stage[] = [
  {
    key: 'define', n: 1, name: 'Define', tagline: 'Configure micro-markets & archetypes',
    question: 'What distinct micro-markets exist, basis similarity?',
    output: 'Archetypes and their mapped micro-markets',
    views: [
      ['mapexplorer', 'Micro-market & district profile'],
      ['archetypes', 'Archetypes'],
      ['configure', 'Configure'],
      // Folded into the map screen above; kept routable, one `true` from returning.
      ['district', 'District profile', true],
    ],
  },
  {
    key: 'review', n: 2, name: 'Review', tagline: 'Diagnose performance gaps',
    question: 'Where are we underperforming — sales issue or product issue?',
    output: 'Archetype trends and quantified gaps (share, network)',
    views: [
      ['market', 'Market explorer'],
      ['arch-details', 'Archetype details'],
      ['network', 'Network coverage'],
      ['compete', 'Competition'],
      ['ucm', 'What drives sales'],
      // Folded into Market explorer above; kept routable, one `true` from returning.
      ['mm-details', 'Micro-market details', true],
    ],
  },
  {
    key: 'plan', n: 3, name: 'Plan', tagline: 'Prioritise markets, forecast demand, set targets',
    question: 'Which archetypes do we grow, and what has to change to grow them?',
    output: 'Defend / Grow / No-fit split, a 6-month forecast, and funnel targets',
    views: [
      ['where', 'Market prioritisation'],
      ['forecast', 'Demand forecast'],
      ['targets', 'Growth targets'],
      ['sku', 'Focus products'],
      ['priorities', 'Prioritise & subsidy'],
      // Hidden, not decommissioned -- still routable, one `true` from returning.
      ['overview', 'Demand map', true],
      ['scenario', 'What-if & forecast', true],
    ],
  },
  {
    key: 'act', n: 4, name: 'Act', tagline: 'Know the archetype, then work it',
    question: 'What do we actually do in this archetype, and what is it worth?',
    output: 'An archetype briefing and a priced playbook',
    views: [
      ['act-summary', 'Archetype summary'],
      ['act-playbook', 'Playbook'],
      // Hidden, not decommissioned -- the original Develop tab is one `true` from returning.
      ['playbooks', 'Playbooks (old)', true],
    ],
  },
]

const VIEW_TO_STAGE: Record<string, Stage> = {}
STAGES.forEach(s => s.views.forEach(([v]) => { VIEW_TO_STAGE[v] = s }))

// What each tab is for, on hover. Written for someone opening the tool cold: what the
// screen decides, not what it renders.
const VIEW_INFO: Record<string, React.ReactNode> = {
  where: <>Sorts every archetype into <b>Defend</b> (already strong — protect it),{' '}
    <b>Grow</b> (the product fits but our share doesn't — the focus list) and{' '}
    <b>No product fit</b> (we can't compete there whatever we spend). For a Grow archetype
    it opens the micro-markets to work, biggest fleet first. Start here — it decides which
    archetypes the other two tabs are worth reading for.</>,
  forecast: <>Six months of demand ahead, from the district model that already explains
    the last ten years. Move a driver — monsoon, mandi prices, credit, diesel — and the
    scenario line bends while the baseline stays put, so you can see what the assumption
    did. Each district reacts with its own estimated elasticity, so the per-state spread
    is the answer, not the headline number.</>,
  mapexplorer: <>Zoom the map from India to a state to a district, then switch the layer to
    the micro-markets inside it. Clicking anything opens the same profile: villages, dealers,
    rainfall, temperature, soil, crop mix and the tractor fleet by HP band. This is where you
    learn what kind of place a market is before deciding anything about it.</>,
  archetypes: <>The segmentation itself — every archetype as zone × TIV tier × HP belt, with
    the crop actually grown there and the strongest branded rival. Ranked by fleet, because
    Define describes the market; the choice of where to sell belongs to Plan.</>,
  configure: <>Edit the categories the archetypes are built from: add or remove a TIV tier,
    move an HP-belt boundary, combine two zones or split one apart. Saving re-labels every
    micro-market in about a second, and the Archetypes tab updates with it.</>,
  market: <>Drill India → state → district, then the micro-markets inside it, and click for
    the three things that decide a market: the BD funnel and what we sold, who farms there,
    and what grows there. Same map and same motion as Define's first tab — that one describes
    the place, this one scores it.</>,
  'arch-details': <>Every archetype with its diagnosis: a <b>sales issue</b> is a proven
    product that is under-sold, a <b>product issue</b> is one that does not fit, and
    <b> monitor</b> is simply too small to plan against. The map shows which of an archetype's
    micro-markets we have actually won.</>,
  network: <>Where our dealers are against where the demand is. Dealer counts are real; the
    coverage indices are modelled from distance, and service is a placeholder until ITL ships
    its service master. The rows to act on are the sales-issue archetypes with weak coverage.</>,
  compete: <>Who holds the volume we want, what is realistically takeable, what of ours is
    exposed, and where our own products bid against each other.</>,
  ucm: <>A time-series decomposition that separates what we did from what the season did.
    Runs on simulated daily history — it proves the machinery, it is not a trading claim.</>,
  'act-summary': <>Everything the tool knows about one archetype in a single view: what kind
    of place it is, how big it is, where we stand against every other OEM, what our funnel
    looks like, what has been moving sales, and what the next six months hold. Read it before
    the playbook.</>,
  'act-playbook': <>What to do in that archetype, with every action priced in units a year,
    share points and the fleet it brings within reach. Plays are built from competitor volume,
    the dealer network, subsidy and the funnel; the survey inputs are yours to set until the
    primary study lands.</>,
  targets: <>Turns a number into a plan for one Grow archetype: set the units you want and
    it back-solves the enquiries and BD activities needed at that archetype's own
    conversion rates, then ranks the levers — more activity, better conversion, wider
    dealer coverage — by how much each closes the gap.</>,
  sku: <>Every SKU ranked by demand potential, split into new vs replacement, with
    penetration and value. Click a SKU to filter every other view by it — this is the
    product-level view under Plan's archetype-level numbers.</>,
  priorities: <>Focus products ranked by demand against the real subsidy lever, flagging
    which to push now, next to district priorities weighed against real cropland — the
    product-and-district shortlist to act on.</>,
}

export default function App() {
  const { view, setView, productLine, setProductLine,
          sku, category, month, setSku, setCategory, setMonth } = useStore()
  const [chatOpen, setChatOpen] = useState(false)
  const skus = useAsync(() => api.skus(), [])
  const cats = Array.from(new Map((skus.data ?? []).map(s => [s.category, s.category_label])))

  const stage = VIEW_TO_STAGE[view]
  // sku / category / month are honoured by exactly two views -- Plan's hidden Demand map and
  // Focus products. On every other screen the selects moved nothing, which is a control that
  // teaches people the tool is broken. So they render only where they act.
  const showFilters = ['overview', 'sku'].includes(view)

  return (
    <div className="app">
      <header className="hdr">
        <div className="brand">
          <b>Sonalika Demand Compass</b>
          <span>A four-stage tool for {productLine} · Punjab · MP · Maharashtra</span>
        </div>

        <div className="switch" role="tablist" aria-label="Product line">
          {(['tractors', 'implements'] as ProductLine[]).map(p => (
            <button key={p} role="tab" aria-selected={productLine === p}
                    className={productLine === p ? 'on' : ''}
                    onClick={() => setProductLine(p)}>
              {p[0].toUpperCase() + p.slice(1)}
            </button>
          ))}
        </div>

        <nav className="nav">
          <button className={view === 'executive' ? 'on' : ''} onClick={() => setView('executive')}>
            Summary
          </button>
          {STAGES.map(s => (
            <button key={s.key}
                    className={'stage-tab' + (stage?.key === s.key ? ' on' : '')}
                    onClick={() => setView(s.views[0][0])}>
              <span className="stage-n">{s.n}</span>{s.name}
            </button>
          ))}
          <button className={'util' + (view === 'data' ? ' on' : '')} onClick={() => setView('data')}>
            Data &amp; method
          </button>
        </nav>

        {showFilters && (
          <div className="filters">
            <select value={category ?? ''} onChange={e => setCategory(e.target.value || undefined)}>
              <option value="">All categories</option>
              {cats.map(([c, label]) => <option key={c} value={c}>{label}</option>)}
            </select>
            <select value={sku ?? ''} onChange={e => setSku(e.target.value || undefined)}>
              <option value="">All products</option>
              {(skus.data ?? []).map(s => <option key={s.sku_id} value={s.sku_id}>{s.name}</option>)}
            </select>
            <select value={month ?? ''}
                    onChange={e => setMonth(e.target.value ? Number(e.target.value) : undefined)}>
              <option value="">Full year</option>
              {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
            </select>
          </div>
        )}
      </header>

      {productLine === 'tractors' && (
        <div className="banner-warn">
          Tractor line — competitor network &amp; agro-climatic layers are real; Sonalika
          sales, share and own-network are modelled placeholders until ITL data lands.
        </div>
      )}

      {stage && (
        <div className="stage-intro">
          <div className="stage-head">
            <span className="stage-badge">{stage.n}</span>
            <div>
              <b>{stage.name}</b> — {stage.tagline}
              <div className="stage-qo">
                <span><i>Key question:</i> {stage.question}</span>
                <span><i>Output:</i> {stage.output}</span>
              </div>
            </div>
          </div>
          <div className="subnav">
            {stage.views.filter(v => !v[2]).map(([k, label]) => (
              <button key={k} className={view === k ? 'on' : ''} onClick={() => setView(k)}>
                {label}{VIEW_INFO[k] && <Info text={VIEW_INFO[k]} wide />}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Remount on the product line: every useAsync re-runs, so a screen cannot keep
          showing the other line's numbers because its dep array forgot to list it. */}
      <main className="main" key={productLine}>
        {view === 'executive' && <Executive />}
        {view === 'overview' && <Overview />}
        {view === 'mapexplorer' && <MapExplorer />}
        {view === 'district' && <DistrictProfile />}
        {view === 'archetypes' && <Archetypes />}
        {view === 'configure' && <Configure />}
        {view === 'where' && <WhereToPlay />}
        {view === 'forecast' && <Forecast />}
        {view === 'targets' && <Targets />}
        {view === 'priorities' && <Priorities />}
        {view === 'sku' && <SkuView />}
        {view === 'ucm' && <WhatDrivesSales />}
        {view === 'scenario' && <ScenarioView />}
        {view === 'market' && <MarketExplorer />}
        {view === 'mm-details' && <MicromarketDetails />}
        {view === 'arch-details' && <ArchetypeDetails />}
        {view === 'network' && <NetworkCoverage />}
        {view === 'compete' && <Competition />}
        {view === 'data' && <DataView />}
        {view === 'act-summary' && <ActSummary />}
        {view === 'act-playbook' && <ActPlaybook />}
        {view === 'playbooks' && <Playbooks />}
      </main>

      <button className="ask-fab" onClick={() => setChatOpen(true)}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M20 2H4a2 2 0 00-2 2v18l4-4h14a2 2 0 002-2V4a2 2 0 00-2-2z" />
        </svg>
        Ask the data
      </button>
      <Chat open={chatOpen} onClose={() => setChatOpen(false)} />
    </div>
  )
}
