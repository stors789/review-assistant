import React, { createContext, useContext, useState, useEffect } from 'react';

export interface ZoteroCollection {
  name: string;
  total: number;
  has_attachment: number;
  missing: number;
}

interface ZoteroContextType {
  collections: ZoteroCollection[];
  loading: boolean;
  error: string;
  refreshCollections: () => Promise<void>;
}

const ZoteroContext = createContext<ZoteroContextType | undefined>(undefined);

export const ZoteroProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [collections, setCollections] = useState<ZoteroCollection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const fetchCollections = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch('/api/zotero/collections');
      if (!res.ok) throw new Error('Failed to fetch collections');
      const data = await res.json();
      setCollections(data.collections || []);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCollections();
  }, []);

  return (
    <ZoteroContext.Provider value={{ collections, loading, error, refreshCollections: fetchCollections }}>
      {children}
    </ZoteroContext.Provider>
  );
};

export const useZotero = () => {
  const context = useContext(ZoteroContext);
  if (context === undefined) {
    throw new Error('useZotero must be used within a ZoteroProvider');
  }
  return context;
};
