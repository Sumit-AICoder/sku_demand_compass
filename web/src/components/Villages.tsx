import React, { useState } from 'react'
import { BarChart, Bar as RBar, XAxis, YAxis, Tooltip, ResponsiveContainer,
         CartesianGrid, Cell, ReferenceLine } from 'recharts'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync, PointMap, Badge } from './common'
import Narrative from './Narrative'
import SkuImage from './SkuImage'

const ACTIONS = ['Convert now', 'Build access', 'Defend', 'Monitor']
const ACTION_COLOR: Record<string, string> = {
  'Convert now': 'var(--good)', 'Build access': 'var(--c1)',
  'Defend': 'var(--c4)', 'Monitor': 'var(--text-3)',
}

/**
 * Village Finder — the operational view.
 *
 * The archetype layer says what KIND of village something is; that is a strategy answer
 * and says nothing about which of 10,000 villages to visit. This view works at the
 * village itself: filter to a target list, see why each one qualifies, and open any
 * single village against the villages most like it.
 */
export default function Villages() {
  const [state, setState] = useState<string>('')
  const [district, setDistrict] = useState<string>('')
  const [action, setAction] = useState<string>('Convert now')
  const [micro, setMicro] = useState<string>('')
  const [sku, setSku] = useState<string>('')
  const [maxKm, setMaxKm] = useState<number>(0)
  const [sort, setSort] = useState('opportunity_score')
  const [sel, setSel] = useState<string>()

  const params: Record<string, any> = { limit: 300, sort }
  if (state) params.state = state
  if (district) params.district = district
  if (action) params.action = action
  if (micro) params.micro_id = micro
  if (sku) params.sku = sku
  if (maxKm) params.max_dealer_km = maxKm

  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== '' && v != null)
      .map(([k, v]) => [k, String(v)])).toString()

  const rows = useAsync<any[]>(() => fetch(`/api/villages?${qs}`).then(r => r.json()), [qs])
  const summary = useAsync<any>(
    () => fetch(`/api/villages/summary${state ? `?state=${encodeURIComponent(state)}` : ''}`)
      .then(r => r.json()), [state])
  const districts = useAsync(() => api.geo('district', { parent: state || undefined }),
                             [state])
  const skus = useAsync(() => api.skus(), [])

  return (
    <div className="grid" style={{ gap: 14 }}>
      <Narrative view="clusters" />

      <Card title="Find villages to work" note="filters build a field target list">
        <div className="filter-row">
          <L t="State"><select value={state} onChange={e => { setState(e.target.value); setDistrict('') }}>
            <option value="">All states</option>
            {['Punjab', 'Madhya Pradesh', 'Maharashtra'].map(s => <option key={s}>{s}</option>)}
          </select></L>
          <L t="District"><select value={district} onChange={e => setDistrict(e.target.value)}>
            <option value="">All districts</option>
            {(districts.data?.items ?? []).map(d => <option key={d.id} value={d.name}>{d.name}</option>)}
          </select></L>
          <L t="What to do"><select value={action} onChange={e => setAction(e.target.value)}>
            <option value="">Any action</option>
            {ACTIONS.map(a => <option key={a}>{a}</option>)}
          </select></L>
          <L t="Pocket"><select value={micro} onChange={e => setMicro(e.target.value)}>
            <option value="">All pockets</option>
            {(summary.data?.micro ?? []).map((m: any) =>
              <option key={m.micro_id} value={m.micro_id}>{m.micro_id}</option>)}
          </select></L>
          <L t="Product"><select value={sku} onChange={e => setSku(e.target.value)}>
            <option value="">All products</option>
            {(skus.data ?? []).map(s => <option key={s.sku_id} value={s.sku_id}>{s.name}</option>)}
          </select></L>
          <L t="Max km to dealer">
            <select value={maxKm} onChange={e => setMaxKm(Number(e.target.value))}>
              <option value={0}>Any distance</option>
              {[5, 10, 15, 25, 40].map(v => <option key={v} value={v}>{v} km</option>)}
            </select></L>
          <L t="Rank by"><select value={sort} onChange={e => setSort(e.target.value)}>
            <option value="opportunity_score">Opportunity</option>
            <option value="headroom">Unserved demand</option>
            <option value="potential_units_yr">Annual demand</option>
            <option value="attach_gap_micro">Gap vs similar villages</option>
            <option value="tractors">Tractor fleet</option>
          </select></L>
        </div>
      </Card>

      <Async state={summary}>{(s: any) => (
        <div className="kpis">
          {(s.actions ?? []).map((a: any) => (
            <button key={a.action_segment}
                    className={`kpi clickable${action === a.action_segment ? ' on' : ''}`}
                    style={{ borderLeft: `3px solid ${ACTION_COLOR[a.action_segment]}` }}
                    onClick={() => setAction(action === a.action_segment ? '' : a.action_segment)}>
              <div className="k">{a.action_segment}</div>
              <div className="v" style={{ fontSize: 20 }}>{a.villages.toLocaleString('en-IN')}</div>
              <div className="s">{fmt.units(a.units)} units · {a.avg_km.toFixed(0)} km avg</div>
              <div className="s muted" style={{ fontSize: 10.5, marginTop: 4 }}>{a.rationale}</div>
            </button>
          ))}
        </div>
      )}</Async>

      <div className="split">
        <Card title="Target villages" tight
              note={`${rows.data?.length ?? 0} shown · click a row for the full picture`}>
          <div className="tbl-wrap" style={{ maxHeight: 480 }}>
            <Async state={rows}>{(r: any[]) => (
              <table>
                <thead><tr>
                  <th>Village</th><th>District</th><th className="n">Score</th>
                  <th className="n">Units/yr</th><th className="n">Unserved</th>
                  <th className="n">Per tractor</th><th className="n">vs similar</th>
                  <th className="n">Km</th><th>Best product</th>
                </tr></thead>
                <tbody>
                  {r.map((v: any) => (
                    <tr key={v.village_id}
                        className={`clickable${sel === v.village_id ? ' sel' : ''}`}
                        onClick={() => setSel(v.village_id)}>
                      <td>{v.village}
                        <div className="muted" style={{ fontSize: 10 }}>
                          #{v.rank_in_district} of {v.villages_in_district} in district
                        </div></td>
                      <td className="muted">{v.district}</td>
                      <td className="n"><b>{Math.round(v.opportunity_score)}</b></td>
                      <td className="n">{fmt.units(v.potential_units_yr)}</td>
                      <td className="n muted">{fmt.units(v.headroom)}</td>
                      <td className="n">{v.attach_rate.toFixed(2)}</td>
                      <td className={`n ${v.attach_gap_micro > 0 ? 'neg' : 'pos'}`}>
                        {v.attach_gap_micro > 0 ? '−' : '+'}{Math.abs(v.attach_gap_micro).toFixed(2)}
                      </td>
                      <td className="n">{v.dealer_distance_km.toFixed(0)}</td>
                      <td className="muted mono">{v.top_sku}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}</Async>
          </div>
        </Card>

        <Card title="Where they are" note="each dot is a village">
          <Async state={rows}>{(r: any[]) => (
            <PointMap height={440} selected={sel}
              items={r.map(v => ({ id: v.village_id, name: `${v.village} — ${v.district}`,
                                   lon: v.lon ?? 0, lat: v.lat ?? 0,
                                   units: v.potential_units_yr }))}
              onSelect={setSel} />
          )}</Async>
        </Card>
      </div>

      {sel && <VillageCard villageId={sel} />}

      <Card title="Village pockets" tight
            note="each broad type splits into pockets that behave differently on the ground">
        <div className="tbl-wrap" style={{ maxHeight: 340 }}>
          <Async state={summary}>{(s: any) => (
            <table>
              <thead><tr>
                <th>Pocket</th><th>Broad type</th><th className="n">Villages</th>
                <th className="n">Units/yr</th><th className="n">Unserved</th>
                <th className="n">Score</th><th className="n">Km</th>
                <th className="n">Per tractor</th><th>Do what</th><th>Best product</th>
              </tr></thead>
              <tbody>
                {(s.micro ?? []).map((m: any) => (
                  <tr key={m.micro_id} className={`clickable${micro === m.micro_id ? ' sel' : ''}`}
                      onClick={() => setMicro(micro === m.micro_id ? '' : m.micro_id)}>
                    <td><strong>{m.micro_id}</strong></td>
                    <td className="muted">{m.archetype}</td>
                    <td className="n">{m.villages.toLocaleString('en-IN')}</td>
                    <td className="n">{fmt.units(m.units)}</td>
                    <td className="n muted">{fmt.units(m.headroom)}</td>
                    <td className="n">{Math.round(m.opp)}</td>
                    <td className="n">{m.avg_km.toFixed(0)}</td>
                    <td className="n">{m.attach.toFixed(2)}</td>
                    <td><span className="pill" style={{ color: ACTION_COLOR[m.main_action],
                                                        borderColor: ACTION_COLOR[m.main_action] }}>
                      {m.main_action}</span></td>
                    <td className="muted mono">{m.top_sku}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}</Async>
        </div>
      </Card>
    </div>
  )
}

function VillageCard({ villageId }: { villageId: string }) {
  const d = useAsync<any>(() => fetch(`/api/village/${villageId}/insight`).then(r => r.json()),
                          [villageId])
  return (
    <Async state={d}>{(x: any) => {
      const v = x.insight
      return (
        <Card title={`${v.village} — ${v.district}, ${v.state}`}
              note={`${v.archetype} · pocket ${v.micro_id.split('·').pop()}`}>
          <div className="village-head">
            <span className="pill lg" style={{ color: ACTION_COLOR[v.action_segment],
                                               borderColor: ACTION_COLOR[v.action_segment] }}>
              {v.action_segment}
            </span>
            <span className="muted">{v.action_rationale}</span>
          </div>
          <p className="headline">{v.headline}</p>

          <div className="grid g3" style={{ marginTop: 12 }}>
            <div>
              <h4 className="sub">The farm here</h4>
              <Row k="Tractors" v={Math.round(v.tractors)} />
              <Row k="Average farm size" v={`${v.avg_holding_ha.toFixed(1)} ha`} />
              <Row k="Land farmed" v={`${Math.round(v.net_sown_ha)} ha`} />
              <Row k="Main crop" v={v.dominant_crop} />
              <Row k="Irrigated" v={`${Math.round(v.irrigation_ratio * 100)}%`} />
              <Row k="Soil" v={`${v.soil_texture} (workability ${v.workability.toFixed(2)})`} />
            </div>
            <div>
              <h4 className="sub">The opportunity</h4>
              <Row k="Opportunity score" v={`${Math.round(v.opportunity_score)} / 100`} />
              <Row k="Rank in district" v={`#${v.rank_in_district} of ${v.villages_in_district}`} />
              <Row k="Demand" v={`${fmt.units(v.potential_units_yr)} units/yr`} />
              <Row k="Unserved" v={fmt.units(v.headroom)} />
              <Row k="Implements per tractor" v={`${v.attach_rate.toFixed(2)} vs ${v.peer_attach_micro.toFixed(2)} for similar villages`} />
              <Row k="Nearest dealer" v={`${v.dealer_distance_km.toFixed(1)} km`} />
            </div>
            <div>
              <h4 className="sub">What makes it different</h4>
              <ul className="dist">
                {[v.distinct_1, v.distinct_2, v.distinct_3].filter(Boolean).map((t: string) =>
                  <li key={t}>{t}</li>)}
              </ul>
              <p className="note" style={{ marginTop: 8 }}>
                Compared against the villages most like it — same type, soil, crop and
                farm size — not against a district average.
              </p>
            </div>
          </div>

          <div className="split" style={{ marginTop: 14 }}>
            <div>
              <h4 className="sub">Best products here</h4>
              <div className="sku-strip">
                {x.top_skus.slice(0, 5).map((s: any) => (
                  <div key={s.sku_id} className="sku-chip" title={s.name}>
                    <SkuImage skuId={s.sku_id} size={46} />
                    <span>{s.name.split(' ').slice(0, 2).join(' ')}</span>
                  </div>
                ))}
              </div>
              <ResponsiveContainer width="100%" height={210}>
                <BarChart data={x.top_skus} layout="vertical" margin={{ left: 8, right: 18 }}>
                  <CartesianGrid stroke="var(--border)" horizontal={false} />
                  <XAxis type="number" tick={{ fontSize: 10 }} />
                  <YAxis type="category" dataKey="name" width={168} tick={{ fontSize: 9.5 }} />
                  <Tooltip formatter={(val: any) => `${Number(val).toFixed(1)} units/yr`} />
                  <RBar dataKey="units" radius={[0, 3, 3, 0]} fill="var(--c1)" />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div>
              <h4 className="sub">Villages most like this one</h4>
              <div className="tbl-wrap" style={{ maxHeight: 210 }}>
                <table>
                  <thead><tr><th>Village</th><th>District</th><th className="n">Score</th>
                             <th className="n">Per tractor</th><th className="n">Km</th></tr></thead>
                  <tbody>
                    {x.peers.map((p: any) => (
                      <tr key={p.village}>
                        <td>{p.village}</td><td className="muted">{p.district}</td>
                        <td className="n">{p.opportunity}</td>
                        <td className="n">{p.attach_rate}</td>
                        <td className="n">{p.km}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </Card>
      )
    }}</Async>
  )
}

function L({ t, children }: { t: string; children: React.ReactNode }) {
  return <label className="fl"><span>{t}</span>{children}</label>
}
function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="kv"><span className="muted">{k}</span><span>{v}</span></div>
}
