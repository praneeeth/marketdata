import { useLayoutEffect, useRef, useState } from 'react'
import type { BenchmarkCurvePoint } from '@panwatch/api'

interface BenchChartProps {
  curve: BenchmarkCurvePoint[]
  height?: number
  className?: string
}

// 3 inner reference lines (evenly spaced; not the top/bottom borders)
const GRID_FRACS = [0.2, 0.5, 0.8]

function BenchChartSvg({
  points,
  width,
  height,
}: {
  points: BenchmarkCurvePoint[]
  width: number
  height: number
}) {
  const padLeft = 2
  const padRight = 40 // room for the % tick labels on the right
  const padTop = 12
  const padBottom = 12
  const innerW = Math.max(10, width - padLeft - padRight)
  const innerH = Math.max(10, height - padTop - padBottom)

  const allVals: number[] = []
  for (const p of points) {
    allVals.push(p.portfolio, p.benchmark)
  }
  let min = Math.min(...allVals)
  let max = Math.max(...allVals)
  if (max - min < 1e-6) {
    max += 1
    min -= 1
  }

  const n = points.length
  const xAt = (i: number) => padLeft + (innerW * i) / (n - 1)
  const yAt = (v: number) => padTop + innerH - (innerH * (v - min)) / (max - min)

  const portfolioPts = points.map((p, i) => [xAt(i), yAt(p.portfolio)] as const)
  const benchmarkPts = points.map((p, i) => [xAt(i), yAt(p.benchmark)] as const)
  const portfolioAttr = portfolioPts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const benchmarkAttr = benchmarkPts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const [x0, y0] = portfolioPts[0]
  const [xN, yN] = portfolioPts[n - 1]
  const baseline = padTop + innerH
  const areaAttr = `${xAt(0).toFixed(1)},${baseline.toFixed(1)} ${portfolioAttr} ${xAt(n - 1).toFixed(1)},${baseline.toFixed(1)}`

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Portfolio NAV vs benchmark chart">
      <defs>
        <linearGradient id="benchchart-area" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity={0.22} />
          <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity={0} />
        </linearGradient>
      </defs>

      {/* Grid lines + % ticks on the right (cumulative return with the curve's start = 100) */}
      {GRID_FRACS.map((frac) => {
        const y = padTop + innerH * frac
        const v = min + (max - min) * (1 - frac)
        const pctVal = v - 100
        const label = `${pctVal >= 0 ? '+' : ''}${pctVal.toFixed(1)}%`
        return (
          <g key={frac}>
            <line
              x1={padLeft}
              x2={padLeft + innerW}
              y1={y}
              y2={y}
              stroke="hsl(var(--border))"
              strokeWidth={1}
              strokeDasharray="4 4"
            />
            <text
              x={padLeft + innerW + 6}
              y={y}
              dominantBaseline="middle"
              fontSize={10}
              fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
              fill="hsl(var(--muted-foreground))"
            >
              {label}
            </text>
          </g>
        )
      })}

      {/* Benchmark: dashed (neutral colour) */}
      <polyline
        points={benchmarkAttr}
        fill="none"
        stroke="hsl(var(--muted-foreground))"
        strokeWidth={1.5}
        strokeDasharray="5 4"
        strokeLinejoin="round"
        strokeLinecap="round"
      />

      {/* Portfolio: solid line + light area (the main line) */}
      <polygon points={areaAttr} fill="url(#benchchart-area)" stroke="none" />
      <polyline
        points={portfolioAttr}
        fill="none"
        stroke="hsl(var(--primary))"
        strokeWidth={2}
        strokeLinejoin="round"
        strokeLinecap="round"
      />

      {/* End-point circles (portfolio line start and end) */}
      <circle cx={x0} cy={y0} r={4} fill="hsl(var(--card))" />
      <circle cx={x0} cy={y0} r={2.5} fill="hsl(var(--primary))" />
      <circle cx={xN} cy={yN} r={4} fill="hsl(var(--card))" />
      <circle cx={xN} cy={yN} r={2.5} fill="hsl(var(--primary))" />
    </svg>
  )
}

/**
 * Portfolio NAV vs benchmark two-line chart, with no third-party chart library.
 * Portfolio (primary) solid line + light area, benchmark dashed (neutral), 3 dashed grid lines + % ticks on the right, end-point circles on the portfolio line.
 * Renders the SVG at the container's clientWidth (measured with ResizeObserver) rather than scaling with CSS, so axis text/line widths don't distort.
 * Nothing renders when the curve is empty or has fewer than 2 valid points (the parent shows placeholder text such as "calculating").
 */
export default function BenchChart({ curve, height = 150, className }: BenchChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)

  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    const update = () => setWidth(el.clientWidth)
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const points = (curve || []).filter(
    (p) => Number.isFinite(p.portfolio) && Number.isFinite(p.benchmark),
  )
  if (points.length < 2) return null

  return (
    <div ref={containerRef} className={className} style={{ width: '100%', height }}>
      {width > 0 && <BenchChartSvg points={points} width={width} height={height} />}
    </div>
  )
}
