import React, { useState } from 'react'
import { useAsync } from './common'

const BASE = '/api'

/**
 * Plain-English briefing for a view.
 *
 * The text is always generated from a fact pack computed server-side, so the numbers
 * in it are the same numbers in the charts below. When a Claude credential is
 * configured the same fact pack is re-written by Claude; the badge says which, because
 * a reader deserves to know whether they're reading a template or a model.
 */
export default function Narrative({ view, params, compact }: {
  view: string
  params?: Record<string, string | number | undefined>
  compact?: boolean
}) {
  const [showFacts, setShowFacts] = useState(false)
  const qs = new URLSearchParams()
  Object.entries(params ?? {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
  })
  const s = useAsync<any>(
    () => fetch(`${BASE}/narrative/${view}${qs.toString() ? `?${qs}` : ''}`)
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`${r.status}`))),
    [view, qs.toString()])

  if (s.err) return null
  if (s.loading) return <div className="narrative loading-thin">Preparing summary…</div>
  if (!s.data) return null

  return (
    <div className={`narrative${compact ? ' compact' : ''}`}>
      <div className="narrative-head">
        <span className="narrative-title">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
            <path d="M12 2l2.09 6.26L20 9.27l-4 3.9.94 5.83L12 16.5l-4.94 2.5L8 13.17l-4-3.9 5.91-1.01z" />
          </svg>
          What this means
        </span>
        <span className="narrative-badges">
          <span className={`badge ${s.data.source === 'computed' ? 'prior' : 'ucm'}`}
                title={s.data.source === 'computed'
                  ? 'Written from the computed numbers by a template'
                  : `Written by ${s.data.source} from the same computed numbers`}>
            {s.data.source === 'computed' ? 'auto-generated'
              : s.data.source === 'azure' ? 'AI written · GPT-4.1'
              : 'AI written · Claude'}
          </span>
          <button className="link-btn" onClick={() => setShowFacts(v => !v)}>
            {showFacts ? 'hide numbers' : 'show numbers'}
          </button>
        </span>
      </div>
      <p className="narrative-text">{s.data.text}</p>
      {showFacts && (
        <pre className="narrative-facts">{JSON.stringify(s.data.facts, null, 2)}</pre>
      )}
    </div>
  )
}
