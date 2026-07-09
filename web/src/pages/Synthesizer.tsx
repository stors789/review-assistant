import React, { useState } from 'react';
import { Send, Loader2, Folder, Search } from 'lucide-react';
import CollectionSelector from '../components/CollectionSelector';

import MarkdownViewer from '../components/MarkdownViewer';

const Synthesizer: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'collection' | 'local'>('collection');
  const [collection, setCollection] = useState('');
  const [localPath, setLocalPath] = useState('');
  const [question, setQuestion] = useState('');
  const [outputPath, setOutputPath] = useState('');
  const [autoPdf, setAutoPdf] = useState(false);
  
  const [logs, setLogs] = useState<string[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [reportPath, setReportPath] = useState<string | null>(null);
  const [articlePath, setArticlePath] = useState<string | null>(null);
  const [viewingFile, setViewingFile] = useState<string | null>(null);

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

  const handleSelectOutputFolder = async () => {
    try {
      const res = await fetch('/api/system/select-folder');
      const data = await res.json();
      if (data.path) {
        setOutputPath(data.path);
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

  const handleReset = () => {
    setLogs([]);
    setIsProcessing(false);
    setReportPath(null);
    setArticlePath(null);
  };

  const handleSynthesize = async () => {
    if (!isReady()) return;
    
    setIsProcessing(true);
    setReportPath(null);
    setArticlePath(null);
    setLogs(['Starting literature synthesis...']);

    const payload = { mode: activeTab, collection, local_path: localPath, question, output_path: outputPath, auto_pdf: autoPdf };

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
              const logMsg = data.status;
              setLogs(prev => [...prev, logMsg]);
              
              if (logMsg.includes('结构化报告:')) {
                const match = logMsg.match(/结构化报告:\s+(.*)$/);
                if (match) setReportPath(match[1]);
              }
              if (logMsg.includes('叙事文章:')) {
                const match = logMsg.match(/叙事文章:\s+(.*)$/);
                if (match) setArticlePath(match[1]);
              }
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

  const hasStarted = logs.length > 0;

  return (
    <div>
      <h1 className="page-title">Literature Synthesizer</h1>
      
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2rem' }}>
        {/* Input Form */}
        <div className="glass-panel card">
          <h2 className="section-title">New Synthesis Task</h2>
          
          <div style={{ opacity: hasStarted ? 0.5 : 1, pointerEvents: hasStarted ? 'none' : 'auto' }}>
            {/* Tabs */}
            <div className="tab-container">
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
                  <label className="form-label">Select Zotero Collection</label>
                  <CollectionSelector value={collection} onChange={setCollection} />
                </>
              )}

              {activeTab === 'local' && (
                <>
                  <label className="form-label">Select Local Folder</label>
                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <input 
                      type="text" 
                      value={localPath} 
                      onChange={(e) => setLocalPath(e.target.value)} 
                      placeholder="Absolute path to folder with PDFs" 
                      className="input-field"
                      style={{ flex: 1, background: 'rgba(0,0,0,0.05)' }}
                    />
                    <button className="btn" onClick={handleSelectLocalFolder} style={{ background: 'var(--surface-color)' }}>Browse</button>
                  </div>
                </>
              )}
            </div>

            <div style={{ marginBottom: '1.5rem' }}>
              <label className="form-label">Research Question</label>
              <textarea 
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="e.g. What are the current limitations of Graph Neural Networks in molecular property prediction?"
                rows={4}
                className="input-field"
                style={{ resize: 'vertical' }}
              />
            </div>

            <div style={{ marginBottom: '1.5rem' }}>
              <label className="form-label">Output Directory (Optional)</label>
              <div style={{ display: 'flex', gap: '0.5rem' }}>
                <input 
                  type="text" 
                  value={outputPath} 
                  onChange={(e) => setOutputPath(e.target.value)} 
                  placeholder="Default: output/" 
                  className="input-field"
                  style={{ flex: 1, background: 'rgba(0,0,0,0.05)' }}
                />
                <button className="btn" onClick={handleSelectOutputFolder} style={{ background: 'var(--surface-color)' }}>Browse</button>
              </div>
            </div>
            
            <div style={{ marginBottom: '1.5rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <input 
                type="checkbox" 
                id="autoPdfSynth" 
                checked={autoPdf} 
                onChange={(e) => setAutoPdf(e.target.checked)} 
                style={{ width: '16px', height: '16px', accentColor: 'var(--accent-color)' }}
              />
              <label htmlFor="autoPdfSynth" style={{ color: 'var(--text-primary)', cursor: 'pointer' }}>
                Automatically export to PDF
              </label>
            </div>
          </div>

          {!hasStarted ? (
            <button 
              className="btn btn-primary" 
              onClick={handleSynthesize}
              disabled={isProcessing || !isReady()}
              style={{ width: '100%', gap: '0.5rem' }}
            >
              {isProcessing ? <Loader2 className="animate-spin" size={18} /> : <Send size={18} />}
              {isProcessing ? 'Synthesizing...' : 'Run Synthesis'}
            </button>
          ) : (
            <div style={{ display: 'flex', gap: '1rem' }}>
              <button 
                className="btn btn-primary" 
                onClick={handleReset}
                disabled={isProcessing}
                style={{ flex: 1, gap: '0.5rem' }}
              >
                {isProcessing ? <Loader2 className="animate-spin" size={18} /> : null}
                {isProcessing ? 'Synthesizing...' : 'New Analysis'}
              </button>
              {(!isProcessing && reportPath) && (
                <button 
                  className="btn" 
                  onClick={() => setViewingFile(reportPath)}
                  style={{ flex: 1, background: 'var(--surface-color)' }}
                >
                  View Report
                </button>
              )}
              {(!isProcessing && articlePath) && (
                <button 
                  className="btn" 
                  onClick={() => setViewingFile(articlePath)}
                  style={{ flex: 1, background: 'var(--surface-color)' }}
                >
                  View Article
                </button>
              )}
            </div>
          )}
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
      
      {viewingFile && (
        <MarkdownViewer filePath={viewingFile} onClose={() => setViewingFile(null)} />
      )}
    </div>
  );
};

export default Synthesizer;
