import React, { useState, useEffect } from 'react';
import { Save, Loader2, Key, Database, Globe } from 'lucide-react';

const Settings: React.FC = () => {
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null);

  useEffect(() => {
    fetch('/api/settings/')
      .then(res => res.json())
      .then(data => {
        setSettings(data.settings || {});
        setLoading(false);
      })
      .catch(err => {
        console.error(err);
        setMessage({ type: 'error', text: 'Failed to load settings.' });
        setLoading(false);
      });
  }, []);

  const handleChange = (key: string, value: string) => {
    setSettings(prev => ({ ...prev, [key]: value }));
  };

  const handleSave = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const res = await fetch('/api/settings/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings })
      });
      if (!res.ok) throw new Error('Failed to save settings');
      setMessage({ type: 'success', text: 'Settings saved successfully.' });
    } catch (err: any) {
      setMessage({ type: 'error', text: err.message });
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-secondary)' }}><Loader2 size={16} className="animate-spin"/> Loading settings...</div>;
  }

  return (
    <div style={{ maxWidth: '800px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
        <h1 className="page-title" style={{ marginBottom: 0 }}>Settings</h1>
        <button 
          className="btn btn-primary" 
          onClick={handleSave} 
          disabled={saving}
          style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}
        >
          {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
          {saving ? 'Saving...' : 'Save Settings'}
        </button>
      </div>

      {message && (
        <div style={{ 
          padding: '1rem', 
          marginBottom: '1.5rem', 
          borderRadius: '8px', 
          background: message.type === 'success' ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)',
          color: message.type === 'success' ? '#10b981' : '#ef4444',
          border: `1px solid ${message.type === 'success' ? '#10b981' : '#ef4444'}`
        }}>
          {message.text}
        </div>
      )}

      {/* LLM Configuration */}
      <div className="glass-panel card" style={{ marginBottom: '1.5rem' }}>
        <h2 style={{ fontSize: '1.2rem', marginBottom: '1rem', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <Key size={20} color="var(--accent-color)" /> LLM Provider
        </h2>
        
        <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '1rem' }}>
          <div>
            <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500, fontSize: '0.9rem' }}>DeepSeek API Key (or OpenAI)</label>
            <input 
              type="password" 
              value={settings['DEEPSEEK_API_KEY'] || settings['OPENAI_API_KEY'] || ''}
              onChange={(e) => handleChange(settings['DEEPSEEK_API_KEY'] !== undefined ? 'DEEPSEEK_API_KEY' : 'OPENAI_API_KEY', e.target.value)}
              placeholder="sk-..."
              className="input-field"
              style={{ width: '100%', padding: '0.75rem', borderRadius: '8px', border: '1px solid var(--surface-border)', background: 'var(--bg-color)', color: 'var(--text-primary)' }}
            />
          </div>
          <div>
            <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500, fontSize: '0.9rem' }}>Base URL</label>
            <input 
              type="text" 
              value={settings['REVIEW_ASSISTANT_BASE_URL'] || ''}
              onChange={(e) => handleChange('REVIEW_ASSISTANT_BASE_URL', e.target.value)}
              placeholder="https://api.deepseek.com"
              className="input-field"
              style={{ width: '100%', padding: '0.75rem', borderRadius: '8px', border: '1px solid var(--surface-border)', background: 'var(--bg-color)', color: 'var(--text-primary)' }}
            />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
            <div>
              <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500, fontSize: '0.9rem' }}>Primary Model</label>
              <input 
                type="text" 
                value={settings['REVIEW_ASSISTANT_MODEL'] || ''}
                onChange={(e) => handleChange('REVIEW_ASSISTANT_MODEL', e.target.value)}
                placeholder="deepseek-v4-pro"
                className="input-field"
                style={{ width: '100%', padding: '0.75rem', borderRadius: '8px', border: '1px solid var(--surface-border)', background: 'var(--bg-color)', color: 'var(--text-primary)' }}
              />
            </div>
            <div>
              <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500, fontSize: '0.9rem' }}>Step 7 Model (Tables/Diagrams)</label>
              <input 
                type="text" 
                value={settings['REVIEW_ASSISTANT_STEP7_MODEL'] || ''}
                onChange={(e) => handleChange('REVIEW_ASSISTANT_STEP7_MODEL', e.target.value)}
                placeholder="deepseek-v4-pro"
                className="input-field"
                style={{ width: '100%', padding: '0.75rem', borderRadius: '8px', border: '1px solid var(--surface-border)', background: 'var(--bg-color)', color: 'var(--text-primary)' }}
              />
            </div>
          </div>
        </div>
      </div>

      {/* Literature APIs */}
      <div className="glass-panel card" style={{ marginBottom: '1.5rem' }}>
        <h2 style={{ fontSize: '1.2rem', marginBottom: '1rem', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <Globe size={20} color="var(--accent-color)" /> Literature Search APIs
        </h2>
        
        <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '1rem' }}>
          <div>
            <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500, fontSize: '0.9rem' }}>Semantic Scholar API Key</label>
            <input 
              type="password" 
              value={settings['SS_API_KEY'] || ''}
              onChange={(e) => handleChange('SS_API_KEY', e.target.value)}
              className="input-field"
              style={{ width: '100%', padding: '0.75rem', borderRadius: '8px', border: '1px solid var(--surface-border)', background: 'var(--bg-color)', color: 'var(--text-primary)' }}
            />
          </div>
          <div>
            <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500, fontSize: '0.9rem' }}>PubMed API Key</label>
            <input 
              type="password" 
              value={settings['PUBMED_API_KEY'] || ''}
              onChange={(e) => handleChange('PUBMED_API_KEY', e.target.value)}
              className="input-field"
              style={{ width: '100%', padding: '0.75rem', borderRadius: '8px', border: '1px solid var(--surface-border)', background: 'var(--bg-color)', color: 'var(--text-primary)' }}
            />
          </div>
        </div>
      </div>

      {/* Zotero API */}
      <div className="glass-panel card" style={{ marginBottom: '1.5rem' }}>
        <h2 style={{ fontSize: '1.2rem', marginBottom: '1rem', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <Database size={20} color="var(--accent-color)" /> Zotero Cloud Sync
        </h2>
        
        <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '1rem' }}>
          <div>
            <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500, fontSize: '0.9rem' }}>Zotero API Key</label>
            <input 
              type="password" 
              value={settings['ZOTERO_API_KEY'] || ''}
              onChange={(e) => handleChange('ZOTERO_API_KEY', e.target.value)}
              className="input-field"
              style={{ width: '100%', padding: '0.75rem', borderRadius: '8px', border: '1px solid var(--surface-border)', background: 'var(--bg-color)', color: 'var(--text-primary)' }}
            />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
            <div>
              <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500, fontSize: '0.9rem' }}>Library Type</label>
              <select 
                value={settings['ZOTERO_LIBRARY_TYPE'] || 'user'}
                onChange={(e) => handleChange('ZOTERO_LIBRARY_TYPE', e.target.value)}
                style={{ width: '100%', padding: '0.75rem', borderRadius: '8px', border: '1px solid var(--surface-border)', background: 'var(--bg-color)', color: 'var(--text-primary)' }}
              >
                <option value="user">Personal (user)</option>
                <option value="group">Group</option>
              </select>
            </div>
            <div>
              <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500, fontSize: '0.9rem' }}>Library ID</label>
              <input 
                type="text" 
                value={settings['ZOTERO_LIBRARY_ID'] || ''}
                onChange={(e) => handleChange('ZOTERO_LIBRARY_ID', e.target.value)}
                className="input-field"
                style={{ width: '100%', padding: '0.75rem', borderRadius: '8px', border: '1px solid var(--surface-border)', background: 'var(--bg-color)', color: 'var(--text-primary)' }}
              />
            </div>
          </div>
        </div>
      </div>

    </div>
  );
};

export default Settings;
