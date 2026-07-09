import React, { useState } from 'react';
import { Send, Loader2, Folder, Search } from 'lucide-react';
import CollectionSelector from '../components/CollectionSelector';

const Synthesizer: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'collection' | 'local'>('collection');
  const [collection, setCollection] = useState('');
  const [localPath, setLocalPath] = useState('');
  const [question, setQuestion] = useState('');
  
  const [logs, setLogs] = useState<string[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);

  const handleSelectLocalFolder = async () => {
    try {
      const res = await fetch('/api/system/select-folder');
      const data = await res.json();
      if (data.path) {
        setLocalPath(data.path);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const isReady = () => {
    if (activeTab === 'collection') return !!collection && !!question;
    if (activeTab === 'local') return !!localPath && !!question;
    return false;
  };

  const handleSynthesize = async () => {
    if (!isReady()) return;
    
    setIsProcessing(true);
    setLogs(['Starting literature synthesis...']);

    const payload = { mode: activeTab, collection, local_path: localPath, question };

    try {
      const response = await fetch('/api/tasks/synthesize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
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
      <h1 className="page-title">Literature Synthesizer</h1>
      
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2rem' }}>
        {/* Input Form */}
        <div className="glass-panel card">
          <h2 style={{ fontSize: '1.2rem', marginBottom: '1rem', fontWeight: 600 }}>New Synthesis Task</h2>
          
          {/* Tabs */}
          <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1.5rem', borderBottom: '1px solid var(--surface-border)', paddingBottom: '0.5rem' }}>
            <button className={`btn ${activeTab === 'collection' ? 'btn-primary' : ''}`} onClick={() => setActiveTab('collection')} style={{ padding: '0.5rem 1rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <Folder size={16}/> Collection
            </button>
            <button className={`btn ${activeTab === 'local' ? 'btn-primary' : ''}`} onClick={() => setActiveTab('local')} style={{ padding: '0.5rem 1rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <Search size={16}/> Local Folder
            </button>
          </div>

          <div style={{ marginBottom: '1.5rem', minHeight: '80px' }}>
            {activeTab === 'collection' && (
              <>
                <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500 }}>Select Zotero Collection</label>
                <CollectionSelector value={collection} onChange={setCollection} />
              </>
            )}

            {activeTab === 'local' && (
              <>
                <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500 }}>Select Local Folder</label>
                <div style={{ display: 'flex', gap: '0.5rem' }}>
                  <input 
                    type="text" 
                    value={localPath} 
                    onChange={(e) => setLocalPath(e.target.value)} 
                    placeholder="Absolute path to folder with PDFs" 
                    style={{ flex: 1, padding: '0.75rem', borderRadius: '8px', border: '1px solid var(--surface-border)', background: 'rgba(0,0,0,0.05)', color: 'var(--text-primary)' }}
                  />
                  <button className="btn" onClick={handleSelectLocalFolder} style={{ background: 'var(--surface-color)' }}>Browse</button>
                </div>
              </>
            )}
          </div>

          <div style={{ marginBottom: '1.5rem' }}>
            <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500 }}>Research Question</label>
            <textarea 
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="e.g. What are the current limitations of Graph Neural Networks in molecular property prediction?"
              rows={4}
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
            onClick={handleSynthesize}
            disabled={isProcessing || !isReady()}
            style={{ width: '100%', gap: '0.5rem' }}
          >
            {isProcessing ? <Loader2 className="animate-spin" size={18} /> : <Send size={18} />}
            {isProcessing ? 'Synthesizing...' : 'Run Synthesis'}
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

export default Synthesizer;
