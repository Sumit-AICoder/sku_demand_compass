import React from 'react'
import { BarChart, Bar, LineChart, Line, PieChart, Pie, ScatterChart, Scatter,
         XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer, Cell, Legend,
         ZAxis } from 'recharts'

const PALETTE = ['var(--c1)', 'var(--c3)', 'var(--c2)', 'var(--c4)',
                 'var(--c5)', 'var(--c6)', 'var(--c7)', 'var(--c8)']

export interface Block {
  type: 'table' | 'chart'
  title?: string
  columns?: string[]
  rows?: Record<string, any>[]
  kind?: 'bar' | 'line' | 'pie' | 'scatter' | 'table'
  x?: string
  series?: string[]
  data?: Record<string, any>[]
}

const label = (s: string) =>
  s.replace(/_/g, ' ').replace(/\bpct\b/i, '%').replace(/^./, c => c.toUpperCase())

const num = (v: any) =>
  typeof v === 'number'
    ? (Math.abs(v) >= 1000 ? Math.round(v).toLocaleString('en-IN')
       : Number.isInteger(v) ? String(v) : v.toFixed(2))
    : (v ?? '—')

/** Renders whatever form the assistant chose for its answer. */
export default function ChatBlocks({ blocks }: { blocks?: Block[] }) {
  if (!blocks?.length) return null
  return (
    <div className="cblocks">
      {blocks.map((b, i) =>
        b.type === 'table' ? <BlockTable key={i} b={b} /> : <BlockChart key={i} b={b} />)}
    </div>
  )
}

function BlockTable({ b }: { b: Block }) {
  const cols = b.columns ?? Object.keys(b.rows?.[0] ?? {})
  const rows = b.rows ?? []
  const csv = () => {
    const head = cols.join(',')
    const body = rows.map(r => cols.map(c => JSON.stringify(r[c] ?? '')).join(',')).join('\n')
    navigator.clipboard?.writeText(`${head}\n${body}`)
  }
  return (
    <figure className="cblock">
      <figcaption>
        <span>{b.title ?? 'Results'}</span>
        <button className="link-btn tiny" onClick={csv} title="Copy as CSV">copy</button>
      </figcaption>
      <div className="cblock-scroll">
        <table className="ctable">
          <thead><tr>{cols.map(c => (
            <th key={c} className={isNumCol(rows, c) ? 'n' : ''}>{label(c)}</th>))}</tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>{cols.map(c => (
                <td key={c} className={isNumCol(rows, c) ? 'n' : ''}>{num(r[c])}</td>))}</tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length >= 15 && <div className="cnote">showing first {rows.length} rows</div>}
    </figure>
  )
}

function isNumCol(rows: Record<string, any>[], c: string) {
  return rows.slice(0, 5).every(r => typeof r[c] === 'number' || r[c] == null)
}

function BlockChart({ b }: { b: Block }) {
  const data = b.data ?? []
  const series = b.series ?? []
  const x = b.x ?? 'name'
  if (!data.length || !series.length) return null

  // Long category names are the norm here (district and product names), so bar charts
  // lie on their side — rotated labels are far harder to read at this width.
  const H = Math.max(190, Math.min(360, data.length * 26 + 60))

  const body = () => {
    switch (b.kind) {
      case 'line':
        return (
          <LineChart data={data} margin={{ left: -14, right: 12, top: 6 }}>
            <CartesianGrid stroke="var(--border)" vertical={false} />
            <XAxis dataKey={x} tick={{ fontSize: 10 }} minTickGap={16} />
            <YAxis tick={{ fontSize: 10 }} />
            <Tooltip formatter={(v: any) => num(v)} />
            {series.length > 1 && <Legend wrapperStyle={{ fontSize: 10 }} />}
            {series.map((s, i) => (
              <Line key={s} type="monotone" dataKey={s} name={label(s)}
                    stroke={PALETTE[i % PALETTE.length]} strokeWidth={2}
                    dot={{ r: 2 }} />
            ))}
          </LineChart>
        )
      case 'pie':
        return (
          <PieChart>
            <Pie data={data} dataKey={series[0]} nameKey={x}
                 innerRadius={44} outerRadius={72} paddingAngle={2}>
              {data.map((_, i) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
            </Pie>
            <Tooltip formatter={(v: any) => num(v)} />
            <Legend wrapperStyle={{ fontSize: 10 }} />
          </PieChart>
        )
      case 'scatter':
        return (
          <ScatterChart margin={{ left: -8, right: 14, top: 8, bottom: 14 }}>
            <CartesianGrid stroke="var(--border)" />
            <XAxis type="number" dataKey={x} name={label(x)} tick={{ fontSize: 10 }} />
            <YAxis type="number" dataKey={series[0]} name={label(series[0])}
                   tick={{ fontSize: 10 }} />
            <ZAxis range={[50, 50]} />
            <Tooltip cursor={{ strokeDasharray: '3 3' }} formatter={(v: any) => num(v)} />
            <Scatter data={data} fill="var(--c1)" fillOpacity={0.7} />
          </ScatterChart>
        )
      default:
        return (
          <BarChart data={data} layout="vertical" margin={{ left: 8, right: 16 }}>
            <CartesianGrid stroke="var(--border)" horizontal={false} />
            <XAxis type="number" tick={{ fontSize: 10 }} />
            <YAxis type="category" dataKey={x} width={124} tick={{ fontSize: 9.5 }} />
            <Tooltip formatter={(v: any) => num(v)} />
            {series.length > 1 && <Legend wrapperStyle={{ fontSize: 10 }} />}
            {series.map((s, i) => (
              <Bar key={s} dataKey={s} name={label(s)} radius={[0, 3, 3, 0]}
                   fill={PALETTE[i % PALETTE.length]} />
            ))}
          </BarChart>
        )
    }
  }

  return (
    <figure className="cblock">
      {b.title && <figcaption><span>{b.title}</span></figcaption>}
      <ResponsiveContainer width="100%" height={b.kind === 'pie' ? 210 : H}>
        {body()}
      </ResponsiveContainer>
    </figure>
  )
}
