import React, { useState } from 'react';
import { VibeSearchRequest } from './types';

interface Props {
  onSearch: (req: VibeSearchRequest) => void;
  isLoading?: boolean;
}

export const VibeSearchBar: React.FC<Props> = ({ onSearch, isLoading }) => {
  const [query, setQuery] = useState('Moody 1980s neon-lit diner with red vinyl booths near downtown NYC under $5k/day');
  const [region, setRegion] = useState('New York, NY');
  const [budgetTier, setBudgetTier] = useState('Low');
  const [timeOfDay, setTimeOfDay] = useState('Night');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    onSearch({
      query,
      region,
      budget_tier: budgetTier,
      time_of_day: timeOfDay,
      limit: 6,
    });
  };

  return (
    <div className="search-bar-panel">
      <form onSubmit={handleSubmit}>
        <div className="search-input-group">
          <input
            type="text"
            className="vibe-input"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Describe the cinematic vibe, architecture, era, or lighting needed for your scene..."
            disabled={isLoading}
          />
          <button type="submit" className="search-btn" disabled={isLoading}>
            {isLoading ? 'Scouting AI...' : 'Scout Locations'}
          </button>
        </div>

        <div className="filter-pills">
          <span className="filter-label">Filters:</span>
          
          <select
            className="pill-select"
            value={region}
            onChange={(e) => setRegion(e.target.value)}
          >
            <option value="New York, NY">Region: New York, NY</option>
            <option value="Los Angeles, CA">Region: Los Angeles, CA</option>
            <option value="London, UK">Region: London, UK</option>
            <option value="Tokyo, Japan">Region: Tokyo, Japan</option>
          </select>

          <select
            className="pill-select"
            value={budgetTier}
            onChange={(e) => setBudgetTier(e.target.value)}
          >
            <option value="Free">Budget: Free / Student</option>
            <option value="Low">Budget: Low (&lt; $5k/day)</option>
            <option value="High">Budget: High (&gt; $5k/day)</option>
          </select>

          <select
            className="pill-select"
            value={timeOfDay}
            onChange={(e) => setTimeOfDay(e.target.value)}
          >
            <option value="Day">Time: Day / Sunlight</option>
            <option value="Night">Time: Night / Neon</option>
            <option value="Magic Hour">Time: Magic Hour / Sunset</option>
          </select>
        </div>
      </form>
    </div>
  );
};
