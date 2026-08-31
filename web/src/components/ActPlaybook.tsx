import React, { useEffect, useState } from 'react'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync, Kpi, Info, Bar } from './common'
import { ArchetypePicker, ScopePicker, useArchetypes, useActScope, BUCKET_COLOR } from './ActPicker'
import { useStore } from '../lib/store'

/**
 * ACT · the execution playbook for one scope.
 *
 * The page answers the client's own question -- "for this archetype, these are the 10
 * things I want to do" -- organised as the seven use cases the business team defined.
 *
 * It is a lot of material, so it is paged rather than stacked: an overview that reads in
 * ten seconds, then one tab per use case. Everything below the fold on a tab is the depth
 * for the person actually doing the work; the overview is for the person deciding whether
 * it should be done. Caveats live in the info tooltips rather than as body text, because a
 * page that explains itself at every row stops being readable.
 *
 * Two things hold the numbers together. The plays are unchanged arithmetic -- each owns one
 * mechanism, so re-homing them across seven tabs moves where they are printed and never
 * what they are worth. And the customer layer is modelled: the primary survey has not run,
 * so every line names the village sub-factor and national percentile it came from.
 */

const CONF: Record<string, { label: string; colour: string; why: string }> = {
  arithmetic: { label: 'arithmetic', colour: 'var(--good)',
    why: 'inverts a relationship already true in the data — no model behind it' },
  estimated: { label: 'estimated', colour: 'var(--c1)',
    why: 'rests on a fitted response — direction sound, size approximate' },
  proxy: { label: 'proxy', colour: 'var(--warn)',
    why: 'uses a stand-in where the real figure is unpublished (national SMAM rate)' },
}

const PROV: Record<string, { cls: string; label: string; why: string }> = {
  'real': { cls: 'pill-real', label: 'real', why: 'observed, published data' },
  'modelled': { cls: 'pill-secondary', label: 'modelled',
    why: 'computed from real inputs through the model rather than observed directly' },
  'EY primary · modelled': { cls: 'pill-primary', label: 'survey · modelled',
    why: 'stands in for the customer survey, which has not been run — every figure names the village sub-factor and national percentile it came from' },
  'ITL pending': { cls: 'pill-client', label: 'ITL pending',
    why: 'waiting on data only Sonalika holds' },
  'judgement': { cls: 'pill-judgement', label: 'rule of thumb',
    why: 'written from experience, not derived from data — nothing in the dataset records this, so change it if your experience differs' },
  'simulated · ITL pending': { cls: 'pill-client', label: 'simulated funnel',
    why: 'rests on the activity → enquiry → delivery funnel, which is simulated until ITL supplies two years of actuals' },
  'mixed': { cls: 'pill-secondary', label: 'part real',
    why: 'part real, part modelled — the note on this section says which columns are which' },
}

function Prov({ p }: { p?: string | null }) {
  if (!p) return null
  const x = PROV[p]
  return x
    ? <span className={`pill ${x.cls}`} title={x.why}>{x.label}</span>
    : <span className="pill pill-secondary">{p}</span>
}

const BARRIERS = [
  ['', 'Let the data decide'], ['finance', 'Finance access'], ['service', 'Service & reach'],
  ['awareness', 'Awareness'], ['product', 'Product fit'],
]

/** Short labels for the tab strip — the full titles are the card headings. */
const TAB_LABEL: Record<string, string> = {
  network: 'Network', customer: 'Customer growth', product: 'Product',
  inventory: 'Inventory', activity: 'Activity plan', sales: 'Sales planning',
  incentives: 'Incentives',
}

/**
 * Counts read as counts and rates read as rates. Anything at or above 100 is a volume --
 * units, tractors, rupees -- and gets thousands separators with no decimals; below that it
 * is a rate, an index or a distance and keeps two places. Nulls are dashes, never zeros:
 * "no dealer data for this district" and "zero dealers" are different facts.
 */
function cell(v: any) {
  if (v == null || v === '') return '—'
  if (typeof v === 'number') {
    if (Number.isInteger(v)) return v.toLocaleString('en-IN')
    return Math.abs(v) >= 100 ? Math.round(v).toLocaleString('en-IN') : v.toFixed(2)
  }
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  return String(v)
}

