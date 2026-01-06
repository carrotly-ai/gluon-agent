# PWA Offline Detection & Animated Robot Implementation Plan

## Overview

This plan addresses the issue where the Gluon PWA shows a black screen when there's no backend connectivity. We'll implement comprehensive offline detection and display a friendly animated robot character with personality.

---

## Problem Analysis

### Current State
1. **Existing `useOnline` hook** - Only uses `navigator.onLine` which detects network interface status, not actual backend connectivity
2. **Existing WebSocket connection tracking** - `connected` state in `useRunsWithWebSocket` tracks WebSocket connectivity
3. **Existing PWA config** - Already has `vite-plugin-pwa` with Workbox, but no offline fallback page configured
4. **Current behavior** - Black screen when backend unreachable because:
   - Initial API fetch fails silently or shows generic error
   - No offline fallback page configured in service worker
   - No visual indication of backend-specific issues vs general offline

### Root Causes
1. No distinction between "device offline" vs "backend unreachable"
2. Service worker doesn't have an offline fallback page
3. No engaging offline experience - just errors or blank screens

---

## Architecture Decisions

### Animation Approach: SVG + CSS
After researching Lottie, Canvas, and pure CSS options, **SVG with CSS animations** is the best choice because:
- **Zero runtime dependencies** (unlike Lottie which needs lottie-web)
- **Pre-cache friendly** - single file, no additional requests
- **Lightweight** - SVG + CSS compresses extremely well
- **GPU-accelerated** - CSS animations are performant
- **Matches the Tokyo-minimal design** - clean, geometric shapes fit the aesthetic

### Offline Detection Strategy: Multi-Layer
1. **Layer 1: `navigator.onLine`** - Device network interface (fast, unreliable)
2. **Layer 2: WebSocket connection state** - Real-time backend connectivity
3. **Layer 3: Health check ping** - Periodic backend reachability test
4. **Layer 4: Service worker catch handler** - Fallback for failed navigation

---

## Implementation Plan

### Phase 1: Enhanced Connectivity Detection Hook

**File: `src/hooks/useConnectivity.ts`** (new file)

```typescript
// Combines multiple signals into a unified connectivity state
interface ConnectivityState {
  isOnline: boolean           // Device has network interface
  isBackendReachable: boolean // Can reach Gluon API
  status: 'online' | 'offline' | 'backend-unreachable' | 'checking'
  lastChecked: Date | null
}
```

**Features:**
- Uses `navigator.onLine` as fast first signal
- Monitors WebSocket connection state
- Performs periodic health check to `/api/health` endpoint (if exists) or `/api/status`
- Debounces state changes to avoid flicker
- Exposes unified `status` for UI consumption

### Phase 2: Animated Offline Robot Component

**File: `src/components/OfflineRobot.tsx`** (new file)

A friendly, animated robot character that fits the Tokyo-minimal aesthetic:

**Design Specifications:**
- Geometric, minimal design using rectangles and circles
- Color palette: Uses `--color-paper`, `--color-stone`, `--color-indigo`, `--color-sky`
- Animations:
  - Gentle bobbing/floating motion (3s ease-in-out)
  - Eye blinking (random intervals, 4-6s)
  - Antenna pulse (syncs with "searching" state)
  - Subtle arm wave (friendly gesture)
  - "Signal search" animation on antenna

**States:**
1. **Searching** - Robot actively looking for connection (antenna animating)
2. **Waiting** - Robot idle but patient (gentle bob only)
3. **Reconnecting** - Robot hopeful (faster antenna, slight smile)

### Phase 3: Offline Page/Overlay Component

**File: `src/components/OfflineOverlay.tsx`** (new file)

**Layout:**
```
┌─────────────────────────────────────┐
│                                     │
│         [Animated Robot]            │
│                                     │
│    "Searching for Gluon..."         │
│                                     │
│    ○ ○ ○  (pulsing dots)           │
│                                     │
│    [Try Again] button (optional)    │
│                                     │
│    "Last connected: 2 min ago"      │
│                                     │
└─────────────────────────────────────┘
```

