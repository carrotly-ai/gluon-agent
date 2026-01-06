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
  sm: { width: 120, height: 160 },
  md: { width: 180, height: 240 },
  lg: { width: 240, height: 320 },
}

/**
 * Animated SVG robot character for offline states.
 *
 * Features:
 * - Gentle floating/bobbing animation
 * - Blinking eyes
 * - Pulsing antenna with signal search rings
 * - Waving arm (when searching)
 * - Matches Tokyo-minimal aesthetic
 */
export function OfflineRobot({ state = 'searching', size = 'md', className }: OfflineRobotProps) {
  const { width, height } = sizeMap[size]

  return (
    <svg
      viewBox="0 0 180 240"
      width={width}
      height={height}
      className={cn('offline-robot', `offline-robot--${state}`, className)}
      aria-label="Offline robot character"
      role="img"
    >
      <defs>
        {/* Gradient for robot body */}
        <linearGradient id="bodyGradient" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="var(--color-indigo)" stopOpacity="1" />
          <stop offset="100%" stopColor="var(--color-indigo)" stopOpacity="0.8" />
        </linearGradient>

        {/* Glow effect for antenna */}
        <filter id="antennaGlow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>

        {/* Subtle shadow */}
        <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0" dy="4" stdDeviation="4" floodOpacity="0.2" />
        </filter>
      </defs>

      {/* Main robot group with floating animation */}
      <g className="robot-body" filter="url(#shadow)">
        {/* Antenna */}
        <g className="antenna">
          {/* Antenna pole */}
          <line
            x1="90"
            y1="35"
            x2="90"
            y2="15"
            stroke="var(--color-stone)"
            strokeWidth="3"
            strokeLinecap="round"
          />

          {/* Antenna ball with glow */}
          <circle
            cx="90"
            cy="12"
            r="6"
            fill="var(--color-harvest)"
            className="antenna-ball"
            filter="url(#antennaGlow)"
          />

          {/* Signal rings (only visible when searching) */}
          <circle
            cx="90"
            cy="12"
            r="10"
            fill="none"
            stroke="var(--color-harvest)"
            strokeWidth="2"
            className="signal-ring signal-ring--1"
            opacity="0"
          />
          <circle
            cx="90"
            cy="12"
            r="18"
            fill="none"
            stroke="var(--color-harvest)"
            strokeWidth="1.5"
            className="signal-ring signal-ring--2"
            opacity="0"
          />
          <circle
            cx="90"
            cy="12"
            r="26"
            fill="none"
            stroke="var(--color-harvest)"
            strokeWidth="1"
            className="signal-ring signal-ring--3"
            opacity="0"
          />
        </g>

        {/* Head */}
        <rect
          x="50"
          y="35"
          width="80"
          height="60"
          rx="8"
          fill="url(#bodyGradient)"
          stroke="var(--color-indigo)"
          strokeWidth="2"
        />

        {/* Face plate / visor */}
        <rect x="58" y="45" width="64" height="40" rx="4" fill="var(--color-ink)" opacity="0.6" />

        {/* Eyes */}
        <g className="eyes">
          {/* Left eye */}
          <ellipse
            cx="75"
            cy="65"
            rx="8"
            ry="10"
            fill="var(--color-sky)"
            className="eye eye--left"
          />
          <circle cx="77" cy="63" r="3" fill="var(--color-paper)" opacity="0.8" />

          {/* Right eye */}
          <ellipse
            cx="105"
            cy="65"
            rx="8"
            ry="10"
            fill="var(--color-sky)"
            className="eye eye--right"
          />
          <circle cx="107" cy="63" r="3" fill="var(--color-paper)" opacity="0.8" />
        </g>

        {/* Mouth - changes based on state */}
        <path
          d={
            state === 'reconnecting'
              ? 'M 78 82 Q 90 90 102 82' // Smile when reconnecting
              : 'M 80 84 L 100 84' // Neutral line otherwise
          }
          stroke="var(--color-sky)"
          strokeWidth="3"
          strokeLinecap="round"
          fill="none"
          className="mouth"
        />

        {/* Body */}
        <rect
          x="45"
          y="100"
          width="90"
          height="80"
          rx="10"
          fill="url(#bodyGradient)"
          stroke="var(--color-indigo)"
          strokeWidth="2"
        />

        {/* Chest screen */}
        <rect x="60" y="115" width="60" height="35" rx="4" fill="var(--color-ink)" opacity="0.5" />

        {/* Status indicator on chest */}
        <circle
          cx="90"
          cy="132"
          r="8"
          fill={state === 'reconnecting' ? 'var(--color-jade)' : 'var(--color-harvest)'}
          className="chest-indicator"
        />

        {/* Chest buttons */}
        <circle cx="70" cy="160" r="4" fill="var(--color-stone)" opacity="0.6" />
        <circle cx="90" cy="160" r="4" fill="var(--color-stone)" opacity="0.6" />
        <circle cx="110" cy="160" r="4" fill="var(--color-stone)" opacity="0.6" />

        {/* Left arm */}
        <g className="arm arm--left">
          <rect
            x="20"
            y="105"
            width="22"
            height="50"
            rx="10"
            fill="url(#bodyGradient)"
            stroke="var(--color-indigo)"
            strokeWidth="2"
          />
          {/* Hand */}
          <circle cx="31" cy="160" r="10" fill="var(--color-indigo)" />
        </g>

        {/* Right arm */}
        <g className="arm arm--right">
          <rect
            x="138"
            y="105"
            width="22"
            height="50"
            rx="10"
            fill="url(#bodyGradient)"
            stroke="var(--color-indigo)"
            strokeWidth="2"
          />
          {/* Hand */}
          <circle cx="149" cy="160" r="10" fill="var(--color-indigo)" />
        </g>

        {/* Legs */}
        <g className="legs">
          {/* Left leg */}
          <rect
            x="55"
            y="182"
            width="25"
            height="40"
            rx="8"
            fill="url(#bodyGradient)"
            stroke="var(--color-indigo)"
            strokeWidth="2"
          />
          {/* Left foot */}
          <ellipse cx="67" cy="228" rx="15" ry="8" fill="var(--color-indigo)" />

          {/* Right leg */}
          <rect
            x="100"
            y="182"
            width="25"
            height="40"
            rx="8"
            fill="url(#bodyGradient)"
            stroke="var(--color-indigo)"
            strokeWidth="2"
          />
          {/* Right foot */}
          <ellipse cx="112" cy="228" rx="15" ry="8" fill="var(--color-indigo)" />
        </g>
      </g>
    </svg>
  )
}
