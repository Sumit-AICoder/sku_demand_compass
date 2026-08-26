import React, { useState } from 'react'
import { useStore } from './lib/store'
import { api, MONTHS } from './lib/api'
import { useAsync } from './components/common'
import Executive from './components/Executive'
import Villages from './components/Villages'
import Chat from './components/Chat'
import Overview from './components/Overview'
import Explore from './components/Explore'
import SkuView from './components/SkuView'
import FactorView from './components/FactorView'
import UcmView from './components/UcmView'
import ClusterView from './components/ClusterView'
import ScenarioView from './components/ScenarioView'
import DataView from './components/DataView'
import Competition from './components/Competition'

/** Two audiences, so two groups of views. Business first; the model internals are
 *  still there for whoever wants them, just not in the executive's way. */
const GROUPS: Array<{ label: string; views: Array<[string, string]> }> = [
  {
    label: 'Business',
    views: [
      ['executive', 'Summary'],
      ['villages', 'Village finder'],
      ['sku', 'Products'],
      ['compete', 'Competition'],
      ['explore', 'Map explorer'],
    ],
  },
  {
    label: 'Analysis',
    views: [
      ['overview', 'Demand map'],
      ['ucm', 'What drives sales'],
      ['clusters', 'Village types'],
      ['factors', 'Demand factors'],
      ['scenario', 'What-if'],
      ['data', 'Data & method'],
    ],
  },
]

export default function App() {
  const { view, setView, sku, category, month, setSku, setCategory, setMonth } = useStore()
  const [chatOpen, setChatOpen] = useState(false)
  const skus = useAsync(() => api.skus(), [])
  const cats = Array.from(new Map((skus.data ?? []).map(s => [s.category, s.category_label])))

  // Filters only make sense on the analytical views; showing them on the summary
  // implies the summary is filtered when it is not.
  const showFilters = !['executive', 'villages', 'data'].includes(view)

  return (
    <div className="app">
      <header className="hdr">
        <div className="brand">
          <b>Sonalika Demand Compass</b>
          <span>Where to sell which implement, village by village · Punjab · MP · Maharashtra</span>
        </div>

        <nav className="nav">
          {GROUPS.map(g => (
            <span key={g.label} className="navgroup">
              <span className="navlabel">{g.label}</span>
              {g.views.map(([k, label]) => (
                <button key={k} className={view === k ? 'on' : ''} onClick={() => setView(k)}>
                  {label}
                </button>
              ))}
            </span>
          ))}
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

      <main className="main">
        {view === 'executive' && <Executive />}
        {view === 'villages' && <Villages />}
        {view === 'overview' && <Overview />}
        {view === 'explore' && <Explore />}
        {view === 'sku' && <SkuView />}
        {view === 'factors' && <FactorView />}
        {view === 'ucm' && <UcmView />}
        {view === 'clusters' && <ClusterView />}
        {view === 'scenario' && <ScenarioView />}
        {view === 'compete' && <Competition />}
        {view === 'data' && <DataView />}
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
