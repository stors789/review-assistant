import React from 'react';
import { Routes, Route, NavLink } from 'react-router-dom';
import { useTheme } from './ThemeContext';
import { Sun, Moon, Library, CheckCircle, FileText, Share2, Sparkles } from 'lucide-react';
import ZoteroExplorer from './pages/ZoteroExplorer';
import ClaimVerifier from './pages/ClaimVerifier';
import PaperBreakdown from './pages/PaperBreakdown';
import Synthesizer from './pages/Synthesizer';

const App: React.FC = () => {
  const { theme, toggleTheme } = useTheme();

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
          <Routes>
            <Route path="/" element={<ZoteroExplorer />} />
            <Route path="/verify" element={<ClaimVerifier />} />
            <Route path="/breakdown" element={<PaperBreakdown />} />
            <Route path="/synthesize" element={<Synthesizer />} />
          </Routes>
        </div>
      </main>
    </div>
  );
};

export default App;
