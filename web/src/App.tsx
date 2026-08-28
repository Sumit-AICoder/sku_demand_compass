import React, { useState } from 'react'
import { useStore, ProductLine } from './lib/store'
import { api, MONTHS } from './lib/api'
import { useAsync } from './components/common'
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
import ArchetypeDetails from './components/ArchetypeDetails'

/**
 * The tool is organised as the client's four-stage workflow — Define, Review, Plan,
 * Develop — run for either product line. Every good view we already had is re-homed
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
  views: Array<[string, string]>
}

const STAGES: Stage[] = [
  {
    key: 'define', n: 1, name: 'Define', tagline: 'Configure micro-markets & archetypes',
    question: 'What distinct micro-markets exist, basis similarity?',
    output: 'Archetypes and their mapped micro-markets',
    views: [
      ['mapexplorer', 'Map Explorer'],
      ['district', 'District profile'],
      ['archetypes', 'Archetypes'],
      ['configure', 'Configure'],
    ],
  },
  {
    key: 'review', n: 2, name: 'Review', tagline: 'Diagnose performance gaps',
    question: 'Where are we underperforming — sales issue or product issue?',
    output: 'Archetype trends and quantified gaps (share, network)',
    views: [
      ['mm-details', 'Micro-market details'],
      ['arch-details', 'Archetype details'],
      ['network', 'Network coverage'],
      ['compete', 'Competition'],
      ['ucm', 'What drives sales'],
    ],
  },
  {
    key: 'plan', n: 3, name: 'Plan', tagline: 'Prioritise opportunities & set targets',
    question: 'Where and what should we focus on?',
    output: 'Forecast, priority archetypes, and targets',
    views: [
      ['priorities', 'Prioritise & subsidy'],
      ['overview', 'Demand map'],
      ['sku', 'Focus products'],
      ['scenario', 'What-if & forecast'],
    ],
  },
  {
    key: 'develop', n: 4, name: 'Develop', tagline: 'Build differentiated playbooks',
    question: 'What strategy will unlock growth?',
    output: 'Archetype playbooks (network, content, engagement, products)',
    views: [
      ['playbooks', 'Playbooks'],
    ],
  },
]

const VIEW_TO_STAGE: Record<string, Stage> = {}
STAGES.forEach(s => s.views.forEach(([v]) => { VIEW_TO_STAGE[v] = s }))

export default function App() {
  const { view, setView, productLine, setProductLine,
          sku, category, month, setSku, setCategory, setMonth } = useStore()
  const [chatOpen, setChatOpen] = useState(false)
  const skus = useAsync(() => api.skus(), [])
  const cats = Array.from(new Map((skus.data ?? []).map(s => [s.category, s.category_label])))

  const stage = VIEW_TO_STAGE[view]
  // Filters only make sense on analytical views, not the summary / finder / data / playbooks.
  const showFilters = !['executive', 'villages', 'data', 'playbooks'].includes(view)

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
            {stage.views.map(([k, label]) => (
              <button key={k} className={view === k ? 'on' : ''} onClick={() => setView(k)}>
                {label}
              </button>
            ))}
          </div>
        </div>
      )}

      <main className="main">
        {view === 'executive' && <Executive />}
        {view === 'overview' && <Overview />}
        {view === 'mapexplorer' && <MapExplorer />}
        {view === 'district' && <DistrictProfile />}
        {view === 'archetypes' && <Archetypes />}
        {view === 'configure' && <Configure />}
        {view === 'priorities' && <Priorities />}
        {view === 'sku' && <SkuView />}
        {view === 'ucm' && <WhatDrivesSales />}
        {view === 'scenario' && <ScenarioView />}
        {view === 'mm-details' && <MicromarketDetails />}
        {view === 'arch-details' && <ArchetypeDetails />}
        {view === 'network' && <NetworkCoverage />}
        {view === 'compete' && <Competition />}
        {view === 'data' && <DataView />}
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
