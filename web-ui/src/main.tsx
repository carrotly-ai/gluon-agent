import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Toaster } from 'sonner'
import './index.css'
import App from './App.tsx'
import { ErrorBoundary } from './components/ErrorBoundary.tsx'
import { RunDetailPage } from './components/RunDetailPage.tsx'
import { CurrentUserProvider } from './hooks/useCurrentUser.ts'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <CurrentUserProvider>
        <Toaster
          position="bottom-right"
          toastOptions={{
            style: {
              background: 'var(--color-ink)',
              border: '1px solid rgba(163,163,163,0.15)',
              color: 'var(--color-paper)',
            },
          }}
        />
        <ErrorBoundary>
          <Routes>
            {/* Redirect root to board */}
            <Route path="/" element={<Navigate to="/board" replace />} />

            {/* Board routes */}
            <Route path="/board" element={<App />} />
            <Route path="/board/:runId" element={<App />} />
            <Route path="/board/:runId/:tab" element={<App />} />

            {/* Full-screen run detail page */}
            <Route path="/runs/:runId" element={<RunDetailPage />} />
            <Route path="/runs/:runId/:tab" element={<RunDetailPage />} />

            {/* List view (sidebar + messages) */}
            <Route path="/list" element={<App />} />

            {/* Activity log */}
            <Route path="/activity" element={<App />} />

            {/* Work queue */}
            <Route path="/queue" element={<App />} />

            {/* Merge queue */}
            <Route path="/merge" element={<App />} />

            {/* Cost dashboard */}
            <Route path="/cost" element={<App />} />

            {/* SDK Sessions */}
            <Route path="/sessions" element={<App />} />

            {/* Task schedules — user-defined recurring tasks */}
            <Route path="/schedules" element={<App />} />
            <Route path="/schedules/:scheduleId" element={<App />} />

            {/* Agent loops — loop-engineering Phase 2 */}
            <Route path="/loops" element={<App />} />

            {/* Settings routes. The :tab segment is one of
              workspaces / projects / account / preferences / formulas.
              Preferences additionally supports a left-rail group
              (agent / integrations / workspace / system) via a nested
              segment, e.g. /settings/preferences/integrations. */}
            <Route path="/settings" element={<App />} />
            <Route path="/settings/:tab" element={<App />} />
            <Route path="/settings/preferences/:group" element={<App />} />

            {/* Admin routes (D5 Phase 2) */}
            <Route path="/admin/users" element={<App />} />

            {/* Catch-all redirect to board */}
            <Route path="*" element={<Navigate to="/board" replace />} />
          </Routes>
        </ErrorBoundary>
      </CurrentUserProvider>
    </BrowserRouter>
  </StrictMode>
)
