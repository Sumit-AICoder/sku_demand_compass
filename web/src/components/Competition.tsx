import React, { useState } from 'react'
import { BarChart, Bar as RBar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
         CartesianGrid, ReferenceLine, ScatterChart, Scatter, ZAxis, Legend } from 'recharts'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync, Kpi, Badge } from './common'
import SkuImage from './SkuImage'

const STATUS_COLOR: Record<string, string> = {
  Leading: 'var(--good)', Winnable: 'var(--c1)',
  Stretch: 'var(--warn)', 'Out of reach': 'var(--text-3)',
}

/**
 * Competition and cannibalisation.
 *
 * Sonalika is a challenger in implements (~8% share), so the useful questions are not
 * "what is our share" but "whose volume can we take, whose taking is a fantasy, what of
 * ours is exposed, and where are we bidding against ourselves".
 */
export default function Competition() {
  const [state, setState] = useState('')
  const [category, setCategory] = useState('')
  const [rival, setRival] = useState('Fieldking')

  const qs = new URLSearchParams()
  if (state) qs.set('state', state)
  if (category) qs.set('category', category)

  const sum = useAsync<any>(() => fetch(`/api/compete/summary?${qs}`).then(r => r.json()),
                            [qs.toString()])
  const players = useAsync<any[]>(() => fetch('/api/compete/players').then(r => r.json()), [])
  const h2h = useAsync<any>(
    () => fetch(`/api/compete/headtohead?rival=${rival}${state ? `&state=${state}` : ''}`)
      .then(r => r.json()), [rival, state])
  const cann = useAsync<any>(() => fetch('/api/compete/cannibalisation').then(r => r.json()), [])
  const skus = useAsync(() => api.skus(), [])
  const cats = Array.from(new Map((skus.data ?? []).map(s => [s.category, s.category_label])))

  return (
    <div className="grid" style={{ gap: 14 }}>
      <div className="filter-row">
        <label className="fl"><span>State</span>
          <select value={state} onChange={e => setState(e.target.value)}>
            <option value="">All states</option>
            {['Punjab', 'Madhya Pradesh', 'Maharashtra'].map(s => <option key={s}>{s}</option>)}
          </select></label>
        <label className="fl"><span>Category</span>
          <select value={category} onChange={e => setCategory(e.target.value)}>
            <option value="">All categories</option>
            {cats.map(([c, l]) => <option key={c} value={c}>{l}</option>)}
          </select></label>
      </div>

      <Async state={sum}>{(d: any) => {
        const t = d.totals
        return (
          <>
            <div className="kpis">
              <Kpi k="Market we play in" v={fmt.units(t.market)} s="implements a year" />
              <Kpi k="Sonalika volume" v={fmt.units(t.sonalika)}
                   s={`${t.sonalika_share_pct}% share — a challenger position`} />
              <Kpi k="Winnable from rivals" v={fmt.units(t.winnable)}
                   s={<span className="pos">where the contest is close enough to flip</span>} />
              <Kpi k="Our volume at risk" v={fmt.units(t.at_risk)}
                   s={<span className="neg">a rival is close behind here</span>} />
              <Kpi k="Market concentration" v={t.hhi.toFixed(2)}
                   s="0 = fragmented, 1 = monopoly" />
            </div>

            <div className="split">
              <Card title="Where we stand, contest by contest"
                    note="a challenger must know what is winnable and what is fantasy">
                <ResponsiveContainer width="100%" height={230}>
                  <BarChart data={d.by_status} layout="vertical" margin={{ left: 10, right: 20 }}>
                    <CartesianGrid stroke="var(--border)" horizontal={false} />
                    <XAxis type="number" tick={{ fontSize: 10 }} />
                    <YAxis type="category" dataKey="status" width={110} tick={{ fontSize: 11 }} />
                    <Tooltip formatter={(v: any) => `${fmt.units(v)} units`} />
                    <RBar dataKey="market" radius={[0, 4, 4, 0]}>
                      {d.by_status.map((r: any, i: number) =>
                        <Cell key={i} fill={STATUS_COLOR[r.status] ?? 'var(--c1)'} />)}
                    </RBar>
                  </BarChart>
                </ResponsiveContainer>
                <p className="note">
                  <b>Winnable</b> means our share is close to the nearest rival's — a dealer
                  or price move could flip it. <b>Stretch</b> needs a structural change.
                  <b> Out of reach</b> is not worth the call.
                </p>
              </Card>

              <Card title="Who holds the volume" tight
                    note="click a rival for the head-to-head">
                <div className="tbl-wrap" style={{ maxHeight: 300 }}>
                  <table>
                    <thead><tr>
                      <th>Rival</th><th className="n">They hold</th>
                      <th className="n">Winnable from them</th><th className="n">We could lose</th>
                    </tr></thead>
                    <tbody>
                      {d.rivals.filter((r: any) => r.rival !== 'Sonalika').map((r: any) => (
                        <tr key={r.rival} className={`clickable${rival === r.rival ? ' sel' : ''}`}
                            onClick={() => setRival(r.rival)}>
                          <td><strong>{r.rival}</strong></td>
                          <td className="n">{fmt.units(r.their_units)}</td>
                          <td className="n pos">{fmt.units(r.winnable_from_them)}</td>
                          <td className="n neg">{fmt.units(r.we_could_lose)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </div>
          </>
        )
      }}</Async>

      <div className="split">
        <Card title={`Head to head — Sonalika vs ${rival}`}
              note="of each category’s rival volume, the green slice is winnable by us">
          <Async state={h2h}>{(d: any) => {
            const data = [...d.by_category]
              .map((c: any) => ({
                ...c,
                out_of_reach: Math.max(0, c.theirs - c.winnable),
                winnable_pct: c.theirs > 0 ? (c.winnable / c.theirs) * 100 : 0,
              }))
              .sort((a: any, b: any) => b.winnable - a.winnable)
            return (
              <>
                <ResponsiveContainer width="100%" height={250}>
                  <BarChart data={data} layout="vertical" margin={{ left: 8, right: 24 }}>
                    <CartesianGrid stroke="var(--border)" horizontal={false} />
                    <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={(v: any) => fmt.units(v)} />
                    <YAxis type="category" dataKey="category" width={128} tick={{ fontSize: 10 }} />
                    <Tooltip formatter={(v: any, n: any, p: any) =>
                      n === 'Winnable by us'
                        ? `${fmt.units(v)} (${p.payload.winnable_pct.toFixed(0)}% of their volume)`
                        : fmt.units(v)} />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <RBar dataKey="winnable" name="Winnable by us" stackId="a"
                          fill="var(--good)" radius={[3, 0, 0, 3]} />
                    <RBar dataKey="out_of_reach" name={`${rival} holds (out of reach)`} stackId="a"
                          fill="var(--c5)" fillOpacity={0.3} radius={[0, 3, 3, 0]} />
                  </BarChart>
                </ResponsiveContainer>
                <p className="note">
                  Each bar is <b>{rival}’s</b> volume in that category; the <b style={{ color: 'var(--good)' }}>green</b> part
                  is the contestable slice where our share is close enough to flip. Same scale, so
                  you can see both <em>how big</em> the rival is and <em>how much is actually winnable</em>.
                </p>
                <div className="tbl-wrap" style={{ maxHeight: 150 }}>
                  <table>
                    <thead><tr><th>District</th><th>State</th>
                      <th className="n">They hold</th><th className="n">Winnable</th></tr></thead>
                    <tbody>
                      {d.top_districts.map((r: any) => (
                        <tr key={r.district + r.state}>
                          <td>{r.district}</td><td className="muted">{r.state}</td>
                          <td className="n">{fmt.units(r.theirs)}</td>
                          <td className="n pos">{fmt.units(r.winnable)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )
          }}</Async>
        </Card>

        <Card title="Price and reach — how each brand competes"
              note="bubble size = share; brands to the left are cheaper, higher up travel further">
          <Async state={players}>{(rows: any[]) => (
            <>
              <ResponsiveContainer width="100%" height={300}>
                <ScatterChart margin={{ left: -6, right: 16, top: 10, bottom: 16 }}>
                  <CartesianGrid stroke="var(--border)" />
                  <XAxis type="number" dataKey="price_index" name="price"
                         domain={[0.65, 1.45]} tick={{ fontSize: 10 }}
                         label={{ value: 'price vs market', position: 'insideBottom', fontSize: 10, dy: 12 }} />
                  <YAxis type="number" dataKey="reach_km" name="reach" tick={{ fontSize: 10 }}
                         label={{ value: 'buyer will travel (km)', angle: -90, position: 'insideLeft', fontSize: 10 }} />
                  <ZAxis type="number" dataKey="share" range={[40, 700]} />
                  <Tooltip cursor={{ strokeDasharray: '3 3' }}
                           formatter={(v: any, n: any) => n === 'share'
                             ? `${(v * 100).toFixed(1)}%` : Number(v).toFixed(2)}
                           labelFormatter={() => ''}
                           content={({ payload }: any) => payload?.[0] ? (
                             <div className="maptip" style={{ position: 'static' }}>
                               <b>{payload[0].payload.player}</b>
                               <div>{(payload[0].payload.share * 100).toFixed(1)}% share</div>
                               <div className="muted">price {payload[0].payload.price_index.toFixed(2)}× · reach {payload[0].payload.reach_km} km</div>
                             </div>) : null} />
                  <Scatter data={rows} fill="var(--c1)" fillOpacity={0.65}>
                    {rows.map((r, i) => (
                      <Cell key={i} fill={r.player === 'Sonalika' ? 'var(--good)' : 'var(--c1)'} />
                    ))}
                  </Scatter>
                </ScatterChart>
              </ResponsiveContainer>
              <p className="note">
                Sonalika (green) competes on price rather than reach. Local fabricators sit
                bottom-left — cheapest, but a buyer will not travel for them, which is why
                they hold volume village-by-village yet lose it wherever a dealer is close.
              </p>
            </>
          )}</Async>
        </Card>
      </div>

      <RivalScenario rival={rival} state={state} />

      <Card title="Our own products competing with each other"
            note="demand two Sonalika SKUs are both counting — adding both at full value double-counts">
        <Async state={cann}>{(d: any) => (
          <div className="split">
            <div className="tbl-wrap" style={{ maxHeight: 320 }}>
              <table>
                <thead><tr>
                  <th colSpan={2}>Competing pair</th><th>Same job</th>
                  <th className="n">Overlap</th><th className="n">Villages</th>
                  <th className="n">Displaced</th>
                </tr></thead>
                <tbody>
                  {d.pairs.map((r: any) => (
                    <tr key={r.sku_a + r.sku_b}>
                      <td>
                        <div className="sku-cell">
                          <SkuImage skuId={r.sku_a} size={34} />
                          <span>{r.name_a}</span>
                        </div>
                      </td>
                      <td>
                        <div className="sku-cell">
                          <SkuImage skuId={r.sku_b} size={34} />
                          <span>{r.name_b}</span>
                        </div>
                      </td>
                      <td className="muted">{r.shared_job}</td>
                      <td className="n">{(r.overlap * 100).toFixed(0)}%</td>
                      <td className="n muted">{r.villages_affected.toLocaleString('en-IN')}</td>
                      <td className="n neg">−{fmt.units(r.displaced_units)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div>
              <h4 className="sub">Share of each product's demand that is not incremental</h4>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={d.by_sku} layout="vertical" margin={{ left: 10, right: 22 }}>
                  <CartesianGrid stroke="var(--border)" horizontal={false} />
                  <XAxis type="number" tick={{ fontSize: 10 }}
                         label={{ value: '% displaced by our own range', position: 'insideBottom', fontSize: 9, dy: 10 }} />
                  <YAxis type="category" dataKey="name" width={168} tick={{ fontSize: 9.5 }} />
                  <Tooltip formatter={(v: any) => `${Number(v).toFixed(1)}%`} />
                  <RBar dataKey="displaced_pct" radius={[0, 3, 3, 0]} fill="var(--warn)" />
                </BarChart>
              </ResponsiveContainer>
              <p className="note">
                Substitution is gated on doing the same <em>job</em>, not on sharing a
                catalogue category — a super seeder and a seed drill compete for the same
                wheat sowing even though they sit in different lines, while a trolley
                competes with nothing.
              </p>
            </div>
          </div>
        )}</Async>
      </Card>
    </div>
  )
}

function RivalScenario({ rival, state }: { rival: string; state: string }) {
  const [dealers, setDealers] = useState(0)
  const [price, setPrice] = useState(0)
  const [res, setRes] = useState<any>(null)
  const [busy, setBusy] = useState(false)

  const run = async () => {
    setBusy(true)
    try {
      const r = await fetch('/api/compete/scenario', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rival, dealer_change_pct: dealers,
                               price_change_pct: price, state: state || undefined }),
      })
      setRes(await r.json())
    } finally { setBusy(false) }
  }

  return (
    <Card title={`What if ${rival} moves?`}
          note="shares come from a choice model, so one brand's move necessarily shifts the others">
      <div className="split">
        <div>
          <div className="slider-row">
            <span>{rival} dealer network</span>
            <input type="range" min={-50} max={100} step={5}
                   value={dealers} onChange={e => setDealers(Number(e.target.value))} />
            <span className="mono n">{dealers > 0 ? '+' : ''}{dealers}%</span>
          </div>
          <div className="slider-row">
            <span>{rival} price</span>
            <input type="range" min={-20} max={20} step={1}
                   value={price} onChange={e => setPrice(Number(e.target.value))} />
            <span className="mono n">{price > 0 ? '+' : ''}{price}%</span>
          </div>
          <button onClick={run} disabled={busy}
                  style={{ marginTop: 10, padding: '7px 16px', borderRadius: 8,
                           border: '1px solid var(--accent)', background: 'var(--accent)',
                           color: '#fff', fontWeight: 600, cursor: 'pointer' }}>
            {busy ? 'Running…' : 'Run'}
          </button>
          {res && (
            <div className="kpis" style={{ marginTop: 12 }}>
              <Kpi k="Our volume in these contests"
                   v={fmt.units(res.total.units_after)}
                   s={<span className={res.total.delta_units >= 0 ? 'pos' : 'neg'}>
                        {res.total.delta_units >= 0 ? '+' : ''}
                        {fmt.units(res.total.delta_units)} units ({res.total.delta_pct}%)
                      </span>} />
            </div>
          )}
          <p className="note" style={{ marginTop: 8 }}>
            This covers only the {res ? res.contests_affected.toLocaleString('en-IN') : '—'} contests
            where {rival} is our nearest rival — not the whole business. That is the slice
            their move can actually touch.
          </p>
        </div>
        <div>
          {res && (
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={res.by_category} layout="vertical" margin={{ left: 10, right: 20 }}>
                <CartesianGrid stroke="var(--border)" horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 10 }} />
                <YAxis type="category" dataKey="category" width={116} tick={{ fontSize: 10 }} />
                <Tooltip formatter={(v: any) => `${Number(v).toFixed(1)}%`} />
                <ReferenceLine x={0} stroke="var(--border-strong)" />
                <RBar dataKey="delta_pct" radius={[0, 3, 3, 0]}>
                  {res.by_category.map((c: any, i: number) => (
                    <Cell key={i} fill={c.delta_pct >= 0 ? 'var(--good)' : 'var(--bad)'} />
                  ))}
                </RBar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    </Card>
  )
}