**Copy variations based on state:**
- `offline`: "You're offline. Check your connection."
- `backend-unreachable`: "Can't reach Gluon server. The backend might be starting up..."
- `checking`: "Searching for Gluon..."

### Phase 4: Service Worker Offline Fallback

**File: `vite.config.ts`** (modify existing)

Add Workbox configuration for offline fallback:

```typescript
workbox: {
  // ... existing config
  navigateFallback: '/index.html',  // SPA fallback
  navigateFallbackDenylist: [/^\/api/],  // Don't fallback API routes
  runtimeCaching: [
    // ... existing rules
    {
      // Offline fallback for failed navigation
      urlPattern: ({ request }) => request.mode === 'navigate',
      handler: 'NetworkFirst',
      options: {
        cacheName: 'pages',
        networkTimeoutSeconds: 5,
        plugins: [
          // Custom plugin to serve offline page on failure
        ]
      }
    }
  ],
  // Add catch handler for failed requests
  offlineFallback: '/offline.html'  // If using separate offline page
}
```

**Alternative approach (recommended for SPA):**
- Don't use separate offline.html
- Pre-cache the app shell
- Let React handle offline state via the OfflineOverlay component
- Service worker serves cached index.html, app detects offline and shows robot

### Phase 5: Integration with App.tsx

**Modify: `src/App.tsx`**

```tsx
function App() {
  const { status } = useConnectivity()
  const { runs, loading, error, connected } = useRunsWithWebSocket()

  // Show offline overlay when backend unreachable
  if (status === 'offline' || status === 'backend-unreachable') {
    return <OfflineOverlay status={status} />
  }

  // ... rest of app
}
```

### Phase 6: PWA Assets Update

**Files to create/modify:**
- `public/offline.html` - Static fallback (optional, for non-SPA scenarios)
- Update `vite.config.ts` - Add offline assets to precache

---

## File Structure

```
web-ui/src/
├── components/
│   ├── OfflineRobot.tsx      # NEW - Animated robot SVG component
│   ├── OfflineOverlay.tsx    # NEW - Full-screen offline experience
│   └── ...existing
├── hooks/
│   ├── useConnectivity.ts    # NEW - Enhanced connectivity detection
│   ├── useOnline.ts          # KEEP - Still useful as primitive
│   └── ...existing
└── ...
```

---

## Detailed Component Specifications

### OfflineRobot.tsx

```tsx
interface OfflineRobotProps {
  state: 'searching' | 'waiting' | 'reconnecting'
  size?: 'sm' | 'md' | 'lg'  // 120px, 200px, 280px
}
```

**SVG Structure:**
```
Robot
├── Head (rounded rect)
│   ├── Left Eye (circle with blink animation)
│   ├── Right Eye (circle with blink animation)
│   ├── Mouth (path, changes with state)
│   └── Antenna (line + circle, pulse animation)
├── Body (rounded rect)
│   ├── Screen/Chest (rect with status indicator)
│   └── Buttons (small circles)
├── Left Arm (rounded rect, wave animation)
├── Right Arm (rounded rect)
├── Left Leg (rounded rect)
└── Right Leg (rounded rect)
```

**CSS Animations:**
```css
@keyframes robot-float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-12px); }
}

@keyframes robot-blink {
  0%, 90%, 100% { transform: scaleY(1); }
  95% { transform: scaleY(0.1); }
}

@keyframes antenna-pulse {
  0%, 100% { opacity: 0.4; transform: scale(1); }
  50% { opacity: 1; transform: scale(1.3); }
}

@keyframes arm-wave {
  0%, 100% { transform: rotate(0deg); }
  25% { transform: rotate(-15deg); }
  75% { transform: rotate(15deg); }
}

@keyframes signal-search {
  0% { opacity: 0; transform: scale(0.5); }
  50% { opacity: 1; }
  100% { opacity: 0; transform: scale(2); }
}
```

