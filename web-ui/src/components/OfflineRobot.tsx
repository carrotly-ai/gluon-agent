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

const sizeMap = {
  sm: { width: 100, height: 120 },
  md: { width: 150, height: 180 },
  lg: { width: 200, height: 240 },
}

/**
 * Minimal monochrome robot for offline states.
 * Clean geometric design matching Tokyo-minimal aesthetic.
 */
export function OfflineRobot({ state = 'searching', size = 'md', className }: OfflineRobotProps) {
  const { width, height } = sizeMap[size]

  return (
    <svg
      viewBox="0 0 100 120"
      width={width}
      height={height}
      className={cn('offline-robot', `offline-robot--${state}`, className)}
      aria-label="Offline robot character"
      role="img"
    >
      {/* Main robot group with floating animation */}
      <g className="robot-body">
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

        {/* Head - rounded rectangle */}
        <rect
          x="25"
          y="20"
          width="50"
          height="38"
          rx="6"
          fill="var(--color-ink)"
          stroke="var(--color-stone)"
          strokeWidth="1.5"
        />

        {/* Eyes - simple geometric circles */}
        <g className="eyes">
          <circle cx="38" cy="36" r="5" fill="var(--color-paper)" className="eye eye--left" />
          <circle cx="62" cy="36" r="5" fill="var(--color-paper)" className="eye eye--right" />

          {/* Pupils */}
          <circle cx="38" cy="36" r="2" fill="var(--color-void)" />
          <circle cx="62" cy="36" r="2" fill="var(--color-void)" />
        </g>

        {/* Mouth - minimal line or arc */}
        <path
          d={
            state === 'reconnecting'
              ? 'M 42 48 Q 50 52 58 48' // Subtle smile
              : 'M 44 49 L 56 49' // Neutral
          }
          stroke="var(--color-stone)"
          strokeWidth="2"
          strokeLinecap="round"
          fill="none"
          className="mouth"
        />

        {/* Body - simple rectangle */}
        <rect
          x="22"
          y="62"
          width="56"
          height="42"
          rx="4"
          fill="var(--color-ink)"
          stroke="var(--color-stone)"
          strokeWidth="1.5"
        />

        {/* Chest detail - minimal lines */}
        <line x1="32" y1="72" x2="68" y2="72" stroke="var(--color-stone)" strokeWidth="1" opacity="0.4" />
        <line x1="32" y1="82" x2="68" y2="82" stroke="var(--color-stone)" strokeWidth="1" opacity="0.4" />
        <line x1="32" y1="92" x2="68" y2="92" stroke="var(--color-stone)" strokeWidth="1" opacity="0.4" />

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
