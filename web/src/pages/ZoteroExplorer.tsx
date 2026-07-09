import React from 'react';
import { Folder, FileText, ChevronRight, Loader2, RefreshCw } from 'lucide-react';
import { useZotero } from '../ZoteroContext';

const ZoteroExplorer: React.FC = () => {
  const { collections, loading, error, refreshCollections } = useZotero();

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
        <h1 className="page-title" style={{ marginBottom: 0 }}>Zotero Collections</h1>
        <button 
          className="btn" 
          onClick={refreshCollections} 
          disabled={loading}
          style={{ 
            background: 'var(--surface-color)', 
            border: '1px solid var(--surface-border)',
            color: 'var(--text-primary)',
            display: 'flex',
            alignItems: 'center',
            gap: '0.5rem'
          }}
        >
          <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
          {loading ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>
      
      <div className="glass-panel card">
        <p style={{ marginBottom: '1.5rem', color: 'var(--text-secondary)' }}>
          Browse your Zotero libraries and see PDF availability for your research papers.
        </p>

        {loading && collections.length === 0 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-secondary)' }}>
            <Loader2 className="animate-spin" /> Loading collections...
          </div>
        )}

        {error && <div style={{ color: '#ef4444' }}>Error: {error}</div>}

        {(!loading || collections.length > 0) && !error && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            {collections.length === 0 ? (
              <p>No collections found.</p>
            ) : (
              collections.map((col, idx) => (
                <div key={idx} className="glass-panel" style={{ padding: '1rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                    <Folder size={20} color="var(--accent-color)" />
                    <span style={{ fontWeight: 500 }}>{col.name}</span>
                  </div>
                  <div style={{ display: 'flex', gap: '1.5rem', color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                      <FileText size={16} /> Total: {col.total}
                    </span>
                    <span style={{ color: col.has_attachment > 0 ? '#10b981' : 'inherit' }}>
                      PDFs: {col.has_attachment}
                    </span>
                    <span style={{ color: col.missing > 0 ? '#ef4444' : 'inherit' }}>
                      Missing: {col.missing}
                    </span>
                    <ChevronRight size={18} />
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default ZoteroExplorer;
