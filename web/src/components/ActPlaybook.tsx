import React, { useEffect, useState } from 'react'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync, Kpi, Info, Bar } from './common'
import { ArchetypePicker, useArchetypes, BUCKET_COLOR } from './ActPicker'

/**
 * ACT · Playbook — what to do in one archetype, and what each action is worth.
 *
 * Every play owns exactly one mechanism, which is what makes the numbers addable: the
 * network play owns reach, the finance play owns loan approval, the activity play owns
 * effort at today's rates, the conversion play owns whatever execution quality is left after
 * those two, price and promotion own the archetype's own estimated elasticities, and subsidy
 * owns policy. Volume winnable from a named rival is the ceiling those plays are measured
 * against, never a line added to them.
 */

const CONF: Record<string, { label: string; colour: string; why: string }> = {
  arithmetic: { label: 'arithmetic', colour: 'var(--good)',
    why: 'inverts a relationship already true in the data — no model behind it' },
  estimated: { label: 'estimated', colour: 'var(--c1)',
    why: 'rests on a fitted response — direction sound, size approximate' },
  proxy: { label: 'proxy', colour: 'var(--warn)',
    why: 'uses a stand-in where the real figure is unpublished (national SMAM rate)' },
}

const BARRIERS = [
  ['finance', 'Finance access'], ['service', 'Service & reach'],
  ['awareness', 'Awareness'], ['product', 'Product fit'],
]

