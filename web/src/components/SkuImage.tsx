import React, { useEffect, useState } from 'react'

interface Meta { file: string; title: string; page: string; licence: string; artist: string }
let CACHE: Record<string, Meta> | null = null
let PENDING: Promise<Record<string, Meta>> | null = null

function load(): Promise<Record<string, Meta>> {
  if (CACHE) return Promise.resolve(CACHE)
  if (!PENDING) {
    PENDING = fetch('/api/sku/images').then(r => r.json())
      .then(d => { CACHE = d; return d }).catch(() => ({}))
  }
  return PENDING
}

/**
 * Product imagery.
 *
 * Photographs are openly licensed (Wikimedia Commons) and carry their licence and
 * author, because most of those licences require attribution — dropping it would
 * breach the terms, not merely be impolite.
 *
 * Where no suitable photograph exists, a drawn icon is used rather than a loosely
 * related photo: a picture of the wrong machine is worse than no picture, because it
 * is read as fact.
 */
export default function SkuImage({ skuId, category, size = 56, showCredit }: {
  skuId: string; category?: string; size?: number; showCredit?: boolean
}) {
  const [meta, setMeta] = useState<Meta | null | undefined>(undefined)
  const [failed, setFailed] = useState(false)
  useEffect(() => { load().then(d => setMeta(d[skuId] ?? null)) }, [skuId])

  if (meta === undefined) return <div className="skuimg ph" style={{ width: size, height: size }} />

  if (meta && !failed) {
    return (
      <figure className="skuimg-fig" style={{ width: size }}>
        <img className="skuimg" src={`/sku/${meta.file}`} alt={meta.title.replace(/^File:/, '')}
             style={{ width: size, height: size }} loading="lazy"
             onError={() => setFailed(true)} />
        {showCredit && (
          <figcaption className="credit">
            <a href={meta.page} target="_blank" rel="noreferrer noopener">
              {meta.licence}
            </a>{meta.artist ? ` · ${meta.artist}` : ''}
          </figcaption>
        )}
      </figure>
    )
  }
  return <CategoryIcon category={category} size={size} />
}

const ICONS: Record<string, React.ReactNode> = {
  tillage: <><path d="M4 15h16" /><path d="M7 15v4M12 15v4M17 15v4" /><path d="M6 9h12l-1 6H7z" /></>,
  sowing: <><circle cx="7" cy="18" r="1.6" /><circle cx="12" cy="18" r="1.6" /><circle cx="17" cy="18" r="1.6" /><path d="M5 6h14v6H5z" /><path d="M7 12v3M12 12v3M17 12v3" /></>,
  crop_protection: <><path d="M5 17h14" /><path d="M7 17v-5h10v5" /><path d="M12 12V5" /><path d="M9 5h6" /><path d="M8 20l1-2M12 20v-2M16 20l-1-2" /></>,
  irrigation: <><path d="M12 3c3 4 5 6.5 5 9a5 5 0 01-10 0c0-2.5 2-5 5-9z" /><path d="M4 20h16" /></>,
  harvesting: <><path d="M4 18h16" /><circle cx="8" cy="18" r="2" /><circle cx="17" cy="18" r="2" /><path d="M5 15h13l-2-6H7z" /></>,
  residue: <><path d="M4 19h16" /><circle cx="9" cy="14" r="4" /><circle cx="17" cy="15" r="3" /></>,
  post_harvest: <><path d="M5 20h14V9H5z" /><path d="M5 9l7-5 7 5" /><path d="M10 20v-6h4v6" /></>,
  haulage: <><circle cx="7" cy="18" r="2" /><circle cx="17" cy="18" r="2" /><path d="M3 8h13v8H3z" /><path d="M16 12h4l1 4h-5z" /></>,
  precision: <><circle cx="12" cy="12" r="3" /><path d="M12 2v4M12 18v4M2 12h4M18 12h4" /><circle cx="12" cy="12" r="8" /></>,
}

function CategoryIcon({ category, size = 56 }: { category?: string; size?: number }) {
  return (
    <div className="skuimg icon" style={{ width: size, height: size }}
         title="No openly-licensed photograph available for this product">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.4}
           strokeLinecap="round" strokeLinejoin="round"
           style={{ width: size * 0.62, height: size * 0.62 }}>
        {ICONS[category ?? ''] ?? ICONS.tillage}
      </svg>
    </div>
  )
}
