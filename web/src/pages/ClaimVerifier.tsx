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
          <h2 className="section-title">New Verification Task</h2>
          
          <div style={{ marginBottom: '1rem' }}>
            <label className="form-label">Zotero Collection</label>
            <CollectionSelector value={collection} onChange={setCollection} />
          </div>

          <div style={{ marginBottom: '1.5rem' }}>
            <label className="form-label">Paragraph to Verify</label>
            <textarea 
              value={paragraph}
              onChange={(e) => setParagraph(e.target.value)}
              placeholder="Paste the paragraph with academic claims here..."
              rows={6}
              className="input-field"
              style={{ resize: 'vertical' }}
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
          <h2 className="section-title">Live Progress</h2>
          <div className="log-box">
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
