export type SportType = 'football' | 'basketball' | 'baseball' | 'ice_hockey' | 'formula_1';

export type FixtureStatus = 'upcoming' | 'live' | 'completed';

export type PredictionModel =
  | 'ELO'
  | 'POISSON'
  | 'ELO + POISSON'
  | 'GLICKO2'
  | 'GRADIENT_BOOSTING'
  | 'GLICKO2 + GRADIENT_BOOSTING'
  | 'ELO + POISSON + GLICKO2'
  | 'ELO + POISSON + GLICKO2 + GRADIENT_BOOSTING';

export type ModelTier = 'production' | 'evaluation';

export interface ApiErrorInfo {
  status: number | string;
  message: string;
  sport?: string;
  stage?: string;
  details?: any;
}

export interface ModelMetadata {
  id: PredictionModel;
  name: string;
  tier: ModelTier;
  description: string;
  isProductionReady: boolean;
  version: string;
}

export interface ScoreState {
  home: number;
  away: number;
  periodOrMinute?: string; // e.g. "64'", "Q3 4:12", "Top 7th", "Lap 42/58"
}

export interface MarketItem {
  id: string;
  marketName: string; // e.g. "Win/Draw/Loss", "Both Teams To Score", "Over/Under 2.5 Goals", "Point Spread"
  selection: string;  // e.g. "Arsenal to Win", "Yes", "Over 2.5", "Lakers -4.5"
  probabilityPercentage: number; // validated percentage, e.g. 74.5
  confidenceRating?: 'High' | 'Solid' | 'Moderate';
  sportSpecificCategory?: string;
  isValidated: boolean;
}

export interface F1DriverStat {
  driverId: string;
  driverName: string;
  constructorId: string;
  constructorName: string;
  rating?: number;
  winProbability?: number;
  podiumProbability?: number;
  top10Probability?: number;
  recentFinishingPosition?: number;
  recentQualifyingPosition?: number;
  recentForm?: string;
}

export interface SportSpecificStats {
  // Football
  xGRecentHome?: number;
  xGRecentAway?: number;
  homeCleanSheets?: number;
  awayCleanSheets?: number;
  avgMatchCorners?: number;

  // Basketball
  pace?: number;
  homePPG?: number;
  awayPPG?: number;
  reboundDifferential?: number;

  // Baseball
  homeERA?: number;
  awayERA?: number;
  bullpenWHIP?: number;
  battingAvg?: number;

  // Ice Hockey
  goalsPerGame?: number;
  powerPlayPct?: number;
  savePct?: number;
  homeGoalsScoredAvg?: number;
  awayGoalsScoredAvg?: number;
  homeGoalsConcededAvg?: number;
  awayGoalsConcededAvg?: number;
  homeShotsAvg?: number;
  awayShotsAvg?: number;
  homeRecentForm?: string;
  awayRecentForm?: string;

  // Formula 1
  gridPosition?: number;
  constructorStanding?: string;
  poleConversionRate?: number;
  circuitName?: string;
  f1Drivers?: F1DriverStat[];
}

export interface ValidatedFixture {
  id: string;
  sport: SportType;
  league: string; // e.g. "UEFA Champions League (UCL)", "Major League Baseball (MLB)", "Premier League"
  homeTeam: string;
  awayTeam: string;
  kickoffUtc: string; // ISO 8601 UTC timestamp
  status: FixtureStatus;
  currentScore?: ScoreState;
  finalScore?: ScoreState;
  venue?: string;
  referee?: string;
  highestPercentagePrediction?: {
    marketName: string;
    selection: string;
    percentage: number;
  } | null;
  validatedMarkets?: MarketItem[];
  sportStats?: SportSpecificStats;
  validationStatus?: 'validated' | 'unpredicted' | 'abstained' | 'rejected' | string;
  modelVersion?: PredictionModel;
  predictionAvailable?: boolean;
  isBestOfDay?: boolean;
}

export interface OperationalFixture extends ValidatedFixture {}

export interface PaginatedFixturesResponse {
  data: OperationalFixture[];
  total: number;
  page: number;
  limit: number;
  offset: number;
  error?: string;
  errorInfo?: ApiErrorInfo;
}

export interface GoalPredictionItem {
  id: string;
  fixtureId: string;
  league: string;
  homeTeam: string;
  awayTeam: string;
  kickoffUtc: string;
  status: FixtureStatus;
  marketType: 'Over 1.5' | 'Over 2.5' | 'Under 2.5' | 'Over 3.5' | 'BTTS Yes' | 'BTTS No' | 'First Half Over 0.5' | string;
  predictedOutcome: string;
  percentage: number;
  xGCombined?: number | null;
  homeAvgScored?: number | null;
  awayAvgScored?: number | null;
  bothTeamsScoredRecentRate?: number | null;
  modelVersion: PredictionModel;
}

export type ConnectionState = 'connected' | 'degraded' | 'disconnected' | 'connecting';

export interface BackendServiceStatus {
  sportsSkills: {
    status: ConnectionState;
    latencyMs: number;
    lastHeartbeat: string;
    version: string;
    error?: string;
  };
  mongoDb: {
    status: ConnectionState;
    latencyMs: number;
    poolActive: number;
    cluster: string;
    error?: string;
  };
  neonPostgres?: {
    status: 'connected' | 'disconnected' | 'degraded';
    latencyMs?: number;
    error?: string;
    failoverReady?: boolean;
    schemaCompatible?: boolean;
    safetyState?: string;
  };
  redis: {
    status: ConnectionState;
    latencyMs: number;
    cacheHitRate: number;
    memoryUsedMb: number;
    error?: string;
  };
  r2Storage: {
    status: ConnectionState;
    latencyMs: number;
    syncedArtifacts: number;
    bucketName: string;
    error?: string;
  };
  duckDb: {
    status: ConnectionState;
    latencyMs: number;
    queryEngine: string;
    inMemoryCatalogRows: number;
    error?: string;
  };
  activeDatabase?: 'mongodb' | 'neon' | string;
  failoverState?: string;
  activeModel: PredictionModel;
  lastSyncTimestamp: string;
  mongoDbStatus?: string;
  neonStatus?: string;
  neonDashboard?: any;
}
