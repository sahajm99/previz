import React from 'react';
import { LocationScoutTab } from './LocationScoutTab';
import './index.css';

export const App: React.FC = () => {
  return (
    <div style={{ minHeight: '100vh', backgroundColor: 'var(--bg-main)' }}>
      <header style={{ padding: '20px 24px', borderBottom: '1px solid var(--border-color)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: '22px', color: '#fff', letterSpacing: '0.5px' }}>
            🎬 PREVIZ <span style={{ color: 'var(--accent-indigo)', fontSize: '16px', fontWeight: 500 }}>// AI Filmmaking Suite</span>
          </h1>
        </div>
        <div style={{ fontSize: '13px', color: 'var(--accent-emerald)', fontWeight: 600, background: 'rgba(16, 185, 129, 0.1)', padding: '6px 14px', borderRadius: '20px', border: '1px solid var(--accent-emerald)' }}>
          ● Vertex AI Multi-Modal Engine Active
        </div>
      </header>

      <main>
        <LocationScoutTab />
      </main>
    </div>
  );
};
