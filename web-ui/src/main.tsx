import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        {/* Redirect root to board */}
        <Route path="/" element={<Navigate to="/board" replace />} />

        {/* Board routes */}
        <Route path="/board" element={<App />} />
        <Route path="/board/:runId" element={<App />} />
        <Route path="/board/:runId/:tab" element={<App />} />

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
