import React, { useEffect, useState, useCallback, useRef } from 'react';
import { ActivePage, TopBar } from './components/common/TopBar';
import { ToastContainer, ToastItem } from './components/common/ToastContainer';
import { MatchDetailsView } from './components/details/MatchDetailsView';
import { DiagnosticDashboard } from './components/diagnostics/DiagnosticDashboard';
import { GoalPredictionsView } from './components/goals/GoalPredictionsView';
import { HomeView } from './components/home/HomeView';
import { MatchesView } from './components/matches/MatchesView';
import { SettingsView } from './components/settings/SettingsView';
import { apiService } from './services/api';
import { diagnosticsService } from './services/diagnostics';
import { BackendServiceStatus, GoalPredictionItem, PredictionModel, ValidatedFixture } from './types';

export default function App() {
  const [activePage, setActivePage] = useState<ActivePage>('home');
  const [selectedFixture, setSelectedFixture] = useState<ValidatedFixture | null>(null);

  // Active Model State
  const [activeModel, setActiveModel] = useState<PredictionModel>(apiService.getActiveModel());

  // Data states
  const [fixtures, setFixtures] = useState<ValidatedFixture[]>([]);
  const [goalPredictions, setGoalPredictions] = useState<GoalPredictionItem[]>([]);
  const [backendStatus, setBackendStatus] = useState<BackendServiceStatus | null>(null);

  // Loading & workflow states
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isRefreshingStatus, setIsRefreshingStatus] = useState<boolean>(false);
  const [isSyncingScores, setIsSyncingScores] = useState<boolean>(false);
  const [refreshResult, setRefreshResult] = useState<any>(null);
  const [syncScoresResult, setSyncScoresResult] = useState<any>(null);

  // Real-time Toast Notifications
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const prevNeonStatusRef = useRef<string | null>(null);

  const addToast = useCallback((toast: Omit<ToastItem, 'id'>) => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).substr(2, 5)}`;
    setToasts((prev) => {
      // Prevent duplicate identical error toasts within 10 seconds
      const exists = prev.some((t) => t.title === toast.title && t.type === toast.type);
      if (exists) return prev;
      return [...prev, { ...toast, id }];
    });

    if (toast.autoClose !== false) {
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, toast.duration || 8000);
    }
  }, []);

  const handleDismissToast = (id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  };

  // Synchronize data from production FastAPI backend
  const loadData = async (isRetry = false) => {
    if (!isRetry) setIsLoading(true);
    try {
      const [fixturesRes, goalsRes, statusRes] = await Promise.all([
        apiService.fetchValidatedFixtures(),
        apiService.fetchGoalPredictions(),
        apiService.fetchBackendStatus(),
      ]);
      if (fixturesRes.error) {
        console.warn('[PredictPro] Fixtures query notice:', fixturesRes.error);
      }
      if (goalsRes.error) {
        console.warn('[PredictPro] Goals query notice:', goalsRes.error);
      }

      setFixtures(fixturesRes.data || []);
      setGoalPredictions(goalsRes.data || []);
      setBackendStatus(statusRes);

      // Check Neon status and alert if disconnected/degraded
      const neonRaw = statusRes.neonStatus || statusRes.neonPostgres?.status || (statusRes as any).Neon;
      const currentNeonStatus = String(neonRaw || 'disconnected').toLowerCase();

      if (
        prevNeonStatusRef.current !== null &&
        prevNeonStatusRef.current === 'connected' &&
        currentNeonStatus !== 'connected'
      ) {
        addToast({
          type: 'error',
          title: 'Neon Postgres Disconnected',
          message: 'Backend health check failed: Neon PostgreSQL connection dropped or unreachable.',
          code: 'NEON_CONN_ERR',
          actionLabel: 'Inspect Diagnostics',
          onAction: () => {
            setActivePage('diagnostics');
            window.scrollTo({ top: 0, behavior: 'smooth' });
          },
        });
      }
      prevNeonStatusRef.current = currentNeonStatus;

      // If backend was initializing and returned empty or notice, retry once automatically
      const isWarmup =
        (fixturesRes.error && fixturesRes.error.toLowerCase().includes('initializing')) ||
        (goalsRes.error && goalsRes.error.toLowerCase().includes('initializing'));
      if (isWarmup && !isRetry) {
        setTimeout(() => loadData(true), 1500);
      }
    } catch (err: any) {
      console.error('[PredictPro] Error loading live data:', err);
      setFixtures([]);
      setGoalPredictions([]);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadData();
    // Periodic health poller to detect disconnects in real-time
    const poller = setInterval(async () => {
      try {
        const quick = await diagnosticsService.getQuickStatus();
        if (!quick.neonConnected && prevNeonStatusRef.current === 'connected') {
          addToast({
            type: 'error',
            title: 'Neon Connection Warning',
            message: `Neon returned as ${quick.status}. ${quick.details || 'Connection dropped.'}`,
            code: 'NEON_STATUS_ERR',
            actionLabel: 'View Breakdown',
            onAction: () => {
              setActivePage('diagnostics');
              window.scrollTo({ top: 0, behavior: 'smooth' });
            },
          });
        }
        prevNeonStatusRef.current = quick.neonConnected ? 'connected' : 'disconnected';
      } catch {
        // Ignore background polling errors
      }
    }, 15000);

    return () => clearInterval(poller);
  }, [addToast]);

  const handleSelectFixture = (fixture: ValidatedFixture) => {
    setSelectedFixture(fixture);
    setActivePage('details');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const handleOpenFixtureById = (fixtureId: string) => {
    const found = fixtures.find((f) => f.id === fixtureId);
    if (found) {
      setSelectedFixture(found);
      setActivePage('details');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  };

  const handleModelChange = async (model: PredictionModel) => {
    setActiveModel(model);
    await apiService.updateModelConfig(model);
    if (selectedFixture) {
      setSelectedFixture({ ...selectedFixture, modelVersion: model });
    }
    loadData();
  };

  const handleRefreshStatus = async () => {
    setIsRefreshingStatus(true);
    const status = await apiService.fetchBackendStatus();
    setBackendStatus(status);
    setTimeout(() => setIsRefreshingStatus(false), 500);
  };

  // 1. Refresh Predictions Workflow: triggers prediction pipeline refresh
  const handleTriggerRefreshPredictions = async (adminKeyOverride?: string) => {
    setIsRefreshingStatus(true);
    const res = await apiService.triggerAdminRefresh(adminKeyOverride);
    if (res.success && res.data) {
      setRefreshResult(res.data);
      await loadData();
      const status = await apiService.fetchBackendStatus();
      setBackendStatus(status);
      addToast({
        type: 'success',
        title: 'Predictions Synchronized',
        message: `Successfully synchronized ${res.data.matches_synced || 0} matches and published ${res.data.predictions_published || 0} predictions.`,
        duration: 5000,
      });
    } else {
      console.warn('[PredictPro] Refresh Predictions notice:', res.error);
      setRefreshResult({
        id: 'failed-' + Date.now(),
        timestamp: new Date().toISOString(),
        status: 'failed',
        matches_synced: 0,
        predictions_published: 0,
        duration_ms: 0,
        error: res.error || 'Refresh failed',
        errors: [res.error || 'Refresh failed'],
        statusCode: res.status,
      });
      const status = await apiService.fetchBackendStatus();
      setBackendStatus(status);
      addToast({
        type: 'warning',
        title: 'Refresh Alert',
        message: res.error || 'Refresh completed with warnings. Check diagnostics for details.',
        actionLabel: 'Open Diagnostics',
        onAction: () => {
          setActivePage('diagnostics');
          window.scrollTo({ top: 0, behavior: 'smooth' });
        },
      });
    }
    setIsRefreshingStatus(false);
  };

  // 2. Sync Scores Workflow: score/result synchronization and evaluation
  const handleTriggerSyncScores = async (adminKeyOverride?: string) => {
    setIsSyncingScores(true);
    const res = await apiService.triggerSyncFeed(adminKeyOverride);
    if (res.success && res.data) {
      setSyncScoresResult(res.data);
      await loadData();
      const status = await apiService.fetchBackendStatus();
      setBackendStatus(status);
      addToast({
        type: 'success',
        title: 'Scores Evaluated',
        message: `Synchronized and calibrated live fixture outcomes.`,
        duration: 5000,
      });
    } else {
      console.warn('[PredictPro] Sync Scores notice:', res.error);
      setSyncScoresResult({
        status: 'failed',
        timestamp: new Date().toISOString(),
        errors: [res.error || 'Sync Scores failed'],
        error: res.error || 'Sync Scores failed',
        statusCode: res.status,
      });
      const status = await apiService.fetchBackendStatus();
      setBackendStatus(status);
      addToast({
        type: 'warning',
        title: 'Sync Feed Alert',
        message: res.error || 'Sync Scores reported a warning.',
        actionLabel: 'Open Diagnostics',
        onAction: () => {
          setActivePage('diagnostics');
          window.scrollTo({ top: 0, behavior: 'smooth' });
        },
      });
    }
    setIsSyncingScores(false);
  };

  const rawNeon = (backendStatus?.neonStatus || backendStatus?.neonPostgres?.status || 'disconnected').toLowerCase();
  const neonStatusForTopBar: 'connected' | 'degraded' | 'disconnected' | 'not_configured' =
    rawNeon === 'connected'
      ? 'connected'
      : rawNeon === 'degraded'
      ? 'degraded'
      : rawNeon === 'not_configured'
      ? 'not_configured'
      : 'disconnected';

  return (
    <div className="min-h-screen bg-[#080c14] text-slate-100 flex flex-col font-sans">
      {/* 1. Header Navigation Bar */}
      <TopBar
        activePage={activePage}
        onNavigate={(page) => {
          setActivePage(page);
          window.scrollTo({ top: 0, behavior: 'smooth' });
        }}
        activeModel={activeModel}
        neonStatus={neonStatusForTopBar}
      />

      {/* 2. System Timezone Sub-Bar */}
      <div className="border-b border-zinc-800 bg-[#0c111c] px-3 py-1.5 sm:px-4">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-1 text-[11px] text-zinc-400 font-mono">
          <div className="flex flex-wrap items-center gap-1.5 sm:gap-2">
            <span className="text-zinc-300 font-medium">PredictPro Engine:</span>
            <span>Africa/Lagos (WAT, UTC+1)</span>
            <span aria-hidden="true" className="hidden sm:inline text-zinc-600">·</span>
            <span className="hidden sm:inline">Active Model: <strong className="text-zinc-200">{activeModel}</strong></span>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => {
                setActivePage('diagnostics');
                window.scrollTo({ top: 0, behavior: 'smooth' });
              }}
              className="flex items-center gap-1.5 text-zinc-400 hover:text-emerald-400 transition-colors"
              title="Open Neon Diagnostics"
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  neonStatusForTopBar === 'connected'
                    ? 'bg-emerald-400'
                    : neonStatusForTopBar === 'degraded'
                    ? 'bg-amber-400'
                    : neonStatusForTopBar === 'not_configured'
                    ? 'bg-zinc-500'
                    : 'bg-red-400'
                }`}
              />
              <span>
                Neon:{' '}
                {neonStatusForTopBar === 'connected'
                  ? 'Connected'
                  : neonStatusForTopBar === 'degraded'
                  ? 'Degraded'
                  : neonStatusForTopBar === 'not_configured'
                  ? 'Not Configured'
                  : 'Disconnected'}
              </span>
            </button>
          </div>
        </div>
      </div>

      {/* 3. Main Content Viewport */}
      <main className="mx-auto w-full max-w-7xl flex-1 px-3.5 py-4 sm:px-6 sm:py-6 lg:px-8 pb-20 md:pb-8">
        {activePage === 'home' && (
          <HomeView
            fixtures={fixtures}
            isLoading={isLoading}
            onSelectFixture={handleSelectFixture}
            onRefresh={() => loadData()}
          />
        )}

        {activePage === 'matches' && (
          <MatchesView
            onSelectFixture={handleSelectFixture}
          />
        )}

        {activePage === 'goals' && (
          <GoalPredictionsView
            goalItems={goalPredictions}
            isLoading={isLoading}
            onOpenFixture={handleOpenFixtureById}
            onRefresh={() => loadData()}
          />
        )}

        {activePage === 'diagnostics' && (
          <DiagnosticDashboard
            onRefreshData={() => loadData()}
          />
        )}

        {activePage === 'settings' && backendStatus && (
          <SettingsView
            backendStatus={backendStatus}
            currentModel={activeModel}
            onModelChange={handleModelChange}
            onRefreshStatus={handleRefreshStatus}
            isRefreshing={isRefreshingStatus}
            onTriggerRefresh={handleTriggerRefreshPredictions}
            refreshResult={refreshResult}
            onTriggerSyncScores={handleTriggerSyncScores}
            isSyncingScores={isSyncingScores}
            syncScoresResult={syncScoresResult}
          />
        )}

        {activePage === 'details' && selectedFixture && (
          <MatchDetailsView
            fixture={selectedFixture}
            onBack={() => setActivePage('home')}
          />
        )}
      </main>

      {/* Real-time Toast Notifications */}
      <ToastContainer toasts={toasts} onDismiss={handleDismissToast} />

      {/* 4. Professional Footer */}
      <footer className="mt-auto border-t border-zinc-800 bg-[#060910] py-4 text-xs text-zinc-500">
        <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-2 px-4 sm:px-6 lg:px-8 sm:flex-row">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-zinc-400">PredictPro</span>
            <span aria-hidden="true">·</span>
            <span>Sports Analytics & Probabilistic Intelligence</span>
          </div>
          <div className="flex items-center gap-3 text-[11px] font-mono">
            <span>Africa/Lagos (WAT, UTC+1)</span>
            <span aria-hidden="true">·</span>
            <button
              onClick={() => {
                setActivePage('diagnostics');
                window.scrollTo({ top: 0, behavior: 'smooth' });
              }}
              className="text-zinc-500 hover:text-emerald-400 transition-colors"
            >
              System Diagnostics
            </button>
          </div>
        </div>
      </footer>
    </div>
  );
}

