import React, { useState, useEffect } from 'react';
import { Loader2 } from 'lucide-react';

interface ZoteroItem {
  key: string;
  title: string;
  authors: string;
  pdf_available: boolean;
  pdf_path: string | null;
}

interface ZoteroItemSelectorProps {
  collection: string;
  value: string;
  onChange: (value: string) => void;
}

const ZoteroItemSelector: React.FC<ZoteroItemSelectorProps> = ({ collection, value, onChange }) => {
  const [items, setItems] = useState<ZoteroItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!collection) {
      setItems([]);
      return;
    }
    setLoading(true);
    fetch(`/api/zotero/collections/${encodeURIComponent(collection)}/items?pdf_only=true`)
      .then(res => {
        if (!res.ok) throw new Error('Failed to fetch items');
        return res.json();
      })
      .then(data => {
        setItems(data.items || []);
        setLoading(false);
      })
      .catch(err => {
        setError(err.message);
        setLoading(false);
      });
  }, [collection]);

  if (!collection) {
    return <div style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', fontStyle: 'italic' }}>Please select a collection first.</div>;
  }

  if (loading) {
    return <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-secondary)' }}><Loader2 size={16} className="animate-spin"/> Loading items...</div>;
  }

  if (error) {
    return <div style={{ color: '#ef4444' }}>Error: {error}</div>;
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
      <option value="">-- Select a PDF --</option>
      {items.map((it, idx) => (
        <option key={idx} value={it.title} style={{ background: 'var(--bg-color)' }}>
          {it.title.length > 60 ? it.title.substring(0, 60) + '...' : it.title}
        </option>
      ))}
    </select>
  );
};

export default ZoteroItemSelector;
