import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Toaster } from 'sonner'
import './index.css'
import App from './App.tsx'
import { RunDetailPage } from './components/RunDetailPage.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
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

        {/* Cost dashboard */}
        <Route path="/cost" element={<App />} />

        {/* Settings routes */}
        <Route path="/settings" element={<App />} />
        <Route path="/settings/:tab" element={<App />} />

        {/* Catch-all redirect to board */}
        <Route path="*" element={<Navigate to="/board" replace />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