### useConnectivity.ts

```tsx
interface UseConnectivityOptions {
  healthCheckUrl?: string        // Default: '/api/status'
  healthCheckInterval?: number   // Default: 10000 (10s)
  debounceMs?: number           // Default: 500
}

interface ConnectivityState {
  isOnline: boolean
  isBackendReachable: boolean
  status: 'online' | 'offline' | 'backend-unreachable' | 'checking'
  lastChecked: Date | null
  retryIn: number | null        // Seconds until next retry
}

function useConnectivity(options?: UseConnectivityOptions): ConnectivityState & {
  checkNow: () => Promise<void>  // Manual retry
}
```

**Logic Flow:**
1. On mount, check `navigator.onLine`
2. If online, perform health check to backend
3. Subscribe to `online`/`offline` events
4. Monitor WebSocket connection state changes
5. Periodic health check every 10s when status isn't 'online'
6. Exponential backoff on repeated failures (10s → 20s → 40s → max 60s)

### OfflineOverlay.tsx

```tsx
interface OfflineOverlayProps {
  status: 'offline' | 'backend-unreachable' | 'checking'
  onRetry?: () => void
}
```

**Features:**
- Full viewport coverage with slight transparency to hint at app beneath
- Centered content with robot, message, and retry option
- Auto-retry countdown display
- Respects light/dark theme
- Accessible (proper ARIA labels, focus management)

---

## Testing Checklist

### Manual Testing
- [ ] Kill backend server → verify robot appears after timeout
- [ ] Disable network (airplane mode) → verify "offline" state
- [ ] Slow network (DevTools throttling) → verify graceful degradation
- [ ] Reconnect network → verify smooth transition back to app
- [ ] Test in PWA standalone mode (installed app)
- [ ] Test in Safari (iOS PWA)

### Service Worker Testing
- [ ] Clear cache, load app, go offline → verify app loads from cache
- [ ] Verify API calls fail gracefully
- [ ] Verify WebSocket reconnection works

---

## Implementation Order

1. **Create `useConnectivity.ts`** - Foundation for all offline detection
2. **Create `OfflineRobot.tsx`** - The star of the show, can test in isolation
3. **Create `OfflineOverlay.tsx`** - Compose robot with messaging
4. **Update `vite.config.ts`** - Service worker improvements
5. **Update `App.tsx`** - Integrate overlay
6. **Update `main.tsx`** - Add service worker registration callbacks
7. **Test thoroughly** - All scenarios above

---

## Estimated Effort

| Phase | Estimated Time |
|-------|---------------|
| Phase 1: useConnectivity hook | 1 hour |
| Phase 2: OfflineRobot component | 2 hours |
| Phase 3: OfflineOverlay component | 1 hour |
| Phase 4: Service worker config | 30 min |
| Phase 5: App integration | 30 min |
| Phase 6: Testing & polish | 1 hour |
| **Total** | **~6 hours** |

---

## Future Enhancements (Out of Scope)

- [ ] Add sound effects for robot (optional, PWA can do audio)
- [ ] Robot Easter eggs (click interactions, different expressions)
- [ ] Offline data viewing (show cached runs in read-only mode)
- [ ] Background sync for queued actions
- [ ] Push notifications when back online

---

## Appendix: Robot Design Inspiration

The robot should feel:
- **Friendly** - Not threatening, approachable
- **Patient** - Conveying "I'm waiting with you"
- **Minimal** - Matches Tokyo-minimal aesthetic
- **Tech-forward** - Fits the AI/agent theme of Gluon

Color usage:
- Body: `--color-indigo` (the calming blue)
- Eyes/highlights: `--color-sky` (bright, alert)
- Accents: `--color-harvest` (warm, friendly antenna glow)
- Dark mode compatible with CSS variables
