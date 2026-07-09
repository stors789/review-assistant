import React, { useState } from 'react';
import { Send, Loader2 } from 'lucide-react';
import CollectionSelector from '../components/CollectionSelector';

const ClaimVerifier: React.FC = () => {
  const [collection, setCollection] = useState('');
  const [paragraph, setParagraph] = useState('');
  const [logs, setLogs] = useState<string[]>([]);
  const [isVerifying, setIsVerifying] = useState(false);

  const handleVerify = async () => {
    if (!collection || !paragraph) return;
    
    setIsVerifying(true);
    setLogs(['Starting verification process...']);

    try {
      const response = await fetch('/api/tasks/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ collection, paragraph })
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
      setIsVerifying(false);
    }
  };

  return (
    <div>
      <h1 className="page-title">Claim Verifier</h1>
      
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2rem' }}>
        {/* Input Form */}
        <div className="glass-panel card">
          <h2 style={{ fontSize: '1.2rem', marginBottom: '1rem', fontWeight: 600 }}>New Verification Task</h2>
          
          <div style={{ marginBottom: '1rem' }}>
            <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500 }}>Zotero Collection</label>
            <CollectionSelector value={collection} onChange={setCollection} />
          </div>

          <div style={{ marginBottom: '1.5rem' }}>
            <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500 }}>Paragraph to Verify</label>
            <textarea 
              value={paragraph}
              onChange={(e) => setParagraph(e.target.value)}
              placeholder="Paste the paragraph with academic claims here..."
              rows={6}
              style={{
                width: '100%',
                padding: '0.75rem',
                borderRadius: '8px',
                border: '1px solid var(--surface-border)',
                background: 'rgba(0,0,0,0.05)',
                color: 'var(--text-primary)',
                resize: 'vertical'
              }}
            />
          </div>

          <button 
            className="btn btn-primary" 
            onClick={handleVerify}
            disabled={isVerifying || !collection || !paragraph}
            style={{ width: '100%', gap: '0.5rem' }}
          >
            {isVerifying ? <Loader2 className="animate-spin" size={18} /> : <Send size={18} />}
            {isVerifying ? 'Verifying...' : 'Run Verification'}
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

export default ClaimVerifier;
