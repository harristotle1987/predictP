import {
  BackendServiceStatus,
  FixtureStatus,
  GoalPredictionItem,
  PredictionModel,
  SportType,
  ValidatedFixture,
  ApiErrorInfo,
} from '../types';
import { isTodayOrFutureInLagos } from '../utils/timezone';

export interface FixtureFilterParams {
  sport?: SportType | 'all';
  league?: string;
  status?: FixtureStatus | 'all';
  date?: string; // YYYY-MM-DD in Africa/Lagos
}

async function fetchWithRetry(
  url: string,
  init?: RequestInit,
  retries = 3,
  delayMs = 1000
): Promise<Response> {
  let lastResponse: Response | null = null;
  let lastError: any = null;

  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const res = await fetch(url, init);
      lastResponse = res;
      const contentType = res.headers.get('content-type') || '';
      // If server returned warmup page (HTTP 200 with text/html) or 5xx/429 during startup, retry
      if (res.status >= 500 || res.status === 429 || (res.ok && contentType.includes('text/html'))) {
        if (attempt < retries) {
          await new Promise((r) => setTimeout(r, delayMs * (attempt + 1)));
          continue;
        }
      }
      return res;
    } catch (err) {
      lastError = err;
      if (attempt < retries) {
        await new Promise((r) => setTimeout(r, delayMs * (attempt + 1)));
        continue;
      }
    }
  }

  if (lastResponse) return lastResponse;
  throw lastError || new Error(`Network request failed for ${url}`);
}

async function safeJsonParse(res: Response): Promise<any> {
  const text = await res.text().catch(() => '');
  if (!text || text.trim().startsWith('<')) {
    throw new Error(`PredictPro engine is initializing (HTTP ${res.status}). Please retry in a few moments.`);
  }
  return JSON.parse(text);
}

function parseApiError(res: Response, rawBody: any, defaultSport?: string, defaultStage?: string): ApiErrorInfo {
  let message = `Request failed: HTTP ${res.status} ${res.statusText}`;
  let sport = defaultSport && defaultSport !== 'all' ? defaultSport : undefined;
  let stage = defaultStage;

  if (rawBody && typeof rawBody === 'object') {
    if (rawBody.detail) {
      message = typeof rawBody.detail === 'string' ? rawBody.detail : JSON.stringify(rawBody.detail);
    } else if (rawBody.message) {
      message = rawBody.message;
    } else if (rawBody.error) {
      message = typeof rawBody.error === 'string' ? rawBody.error : JSON.stringify(rawBody.error);
    }
    if (rawBody.sport) sport = rawBody.sport;
    if (rawBody.stage || rawBody.firstFailingStage) stage = rawBody.stage || rawBody.firstFailingStage;
  } else if (typeof rawBody === 'string' && rawBody.trim()) {
    message = rawBody;
  }

  return {
    status: res.status,
    message,
    sport,
    stage,
    details: rawBody,
  };
}

/**
 * PredictPro Client Presentation Service
 * React is presentation only. It never calculates predictions or calls SportsSkills.
 */
class PredictProApiService {
  private activeModel: PredictionModel = 'ELO + POISSON';
  private adminKey: string | null = null;

  public getAdminKey(): string | null {
    if (this.adminKey) return this.adminKey;
    try {
      const stored = sessionStorage.getItem('predictpro_admin_api_key') || localStorage.getItem('predictpro_admin_api_key');
      if (stored) {
        this.adminKey = stored;
        return stored;
      }
    } catch {
      // Ignore storage access errors
    }
    return null;
  }

  public setAdminKey(key: string | null): void {
    this.adminKey = key;
    try {
      if (key) {
        sessionStorage.setItem('predictpro_admin_api_key', key);
      } else {
        sessionStorage.removeItem('predictpro_admin_api_key');
        localStorage.removeItem('predictpro_admin_api_key');
      }
    } catch {
      // Ignore storage errors
    }
  }

  public getActiveModel(): PredictionModel {
    const saved = localStorage.getItem('predictpro_active_model');
    if (saved) {
      return saved as PredictionModel;
    }
    return this.activeModel;
  }

  public setActiveModel(model: PredictionModel): void {
    this.activeModel = model;
    localStorage.setItem('predictpro_active_model', model);
  }

