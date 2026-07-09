import React from 'react';
import { Loader2, RefreshCw } from 'lucide-react';
import { useZotero } from '../ZoteroContext';

interface CollectionSelectorProps {
  value: string;
  onChange: (value: string) => void;
}

const CollectionSelector: React.FC<CollectionSelectorProps> = ({ value, onChange }) => {
  const { collections, loading, error, refreshCollections } = useZotero();

  if (loading && collections.length === 0) {
    return <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-secondary)', padding: '0.75rem 0' }}><Loader2 size={16} className="animate-spin"/> Loading collections...</div>;
  }

  if (error) {
    return <div style={{ color: '#ef4444', padding: '0.75rem 0' }}>Error: {error}</div>;
  }

  return (
    <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
      <select 
        value={value} 
        onChange={(e) => onChange(e.target.value)}
        style={{
          flex: 1,
          padding: '0.75rem',
          borderRadius: '8px',
          border: '1px solid var(--surface-border)',
          background: 'rgba(128,128,128,0.1)',
          color: 'var(--text-primary)',
          outline: 'none',
          cursor: 'pointer',
        }}
      >
        <option value="">-- Select a collection --</option>
        {collections.map((col, idx) => (
          <option key={idx} value={col.name} style={{ background: 'var(--bg-color)' }}>
            {col.name} ({col.has_attachment} PDFs)
          </option>
        ))}
      </select>
      <button 
        type="button"
        onClick={refreshCollections}
        disabled={loading}
        className="btn-icon"
        title="Refresh Zotero Collections"
        style={{
          padding: '0.75rem',
          borderRadius: '8px',
          background: 'var(--surface-color)',
          border: '1px solid var(--surface-border)'
        }}
      >
        <RefreshCw size={18} className={loading ? 'animate-spin' : ''} />
      </button>
    </div>
  );
};

export default CollectionSelector;
