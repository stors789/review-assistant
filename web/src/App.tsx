import React from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { useTheme } from './ThemeContext';
import { Sun, Moon, Library, CheckCircle, FileText, Share2, Sparkles, Settings as SettingsIcon } from 'lucide-react';
import ZoteroExplorer from './pages/ZoteroExplorer';
import ClaimVerifier from './pages/ClaimVerifier';
import PaperBreakdown from './pages/PaperBreakdown';
import Synthesizer from './pages/Synthesizer';
import Settings from './pages/Settings';

const App: React.FC = () => {
  const { theme, toggleTheme } = useTheme();
  const location = useLocation();

  return (
    <div className="app-container">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="sidebar-logo">
          <Sparkles className="logo-icon" size={28} />
          <span>ReviewAssist</span>
        </div>
        
        <nav className="nav-menu">
          <NavLink to="/" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            <Library size={20} /> Zotero Explorer
          </NavLink>
          <NavLink to="/verify" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            <CheckCircle size={20} /> Claim Verifier
          </NavLink>
          <NavLink to="/breakdown" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            <FileText size={20} /> Paper Breakdown
          </NavLink>
          <NavLink to="/synthesize" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            <Share2 size={20} /> Synthesizer
          </NavLink>
        </nav>
        
        <div style={{ marginTop: 'auto', borderTop: '1px solid var(--surface-border)', paddingTop: '1rem' }}>
          <NavLink to="/settings" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            <SettingsIcon size={20} /> Settings
          </NavLink>
        </div>
      </aside>

      {/* Main Content */}
      <main className="main-content">
        {/* Topbar */}
        <header className="topbar">
          <button className="btn-icon" onClick={toggleTheme} aria-label="Toggle Theme">
            {theme === 'dark' ? <Sun size={24} /> : <Moon size={24} />}
          </button>
        </header>

        {/* Page Content */}
        <div className="page-content animate-fade-in">
          <div style={{ display: location.pathname === '/' ? 'block' : 'none' }}>
            <ZoteroExplorer />
          </div>
          <div style={{ display: location.pathname === '/verify' ? 'block' : 'none' }}>
            <ClaimVerifier />
          </div>
          <div style={{ display: location.pathname === '/breakdown' ? 'block' : 'none' }}>
            <PaperBreakdown />
          </div>
          <div style={{ display: location.pathname === '/synthesize' ? 'block' : 'none' }}>
            <Synthesizer />
          </div>
          <div style={{ display: location.pathname === '/settings' ? 'block' : 'none' }}>
            <Settings />
          </div>
        </div>
      </main>
    </div>
  );
};

export default App;
