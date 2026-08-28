import React, { useEffect, useState } from 'react'
import { api, fmt } from '../lib/api'
import { Card, Async, useAsync, Kpi, Info } from './common'

/**
 * DEFINE · Configure — edit the categories an archetype is built from.
 *
 * The old version could carve out one archetype from a single rule and called it
 * "re-cluster", which it was not: it relabelled matching micro-markets and blanked the very
 * columns they were matched on, so a custom archetype dropped out of every join in Review,
 * Plan and Act.
 *
 * This edits the taxonomy itself — the TIV tiers, HP belts and crop categories an archetype
 * is made of — and saving re-labels all 23,389 micro-markets against it in about a second.
 * The archetype ids it produces are real category codes, so a customised taxonomy keeps
 * working in Review, Plan and Act instead of vanishing behind a `custom-` prefix.
 *
 * Zones are shown but not editable: they are the published ICAR agro-climatic scheme, and
 * a client-invented zone would no longer mean what the soil and growing-season data say.
 */

type Tier = { name: string; code?: string; upto: number }
type Belt = { name: string; code?: string; upto: number | null }
type Zone = { id: string; name: string; subzones: string[] }
type Crop = { name: string; share_column?: string; values?: string[] }

export default function Configure() {
  const t = useAsync(() => api.taxonomy(), [])
  const [tax, setTax] = useState<any>()
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string>()
  const [result, setResult] = useState<any>()

  useEffect(() => { if (t.data && !tax) setTax(structuredClone(t.data.taxonomy)) }, [t.data])

  const edit = (patch: any) => { setTax({ ...tax, ...patch }); setResult(undefined) }

  const save = async () => {
    setBusy(true); setErr(undefined)
    try { setResult(await api.saveTaxonomy(tax)) }
    catch (e: any) { setErr(String(e)) } finally { setBusy(false) }
  }
  const reset = async () => {
    setBusy(true); setErr(undefined)
    try {
      const r = await api.resetTaxonomy()
      setTax(structuredClone(r.taxonomy)); setResult(r)
    } catch (e: any) { setErr(String(e)) } finally { setBusy(false) }
  }

  if (!tax) return <Async state={t}>{() => <div className="loading">loading…</div>}</Async>

  const tiers: Tier[] = tax.tiv_tiers
  const belts: Belt[] = tax.hp_belts
  const zones: Zone[] = tax.zones
  const crops: Crop[] = tax.crops ?? []
  const covered = new Set(crops.flatMap(c => c.values ?? [c.name.toLowerCase()]))
  const uncovered = (t.data?.crops_present ?? []).filter((c: string) => !covered.has(c))

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="stage-note">
        The categories every archetype is built from. Edit them here and <b>Save</b> re-labels
        every micro-market against the new definition — the archetypes on the previous tab,
        and everything downstream that reads them, follow within a second.
        {t.data?.customised
          ? <span className="pill pill-primary" style={{ marginLeft: 8 }}>customised</span>
          : <span className="pill pill-real" style={{ marginLeft: 8 }}>shipped defaults</span>}
      </div>

      <div className="grid g4">
        <Kpi k={<span>Archetypes now<Info text={<>What the taxonomy in force produces.
              Saving an edit recomputes this immediately.</>} /></span>}
             v={result?.n_archetypes ?? t.data?.n_archetypes}
             s={result?.was != null && result.was !== result.n_archetypes
                 ? `was ${result.was}` : (t.data?.describes ?? '')} />
        <Kpi k="TIV tiers" v={tiers.length} s={tiers.map(x => x.name).join(' · ')} />
        <Kpi k="HP belts" v={belts.length} s={belts.map(x => x.name).join(' · ')} />
        <Kpi k="Crop categories" v={crops.length}
             s={uncovered.length ? `${uncovered.length} crop${uncovered.length > 1 ? 's' : ''} not categorised`
                                 : 'every crop in the data is covered'} />
      </div>

      <div className="split">
        <Card title={<>TIV tiers
              <Info wide text={<>How the fleet is banded. <b>Up to</b> is a share of
                micro-markets, so 0.33 means "the smallest third by tractors". The last tier
                must end at 1.00. Three tiers is what the client asked for — high, medium,
                low — and the cuts are yours to move.</>} /></>}
              note="quantile cuts on tractors in the field">
          <table>
            <thead><tr><th>Name</th><th>Code</th><th style={{ textAlign: 'right' }}>Up to</th><th></th></tr></thead>
            <tbody>
              {tiers.map((row, i) => (
                <tr key={i}>
                  <td><input value={row.name} style={{ width: 110 }}
                             onChange={e => { const v = [...tiers]; v[i] = { ...row, name: e.target.value }; edit({ tiv_tiers: v }) }} /></td>
                  <td><input value={row.code ?? ''} style={{ width: 46 }}
                             onChange={e => { const v = [...tiers]; v[i] = { ...row, code: e.target.value }; edit({ tiv_tiers: v }) }} /></td>
                  <td style={{ textAlign: 'right' }}>
                    <input type="number" min={0} max={1} step={0.01} value={row.upto} style={{ width: 72 }}
                           onChange={e => { const v = [...tiers]; v[i] = { ...row, upto: Number(e.target.value) }; edit({ tiv_tiers: v }) }} /></td>
                  <td><button onClick={() => edit({ tiv_tiers: tiers.filter((_, j) => j !== i) })}>Delete</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          <button style={{ marginTop: 8 }}
                  onClick={() => edit({ tiv_tiers: [...tiers, { name: 'New tier', code: 'N', upto: 1 }] })}>
            Add tier
          </button>
        </Card>

        <Card title={<>HP belts
              <Info wide text={<>Bands on the micro-market's fleet-weighted mean tractor HP.
                <b> Up to</b> is an HP number; leave the top band empty for "everything
                above". The belt decides which implements physically fit, which is why it is
                an archetype axis rather than a display field.</>} /></>}
              note="bands on mean tractor HP">
          <table>
            <thead><tr><th>Name</th><th>Code</th><th style={{ textAlign: 'right' }}>Up to HP</th><th></th></tr></thead>
            <tbody>
              {belts.map((row, i) => (
                <tr key={i}>
                  <td><input value={row.name} style={{ width: 110 }}
                             onChange={e => { const v = [...belts]; v[i] = { ...row, name: e.target.value }; edit({ hp_belts: v }) }} /></td>
                  <td><input value={row.code ?? ''} style={{ width: 56 }}
                             onChange={e => { const v = [...belts]; v[i] = { ...row, code: e.target.value }; edit({ hp_belts: v }) }} /></td>
                  <td style={{ textAlign: 'right' }}>
                    <input type="number" value={row.upto ?? ''} placeholder="open" style={{ width: 72 }}
                           onChange={e => { const v = [...belts]; v[i] = { ...row, upto: e.target.value === '' ? null : Number(e.target.value) }; edit({ hp_belts: v }) }} /></td>
                  <td><button onClick={() => edit({ hp_belts: belts.filter((_, j) => j !== i) })}>Delete</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          <button style={{ marginTop: 8 }}
                  onClick={() => edit({ hp_belts: [...belts, { name: 'New belt', code: 'NEW', upto: null }] })}>
            Add belt
          </button>
        </Card>
      </div>

      <Card title={<>Dominant crop
            <Info wide text={<>The vocabulary archetypes are named from. <b>Covers</b> lists the
              raw crops in the data this category stands for — put several in one row to merge
              them (a "Cereals" category covering wheat, rice and maize), or delete a row to
              stop naming archetypes by that crop, in which case they take their next-biggest
              one. A crop belongs to at most one category.</>} /></>}
            note="what an archetype is named after">
        {uncovered.length > 0 && (
          <div className="stage-note" style={{ marginBottom: 10 }}>
            <b>Not categorised:</b> {uncovered.join(', ')}. Archetypes where one of these is
            the biggest crop will be named after their next-biggest instead, or read
            <i> Mixed</i> if they have none. Add them to a category to name them.
          </div>
        )}
        <table>
          <thead><tr>
            <th style={{ width: 180 }}>Category</th><th>Covers</th>
            <th style={{ width: 190 }}>Add a crop</th><th style={{ width: 90 }}></th>
          </tr></thead>
          <tbody>
            {crops.map((c, i) => {
              const vals = c.values ?? [c.name.toLowerCase()]
              return (
                <tr key={i}>
                  <td><input value={c.name} style={{ width: 160 }}
                             onChange={e => { const v = [...crops]; v[i] = { ...c, name: e.target.value }; edit({ crops: v }) }} /></td>
                  <td className="dim" style={{ fontSize: 12 }}>
                    {vals.length ? vals.map(x => (
                      <span key={x} className="pill" style={{ marginRight: 6 }}>
                        {x}
                        <a role="button" title={`remove ${x}`} style={{ marginLeft: 5, cursor: 'pointer' }}
                           onClick={() => { const v = [...crops]
                             v[i] = { ...c, values: vals.filter(y => y !== x) }; edit({ crops: v }) }}>×</a>
                      </span>
                    )) : <i>nothing — this category names no archetype</i>}
                  </td>
                  <td>
                    <select value="" onChange={e => {
                      const raw = e.target.value
                      if (!raw) return
                      // A crop belongs to one category, so pulling it here takes it off any other.
                      const v = crops.map(x => ({ ...x, values: (x.values ?? [x.name.toLowerCase()]).filter(y => y !== raw) }))
                      v[i] = { ...v[i], values: [...(v[i].values ?? []), raw].sort() }
                      edit({ crops: v })
                    }}>
                      <option value="">add a crop…</option>
                      {(t.data?.crops_present ?? []).filter((x: string) => !vals.includes(x))
                        .map((x: string) => <option key={x} value={x}>{x}</option>)}
                    </select>
                  </td>
                  <td><button onClick={() => edit({ crops: crops.filter((_, j) => j !== i) })}>Delete</button></td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <div className="row" style={{ gap: 10, marginTop: 10 }}>
          <button onClick={() => edit({ crops: [...crops, { name: 'New category', values: [] }] })}>
            Add category
          </button>
          <span className="dim" style={{ fontSize: 11 }}>
            Merging = put both crops in one row. Deleting a row leaves its crops uncategorised.
          </span>
        </div>
      </Card>

      <Card title={<>Zones
            <Info wide text={<>The published ICAR agro-climatic scheme, and the third axis of
              every archetype. It is shown rather than edited: the soil, climate and
              growing-season figures on the profile panel are measured against these
              boundaries, so a redrawn zone would carry data that no longer describes it.</>} /></>}
            note="fixed · the ICAR agro-climatic scheme">
        <table>
          <thead><tr><th>Id</th><th>Name</th><th>Sub-zones</th></tr></thead>
          <tbody>
            {zones.map(z => (
              <tr key={z.id}>
                <td>{z.id}</td>
                <td>{z.name}</td>
                <td className="dim" style={{ fontSize: 12 }}>{z.subzones.join(', ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <div className="row" style={{ gap: 10 }}>
        <button onClick={save} disabled={busy}
                style={{ padding: '8px 18px', borderRadius: 8, border: '1px solid var(--accent)',
                         background: 'var(--accent)', color: '#fff',
                         cursor: 'pointer', fontWeight: 600 }}>
          {busy ? 'Re-labelling…' : 'Save & re-label'}
        </button>
        <button onClick={reset} disabled={busy}
                style={{ padding: '8px 18px', borderRadius: 8, border: '1px solid var(--border-strong)',
                         background: 'var(--panel)', color: 'var(--text)', cursor: 'pointer' }}>
          Reset to shipped
        </button>
        <span className="dim" style={{ fontSize: 11 }}>
          Saving re-labels all micro-markets. It does not regroup villages — which villages
          form a micro-market is fixed by the pipeline.
        </span>
      </div>

      {err && <div className="err">{err}</div>}

      {result && (
        <Card title={<>{result.n_archetypes} archetypes after re-labelling
              <Info text={<>The same table the Archetypes tab shows, recomputed against the
                taxonomy you just saved.</>} /></>}
              note={`${fmt.count(result.moved_micromarkets ?? 0)} micro-markets re-labelled`}>
          <div style={{ maxHeight: 320, overflow: 'auto' }}>
            <table>
              <thead><tr>
                <th>Archetype</th><th>Zone</th><th>HP belt</th><th>TIV tier</th>
                <th style={{ textAlign: 'right' }}>Micro-mkts</th>
                <th style={{ textAlign: 'right' }}>TIV</th>
              </tr></thead>
              <tbody>
                {(result.archetypes ?? []).map((r: any) => (
                  <tr key={r.archetype_id}>
                    <td>{r.base_name}<div className="dim" style={{ fontSize: 11 }}>{r.archetype_id}</div></td>
                    <td className="dim" style={{ fontSize: 12 }}>{r.zone} {r.zone_name}</td>
                    <td>{r.hp_belt}</td>
                    <td>{r.tiv_tier}</td>
                    <td style={{ textAlign: 'right' }}>{fmt.count(r.n_micromarkets)}</td>
                    <td style={{ textAlign: 'right' }}>{fmt.units(r.tiv)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}