/** A table that opens short. Six rows is enough to see the shape; the rest is one click. */
function Rows({ columns, rows, cap = 6 }: { columns: any[]; rows: any[]; cap?: number }) {
  const [all, setAll] = useState(false)
  const shown = all ? rows : rows.slice(0, cap)
  return (
    <>
      <div className="uc-scroll">
        <table>
          <thead><tr>
            {columns.map((c: any) => <th key={c.key} style={{ textAlign: c.align }}>{c.label}</th>)}
          </tr></thead>
          <tbody>
            {shown.map((r: any, i: number) => (
              <tr key={i}>
                {columns.map((c: any) => (
                  <td key={c.key} style={{ textAlign: c.align }}>{cell(r[c.key])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > cap && (
        <button className="linkish" onClick={() => setAll(a => !a)}>
          {all ? `show fewer` : `show all ${rows.length} rows`}
        </button>
      )}
    </>
  )
}

/**
 * One section inside a use-case card. The backend emits three shapes -- table, facts,
 * list -- so a new section is a data change there rather than a new component here.
 *
 * The heading is plain English; the business team's own wording from the Act slide and the
 * section's caveat both live in the tooltip. They have to be available, they do not have to
 * be in the way.
 *
 * An empty section says WHY it is empty. "No dealer file for Punjab" and "no gaps here"
 * look identical as a blank table and mean opposite things.
 */
function Section({ s }: { s: any }) {
  const rows = s.rows ?? s.items ?? []
  return (
    <div className={`uc-section${s.wide ? ' wide' : ''}`}>
      <h4>
        {s.title}<Prov p={s.provenance} />
        {(s.note || s.bullet) && <Info wide text={<>
          {s.note}
          {s.bullet && <><br /><br /><i>On the Act framework this is: “{s.bullet}”.</i></>}
        </>} />}
      </h4>

      {!rows.length && <p className="uc-empty">{s.empty ?? 'Nothing here for this scope.'}</p>}

      {!!rows.length && s.kind === 'table' && <Rows columns={s.columns} rows={s.rows} />}

      {!!rows.length && s.kind === 'facts' && (
        <div className="pb-grid">
          {s.items.map((x: any, i: number) => (
            <div className="pb-cell" key={i}>
              <div className="pb-k">{x.k}</div>
              <div className="pb-v">{x.v}</div>
              {x.note && <div className="dim" style={{ fontSize: 11 }}>{x.note}</div>}
            </div>
          ))}
        </div>
      )}

      {!!rows.length && s.kind === 'list' && (
        <ul className="uc-list">
          {s.items.map((x: any, i: number) => (
            <li key={i}>
              <b>{x.title}</b>{x.tag && <span className="uc-tag">{x.tag}</span>}
              <div className="dim">{x.detail}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** One priced play. The plan stays visible; the place list folds away. */
function Play({ p }: { p: any }) {
  const ex = p.execution ?? {}
  const icon = p.mode === 'protect' ? '🛡' : p.mode === 'stop' ? '⛔' : '▸'
  return (
    <div className="play">
      <div className="play-head">
        <span className="play-title">{icon} {p.play}</span>
        <span className="play-nums">
          {p.mode === 'protect'
            ? <b style={{ color: 'var(--warn)' }}>{fmt.count(p.units)} held</b>
            : p.units ? <b style={{ color: 'var(--good)' }}>+{fmt.count(p.units)} units/yr</b>
            : <b className="dim">a decision, not a lever</b>}
          {!!p.units && <span className="dim">{p.share_pts} share pts</span>}
          {!!p.tiv_reached && <span className="dim">{fmt.count(p.tiv_reached)} TIV reached</span>}
          <span className="pill conf" title={CONF[p.confidence]?.why}
                style={{ borderColor: CONF[p.confidence]?.colour, color: CONF[p.confidence]?.colour }}>
            {CONF[p.confidence]?.label ?? p.confidence}
          </span>
        </span>
      </div>

      <div className="play-body">
        <p className="play-obj">{ex.objective}</p>

        <div className="play-cols">
          {!!ex.how?.length && (
            <div className="play-block">
              <h5>How to execute</h5>
              <ol className="play-steps">
                {ex.how.map((h: any) => (
                  <li key={h.step}>
                    <b>{h.what}</b><span className="uc-tag">{h.when}</span>
                    <div className="dim">{h.detail}</div>
                  </li>
                ))}
              </ol>
            </div>
          )}

          <div className="play-side">
            {!!ex.why?.length && (
              <div className="play-block">
                <h5>Why here</h5>
                <ul>{ex.why.map((w: string, i: number) => <li key={i}>{w}</li>)}</ul>
              </div>
            )}
            <div className="pb-grid">
              <div className="pb-cell"><div className="pb-k">Cadence</div>
                <div className="pb-v">{ex.cadence ?? '—'}</div></div>
              <div className="pb-cell"><div className="pb-k">Owner</div>
                <div className="pb-v">{ex.owner ?? '—'}</div></div>
              <div className="pb-cell"><div className="pb-k">Track</div>
                <div className="pb-v">{ex.kpi?.metric ?? '—'}</div>
                {ex.kpi && <div className="dim" style={{ fontSize: 11 }}>
                  {ex.kpi.baseline} → <b>{ex.kpi.target}</b> by {ex.kpi.by_when}</div>}</div>
            </div>
          </div>
        </div>

        {!!ex.where?.length && (
          <details className="fold">
            <summary>Where to do it — {ex.where.length} named micro-markets</summary>
            <Rows
              columns={[{ key: 'micro_market', label: 'Micro-market' },
                        { key: 'district', label: 'District' },
                        { key: 'tiv', label: 'Tractors', align: 'right' },
                        { key: 'units', label: 'Units/yr', align: 'right' },
                        { key: 'why_here', label: 'Why here' }]}
              rows={ex.where} cap={ex.where.length} />
          </details>
        )}

        {ex.cost_note && <p className="note">{ex.cost_note}</p>}
      </div>
    </div>
  )
}

function UseCaseCard({ c }: { c: any }) {
  return (
    <Card title={<span className="uc-h"><span className="uc-n">{c.n}</span>{c.title}<Prov p={c.provenance} /></span>}
          note={c.units ? `${fmt.count(c.units)} units/yr from this use case`
                : c.key === 'customer' || c.key === 'inventory'
                  ? 'aims volume the other use cases create'
                  : 'no priced play in this scope'}>
      <p className="uc-summary">{c.summary}</p>
      {!!c.plays?.length && c.plays.map((p: any) => <Play key={p.play} p={p} />)}
      <div className="uc-sections">
        {c.sections.map((s: any, i: number) => <Section key={i} s={s} />)}
      </div>
    </Card>
  )
}

/** The customer layer, in full. Referenced by all seven use cases, so it gets its own tab. */
function CustomerSignal({ s }: { s: any }) {
  const p = s.perception ?? {}
  const bb = s.buying_behaviour ?? {}
  const cm = s.channel_mix ?? {}
  const maxDriver = Math.max(100, ...(s.purchase_drivers ?? []).map((x: any) => x.score))

  return (
    <Card title={<span className="uc-h">What customers here are telling us<Prov p={s.provenance} /><Info wide text={<>
            The primary study has not run. Everything here is modelled from{' '}
            <b>village_factors</b>, whose 44 sub-factors are percentile-ranked 0–100 across
            every village in the country — so “72” means this scope sits at the 72nd
            percentile nationally, a statement about real data rather than an invented survey
            response. Hover any row for the sub-factors behind it.</>} /></span>}
          note={`${fmt.count(s.n_villages)} villages · loudest complaint sets the order of the seven use cases`}>

      <div className="split">
        <div className="uc-section">
          <h4>What buyers here weigh most<Info text="Hover a row for the village sub-factors and percentiles behind it." /></h4>
          {(s.purchase_drivers ?? []).map((x: any) => (
            <div className="driver-row" key={x.driver} title={x.evidence}>
              <span>{x.driver}</span>
              <Bar value={x.score} max={maxDriver}
                   color={x.vs_national >= 0 ? 'var(--c1)' : 'var(--text-3)'} />
              <span className="mono n">{x.score.toFixed(0)}</span>
              <span className="dim">{x.implication}</span>
            </div>
          ))}
        </div>

        <div className="uc-section">
          <h4>How they see us<Info wide text={p.evidence} /></h4>
          <div className="nps">
            <i style={{ width: `${p.satisfied_pct}%`, background: 'var(--good)' }} />
            <i style={{ width: `${p.neutral_pct}%`, background: 'var(--border-strong)' }} />
            <i style={{ width: `${p.detractor_pct}%`, background: 'var(--bad)' }} />
          </div>
          <div className="row" style={{ gap: 14, fontSize: 11.5, marginBottom: 4 }}>
            <span style={{ color: 'var(--good)' }}>{p.satisfied_pct}% happy</span>
            <span className="dim">{p.neutral_pct}% neutral</span>
            <span style={{ color: 'var(--bad)' }}>{p.detractor_pct}% unhappy</span>
          </div>
          <div className="pb-grid">
            <div className="pb-cell" title={p.praise_evidence}>
              <div className="pb-k">They like</div><div className="pb-v">{p.top_praise}</div></div>
            <div className="pb-cell" title={p.complaint_evidence}>
              <div className="pb-k">They complain about</div>
              <div className="pb-v" style={{ color: 'var(--bad)' }}>{p.top_complaint}</div></div>
          </div>
        </div>
      </div>

      <div className="split">
        <div className="uc-section">
          <h4>How they buy<Info wide text={bb.evidence} /></h4>
          <div className="pb-grid">
            <div className="pb-cell"><div className="pb-k">Buy to own</div>
              <div className="pb-v">{bb.own_vs_rent_pct}%</div>
              <div className="dim" style={{ fontSize: 11 }}>{bb.rent_pct}% hire instead</div></div>
            <div className="pb-cell"><div className="pb-k">On credit</div>
              <div className="pb-v">{bb.finance_led_pct}%</div></div>
            <div className="pb-cell"><div className="pb-k">Scheme-led</div>
              <div className="pb-v">{bb.subsidy_led_pct}%</div></div>
            <div className="pb-cell"><div className="pb-k">Buying peaks</div>
              <div className="pb-v">{bb.season_peak_month ?? '—'}</div></div>
            <div className="pb-cell"><div className="pb-k">Who they listen to</div>
              <div className="pb-v">{bb.influencer}</div></div>
            <div className="pb-cell" title={cm.evidence}><div className="pb-k">Reached by</div>
              <div className="pb-v">{cm.btl_pct}% BTL</div>
              <div className="dim" style={{ fontSize: 11 }}>
                {cm.digital_pct}% digital · {cm.dealer_pct}% counter</div></div>
          </div>
        </div>

        <div className="uc-section">
          <h4>What would make them switch</h4>
          <table>
            <thead><tr><th>Trigger</th><th style={{ textAlign: 'right' }}>Strength</th></tr></thead>
            <tbody>
              {(s.switching_triggers ?? []).map((t: any) => (
                <tr key={t.trigger}><td title={t.evidence}>{t.trigger}</td>
                  <td style={{ textAlign: 'right' }}>{t.strength.toFixed(0)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {!!(s.unmet_needs ?? []).length && (
        <div className="uc-section">
          <h4>What the product still does not do</h4>
          <Rows columns={[{ key: 'need', label: 'Unmet need' },
                          { key: 'severity', label: 'Severity', align: 'right' },
                          { key: 'evidence', label: 'Evidence' }]}
                rows={s.unmet_needs} cap={6} />
        </div>
      )}
    </Card>
  )
}

export default function ActPlaybook() {
  const productLine = useStore(s => s.productLine)
  const { b, rows, sel, setSel } = useArchetypes()
  const scope = useActScope(sel)
  const [tab, setTab] = useState('overview')
  const [barrier, setBarrier] = useState('')
  const [approval, setApproval] = useState<number>()
  const [awareness, setAwareness] = useState(0.38)
  const [density, setDensity] = useState(20)
  const [activity, setActivity] = useState(25)
  const [cover, setCover] = useState(1.5)
  const [demo, setDemo] = useState(12)
  const [run, setRun] = useState(0)                 // bumped only by the Run button

  const p = useAsync(() => api.actPlaybook({
    archetype_id: sel,
    district_id: scope.district ?? null,
    micro_market_id: scope.micro ?? null,
    assumptions: {
      top_barrier: barrier || null, approval_rate: approval, awareness,
      dealer_density_pct: density, activity_uplift_pct: activity,
      months_of_cover: cover, demo_units: demo,
    },
  }), [sel, scope.district, scope.micro, productLine, run], !!sel)

  // Hold the last good response so narrowing the scope never blanks the page mid-read.
  const [last, setLast] = useState<any>()
  useEffect(() => { if (p.data) setLast(p.data) }, [p.data])
  useEffect(() => { setApproval(undefined) }, [sel, scope.district, scope.micro])
  const d = p.data ?? last
  const card = d?.cards?.find((c: any) => c.key === tab)

  return (
    <div className="grid" style={{ gap: 14 }}>
      <Async state={b}>{() => (
        <div className="scope-bar">
          <ArchetypePicker rows={rows} sel={sel} setSel={setSel} />
          <ScopePicker scope={scope} />
        </div>
      )}</Async>

      {p.err && <div className="err">{String(p.err)}</div>}
      {!d && !p.err && <div className="loading">building the playbook…</div>}

      {d && (
        <>
          <div className="verdict" style={{ borderLeftColor: BUCKET_COLOR[d.bucket] }}>
            <div>
              <b style={{ color: BUCKET_COLOR[d.bucket] }}>{d.bucket}</b> ·{' '}
              <b>{d.archetype}</b>
              {d.scope.level !== 'archetype' &&
                <> — {d.scope.level === 'district'
                  ? <b>{d.scope.district}</b>
                  : <>micro-market <b className="mono">{d.scope.micro_market_id}</b></>}</>}
              <span className="dim">
                {' '}· {fmt.count(d.scope.micromarkets)} micro-markets · {d.scope.states}
              </span>
            </div>
            <div className="dim">
              We hold <b>{(d.situation.share * 100).toFixed(1)}%</b> of{' '}
              {fmt.count(d.situation.demand)} units/yr · {d.situation.leader} leads at{' '}
              {d.situation.leader_share == null ? '—' : (d.situation.leader_share * 100).toFixed(0) + '%'}
              {' '}· product fit {(d.situation.product_fit * 100).toFixed(0)}% · dealer coverage{' '}
              {(d.situation.sales_coverage * 100).toFixed(0)}%
              <Info wide text={d.situation.scope_note} />
            </div>
          </div>

          <div className="uc-tabs">
            <button className={tab === 'overview' ? 'on' : ''} onClick={() => setTab('overview')}>
              Overview
            </button>
            <button className={tab === 'signal' ? 'on' : ''} onClick={() => setTab('signal')}>
              What customers say
            </button>
            <span className="uc-tabs-sep" />
            {d.cards.slice().sort((a: any, b2: any) => a.n - b2.n).map((c: any) => (
              <button key={c.key} className={tab === c.key ? 'on' : ''} onClick={() => setTab(c.key)}>
                <span className="uc-n sm">{c.n}</span>{TAB_LABEL[c.key] ?? c.title}
              </button>
            ))}
          </div>

          {tab === 'overview' && <Overview d={d} setTab={setTab} controls={
            <Controls {...{ d, p, barrier, setBarrier, approval, setApproval, awareness,
                            setAwareness, density, setDensity, activity, setActivity,
                            cover, setCover, demo, setDemo, setRun, productLine }} />} />}

          {tab === 'signal' && <CustomerSignal s={d.survey} />}

          {card && <UseCaseCard c={card} />}
        </>
      )}
    </div>
  )
}

/** The ten-second read: what it is worth, what customers say, what the seven do, what to do. */
function Overview({ d, setTab, controls }: { d: any; setTab: (t: string) => void; controls: React.ReactNode }) {
  const s = d.survey
  const pc = s.perception ?? {}
  const drivers = s.purchase_drivers ?? []
  const ordered = d.cards            // already barrier-ordered by the backend

  return (
    <>
      <div className="grid g4">
        <Kpi k={<span>Growth on the table<Info wide text={<>
              The plays that <b>grow</b> volume, added up. They are addable because each one
              moves a different part of the same identity — reach, approval, effort, execution,
              price, policy — so nothing is counted twice, and splitting them across seven use
              cases does not change that. Capped at unclaimed demand.</>} /></span>}
             v={fmt.count(d.total.capped_units)}
             s={d.total.capped_by ? `capped by ${d.total.capped_by}` : 'units/yr across all seven'} />
        <Kpi k={<span>Winnable from rivals<Info wide text={<>
              Volume in contests where a rival is closest and beatable. Narrower than the
              growth total because it counts only share we would take from a named rival.
              Context, never added to the plays.</>} /></span>}
             v={fmt.count(d.winnable)} s="units/yr in beatable contests" />
        <Kpi k={<span>At risk<Info text="Volume where a rival is closest and our lead is narrow." /></span>}
             v={fmt.count(d.at_risk)} s="units/yr we could lose" />
        <Kpi k="Unclaimed demand" v={fmt.count(d.total.headroom)} s="the ceiling on everything" />
      </div>

      <Card title={<span className="uc-h">What customers here are telling us<Prov p={s.provenance} /></span>}
            note={<button className="linkish" onClick={() => setTab('signal')}>see the full picture</button>}>
        <div className="signal-strip">
          <div>
            <div className="pb-k">Loudest complaint</div>
            <div className="pb-v" style={{ color: 'var(--bad)' }} title={pc.complaint_evidence}>
              {pc.top_complaint}</div>
            <div className="dim">so <b>{ordered[0]?.title}</b> leads the plan below</div>
          </div>
          <div>
            <div className="pb-k">What they weigh most</div>
            <div className="pb-v" title={drivers[0]?.evidence}>{drivers[0]?.driver}</div>
            <div className="dim">{drivers[0]?.implication}</div>
          </div>
          <div>
            <div className="pb-k">How they feel about us</div>
            <div className="nps" style={{ margin: '5px 0 6px' }}>
              <i style={{ width: `${pc.satisfied_pct}%`, background: 'var(--good)' }} />
              <i style={{ width: `${pc.neutral_pct}%`, background: 'var(--border-strong)' }} />
              <i style={{ width: `${pc.detractor_pct}%`, background: 'var(--bad)' }} />
            </div>
            <div className="dim">
              <span style={{ color: 'var(--good)' }}>{pc.satisfied_pct}% happy</span> ·{' '}
              <span style={{ color: 'var(--bad)' }}>{pc.detractor_pct}% unhappy</span>
            </div>
          </div>
        </div>
      </Card>

      <Card title="The seven use cases"
            note="in the order this scope's customers argue for · click one to open it">
        <div className="uc-tiles">
          {ordered.map((c: any) => (
            <button key={c.key} className="uc-tile" onClick={() => setTab(c.key)}>
              <div className="uc-tile-head">
                <span className="uc-n">{c.n}</span>{c.title}
              </div>
              <div className="uc-tile-v">
                {c.units ? <b style={{ color: 'var(--good)' }}>+{fmt.count(c.units)} units/yr</b>
                  : c.key === 'customer' || c.key === 'inventory'
                    ? <span className="dim">aims what the others create</span>
                    : <span className="dim">no priced play here</span>}
              </div>
              <div className="dim">{c.summary}</div>
            </button>
          ))}
        </div>
      </Card>

      <Card title={<span className="uc-h">The {d.action_list.length} things to do here<Info wide text={<>
              The seven use cases stitched into one sequence, ordered by when the work has to
              start rather than by what it is worth — a touchpoint appointed in month four
              cannot host a demo in month two.</>} /></span>}
            note="execution order">
        <ol className="action-list">
          {d.action_list.map((x: any) => (
            <li key={x.n}>
              <div className="action-head">
                <b>{x.action}</b>
                {!!x.worth_units && <span className="uc-tag good">+{fmt.count(x.worth_units)} units/yr</span>}
              </div>
              <div className="dim">{x.use_case} · {x.when} · {x.owner}</div>
            </li>
          ))}
        </ol>
      </Card>

      <details className="fold">
        <summary>Track playbook performance — baseline → target per use case</summary>
        <Card title={<span className="uc-h">Track playbook performance<Info wide text={<>
                Every target is the play arithmetic restated as a metric, not a second guess.
                The actuals column stays empty until ITL supplies two years of activity,
                enquiry and delivery history.</>} /></span>}>
          <div className="uc-scroll">
            <table>
              <thead><tr>
                <th>Use case</th><th>Metric</th>
                <th style={{ textAlign: 'right' }}>Today</th>
                <th style={{ textAlign: 'right' }}>Target</th>
                <th style={{ textAlign: 'right' }}>Units at stake</th>
                <th>Review</th><th>Actual</th>
              </tr></thead>
              <tbody>
                {d.tracking.map((t: any) => (
                  <tr key={t.key}>
                    <td>{t.use_case}</td><td className="dim">{t.metric}</td>
                    <td style={{ textAlign: 'right' }}>{cell(t.baseline_now)}</td>
                    <td style={{ textAlign: 'right' }}><b>{cell(t.target)}</b></td>
                    <td style={{ textAlign: 'right' }}>
                      {t.units_at_stake ? fmt.count(t.units_at_stake) : '—'}</td>
                    <td className="dim">{t.review_cadence}</td>
                    <td><span className="pill pill-client">ITL pending</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </details>

      <details className="fold">
        <summary>Assumptions — the inputs that are yours, not the data's</summary>
        <Card title="Your overrides"
              note="the barrier re-orders the seven · it never changes a number">
          {controls}
        </Card>
      </details>

      <div className="conf-key">
        {Object.entries(CONF).map(([k, c]) => (
          <span key={k}>
            <i className="pill conf" style={{ borderColor: c.colour, color: c.colour }}>{c.label}</i>
            {c.why}
          </span>
        ))}
      </div>
    </>
  )
}

function Controls(a: any) {
  const { d, p } = a
  return (
    <>
      <div className="row" style={{ gap: 10, marginBottom: 10 }}>
        <span className="dim" style={{ fontSize: 12, width: 108 }}>Top barrier</span>
        <select value={a.barrier} onChange={e => a.setBarrier(e.target.value)}>
          {BARRIERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </div>
      <div className="slider-row">
        <span>Loan approval</span>
        <input type="range" min={0.4} max={0.95} step={0.01}
               value={a.approval ?? Math.min(d.situation.approval_rate + 0.05, 0.95)}
               onChange={e => a.setApproval(Number(e.target.value))} />
        <span className="mono n" style={{ textAlign: 'right' }}>
          {((a.approval ?? Math.min(d.situation.approval_rate + 0.05, 0.95)) * 100).toFixed(0)}%
        </span>
      </div>
      <div className="slider-row">
        <span>Awareness</span>
        <input type="range" min={0} max={1} step={0.02} value={a.awareness}
               onChange={e => a.setAwareness(Number(e.target.value))} />
        <span className="mono n" style={{ textAlign: 'right' }}>{(a.awareness * 100).toFixed(0)}%</span>
      </div>
      <div className="slider-row">
        <span>Network expansion</span>
        <input type="range" min={0} max={100} step={5} value={a.density}
               onChange={e => a.setDensity(Number(e.target.value))} />
        <span className="mono n" style={{ textAlign: 'right' }}>+{a.density}%</span>
      </div>
      <div className="slider-row">
        <span>BD activity push</span>
        <input type="range" min={0} max={100} step={5} value={a.activity}
               onChange={e => a.setActivity(Number(e.target.value))} />
        <span className="mono n" style={{ textAlign: 'right' }}>+{a.activity}%</span>
      </div>
      <div className="slider-row">
        <span>Stock cover</span>
        <input type="range" min={0.5} max={4} step={0.5} value={a.cover}
               onChange={e => a.setCover(Number(e.target.value))} />
        <span className="mono n" style={{ textAlign: 'right' }}>{a.cover}m</span>
      </div>
      {a.productLine === 'tractors' && (
        <div className="slider-row">
          <span>Demo fleet</span>
          <input type="range" min={2} max={60} step={2} value={a.demo}
                 onChange={e => a.setDemo(Number(e.target.value))} />
          <span className="mono n" style={{ textAlign: 'right' }}>{a.demo}</span>
        </div>
      )}
      <p className="note" style={{ marginTop: 8 }}>
        Loan approval across this scope's villages is today{' '}
        <b>{(d.situation.approval_rate * 100).toFixed(0)}%</b>.
      </p>
      <button className="btn-primary" onClick={() => a.setRun((r: number) => r + 1)}>
        {p.loading ? 'Running…' : 'Re-run the playbook'}
      </button>
    </>
  )
}
