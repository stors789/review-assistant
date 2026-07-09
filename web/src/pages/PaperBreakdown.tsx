import React, { useState } from 'react';
import { Send, Loader2 } from 'lucide-react';
import CollectionSelector from '../components/CollectionSelector';

const PaperBreakdown: React.FC = () => {
  const [collection, setCollection] = useState('');
  const [logs, setLogs] = useState<string[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);

  const handleBreakdown = async () => {
    if (!collection) return;
    
    setIsProcessing(true);
    setLogs(['Starting paper breakdown process...']);

    try {
      const response = await fetch('/api/tasks/breakdown', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ collection })
      });

      if (!response.body) throw new Error('No readable stream available');

      const reader = response.body.getReader();
      const decoder = new TextDecoder('utf-8');

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        
        const chunk = decoder.decode(value);
        const lines = chunk.split('\n');
        
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.substring(6));
              setLogs(prev => [...prev, data.status]);
            } catch (e) {
              console.error('Error parsing SSE', e);
            }
          }
        }
      }
    } catch (err: any) {
      setLogs(prev => [...prev, `Error: ${err.message}`]);
    } finally {
      setIsProcessing(false);
    }
  };

  return (
    <div>
      <h1 className="page-title">Paper Breakdown</h1>
      
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2rem' }}>
        {/* Input Form */}
        <div className="glass-panel card">
          <h2 style={{ fontSize: '1.2rem', marginBottom: '1rem', fontWeight: 600 }}>New Breakdown Task</h2>
          
          <div style={{ marginBottom: '1.5rem' }}>
            <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500 }}>Zotero Collection</label>
            <CollectionSelector value={collection} onChange={setCollection} />
            <p style={{ marginTop: '0.5rem', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
              Select a collection to extract structured fields (background, methods, conclusions, etc.) from all PDF papers within it.
            </p>
          </div>

          <button 
            className="btn btn-primary" 
            onClick={handleBreakdown}
            disabled={isProcessing || !collection}
            style={{ width: '100%', gap: '0.5rem' }}
          >
            {isProcessing ? <Loader2 className="animate-spin" size={18} /> : <Send size={18} />}
            {isProcessing ? 'Processing...' : 'Start Breakdown'}
          </button>
        </div>

        {/* Real-time Logs */}
        <div className="glass-panel card" style={{ display: 'flex', flexDirection: 'column' }}>
          <h2 style={{ fontSize: '1.2rem', marginBottom: '1rem', fontWeight: 600 }}>Live Progress</h2>
          <div style={{ 
            flex: 1, 
            background: 'var(--bg-color)', 
            borderRadius: '8px', 
            padding: '1rem',
            fontFamily: 'monospace',
            fontSize: '0.9rem',
            overflowY: 'auto',
            minHeight: '300px'
          }}>
            {logs.length === 0 ? (
              <div style={{ color: 'var(--text-secondary)', fontStyle: 'italic' }}>
                Awaiting task start...
              </div>
            ) : (
              logs.map((log, i) => (
                <div key={i} style={{ marginBottom: '0.5rem', color: 'var(--text-primary)', animation: 'fadeIn 0.3s' }}>
                  <span style={{ color: 'var(--accent-color)', marginRight: '0.5rem' }}>&gt;</span>
                  {log}
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default PaperBreakdown;
