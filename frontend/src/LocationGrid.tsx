import React from 'react';
import { LocationCard } from './LocationCard';
import { LocationSuggestion } from './types';

interface Props {
  locations: LocationSuggestion[];
  shortlistIds?: Set<string>;
  onToggleShortlist?: (loc: LocationSuggestion, current: boolean) => void;
  onFindSimilar?: (loc: LocationSuggestion) => void;
  onAddToCanvas?: (loc: LocationSuggestion) => void;
}

export const LocationGrid: React.FC<Props> = ({
  locations,
  shortlistIds = new Set(),
  onToggleShortlist,
  onFindSimilar,
  onAddToCanvas,
}) => {
  if (!locations || locations.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '60px 20px', color: '#9CA3AF' }}>
        <h3>No locations found</h3>
        <p>Try searching for a different cinematic vibe or adjusting your filters.</p>
      </div>
    );
  }

  return (
    <div className="grid-layout">
      {locations.map((loc) => {
        const id = loc.id || loc.name;
        return (
          <LocationCard
            key={id}
            location={loc}
            isShortlisted={shortlistIds.has(id)}
            onToggleShortlist={onToggleShortlist}
            onFindSimilar={onFindSimilar}
            onAddToCanvas={onAddToCanvas}
          />
        );
      })}
    </div>
  );
};
