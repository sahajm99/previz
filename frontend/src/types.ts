export interface LocationSuggestion {
  id: string;
  name: string;
  address: string;
  lat: number;
  lng: number;
  maps_url: string;
  photo_url?: string;
  place_types: string[];
  rating?: number;
  budget_tier: 'Free' | 'Low' | 'High';
  permit_status: string;
  tech_reqs: string[];
  vibe_match_score?: number;
  vibe_reasoning?: string;
  photo_references: string[];
  street_view_url?: string;
  embedding?: number[];
  similar_place_ids: string[];
}

export interface CanvasNode {
  node_id: string;
  scene_id: string;
  scene_name: string;
  location_id?: string;
  location?: LocationSuggestion;
  time_of_day: 'Day' | 'Night' | 'Magic Hour';
  x: number;
  y: number;
  notes?: string;
}

export interface CanvasConnection {
  from_node: string;
  to_node: string;
  travel_time_mins?: number;
  distance_km?: number;
}

export interface CanvasBoard {
  nodes: CanvasNode[];
  connections: CanvasConnection[];
  logistics_summary: {
    total_scenes?: number;
    est_travel_time_mins?: number;
    notes?: string;
  };
}

export interface VibeSearchRequest {
  query: string;
  region?: string;
  budget_tier?: string;
  time_of_day?: string;
  indoor_outdoor?: string;
  limit?: number;
}