export default function ActPlaybook() {
  const { b, rows, sel, setSel, chosen } = useArchetypes()
  const [barrier, setBarrier] = useState('finance')
  const [approval, setApproval] = useState<number>()
  const [awareness, setAwareness] = useState(0.38)
  const [density, setDensity] = useState(20)
  const [activity, setActivity] = useState(25)
  const [run, setRun] = useState(0)

  // Debounce the sliders into one request, and keep the last good answer on screen while
  // the next one lands so the panel never blanks mid-drag.
  useEffect(() => {
    const t = setTimeout(() => setRun(r => r + 1), 300)
    return () => clearTimeout(t)
  }, [barrier, approval, awareness, density, activity])

  const p = useAsync(() => api.actPlaybook({
    archetype_id: sel,
    assumptions: {
      top_barrier: barrier, approval_rate: approval, awareness,
      dealer_density_pct: density, activity_uplift_pct: activity,
    },
  }), [sel, run], !!sel)
  const [last, setLast] = useState<any>()
  useEffect(() => { if (p.data) setLast(p.data) }, [p.data])
  const d = p.data ?? last

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="stage-note">
        What to do in this archetype, and what each action is worth. Plays are built from
        competitor volume, the dealer network, subsidy and the funnel; the survey inputs below
        are <span className="pill pill-primary">assumptions</span> until the primary study
        lands, and every one of them is named on screen rather than hidden in a weight.
      </div>

      <Async state={b}>{() => <ArchetypePicker rows={rows} sel={sel} setSel={setSel} />}</Async>

      {p.err && <div className="err">{String(p.err)}</div>}
      {!d && <div className="loading">building the playbook…</div>}
      {d && (
        <>
          <div className="stage-note" style={{ borderColor: BUCKET_COLOR[d.bucket] }}>
            <b style={{ color: BUCKET_COLOR[d.bucket] }}>{d.bucket}.</b>{' '}
            <b>{d.archetype}</b> — we hold {(d.situation.share * 100).toFixed(1)}% of{' '}
            {fmt.count(d.situation.demand)} units a year against {d.situation.leader} at{' '}
            {d.situation.leader_share == null ? '—' : (d.situation.leader_share * 100).toFixed(0) + '%'}.
            Product fit {(d.situation.product_fit * 100).toFixed(0)}%, dealer coverage{' '}
            {(d.situation.sales_coverage * 100).toFixed(0)}%, loan approval{' '}
            {(d.situation.approval_rate * 100).toFixed(0)}%.{' '}
            {d.bucket === 'Grow' && 'The plays below are ranked by what they are worth.'}
            {d.bucket === 'Defend' && 'Holding what we have comes before adding to it.'}
            {d.bucket === 'No product fit' && 'There is no selling play here — only a product decision.'}
          </div>

          <div className="grid g4">
            <Kpi k={<span>Growth on the table<Info wide text={<>
                  The plays that <b>grow</b> volume, added up. They are addable because each
                  one moves a different part of the same identity — reach, approval, effort,
                  execution, price, policy — so nothing is counted twice. The total is capped
                  at the archetype's unclaimed demand.</>} /></span>}
                 v={fmt.count(d.total.capped_units)}
                 s={d.total.capped_by ? `capped by ${d.total.capped_by}` : 'units/yr across all plays'} />
            <Kpi k={<span>Winnable from rivals<Info wide text={<>
                  Volume sitting in contests where a rival is closest and beatable. It is
                  narrower than the growth total because it only counts share we would take
                  from a named rival — not demand we grow for ourselves. Shown as context,
                  never added to the plays.</>} /></span>}
                 v={fmt.count(d.winnable)} s="units/yr in beatable contests" />
            <Kpi k={<span>At risk<Info text={<>Volume where a rival is closest and our lead is
                  narrow — what a Defend play protects.</>} /></span>}
                 v={fmt.count(d.at_risk)} s="units/yr we could lose" />
            <Kpi k="Unclaimed demand" v={fmt.count(d.total.headroom)}
                 s="the ceiling on everything below" />
          </div>

          <div className="split">
              <Card title={<>Survey assumptions<Info wide text={<>
                    The primary study has not run, so these are <b>your</b> assumptions, not
                    data. Each moves something specific: the barrier changes only the ranking;
                    approval feeds the model's own conversion identity; awareness scales what
                    an extra visit yields — it is the one input with no data proxy anywhere in
                    the repo; density and activity set the size of the move being priced.</>} /></>}
                    note="assumptions · they hot-swap when the study lands">
                <div className="row" style={{ gap: 10, marginBottom: 12 }}>
                  <span className="dim" style={{ fontSize: 12, width: 108 }}>Top barrier</span>
                  <select value={barrier} onChange={e => setBarrier(e.target.value)}>
                    {BARRIERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                </div>
                <p className="dim" style={{ fontSize: 11, margin: '0 0 12px' }}>
                  The barrier <b>re-orders</b> the plays so the one your customers actually
                  complain about leads. It never changes a units figure — saying “finance is
                  the barrier” is a view, not a measurement, and the numbers stay measured.
                  The four sliders below are what move them.
                </p>
                <div className="slider-row">
                  <span>Loan approval</span>
                  <input type="range" min={0.4} max={0.95} step={0.01}
                         value={approval ?? Math.min(d.situation.approval_rate + 0.05, 0.95)}
                         onChange={e => setApproval(Number(e.target.value))} />
                  <span className="mono n" style={{ textAlign: 'right' }}>
                    {((approval ?? Math.min(d.situation.approval_rate + 0.05, 0.95)) * 100).toFixed(0)}%
                  </span>
                </div>
                <div className="slider-row">
                  <span>Awareness</span>
                  <input type="range" min={0} max={1} step={0.02} value={awareness}
                         onChange={e => setAwareness(Number(e.target.value))} />
                  <span className="mono n" style={{ textAlign: 'right' }}>{(awareness * 100).toFixed(0)}%</span>
                </div>
                <div className="slider-row">
                  <span>Network expansion</span>
                  <input type="range" min={0} max={100} step={5} value={density}
                         onChange={e => setDensity(Number(e.target.value))} />
                  <span className="mono n" style={{ textAlign: 'right' }}>+{density}%</span>
                </div>
                <div className="slider-row">
                  <span>BD activity push</span>
                  <input type="range" min={0} max={100} step={5} value={activity}
                         onChange={e => setActivity(Number(e.target.value))} />
                  <span className="mono n" style={{ textAlign: 'right' }}>+{activity}%</span>
                </div>
                <p className="note" style={{ marginTop: 8 }}>
                  Today's loan approval across this archetype's villages is{' '}
                  <b>{(d.situation.approval_rate * 100).toFixed(0)}%</b>.{' '}
                  {p.loading && <span className="dim">updating…</span>}
                </p>
              </Card>

              <Card title={<>Who we are up against<Info wide text={<>
                    The rivals closest to us inside this archetype, with the volume we could
                    take from each and the volume they could take from us. “Local” is the
                    unbranded segment — in implements it is usually the biggest single block
                    of volume, and it is beatable in a way a branded rival is not.</>} /></>}
                    note="units/yr in contested villages">
                <table>
                  <thead><tr>
                    <th>Rival</th>
                    <th style={{ textAlign: 'right' }}>Theirs</th>
                    <th style={{ textAlign: 'right' }}>Winnable</th>
                    <th style={{ textAlign: 'right' }}>At risk</th>
                  </tr></thead>
                  <tbody>
                    {d.rivals.map((x: any) => (
                      <tr key={x.rival}>
                        <td>{x.rival}</td>
                        <td style={{ textAlign: 'right' }}>{fmt.count(x.their_units)}</td>
                        <td style={{ textAlign: 'right', color: 'var(--good)' }}>{fmt.count(x.winnable)}</td>
                        <td style={{ textAlign: 'right', color: 'var(--bad)' }}>{fmt.count(x.at_risk)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
          </div>

          <Card title={<>Recommended plays<Info wide text={<>
                  Each play names the one mechanism it moves and is priced in units a year at
                  this archetype's own rates. “TIV reached” is the fleet an action brings
                  within commercial reach — only the network play changes that, so the others
                  read “—” rather than borrow a number that isn't theirs.</>} /></>}
                  note={`${d.plays.length} plays · ranked`}>
              <table>
                <thead><tr>
                  <th>Play</th>
                  <th style={{ textAlign: 'right' }}>Units/yr</th>
                  <th style={{ textAlign: 'right' }}>Share pts</th>
                  <th style={{ textAlign: 'right' }}>TIV reached
                    <Info wide text={<>The fleet an action brings <b>within commercial
                      reach</b> — tractors in micro-markets that cross from "too far from a
                      dealer" to "close enough to sell to". Only the network play changes
                      reach, so every other row reads “—” rather than borrow a number that
                      isn't its own. It is not TIV we create: the tractors already exist.</>} />
                  </th>
                  <th>Confidence
                    <Info wide text={<>How much to trust the number, not whether the action is
                      a good idea.</>} />
                  </th>
                </tr></thead>
                <tbody>
                  {d.plays.map((x: any, i: number) => (
                    <tr key={x.play}>
                      <td>
                        <b>{x.mode === 'protect' ? '🛡 ' : x.mode === 'stop' ? '⛔ ' : `${i + 1}. `}{x.play}</b>
                        <div className="dim" style={{ fontSize: 11 }}>{x.detail}</div>
                      </td>
                      <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                        {x.mode === 'protect'
                          ? <span style={{ color: 'var(--warn)' }}>{fmt.count(x.units)} held</span>
                          : x.units ? `+${fmt.count(x.units)}` : '—'}
                      </td>
                      <td style={{ textAlign: 'right' }}>{x.units ? x.share_pts : '—'}</td>
                      <td style={{ textAlign: 'right' }}>
                        {x.tiv_reached ? fmt.count(x.tiv_reached) : '—'}</td>
                      <td>
                        <span className="pill" title={CONF[x.confidence]?.why}
                              style={{ borderColor: CONF[x.confidence]?.colour,
                                       color: CONF[x.confidence]?.colour, marginLeft: 0 }}>
                          {CONF[x.confidence]?.label ?? x.confidence}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="conf-key">
                {Object.entries(CONF).map(([k, c]) => (
                  <span key={k}>
                    <i className="pill" style={{ borderColor: c.colour, color: c.colour }}>{c.label}</i>
                    {c.why}
                  </span>
                ))}
              </div>
              {d.total.raw_units !== d.total.capped_units && (
                <p className="dim" style={{ fontSize: 11, marginTop: 10 }}>
                  The plays add to {fmt.count(d.total.raw_units)} units, capped to{' '}
                  {fmt.count(d.total.capped_units)} by unclaimed demand — we cannot sell more than
                  the archetype has left to give.
                </p>
              )}
          </Card>
        </>
      )}
    </div>
  )
}
