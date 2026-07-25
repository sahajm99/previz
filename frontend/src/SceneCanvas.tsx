import React from 'react';
import { CanvasBoard, CanvasNode } from './types';

interface Props {
  board: CanvasBoard;
  onUpdateNode?: (node: CanvasNode) => void;
}

export const SceneCanvas: React.FC<Props> = ({ board }) => {
  const { nodes = [], connections = [], logistics_summary = {} } = board;

  return (
    <div>
      <div className="logistics-banner">
        <div>
          <strong>Production Logistics Summary:</strong> {logistics_summary.notes || 'No logistics calculated yet.'}
        </div>
        <div style={{ display: 'flex', gap: '16px' }}>
          <span>📍 <strong>{logistics_summary.total_scenes || nodes.length}</strong> Scenes</span>
          <span>🚗 <strong>~{logistics_summary.est_travel_time_mins || 28} mins</strong> Total Travel Time</span>
        </div>
      </div>

      <div className="canvas-board">
        {nodes.map((node) => {
          const locName = node.location?.name || 'Unassigned Location';
          const locAddr = node.location?.address || 'Click to attach location from shortlist';
          const photoUrl = node.location?.photo_url;

          return (
            <div
              key={node.node_id}
              className="canvas-node"
              style={{ left: `${node.x}px`, top: `${node.y}px` }}
            >
              <div className="node-header">
                <span className="node-scene-id">{node.scene_id}: {node.scene_name}</span>
                <span className="node-tod">☀ {node.time_of_day}</span>
              </div>

              {photoUrl && (
                <img
                  src={photoUrl}
                  alt={locName}
                  style={{ width: '100%', height: '120px', objectFit: 'cover', borderRadius: '6px', marginBottom: '8px' }}
                />
              )}

              <div style={{ fontSize: '14px', fontWeight: 600, color: '#fff', marginBottom: '4px' }}>
                {locName}
              </div>
              <div style={{ fontSize: '12px', color: '#9CA3AF', marginBottom: '8px' }}>
                {locAddr}
              </div>

              {node.notes && (
                <div style={{ fontSize: '12px', background: 'rgba(255,255,255,0.03)', padding: '6px', borderRadius: '4px', color: '#D1D5DB' }}>
                  📝 {node.notes}
                </div>
              )}
            </div>
          );
        })}

        {connections.map((conn, idx) => (
          <div
            key={idx}
            style={{
              position: 'absolute',
              bottom: '16px',
              right: '16px',
              background: 'rgba(16, 185, 129, 0.15)',
              border: '1px solid #10B981',
              padding: '8px 14px',
              borderRadius: '20px',
              fontSize: '13px',
              color: '#10B981',
              fontWeight: 600,
            }}
          >
            🚙 Route: {conn.from_node} ➔ {conn.to_node} ({conn.travel_time_mins || 28} mins / {conn.distance_km || 9.4} km)
          </div>
        ))}
      </div>
    </div>
  );
};
