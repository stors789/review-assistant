import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import App from './App.tsx'
import { ThemeProvider } from './ThemeContext.tsx'
import { ZoteroProvider } from './ZoteroContext.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <ThemeProvider>
        <ZoteroProvider>
          <App />
        </ZoteroProvider>
      </ThemeProvider>
    </BrowserRouter>
  </StrictMode>,
)
