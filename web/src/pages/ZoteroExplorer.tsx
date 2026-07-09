import React, { useState, useEffect } from 'react';
import { Folder, FileText, ChevronRight, Loader2 } from 'lucide-react';

interface ZoteroCollection {
  name: string;
  total: number;
  has_attachment: number;
  missing: number;
}

const ZoteroExplorer: React.FC = () => {
  const [collections, setCollections] = useState<ZoteroCollection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    fetch('/api/zotero/collections')
      .then(res => {
        if (!res.ok) throw new Error('Failed to fetch collections');
        return res.json();
      })
      .then(data => {
        setCollections(data.collections || []);
        setLoading(false);
      })
      .catch(err => {
        setError(err.message);
        setLoading(false);
      });
  }, []);

  return (
    <div>
      <h1 className="page-title">Zotero Collections</h1>
      <div className="glass-panel card">
        <p style={{ marginBottom: '1.5rem', color: 'var(--text-secondary)' }}>
          Browse your Zotero libraries and see PDF availability for your research papers.
        </p>

        {loading && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-secondary)' }}>
            <Loader2 className="animate-spin" /> Loading collections...
          </div>
        )}

        {error && <div style={{ color: '#ef4444' }}>Error: {error}</div>}

        {!loading && !error && (
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
