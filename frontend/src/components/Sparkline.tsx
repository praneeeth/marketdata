import { useId } from 'react'

interface SparklineProps {
  /** Value series (e.g. the last 20 closes/NAVs), min-max normalised to the canvas height */
  data: number[]
  width?: number
  height?: number
  /** Line colour: currentColor or any CSS colour (including hsl(var(--xx))), readable in light and dark themes */
  stroke?: string
  /** When given, renders a gradient area (semi-transparent at the top -> transparent at the bottom); otherwise just the line */
  fill?: string
  className?: string
}

/**
 * Minimal trend line with no third-party chart library: SVG polyline + optional gradient area + end-point circle.
 * The viewBox matches width/height exactly, and with preserveAspectRatio="none" + width="100%"
 * the parent controls the rendered width; vector-effect="non-scaling-stroke" keeps the line width from stretching.
 */
export default function Sparkline({
  data,
  width = 100,
  height = 28,
  stroke = 'currentColor',
  fill,
  className,
}: SparklineProps) {
  const gradId = useId()
  const vals = (data || []).filter((v) => typeof v === 'number' && Number.isFinite(v))
  if (vals.length < 2) return null

  let min = Math.min(...vals)
  let max = Math.max(...vals)
  if (max - min < 1e-9) {
    const pad = Math.abs(max) * 0.02 || 1
    max += pad
    min -= pad
  }

  const n = vals.length
  const padY = Math.max(1.5, height * 0.12)
  const innerH = height - padY * 2
  const xAt = (i: number) => (width * i) / (n - 1)
  const yAt = (v: number) => padY + innerH - (innerH * (v - min)) / (max - min)
  const points = vals.map((v, i) => [xAt(i), yAt(v)] as const)
  const pointsAttr = points.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(' ')
  const [lastX, lastY] = points[n - 1]
  const fillColor = fill || stroke
  const gradientId = `spark-fill-${gradId.replace(/[^a-zA-Z0-9_-]/g, '')}`

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      width="100%"
      height={height}
      className={className}
      role="img"
      aria-hidden="true"
    >
      {fill && (
        <>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={fillColor} stopOpacity={0.32} />
              <stop offset="100%" stopColor={fillColor} stopOpacity={0} />
            </linearGradient>
          </defs>
          <polygon
            points={`${xAt(0).toFixed(2)},${height} ${pointsAttr} ${xAt(n - 1).toFixed(2)},${height}`}
            fill={`url(#${gradientId})`}
            stroke="none"
          />
        </>
      )}
      <polyline
        points={pointsAttr}
        fill="none"
        stroke={stroke}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      <circle cx={lastX} cy={lastY} r={2.2} fill={stroke} />
    </svg>
  )
}
