import React, { useState, useEffect } from 'react';
import { Save, Loader2, Key, Database, Globe, FolderOpen } from 'lucide-react';

const Settings: React.FC = () => {
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [envPath, setEnvPath] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null);

  useEffect(() => {
    Promise.all([
      fetch('/api/settings/').then(res => res.json()),
      fetch('/api/settings/env-path').then(res => res.json())
    ])
    .then(([settingsData, pathData]) => {
      const s = settingsData.settings || {};
      Object.keys(s).forEach(k => {
        if ((k.startsWith('DEEPSEEK_API_KEY_') || k.startsWith('OPENAI_API_KEY_')) && s[k] === '') {
          s[k] = '__REMOVED__';
        }
      });
      setSettings(s);
      setEnvPath(pathData.path || '');
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
      const finalSettings = { ...settings };
      Object.keys(finalSettings).forEach(k => {
        if (finalSettings[k] === '__REMOVED__') finalSettings[k] = '';
      });
      const res = await fetch('/api/settings/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings: finalSettings })
      });
      if (!res.ok) throw new Error('Failed to save settings');
      setMessage({ type: 'success', text: 'Settings saved successfully.' });
    } catch (err: any) {
      setMessage({ type: 'error', text: err.message });
    } finally {
      setSaving(false);
    }
  };

  // Extract extra API keys
  const extraKeys = Object.keys(settings).filter(k => 
    (k.startsWith('DEEPSEEK_API_KEY_') || k.startsWith('OPENAI_API_KEY_')) && 
    k !== 'DEEPSEEK_API_KEY' && k !== 'OPENAI_API_KEY'
  ).sort();

  const handleAddKey = () => {
    const nextIdx = extraKeys.length ? Math.max(...extraKeys.map(k => parseInt(k.split('_').pop() || '1') || 1)) + 1 : 2;
    const provider = (settings['OPENAI_API_KEY'] !== undefined && settings['DEEPSEEK_API_KEY'] === undefined) ? 'OPENAI_API_KEY_' : 'DEEPSEEK_API_KEY_';
    handleChange(`${provider}${nextIdx}`, '');
  };

  const handleRemoveKey = (k: string) => {
    setSettings(prev => {
      const copy = { ...prev };
      copy[k] = '__REMOVED__';
      return copy;
    });
  };

  const handleSelectEnv = async () => {
    try {
      const res = await fetch('/api/system/select-env-file');
      const data = await res.json();
      if (data.path) {
        setEnvPath(data.path);
        await fetch('/api/settings/env-path', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: data.path })
        });
        
        // Reload settings from new path
        setLoading(true);
        const settingsRes = await fetch('/api/settings/');
        const settingsData = await settingsRes.json();
        const s = settingsData.settings || {};
        Object.keys(s).forEach(k => {
          if ((k.startsWith('DEEPSEEK_API_KEY_') || k.startsWith('OPENAI_API_KEY_')) && s[k] === '') {
            s[k] = '__REMOVED__';
          }
        });
        setSettings(s);
        setMessage({ type: 'success', text: `Loaded settings from ${data.path}` });
        setLoading(false);
      }
    } catch (err) {
      console.error(err);
      setMessage({ type: 'error', text: 'Failed to select environment file.' });
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

      {/* Global Configuration */}
      <div className="glass-panel card" style={{ marginBottom: '1.5rem' }}>
        <h2 className="section-title">
          <FolderOpen size={20} color="var(--accent-color)" /> Configuration Location
        </h2>
        <div style={{ display: 'flex', gap: '1rem', alignItems: 'center' }}>
          <input 
            type="text" 
            value={envPath} 
            readOnly
            className="input-field"
            placeholder="No environment folder selected"
            style={{ flex: 1, background: 'rgba(0,0,0,0.05)' }}
          />
          <button className="btn btn-primary" onClick={handleSelectEnv} style={{ padding: '0.75rem 1.5rem' }}>
            Choose Folder
          </button>
        </div>
      </div>

      {/* LLM Configuration */}
      <div className="glass-panel card" style={{ marginBottom: '1.5rem' }}>
        <h2 className="section-title" style={{ justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <Key size={20} color="var(--accent-color)" /> LLM Provider
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.9rem', fontWeight: 'normal' }}>
             <label>Concurrency (Workers):</label>
             <input 
               type="number" 
               min="1" max="20"
               value={settings['REVIEW_ASSISTANT_WORKERS'] || '5'}
               onChange={(e) => handleChange('REVIEW_ASSISTANT_WORKERS', e.target.value)}
               title="Set to 1 to disable concurrent requests"
               className="input-field" style={{ width: '60px', padding: '0.25rem 0.5rem' }}
             />
          </div>
        </h2>
        
        <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '1rem' }}>
          <div>
            <label className="form-label">Primary API Key</label>
            <input 
              type="password" 
              value={settings['DEEPSEEK_API_KEY'] || settings['OPENAI_API_KEY'] || ''}
              onChange={(e) => handleChange(settings['DEEPSEEK_API_KEY'] !== undefined ? 'DEEPSEEK_API_KEY' : 'OPENAI_API_KEY', e.target.value)}
              placeholder="sk-..."
              className="input-field"
              
            />
          </div>
          
          {extraKeys.map((k) => settings[k] !== '__REMOVED__' && (
            <div key={k} style={{ display: 'flex', gap: '0.5rem', alignItems: 'flex-end' }}>
              <div style={{ flex: 1 }}>
                <label className="form-label" style={{ color: 'var(--text-secondary)' }}>Backup API Key ({k})</label>
                <input 
                  type="password" 
                  value={settings[k]}
                  onChange={(e) => handleChange(k, e.target.value)}
                  placeholder="sk-..."
                  className="input-field"
                  
                />
              </div>
              <button className="btn" style={{ height: '46px', color: '#ef4444', borderColor: '#ef4444' }} onClick={() => handleRemoveKey(k)}>Remove</button>
            </div>
          ))}

          <button className="btn" onClick={handleAddKey} style={{ alignSelf: 'flex-start', fontSize: '0.9rem', padding: '0.5rem 1rem' }}>+ Add Extra Key for Parallel Workflows</button>

          <div>
            <label className="form-label" style={{ marginTop: '1rem' }}>Base URL</label>
            <input 
              type="text" 
              value={settings['REVIEW_ASSISTANT_BASE_URL'] || ''}
              onChange={(e) => handleChange('REVIEW_ASSISTANT_BASE_URL', e.target.value)}
              placeholder="https://api.deepseek.com"
              className="input-field"
              
            />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
            <div>
              <label className="form-label">Primary Model</label>
              <input 
                type="text" 
                value={settings['REVIEW_ASSISTANT_MODEL'] || ''}
                onChange={(e) => handleChange('REVIEW_ASSISTANT_MODEL', e.target.value)}
                placeholder="deepseek-v4-pro"
                className="input-field"
                
              />
            </div>
            <div>
              <label className="form-label">Step 7 Model (Tables/Diagrams)</label>
              <input 
                type="text" 
                value={settings['REVIEW_ASSISTANT_STEP7_MODEL'] || ''}
                onChange={(e) => handleChange('REVIEW_ASSISTANT_STEP7_MODEL', e.target.value)}
                placeholder="deepseek-v4-pro"
                className="input-field"
                
              />
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginTop: '0.5rem' }}>
            <div>
              <label className="form-label">Temperature</label>
              <input 
                type="number" 
                step="0.1" min="0" max="2"
                value={settings['REVIEW_ASSISTANT_TEMPERATURE'] || '0.0'}
                onChange={(e) => handleChange('REVIEW_ASSISTANT_TEMPERATURE', e.target.value)}
                title="Higher values make output more random (0.0 - 2.0)"
                className="input-field"
                
              />
            </div>
            <div>
              <label className="form-label">Thinking Level (Reasoning Effort)</label>
              <select 
                value={settings['REVIEW_ASSISTANT_REASONING_EFFORT'] || 'high'}
                onChange={(e) => handleChange('REVIEW_ASSISTANT_REASONING_EFFORT', e.target.value)}
                className="input-field"
                
              >
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </select>
            </div>
          </div>

          <div style={{ marginTop: '0.5rem' }}>
            <label className="form-label">Global System Prompt Prefix</label>
            <textarea 
              value={settings['REVIEW_ASSISTANT_SYSTEM_PROMPT_PREFIX'] || ''}
              onChange={(e) => handleChange('REVIEW_ASSISTANT_SYSTEM_PROMPT_PREFIX', e.target.value)}
              placeholder="e.g. Always respond in Traditional Chinese... (Optional)"
              rows={3}
              className="input-field"
              style={{ resize: 'vertical' }}
            />
          </div>
        </div>
      </div>

      {/* Literature APIs */}
      <div className="glass-panel card" style={{ marginBottom: '1.5rem' }}>
        <h2 className="section-title">
          <Globe size={20} color="var(--accent-color)" /> Literature Search APIs
        </h2>
        
        <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '1rem' }}>
          <div>
            <label className="form-label">Semantic Scholar API Key</label>
            <input 
              type="password" 
              value={settings['SS_API_KEY'] || ''}
              onChange={(e) => handleChange('SS_API_KEY', e.target.value)}
              className="input-field"
              
            />
          </div>
          <div>
            <label className="form-label">PubMed API Key</label>
            <input 
              type="password" 
              value={settings['PUBMED_API_KEY'] || ''}
              onChange={(e) => handleChange('PUBMED_API_KEY', e.target.value)}
              className="input-field"
              
            />
          </div>
        </div>
      </div>

      {/* Zotero API */}
      <div className="glass-panel card" style={{ marginBottom: '1.5rem' }}>
        <h2 className="section-title">
          <Database size={20} color="var(--accent-color)" /> Zotero Cloud Sync
        </h2>
        
        <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '1rem' }}>
          <div>
            <label className="form-label">Zotero API Key</label>
            <input 
              type="password" 
              value={settings['ZOTERO_API_KEY'] || ''}
              onChange={(e) => handleChange('ZOTERO_API_KEY', e.target.value)}
              className="input-field"
              
            />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
            <div>
              <label className="form-label">Library Type</label>
              <select 
                value={settings['ZOTERO_LIBRARY_TYPE'] || 'user'}
                onChange={(e) => handleChange('ZOTERO_LIBRARY_TYPE', e.target.value)}
                className="input-field"
                
              >
                <option value="user">Personal (user)</option>
                <option value="group">Group</option>
              </select>
            </div>
            <div>
              <label className="form-label">Library ID</label>
              <input 
                type="text" 
                value={settings['ZOTERO_LIBRARY_ID'] || ''}
                onChange={(e) => handleChange('ZOTERO_LIBRARY_ID', e.target.value)}
                className="input-field"
                
              />
            </div>
          </div>
        </div>
      </div>

    </div>
  );
};

export default Settings;
