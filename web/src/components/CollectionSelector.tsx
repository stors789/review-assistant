import React, { useState, useEffect } from 'react';
import { Loader2 } from 'lucide-react';

interface ZoteroCollection {
  name: string;
  total: number;
  has_attachment: number;
}

interface CollectionSelectorProps {
  value: string;
  onChange: (value: string) => void;
}

const CollectionSelector: React.FC<CollectionSelectorProps> = ({ value, onChange }) => {
  const [collections, setCollections] = useState<ZoteroCollection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    fetch('/api/zotero/collections')
      .then(res => {
        if (!res.ok) throw new Error('Failed to fetch');
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

  if (loading) {
    return <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-secondary)', padding: '0.75rem 0' }}><Loader2 size={16} className="animate-spin"/> Loading collections...</div>;
  }

  if (error) {
    return <div style={{ color: '#ef4444', padding: '0.75rem 0' }}>Error: {error}</div>;
  }

  return (
    <select 
      value={value} 
      onChange={(e) => onChange(e.target.value)}
      style={{
        width: '100%',
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
  );
};

export default CollectionSelector;