  /**
   * Fetches published predictions feed from /api/predictions/feed with optional date parameter.
   * Query format: /api/predictions/feed?date=YYYY-MM-DD
   */
  public async fetchPredictionFeed(
    filters?: FixtureFilterParams
  ): Promise<{ data: ValidatedFixture[]; error?: string; errorInfo?: ApiErrorInfo; count?: number }> {
    try {
      const query = new URLSearchParams();
      if (filters?.sport && filters.sport !== 'all') {
        query.append('sport', filters.sport);
      }
      if (filters?.league && filters.league !== 'all') {
        query.append('league', filters.league);
      }
      if (filters?.date) {
        query.append('date', filters.date);
      }

      const queryString = query.toString();
      const url = queryString ? `/api/predictions/feed?${queryString}` : '/api/predictions/feed';

      const res = await fetchWithRetry(url, {
        headers: { Accept: 'application/json' },
      });

      if (!res.ok) {
        const text = await res.text().catch(() => '');
        let jsonBody: any = null;
        try {
          jsonBody = JSON.parse(text);
        } catch {
          jsonBody = text;
        }
        const errInfo = parseApiError(res, jsonBody, filters?.sport, 'PREDICTION_FEED');
        return {
          data: [],
          error: errInfo.message,
          errorInfo: errInfo,
        };
      }

      const json = await safeJsonParse(res);
      const rawList = Array.isArray(json) ? json : json.data || [];

      if (!Array.isArray(rawList)) {
        return { data: [] };
      }

      return { data: rawList.slice(0, 20), count: rawList.length };
    } catch (error) {
      const msg = error instanceof Error ? error.message : 'Prediction feed request failed';
      return {
        data: [],
        error: msg,
        errorInfo: {
          status: 'NETWORK_ERROR',
          message: msg,
          sport: filters?.sport && filters.sport !== 'all' ? filters.sport : undefined,
          stage: 'PREDICTION_FEED',
        },
      };
    }
  }

  /**
   * Fetches operational fixtures catalogue from /api/fixtures with pagination and filters.
   * Does NOT restrict dataset to 20 or require predictions.
   */
  public async fetchOperationalFixtures(params?: {
    sport?: SportType | 'all';
    league?: string;
    status?: FixtureStatus | 'all';
    date?: string; // YYYY-MM-DD in Africa/Lagos
    startDate?: string;
    endDate?: string;
    limit?: number;
    page?: number;
    offset?: number;
  }): Promise<{ data: ValidatedFixture[]; total: number; page: number; limit: number; error?: string; errorInfo?: ApiErrorInfo }> {
    try {
      const query = new URLSearchParams();
      if (params?.sport && params.sport !== 'all') {
        query.append('sport', params.sport);
      }
      if (params?.league && params.league !== 'all') {
        query.append('league', params.league);
      }
      if (params?.status && params.status !== 'all') {
        query.append('status', params.status);
      }
      if (params?.date) {
        query.append('date', params.date);
      }
      if (params?.startDate) {
        query.append('startDate', params.startDate);
      }
      if (params?.endDate) {
        query.append('endDate', params.endDate);
      }
      if (params?.limit) {
        query.append('limit', String(params.limit));
      }
      if (params?.page) {
        query.append('page', String(params.page));
      } else if (params?.offset !== undefined) {
        query.append('offset', String(params.offset));
      }

      const queryString = query.toString();
      const url = queryString ? `/api/fixtures?${queryString}` : '/api/fixtures';

      const res = await fetchWithRetry(url, {
        headers: { Accept: 'application/json' },
      });

      if (!res.ok) {
        const text = await res.text().catch(() => '');
        let jsonBody: any = null;
        try {
          jsonBody = JSON.parse(text);
        } catch {
          jsonBody = text;
        }
        const errInfo = parseApiError(res, jsonBody, params?.sport, 'FIXTURES_CATALOGUE');
        return {
          data: [],
          total: 0,
          page: params?.page || 1,
          limit: params?.limit || 50,
          error: errInfo.message,
          errorInfo: errInfo,
        };
      }

      const json = await safeJsonParse(res);
      const list = Array.isArray(json) ? json : json.data || [];
      const total = typeof json.total === 'number' ? json.total : list.length;
      const currentPage = typeof json.page === 'number' ? json.page : (params?.page || 1);
      const limit = typeof json.limit === 'number' ? json.limit : (params?.limit || 50);

      return {
        data: Array.isArray(list) ? list : [],
        total,
        page: currentPage,
        limit,
      };
    } catch (error) {
      const msg = error instanceof Error ? error.message : 'Operational fixtures request failed';
      return {
        data: [],
        total: 0,
        page: params?.page || 1,
        limit: params?.limit || 50,
        error: msg,
        errorInfo: {
          status: 'NETWORK_ERROR',
          message: msg,
          sport: params?.sport && params.sport !== 'all' ? params.sport : undefined,
          stage: 'FIXTURES_CATALOGUE',
        },
      };
    }
  }

