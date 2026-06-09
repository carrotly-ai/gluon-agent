import { useId } from 'react'
import { cn } from '@/lib/utils'

export type RobotState = 'searching' | 'waiting' | 'reconnecting'

interface OfflineRobotProps {
  /** Animation state of the robot */
  state?: RobotState
  /** Size variant */
  size?: 'sm' | 'md' | 'lg'
  /** Additional CSS classes */
  className?: string
}

// Heights preserve the viewBox aspect ratio (100 × 136 → 1.36×).
const sizeMap = {
  sm: { width: 100, height: 136 },
  md: { width: 150, height: 204 },
  lg: { width: 200, height: 272 },
}

/**
 * Minimal monochrome robot for offline states.
 *
 * "Refined Humanoid" — keeps the familiar humanoid silhouette but polished:
 * softer rounded head/body, an eye visor with a sweeping scan-line, expressive
 * blink, antenna signal waves, a gentle head-tilt + arm-wave when searching,
 * and a pulsing chest indicator. Clean geometric design matching the
 * Tokyo-minimal aesthetic.
 */
export function OfflineRobot({ state = 'searching', size = 'md', className }: OfflineRobotProps) {
  const { width, height } = sizeMap[size]
  // Unique clip id so multiple robots on one page don't collide.
  const rawId = useId()
  const visorClipId = `visor-clip-${rawId.replace(/:/g, '')}`

  return (
    <svg
      // Top padding (−16) gives the antenna ball + signal rings room to float
      // and pulse without being clipped by the canvas edge.
      viewBox="0 -16 100 136"
      width={width}
      height={height}
      className={cn('offline-robot', `offline-robot--${state}`, className)}
      aria-label="Offline robot character"
      role="img"
    >
      <defs>
        {/* Clip the scan-line so it only sweeps within the eye visor */}
        <clipPath id={visorClipId}>
          <rect x="30" y="28" width="40" height="18" rx="9" />
        </clipPath>
      </defs>

      {/* Soft ground shadow — scales subtly with the float for depth */}
      <ellipse
        cx="50"
        cy="116"
        rx="26"
        ry="3.5"
        fill="var(--color-void)"
        opacity="0.18"
        className="robot-shadow"
      />

      {/* Main robot group with floating animation */}
      <g className="robot-body">
        {/* Head + antenna tilt together for a "listening" head-tilt */}
        <g className="head">
          {/* Antenna */}
          <g className="antenna">
            {/* Antenna pole */}
            <line
              x1="50"
              y1="20"
              x2="50"
              y2="8"
              stroke="var(--color-stone)"
              strokeWidth="2"
              strokeLinecap="round"
            />

            {/* Antenna ball */}
            <circle
              cx="50"
              cy="6"
              r="4"
              fill="var(--color-paper)"
              stroke="var(--color-stone)"
              strokeWidth="1.5"
              className="antenna-ball"
            />

            {/* Signal rings */}
            <circle
              cx="50"
              cy="6"
              r="8"
              fill="none"
              stroke="var(--color-stone)"
              strokeWidth="1"
              className="signal-ring signal-ring--1"
              opacity="0"
            />
            <circle
              cx="50"
              cy="6"
              r="14"
              fill="none"
              stroke="var(--color-stone)"
              strokeWidth="0.75"
              className="signal-ring signal-ring--2"
              opacity="0"
            />
            <circle
              cx="50"
              cy="6"
              r="20"
              fill="none"
              stroke="var(--color-stone)"
              strokeWidth="0.5"
              className="signal-ring signal-ring--3"
              opacity="0"
            />
          </g>

          {/* Head - softer rounded rectangle */}
          <rect
            x="25"
            y="20"
            width="50"
            height="38"
            rx="10"
            fill="var(--color-ink)"
            stroke="var(--color-stone)"
            strokeWidth="1.5"
          />

          {/* Eye visor - houses the eyes and the sweeping scan-line */}
          <rect
            x="30"
            y="28"
            width="40"
            height="18"
            rx="9"
            fill="var(--color-void)"
            opacity="0.55"
          />

          {/* Eyes - simple geometric circles */}
          <g className="eyes">
            <circle cx="38" cy="37" r="5" fill="var(--color-paper)" className="eye eye--left" />
            <circle cx="62" cy="37" r="5" fill="var(--color-paper)" className="eye eye--right" />

            {/* Pupils */}
            <circle cx="38" cy="37" r="2" fill="var(--color-void)" />
            <circle cx="62" cy="37" r="2" fill="var(--color-void)" />
          </g>

          {/* Scan-line sweeping across the visor (searching/reconnecting) */}
          <g clipPath={`url(#${visorClipId})`}>
            <rect
              x="28"
              y="28"
              width="6"
              height="18"
              fill="var(--color-sky)"
              opacity="0.35"
              className="scan-line"
            />
          </g>

          {/* Mouth - minimal line or arc */}
          <path
            d={
              state === 'reconnecting'
                ? 'M 42 50 Q 50 54 58 50' // Subtle smile
                : 'M 44 51 L 56 51' // Neutral
            }
            stroke="var(--color-stone)"
            strokeWidth="2"
            strokeLinecap="round"
            fill="none"
            className="mouth"
          />
        </g>

        {/* Body - softer rounded rectangle */}
        <rect
          x="22"
          y="62"
          width="56"
          height="42"
          rx="8"
          fill="var(--color-ink)"
          stroke="var(--color-stone)"
          strokeWidth="1.5"
        />

        {/* Chest detail - minimal lines */}
        <line
          x1="32"
          y1="72"
          x2="68"
          y2="72"
          stroke="var(--color-stone)"
          strokeWidth="1"
          opacity="0.4"
        />
        <line
          x1="32"
          y1="82"
          x2="68"
          y2="82"
          stroke="var(--color-stone)"
          strokeWidth="1"
          opacity="0.4"
        />
        <line
          x1="32"
          y1="92"
          x2="68"
          y2="92"
          stroke="var(--color-stone)"
          strokeWidth="1"
          opacity="0.4"
        />

        {/* Status indicator */}
        <circle
          cx="50"
          cy="82"
          r="3"
          fill="var(--color-paper)"
          className="chest-indicator"
          opacity="0.8"
        />

        {/* Arms - simple rectangles */}
        <g className="arm arm--left">
          <rect
            x="10"
            y="66"
            width="10"
            height="30"
            rx="5"
            fill="var(--color-ink)"
            stroke="var(--color-stone)"
            strokeWidth="1.5"
          />
        </g>

        <g className="arm arm--right">
          <rect
            x="80"
            y="66"
            width="10"
            height="30"
            rx="5"
            fill="var(--color-ink)"
            stroke="var(--color-stone)"
            strokeWidth="1.5"
          />
        </g>

        {/* Legs - simple rectangles */}
        <g className="legs">
          <rect
            x="30"
            y="106"
            width="14"
            height="12"
            rx="3"
            fill="var(--color-ink)"
            stroke="var(--color-stone)"
            strokeWidth="1.5"
          />
          <rect
            x="56"
            y="106"
            width="14"
            height="12"
            rx="3"
            fill="var(--color-ink)"
            stroke="var(--color-stone)"
            strokeWidth="1.5"
          />
        </g>
      </g>
    </svg>
  )
}
