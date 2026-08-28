import React from 'react'
import { BarChart, Bar as RBar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
         LineChart, Line, CartesianGrid } from 'recharts'
import { api, fmt, MONTHS } from '../lib/api'
import { useStore } from '../lib/store'
import { Card, Kpi, Async, useAsync, Bar } from './common'
import IndiaMap, { MapNode } from './IndiaMap'
import Narrative from './Narrative'

export default function Overview() {
  const { sku, category, month, setView, push } = useStore()
  const [mapPath, setMapPath] = React.useState<MapNode[]>([])

  const districts = useAsync(() => api.geo('district', { sku, category, month }), [sku, category, month])
  const skus = useAsync(() => api.scores({ level: 'district', category, limit: 12 }), [category])
  const meta = useAsync(() => api.meta(), [])

  const items = districts.data?.items ?? []
  const totUnits = items.reduce((a, b) => a + (b.units || 0), 0)
  const totValue = items.reduce((a, b) => a + (b.value || 0), 0)
  const totHead = items.reduce((a, b) => a + (b.headroom || 0), 0)
  const totAddr = items.reduce((a, b) => a + (b.addressable || 0), 0)

  const byState: Record<string, number> = {}
  items.forEach(i => { byState[i.parent ?? '—'] = (byState[i.parent ?? '—'] ?? 0) + (i.units || 0) })

  return (
    <div className="grid" style={{ gap: 14 }}>
      <Narrative view="overview" params={{ sku, category, month }} />
      <div className="kpis">
        <Kpi k="Demand potential" v={fmt.units(totUnits)}
             s={`units / yr${month ? ` · ${MONTHS[month - 1]} run-rate` : ''}`} />
        <Kpi k="Market value" v={fmt.cr(totValue)} s="at indicative SKU prices" />
        <Kpi k="Unserved headroom" v={fmt.units(totHead)}
             s={`${fmt.pct(totAddr ? (totHead / totAddr) * 100 : 0, 0)} of addressable fleet`} />
        <Kpi k="Districts" v={items.length}
             s={`${meta.data?.counts.villages.toLocaleString('en-IN') ?? '—'} villages scored`} />
        <Kpi k="Seasonal factor" v={month ? `${(districts.data?.season_factor ?? 1).toFixed(2)}×` : '—'}
             s={month ? 'UCM-estimated × SKU window' : 'select a month'} />
      </div>

      <div className="split">
        <Card title="Where the demand is"
              note={`${sku ?? category ?? 'all products'} · click to drill in`}>
          <IndiaMap path={mapPath} onDrill={n => setMapPath(p => [...p, n])}
                    onUp={i => setMapPath(p => (i < 0 ? [] : p.slice(0, i + 1)))}
                    sku={sku} category={category} month={month} height={420} />
        </Card>

        <div className="grid" style={{ gap: 14, alignContent: 'start' }}>
          <Card title="By state">
            <ResponsiveContainer width="100%" height={150}>
              <BarChart data={Object.entries(byState).map(([k, v]) => ({ state: k, units: v }))}
                        layout="vertical" margin={{ left: 8, right: 18 }}>
                <XAxis type="number" tick={{ fontSize: 11 }} />
                <YAxis type="category" dataKey="state" width={104} tick={{ fontSize: 11 }} />
                <Tooltip formatter={(v: any) => fmt.units(v)} />
                <RBar dataKey="units" radius={[0, 4, 4, 0]}>
                  {Object.keys(byState).map((_, i) =>
                    <Cell key={i} fill={`var(--c${(i % 8) + 1})`} />)}
                </RBar>
              </BarChart>
            </ResponsiveContainer>
          </Card>

          <Card title="Top districts" tight>
            <div className="tbl-wrap" style={{ maxHeight: 250 }}>
              <table>
                <thead><tr><th>District</th><th>State</th><th className="n">Units/yr</th><th>Top SKU</th></tr></thead>
                <tbody>
                  {items.slice(0, 12).map(i => (
                    <tr key={i.id} className="clickable" onClick={() => {
                      push({ level: 'state', id: i.parent!, name: i.parent! })
                      push({ level: 'district', id: i.id, name: i.name })
                      setView('drill')
                    }}>
                      <td>{i.name}</td>
                      <td className="muted">{i.parent}</td>
                      <td className="n">{fmt.units(i.units)}</td>
                      <td className="muted mono">{i.top_sku ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      </div>

      <div className="split">
        <Card title="Top SKUs by demand potential" tight>
          <div className="tbl-wrap" style={{ maxHeight: 380 }}>
            <table>
              <thead>
                <tr><th>SKU</th><th>Category</th><th className="n">Units/yr</th>
                    <th className="n">New</th><th className="n">Replace</th>
                    <th className="n">Value</th><th style={{ width: 90 }} /></tr>
              </thead>
              <Async state={skus}>{(rows: any[]) => {
                const max = Math.max(...rows.map(r => r.units))
                return <tbody>
                  {rows.map(r => (
                    <tr key={r.sku_id} className="clickable"
                        onClick={() => useStore.getState().setSku(r.sku_id)}>
                      <td>{r.name}</td>
                      <td className="muted">{r.category_label}</td>
                      <td className="n">{fmt.units(r.units)}</td>
                      <td className="n muted">{fmt.units(r.new_units)}</td>
                      <td className="n muted">{fmt.units(r.replacement_units)}</td>
                      <td className="n">{fmt.cr(r.value)}</td>
                      <td><Bar value={r.units} max={max} /></td>
                    </tr>
                  ))}
                </tbody>
              }}</Async>
            </table>
          </div>
        </Card>

        <Card title="Seasonality" note="UCM-estimated seasonal × SKU demand window">
          <SeasonStrip sku={sku} />
          <p className="note" style={{ marginTop: 10 }}>
            The monthly shape is <strong>estimated</strong> from the tractor registration
            series by the UCM's stochastic seasonal component, not asserted. It peaks
            post-kharif and through the festive window, and troughs at monsoon onset.
            Each SKU's own agronomic window from the Excel Seasonality sheet is layered
            on top.
          </p>
        </Card>
      </div>
    </div>
  )
}

function SeasonStrip({ sku }: { sku?: string }) {
  const s = useAsync(() => api.geo('state', {}), [])
  const seas = useAsync(async () => {
    const all = await Promise.all(
      Array.from({ length: 12 }, (_, m) => api.geo('state', { sku, month: m + 1 })))
    return all.map((r, i) => ({ month: MONTHS[i], factor: r.season_factor }))
  }, [sku])
  return (
    <Async state={seas}>{(rows: any[]) => (
      <ResponsiveContainer width="100%" height={170}>
        <LineChart data={rows} margin={{ left: -18, right: 8, top: 6 }}>
          <CartesianGrid stroke="var(--border)" vertical={false} />
          <XAxis dataKey="month" tick={{ fontSize: 10 }} />
          <YAxis tick={{ fontSize: 10 }} />
          <Tooltip formatter={(v: any) => `${Number(v).toFixed(2)}×`} />
          <Line type="monotone" dataKey="factor" stroke="var(--c1)" strokeWidth={2} dot={{ r: 2.5 }} />
        </LineChart>
      </ResponsiveContainer>
    )}</Async>
  )
}