  /**
   * Fetches single fixture / match details from /api/fixtures/:id
   */
  public async fetchFixtureById(matchId: string): Promise<{ data: ValidatedFixture | null; error?: string }> {
    try {
      const res = await fetchWithRetry(`/api/fixtures/${encodeURIComponent(matchId)}`, {
        headers: { Accept: 'application/json' },
      });
      if (!res.ok) {
        return { data: null, error: `Fixture ${matchId} not found` };
      }
      const json = await safeJsonParse(res);
      return { data: json };
    } catch (error) {
      return { data: null, error: error instanceof Error ? error.message : 'Failed to load fixture detail' };
    }
  }

  /**
   * Fetches published predictions from backend API with date filter support.
   */
  public async fetchPublishedPredictions(
    filters?: FixtureFilterParams
  ): Promise<{ data: ValidatedFixture[]; error?: string; errorInfo?: ApiErrorInfo }> {
    try {
      const query = new URLSearchParams();
      if (filters?.sport && filters.sport !== 'all') {
        query.append('sport', filters.sport);
      }
      if (filters?.league && filters.league !== 'all') {
        query.append('league', filters.league);
      }
      if (filters?.status && filters.status !== 'all') {
        query.append('status', filters.status);
      }
      if (filters?.date) {
        query.append('date', filters.date);
      }

      const queryString = query.toString();
      const url = queryString ? `/api/predictions/feed?${queryString}` : '/api/predictions/feed';

      const res = await fetchWithRetry(url, {
        headers: { Accept: 'application/json' },
      });

      if (!res.ok) {
        const text = await res.text().catch(() => '');
        let jsonBody: any = null;
        try {
          jsonBody = JSON.parse(text);
        } catch {
          jsonBody = text;
        }
        const errInfo = parseApiError(res, jsonBody, filters?.sport, 'FIXTURES_FETCH');
        return {
          data: [],
          error: errInfo.message,
          errorInfo: errInfo,
        };
      }

      const raw = await safeJsonParse(res);
      const list = Array.isArray(raw) ? raw : raw.data || [];

      if (!Array.isArray(list)) {
        return { data: [] };
      }

      let filtered = list;
      if (!filters?.date) {
        filtered = list.filter((f: ValidatedFixture) => isTodayOrFutureInLagos(f.kickoffUtc));
      }

      return { data: filtered.slice(0, 20) };
    } catch (error) {
      const msg = error instanceof Error ? error.message : 'Published predictions request failed';
      return {
        data: [],
        error: msg,
        errorInfo: {
          status: 'NETWORK_ERROR',
          message: msg,
          sport: filters?.sport && filters.sport !== 'all' ? filters.sport : undefined,
          stage: 'FIXTURES_FETCH',
        },
      };
    }
  }

  /**
   * Alias for fetchPublishedPredictions for backward compatibility.
   */
  public async fetchValidatedFixtures(
    filters?: FixtureFilterParams
  ): Promise<{ data: ValidatedFixture[]; error?: string; errorInfo?: ApiErrorInfo }> {
    return this.fetchPublishedPredictions(filters);
  }

  /**
   * Fetches available dates with published predictions
   */
  public async fetchAvailableDates(): Promise<{ data: string[]; error?: string }> {
    try {
      const url = '/api/predictions/available-dates';
      const res = await fetchWithRetry(url, {
        headers: { Accept: 'application/json' },
      });
      if (!res.ok) {
        const body = await res.text().catch(() => '');
        return {
          data: [],
          error: `GET ${url} failed: HTTP ${res.status} ${res.statusText}${body ? ` — ${body}` : ''}`,
        };
      }
      const json = await safeJsonParse(res);
      const dates = Array.isArray(json.availableDates)
        ? json.availableDates
        : Array.isArray(json)
        ? json
        : [];
      return { data: dates };
    } catch (error) {
      return {
        data: [],
        error: error instanceof Error ? error.message : 'Available dates request failed',
      };
    }
  }

