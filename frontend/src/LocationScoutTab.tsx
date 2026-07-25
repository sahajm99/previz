import React, { useEffect, useState } from 'react';
import { LocationGrid } from './LocationGrid';
import { SceneCanvas } from './SceneCanvas';
import { VibeSearchBar } from './VibeSearchBar';
import { CanvasBoard, LocationSuggestion, VibeSearchRequest } from './types';

export const LocationScoutTab: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'all' | 'shortlist' | 'canvas'>('all');
  const [locations, setLocations] = useState<LocationSuggestion[]>([]);
  const [shortlist, setShortlist] = useState<LocationSuggestion[]>([]);
  const [canvasBoard, setCanvasBoard] = useState<CanvasBoard>({ nodes: [], connections: [], logistics_summary: {} });
  const [isLoading, setIsLoading] = useState(false);

  const shortlistIds = new Set(shortlist.map((l) => l.id || l.name));

  const fetchAll = async () => {
    try {
      const res = await fetch('/api/v1/locations/all');
      if (res.ok) {
        const data = await res.json();
        setLocations(data);
      }
    } catch (e) {
      console.error('Failed to fetch locations:', e);
    }
  };

  const fetchShortlist = async () => {
    try {
      const res = await fetch('/api/v1/locations/shortlist');
      if (res.ok) {
        const data = await res.json();
        setShortlist(data);
      }
    } catch (e) {
      console.error('Failed to fetch shortlist:', e);
    }
  };

  const fetchCanvas = async () => {
    try {
      const res = await fetch('/api/v1/locations/canvas');
      if (res.ok) {
        const data = await res.json();
        setCanvasBoard(data);
      }
    } catch (e) {
      console.error('Failed to fetch canvas:', e);
    }
  };

  useEffect(() => {
    fetchAll();
    fetchShortlist();
    fetchCanvas();
  }, []);

  const handleSearch = async (req: VibeSearchRequest) => {
    setIsLoading(true);
    try {
      const res = await fetch('/api/v1/locations/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(req),
      });
      if (res.ok) {
        const data = await res.json();
        setLocations(data);
        setActiveTab('all');
      }
    } catch (e) {
      console.error('Search failed:', e);
    } finally {
      setIsLoading(false);
    }
  };

  const handleToggleShortlist = async (loc: LocationSuggestion, current: boolean) => {
    try {
      const res = await fetch('/api/v1/locations/shortlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ location: loc, shortlisted: !current }),
      });
      if (res.ok) {
        await fetchShortlist();
      }
    } catch (e) {
      console.error('Shortlist toggle failed:', e);
    }
  };

  const handleFindSimilar = async (loc: LocationSuggestion) => {
    setIsLoading(true);
    try {
      const res = await fetch('/api/v1/locations/similar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ place_id: loc.id || loc.name, limit: 3 }),
      });
      if (res.ok) {
        const data = await res.json();
        setLocations(data);
        setActiveTab('all');
      }
    } catch (e) {
      console.error('Similar search failed:', e);
    } finally {
      setIsLoading(false);
    }
  };

  const handleAddToCanvas = async (loc: LocationSuggestion) => {
    if (canvasBoard.nodes && canvasBoard.nodes.length > 0) {
      const updated = { ...canvasBoard };
      updated.nodes[0].location_id = loc.id || loc.name;
      updated.nodes[0].location = loc;
      try {
        const res = await fetch('/api/v1/locations/canvas', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(updated),
        });
        if (res.ok) {
          const data = await res.json();
          setCanvasBoard(data);
          setActiveTab('canvas');
        }
      } catch (e) {
        console.error('Canvas update failed:', e);
      }
    }
  };

  return (
    <div className="scout-container">
      <VibeSearchBar onSearch={handleSearch} isLoading={isLoading} />

      <div className="tab-header">
        <button
          className={`tab-btn ${activeTab === 'all' ? 'active' : ''}`}
          onClick={() => setActiveTab('all')}
        >
          Scouted Locations ({locations.length})
        </button>
        <button
          className={`tab-btn ${activeTab === 'shortlist' ? 'active' : ''}`}
          onClick={() => setActiveTab('shortlist')}
        >
          ★ Shortlist ({shortlist.length})
        </button>
        <button
          className={`tab-btn ${activeTab === 'canvas' ? 'active' : ''}`}
          onClick={() => setActiveTab('canvas')}
        >
          📍 Interactive Scene Canvas
        </button>
      </div>

      {activeTab === 'all' && (
        <LocationGrid
          locations={locations}
          shortlistIds={shortlistIds}
          onToggleShortlist={handleToggleShortlist}
          onFindSimilar={handleFindSimilar}
          onAddToCanvas={handleAddToCanvas}
        />
      )}

      {activeTab === 'shortlist' && (
        <LocationGrid
          locations={shortlist}
          shortlistIds={shortlistIds}
          onToggleShortlist={handleToggleShortlist}
          onFindSimilar={handleFindSimilar}
          onAddToCanvas={handleAddToCanvas}
        />
      )}

      {activeTab === 'canvas' && <SceneCanvas board={canvasBoard} />}
    </div>
  );
};
