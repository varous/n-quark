import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import AdminApp from './admin/AdminApp.tsx'

// The frontend now boots into the internal Admin Console (Admin Phase A). The earlier demo panels
// remain in src/components for reference but are no longer the entry point.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AdminApp />
  </StrictMode>,
)
