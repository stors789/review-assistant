import React, { useState } from 'react';
import { Send, Loader2, Folder, File, Search } from 'lucide-react';
import CollectionSelector from '../components/CollectionSelector';
import ZoteroItemSelector from '../components/ZoteroItemSelector';

const PaperBreakdown: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'collection' | 'item' | 'local'>('collection');
  const [collection, setCollection] = useState('');
  const [item, setItem] = useState('');
  const [localPath, setLocalPath] = useState('');
  
  const [logs, setLogs] = useState<string[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);

  const handleSelectLocalFile = async () => {
    try {
      const res = await fetch('/api/system/select-file');
      const data = await res.json();
      if (data.path) {
        setLocalPath(data.path);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const isReady = () => {
    if (activeTab === 'collection') return !!collection;
    if (activeTab === 'item') return !!collection && !!item;
    if (activeTab === 'local') return !!localPath;
    return false;
  };

  const handleBreakdown = async () => {
    if (!isReady()) return;
    
    setIsProcessing(true);
    setLogs(['Starting paper breakdown process...']);

    const payload = { mode: activeTab, collection, item, local_path: localPath };

    try {
      const response = await fetch('/api/tasks/breakdown', {
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
      <h1 className="page-title">Paper Breakdown</h1>
      
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2rem' }}>
        {/* Input Form */}
        <div className="glass-panel card">
          <h2 className="section-title">New Breakdown Task</h2>
          
          {/* Tabs */}
          <div className="tab-container">
            <button className={`btn ${activeTab === 'collection' ? 'btn-primary' : ''}`} onClick={() => setActiveTab('collection')} style={{ padding: '0.5rem 1rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <Folder size={16}/> Collection
            </button>
            <button className={`btn ${activeTab === 'item' ? 'btn-primary' : ''}`} onClick={() => setActiveTab('item')} style={{ padding: '0.5rem 1rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <File size={16}/> Single Zotero PDF
            </button>
            <button className={`btn ${activeTab === 'local' ? 'btn-primary' : ''}`} onClick={() => setActiveTab('local')} style={{ padding: '0.5rem 1rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <Search size={16}/> Local PDF
            </button>
          </div>
          
          <div style={{ marginBottom: '1.5rem', minHeight: '120px' }}>
            {activeTab === 'collection' && (
              <>
                <label className="form-label">Select Zotero Collection</label>
                <CollectionSelector value={collection} onChange={setCollection} />
                <p style={{ marginTop: '0.5rem', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>Extract structured fields from all PDFs in this collection.</p>
              </>
            )}
            
            {activeTab === 'item' && (
              <>
                <label className="form-label">Select Zotero Collection</label>
                <div style={{ marginBottom: '1rem' }}>
                  <CollectionSelector value={collection} onChange={setCollection} />
                </div>
                <label className="form-label">Select PDF Paper</label>
                <ZoteroItemSelector collection={collection} value={item} onChange={setItem} />
              </>
            )}

            {activeTab === 'local' && (
              <>
                <label className="form-label">Select Local PDF File</label>
                <div style={{ display: 'flex', gap: '0.5rem' }}>
                  <input 
                    type="text" 
                    value={localPath} 
                    onChange={(e) => setLocalPath(e.target.value)} 
                    placeholder="Absolute path to .pdf" 
                    className="input-field"
                    style={{ flex: 1, background: 'rgba(0,0,0,0.05)' }}
                  />
                  <button className="btn" onClick={handleSelectLocalFile} style={{ background: 'var(--surface-color)' }}>Browse</button>
                </div>
              </>
            )}
          </div>

          <button 
            className="btn btn-primary" 
            onClick={handleBreakdown}
            disabled={isProcessing || !isReady()}
            style={{ width: '100%', gap: '0.5rem' }}
          >
            {isProcessing ? <Loader2 className="animate-spin" size={18} /> : <Send size={18} />}
            {isProcessing ? 'Processing...' : 'Start Breakdown'}
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

export default PaperBreakdown;
