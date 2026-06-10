import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  /** Optional custom fallback. Receives the error and a reset callback. */
  fallback?: (error: Error, reset: () => void) => ReactNode
}

interface State {
  error: Error | null
}

/**
 * Catches render-time errors in its subtree so one broken component cannot
 * white-screen the whole dashboard. Without this, an exception thrown while
 * rendering (e.g. a malformed run in StreamingLogViewer) unmounts the entire
 * React tree and the user sees a blank page.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary] Caught render error:', error, info.componentStack)
  }

  reset = () => this.setState({ error: null })

  render() {
    const { error } = this.state
    if (error) {
      if (this.props.fallback) return this.props.fallback(error, this.reset)
      return (
        <div
          role="alert"
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '1rem',
            minHeight: '60vh',
            padding: '2rem',
            textAlign: 'center',
            color: 'var(--color-paper)',
          }}
        >
          <h2 style={{ margin: 0 }}>Something went wrong</h2>
          <p style={{ opacity: 0.7, maxWidth: 480 }}>
            This view hit an unexpected error. Reloading usually fixes it; if it keeps happening,
            check the browser console for details.
          </p>
          <div style={{ display: 'flex', gap: '0.75rem' }}>
            <button
              type="button"
              onClick={this.reset}
              style={{
                padding: '0.5rem 1rem',
                borderRadius: 8,
                border: '1px solid rgba(163,163,163,0.3)',
                background: 'transparent',
                color: 'inherit',
                cursor: 'pointer',
              }}
            >
              Try again
            </button>
            <button
              type="button"
              onClick={() => window.location.reload()}
              style={{
                padding: '0.5rem 1rem',
                borderRadius: 8,
                border: '1px solid rgba(163,163,163,0.3)',
                background: 'var(--color-ink)',
                color: 'inherit',
                cursor: 'pointer',
              }}
            >
              Reload
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