  /**
   * Fetches evaluation feed from backend API
   */
  public async fetchEvaluationFeed(): Promise<{ data: any; error?: string }> {
    try {
      const url = '/api/predictions/evaluation';
      const res = await fetchWithRetry(url, {
        headers: { Accept: 'application/json' },
      });
      if (!res.ok) {
        const body = await res.text().catch(() => '');
        return {
          data: null,
          error: `GET ${url} failed: HTTP ${res.status} ${res.statusText}${body ? ` — ${body}` : ''}`,
        };
      }
      const data = await safeJsonParse(res);
      return { data };
    } catch (error) {
      return {
        data: null,
        error: error instanceof Error ? error.message : 'Evaluation feed request failed',
      };
    }
  }

  /**
   * Fetches goal-specific predictions for football from backend API
   */
  public async fetchGoalPredictions(): Promise<{ data: GoalPredictionItem[]; error?: string }> {
    try {
      const url = '/api/predictions/goals';
      const res = await fetchWithRetry(url, {
        headers: { Accept: 'application/json' },
      });

      if (!res.ok) {
        const body = await res.text().catch(() => '');
        return {
          data: [],
          error: `GET ${url} failed: HTTP ${res.status} ${res.statusText}${body ? ` — ${body}` : ''}`,
        };
      }

      const raw = await safeJsonParse(res);
      if (!Array.isArray(raw)) {
        return { data: [] };
      }

      const filtered = raw.filter((item: GoalPredictionItem) =>
        isTodayOrFutureInLagos(item.kickoffUtc)
      );

      return { data: filtered };
    } catch (error) {
      return {
        data: [],
        error: error instanceof Error ? error.message : 'Goal predictions request failed',
      };
    }
  }

  /**
   * Fetches single fixture details for Match Details page
   */
  public async fetchFixtureDetails(id: string): Promise<{ data: ValidatedFixture | null; error?: string }> {
    try {
      const url = `/api/fixtures/${encodeURIComponent(id)}`;
      const res = await fetchWithRetry(url, {
        headers: { Accept: 'application/json' },
      });

      if (!res.ok) {
        const body = await res.text().catch(() => '');
        return {
          data: null,
          error: `GET ${url} failed: HTTP ${res.status} ${res.statusText}${body ? ` — ${body}` : ''}`,
        };
      }

      const data = await safeJsonParse(res);
      return { data };
    } catch (error) {
      return {
        data: null,
        error: error instanceof Error ? error.message : 'Fixture details request failed',
      };
    }
  }

  /**
   * Fetches backend services connection health
   */
  public async fetchBackendStatus(): Promise<BackendServiceStatus> {
    try {
      const res = await fetchWithRetry('/api/settings/status', {
        headers: { Accept: 'application/json' },
      });

      if (res.ok) {
        return await safeJsonParse(res);
      }
    } catch {
      // Fall through to awaiting state
    }

    // Default status reflecting backend pipeline waiting for Step 2 deployment
    return {
      sportsSkills: {
        status: 'disconnected',
        latencyMs: 0,
        lastHeartbeat: new Date().toISOString(),
        version: 'SportsSkills v2.8',
      },
      mongoDb: {
        status: 'disconnected',
        latencyMs: 0,
        poolActive: 0,
        cluster: 'predictpro-primary-cluster',
      },
      neonPostgres: {
        status: 'disconnected',
        latencyMs: 0,
        failoverReady: false,
      },
      redis: {
        status: 'disconnected',
        latencyMs: 0,
        cacheHitRate: 0,
        memoryUsedMb: 0,
      },
      r2Storage: {
        status: 'disconnected',
        latencyMs: 0,
        syncedArtifacts: 0,
        bucketName: 'predictpro-model-weights',
      },
      duckDb: {
        status: 'disconnected',
        latencyMs: 0,
        queryEngine: 'DuckDB In-Memory Analytical Core',
        inMemoryCatalogRows: 0,
      },
      activeModel: this.getActiveModel(),
      lastSyncTimestamp: new Date().toISOString(),
    };
  }

  /**
   * Updates active prediction model on the backend
   */
  public async updateModelConfig(model: PredictionModel, adminKeyOverride?: string): Promise<void> {
    this.setActiveModel(model);
    try {
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      const key = adminKeyOverride || this.getAdminKey();
      if (key) {
        headers['x-admin-api-key'] = key;
      }
      await fetch('/api/settings/model', {
        method: 'POST',
        headers,
        body: JSON.stringify({ activeModel: model }),
      });
    } catch {
      // Handled gracefully
    }
  }

