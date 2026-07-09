import React, { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { X, Printer, Loader2 } from 'lucide-react';

interface MarkdownViewerProps {
  filePath: string;
  onClose: () => void;
}

const MarkdownViewer: React.FC<MarkdownViewerProps> = ({ filePath, onClose }) => {
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const fetchContent = async () => {
      setLoading(true);
      try {
        const response = await fetch('/api/files/read', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: filePath })
        });
        const data = await response.json();
        
        if (data.error) {
          setError(data.error);
        } else {
          setContent(data.content);
        }
      } catch (err: any) {
        setError(err.message || 'Failed to read file');
      } finally {
        setLoading(false);
      }
    };
    
    if (filePath) {
      fetchContent();
    }
  }, [filePath]);

  const handlePrint = () => {
    window.print();
  };

  return (
    <div className="modal-overlay" style={{
      position: 'fixed',
      top: 0, left: 0, right: 0, bottom: 0,
      backgroundColor: 'rgba(0, 0, 0, 0.5)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 1000,
      backdropFilter: 'blur(4px)'
    }}>
      <div className="glass-panel" style={{
        width: '90%',
        height: '90%',
        maxWidth: '1000px',
        display: 'flex',
        flexDirection: 'column',
        backgroundColor: 'var(--bg-color)',
        border: '1px solid var(--surface-border)',
        borderRadius: '12px',
        overflow: 'hidden',
        boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5)'
      }}>
        {/* Header */}
        <div style={{
          padding: '1rem',
          borderBottom: '1px solid var(--surface-border)',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          backgroundColor: 'var(--surface-color)'
        }}>
          <h3 style={{ margin: 0, fontSize: '1.2rem', fontWeight: 600 }}>{filePath.split('/').pop() || 'Document Viewer'}</h3>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button className="btn btn-primary" onClick={handlePrint} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.5rem 1rem' }}>
              <Printer size={16} /> Export PDF (Print)
            </button>
            <button className="btn btn-icon" onClick={onClose} style={{ background: 'var(--bg-color)', border: '1px solid var(--surface-border)' }}>
              <X size={20} />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="markdown-body" style={{
          flex: 1,
          padding: '2rem',
          overflowY: 'auto',
          backgroundColor: 'var(--bg-color)',
          color: 'var(--text-primary)'
        }}>
          {loading ? (
            <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', color: 'var(--text-secondary)' }}>
              <Loader2 className="animate-spin" size={32} />
            </div>
          ) : error ? (
            <div style={{ color: 'var(--error-color)', padding: '1rem', border: '1px solid var(--error-color)', borderRadius: '8px' }}>
              <strong>Error loading file:</strong> {error}
            </div>
          ) : (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {content}
            </ReactMarkdown>
          )}
        </div>
      </div>
      
      {/* Print styles for hiding UI elements during PDF export */}
      <style>{`
        @media print {
          body * {
            visibility: hidden;
          }
          .markdown-body, .markdown-body * {
            visibility: visible;
          }
          .markdown-body {
            position: absolute;
            left: 0;
            top: 0;
            width: 100%;
            padding: 0 !important;
            overflow: visible !important;
          }
        }
      `}</style>
    </div>
  );
};

export default MarkdownViewer;
