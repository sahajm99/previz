import React from 'react';
import { LocationSuggestion } from './types';

interface Props {
  location: LocationSuggestion;
  isShortlisted?: boolean;
  onToggleShortlist?: (loc: LocationSuggestion, current: boolean) => void;
  onFindSimilar?: (loc: LocationSuggestion) => void;
  onAddToCanvas?: (loc: LocationSuggestion) => void;
}

export const LocationCard: React.FC<Props> = ({
  location,
  isShortlisted = false,
  onToggleShortlist,
  onFindSimilar,
  onAddToCanvas,
}) => {
  const scorePct = location.vibe_match_score ? Math.round(location.vibe_match_score * 100) : 85;
  const badgeClass = scorePct >= 90 ? 'vibe-badge high' : 'vibe-badge medium';
  const imgUrl = location.photo_url || location.street_view_url || `https://images.unsplash.com/photo-1555396273-367ea4eb4db5?auto=format&fit=crop&w=800&q=80`;

  return (
    <div className="location-card">
      <div className="card-img-wrapper">
        <img src={imgUrl} alt={location.name} className="card-img" />
        <div className={badgeClass}>{scorePct}% Vibe Match</div>
      </div>

      <div className="card-content">
        <h3 className="card-title">{location.name}</h3>
        <p className="card-address">{location.address}</p>

        {location.vibe_reasoning && (
          <div className="vibe-reason">
            <strong>AI Vibe Analysis:</strong> {location.vibe_reasoning}
          </div>
        )}

        <div className="tag-list">
          <span className="tag">Budget: {location.budget_tier}</span>
          <span className="tag">{location.permit_status}</span>
          {location.place_types.slice(0, 2).map((t, idx) => (
            <span key={idx} className="tag">{t.replace('_', ' ')}</span>
          ))}
        </div>

        <div className="card-actions">
          {onToggleShortlist && (
            <button
              className={`btn-action ${isShortlisted ? 'primary' : ''}`}
              onClick={() => onToggleShortlist(location, isShortlisted)}
            >
              {isShortlisted ? '★ Shortlisted' : '☆ Shortlist'}
            </button>
          )}

          {onFindSimilar && (
            <button className="btn-action" onClick={() => onFindSimilar(location)}>
              Similar Places
            </button>
          )}

          {onAddToCanvas && (
            <button className="btn-action primary" onClick={() => onAddToCanvas(location)}>
              + Canvas
            </button>
          )}
        </div>
      </div>
    </div>
  );
};