  /**
   * Triggers administrative refresh & feature sync
   */
  public async triggerAdminRefresh(
    optionsOrKey?: { sports?: string[]; date?: string; date_range?: string[]; competitions?: string[]; force?: boolean } | string,
    adminKeyOverride?: string
  ): Promise<{ success: boolean; data?: any; error?: string; status?: number }> {
    try {
      const url = '/api/admin/refresh';
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };
      
      let options: { sports?: string[]; date?: string; date_range?: string[]; competitions?: string[]; force?: boolean } = {};
      let key = this.getAdminKey();

      if (typeof optionsOrKey === 'string') {
        key = optionsOrKey;
      } else if (optionsOrKey && typeof optionsOrKey === 'object') {
        options = optionsOrKey;
        if (adminKeyOverride) key = adminKeyOverride;
      } else if (adminKeyOverride) {
        key = adminKeyOverride;
      }

      if (key) {
        headers['x-admin-api-key'] = key;
      }

      const res = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify(options),
      });
      if (res.ok) {
        const data = await res.json();
        return { success: true, data, status: res.status };
      }
      const rawText = await res.text().catch(() => '');
      let errorMsg = `Refresh failed: HTTP ${res.status} ${res.statusText}`;
      try {
        const parsed = JSON.parse(rawText);
        if (parsed.detail) errorMsg = typeof parsed.detail === 'string' ? parsed.detail : JSON.stringify(parsed.detail);
        else if (parsed.message) errorMsg = parsed.message;
        else if (parsed.error) errorMsg = parsed.error;
      } catch {
        if (rawText) errorMsg += ` — ${rawText}`;
      }

      return {
        success: false,
        status: res.status,
        error: errorMsg,
      };
    } catch (error) {
      return {
        success: false,
        error: error instanceof Error ? error.message : 'Refresh request failed',
      };
    }
  }

  /**
   * Triggers administrative SportsSkills score & result sync
   */
  public async triggerSyncFeed(adminKeyOverride?: string): Promise<{ success: boolean; data?: any; error?: string; status?: number }> {
    try {
      const url = '/api/admin/sync-feed';
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };
      const key = adminKeyOverride || this.getAdminKey();
      if (key) {
        headers['x-admin-api-key'] = key;
      }

      const res = await fetch(url, {
        method: 'POST',
        headers,
      });
      if (res.ok) {
        const data = await res.json();
        return { success: true, data, status: res.status };
      }
      const rawText = await res.text().catch(() => '');
      let errorMsg = `Sync Feed failed: HTTP ${res.status} ${res.statusText}`;
      try {
        const parsed = JSON.parse(rawText);
        if (parsed.detail) errorMsg = typeof parsed.detail === 'string' ? parsed.detail : JSON.stringify(parsed.detail);
        else if (parsed.message) errorMsg = parsed.message;
        else if (parsed.error) errorMsg = parsed.error;
      } catch {
        if (rawText) errorMsg += ` — ${rawText}`;
      }

      return {
        success: false,
        status: res.status,
        error: errorMsg,
      };
    } catch (error) {
      return {
        success: false,
        error: error instanceof Error ? error.message : 'Sync Feed request failed',
      };
    }
  }

  /**
   * Updates NEON_DATABASE_URL environment variable and re-establishes Neon PostgreSQL connection
   */
  public async updateNeonUrl(neonUrl: string, adminKeyOverride?: string): Promise<{ success: boolean; data?: any; error?: string }> {
    try {
      const url = '/api/admin/config/neon';
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };
      const key = adminKeyOverride || this.getAdminKey();
      if (key) {
        headers['x-admin-api-key'] = key;
      }

      const res = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify({ neon_database_url: neonUrl }),
      });

      if (res.ok) {
        const data = await res.json();
        return { success: true, data };
      }

      const rawText = await res.text().catch(() => '');
      let errorMsg = `HTTP ${res.status} ${res.statusText}`;
      try {
        const parsed = JSON.parse(rawText);
        if (parsed.detail) errorMsg = typeof parsed.detail === 'string' ? parsed.detail : JSON.stringify(parsed.detail);
        else if (parsed.message) errorMsg = parsed.message;
      } catch {
        if (rawText) errorMsg = rawText;
      }

      return { success: false, error: errorMsg };
    } catch (error) {
      return {
        success: false,
        error: error instanceof Error ? error.message : 'Failed to update Neon URL',
      };
    }
  }
}

export const apiService = new PredictProApiService();
