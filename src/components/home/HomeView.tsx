import React, { useState, useEffect, useCallback } from 'react';
import { SportType, ValidatedFixture, ApiErrorInfo } from '../../types';
import { EmptyState } from '../common/EmptyState';
import { SkeletonCard } from '../common/SkeletonCard';
import { FixtureCard } from './FixtureCard';
import { PredictionCalendar } from '../common/PredictionCalendar';
import { getLagosTodayYmd, formatLagosDateDisplay } from '../../utils/timezone';
import { apiService } from '../../services/api';
import {
  Calendar,
  CheckCircle2,
  RefreshCw,
  TrendingUp,
} from 'lucide-react';

interface HomeViewProps {
  fixtures: ValidatedFixture[];
  isLoading: boolean;
  onSelectFixture: (fixture: ValidatedFixture) => void;
  onRefresh: () => void;
  previewMode?: boolean;
}

const SPORTS_OPTIONS: { id: 'all' | SportType; label: string }[] = [
  { id: 'all', label: 'All Sports' },
  { id: 'football', label: 'Football' },
  { id: 'basketball', label: 'Basketball' },
  { id: 'baseball', label: 'Baseball' },
  { id: 'ice_hockey', label: 'Ice Hockey' },
  { id: 'formula_1', label: 'Formula 1' },
];

export const HomeView: React.FC<HomeViewProps> = ({
  isLoading,
  onSelectFixture,
  onRefresh,
}) => {
  const todayLagos = getLagosTodayYmd();
  const [selectedDate, setSelectedDate] = useState<string>(todayLagos);
  const [dateFixtures, setDateFixtures] = useState<ValidatedFixture[]>([]);
  const [availableDates, setAvailableDates] = useState<string[]>([todayLagos]);
  const [isLoadingDate, setIsLoadingDate] = useState<boolean>(true);
  const [feedError, setFeedError] = useState<ApiErrorInfo | null>(null);

  const [selectedSport, setSelectedSport] = useState<'all' | SportType>('all');
  const [selectedLeague, setSelectedLeague] = useState<string>('all');
  const [statusFilter, setStatusFilter] = useState<'all' | 'upcoming' | 'live' | 'completed'>('all');

  // Generate quick date carousel for Today + next 4 days in Lagos
  const quickDates = React.useMemo(() => {
    const list: { ymd: string; label: string; dayName: string }[] = [];
    const parts = todayLagos.split('-').map(Number);
    const baseDate = new Date(parts[0], parts[1] - 1, parts[2]);

    for (let i = 0; i < 5; i++) {
      const d = new Date(baseDate);
      d.setDate(baseDate.getDate() + i);
      const yyyy = d.getFullYear();
      const mm = String(d.getMonth() + 1).padStart(2, '0');
      const dd = String(d.getDate()).padStart(2, '0');
      const ymd = `${yyyy}-${mm}-${dd}`;

      let label = 'Today';
      if (i === 1) label = 'Tomorrow';
      else if (i > 1) {
        label = new Intl.DateTimeFormat('en-GB', { weekday: 'short', month: 'short', day: 'numeric' }).format(d);
      }

      const dayName = new Intl.DateTimeFormat('en-GB', { weekday: 'short' }).format(d);
      list.push({ ymd, label, dayName });
    }
    return list;
  }, [todayLagos]);

  // Fetch available dates for the calendar on mount
  useEffect(() => {
    let isMounted = true;
    const loadDates = async (retried = false) => {
      const res = await apiService.fetchAvailableDates();
      if (res.error) {
        if (!retried && res.error.toLowerCase().includes('initializing')) {
          setTimeout(() => {
            if (isMounted) loadDates(true);
          }, 1500);
        }
      }
      const dates = res.data || [];
      if (isMounted && dates.length > 0) {
        setAvailableDates(dates);
      }
    };
    loadDates();
    return () => {
      isMounted = false;
    };
  }, []);

  const [isManualRefreshing, setIsManualRefreshing] = useState<boolean>(false);
  const [emptyNotice, setEmptyNotice] = useState<string | null>(null);

  // Fetch predictions for the selected date strictly reading pre-computed feed
  const fetchForDate = useCallback(async (dateYmd: string, isWarmupRetry = false) => {
    setIsLoadingDate(true);
    setFeedError(null);
    setEmptyNotice(null);
    try {
      const res = await apiService.fetchPublishedPredictions({ date: dateYmd });
      if (res.errorInfo) {
        const isWarmup = res.errorInfo.message?.toLowerCase().includes('initializing');
        if (isWarmup && !isWarmupRetry) {
          setTimeout(() => fetchForDate(dateYmd, true), 1500);
          return;
        }
        setFeedError(res.errorInfo);
        setDateFixtures([]);
      } else if (res.error) {
        const isWarmup = res.error.toLowerCase().includes('initializing');
        if (isWarmup && !isWarmupRetry) {
          setTimeout(() => fetchForDate(dateYmd, true), 1500);
          return;
        }
        setFeedError({
          status: 'ERROR',
          message: res.error,
          stage: 'PREDICTION_FEED',
        });
        setDateFixtures([]);
      } else {
        const items = res.data || [];
        setDateFixtures(items);
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : 'Prediction feed request failed';
      setFeedError({
        status: 'NETWORK_ERROR',
        message: msg,
        stage: 'PREDICTION_FEED',
      });
      setDateFixtures([]);
    } finally {
      setIsLoadingDate(false);
    }
  }, []);

  const handleManualRefresh = async () => {
    setIsManualRefreshing(true);
    setFeedError(null);
    setEmptyNotice(null);
    try {
      // 1. POST /api/admin/refresh
      const refreshRes = await apiService.triggerRefresh({
        force: true,
        date: selectedDate,
      });

      const publishedCount =
        refreshRes?.predictions_published ??
        (refreshRes as any)?.publishedCount ??
        (refreshRes?.diagnostics as any)?.publishedCount ??
        0;

      // 2. GET /api/predictions/feed?date=YYYY-MM-DD
      const feedRes = await apiService.fetchPublishedPredictions({ date: selectedDate });
      const items = feedRes.data || [];
      setDateFixtures(items);

      if (publishedCount === 0 || items.length === 0) {
        setEmptyNotice('No published predictions were generated for this date.');
      }
    } catch (err: any) {
      const feedRes = await apiService.fetchPublishedPredictions({ date: selectedDate });
      const items = feedRes.data || [];
      setDateFixtures(items);
      if (items.length === 0) {
        setEmptyNotice('No published predictions were generated for this date.');
      }
    } finally {
      setIsManualRefreshing(false);
    }
  };

  useEffect(() => {
    fetchForDate(selectedDate);
  }, [selectedDate, fetchForDate]);

  // Derive unique leagues present in the fixtures list
  const availableLeagues = React.useMemo(() => {
    const list = new Set<string>();
    dateFixtures.forEach((f) => {
      const matchSport = selectedSport === 'all' || f.sport === selectedSport || (selectedSport === 'ice_hockey' && (f.sport as string) === 'hockey');
      if (matchSport && f.league) {
        list.add(f.league);
      }
    });
    return Array.from(list);
  }, [dateFixtures, selectedSport]);

  // Apply filters and enforce strict maximum 15-20 validated predictions
  const filteredFixtures = React.useMemo(() => {
    return dateFixtures
      .filter((f) => {
        // Enforce: Never display rejected, abstained, or diagnostic records
        if (f.validationStatus !== 'validated') return false;

        // Sport filter
        const matchSport = selectedSport === 'all' || f.sport === selectedSport || (selectedSport === 'ice_hockey' && (f.sport as string) === 'hockey');
        if (!matchSport) return false;

        // League filter
        if (selectedLeague !== 'all' && f.league !== selectedLeague) return false;

        // Status filter
        if (statusFilter !== 'all' && f.status !== statusFilter) return false;

        return true;
      })
      .slice(0, 20); // Strict maximum 20 validated predictions
  }, [dateFixtures, selectedSport, selectedLeague, statusFilter]);

  // Separate into Live, Upcoming, and Completed groups
  const liveFixtures = React.useMemo(() => filteredFixtures.filter((f) => f.status === 'live'), [filteredFixtures]);
  const upcomingFixtures = React.useMemo(() => filteredFixtures.filter((f) => f.status === 'upcoming'), [filteredFixtures]);
  const completedFixtures = React.useMemo(() => filteredFixtures.filter((f) => f.status === 'completed'), [filteredFixtures]);

  const isCurrentLoading = Boolean(isLoading) || isLoadingDate || isManualRefreshing;
  const publishedDateSet = new Set(availableDates);

  // Highest probability across published fixtures
  const topEdge = React.useMemo(() => {
    let max = 0;
    filteredFixtures.forEach((f) => {
      if (f.highestPercentagePrediction?.percentage && f.highestPercentagePrediction.percentage > max) {
        max = f.highestPercentagePrediction.percentage;
      }
    });
    return max > 0 ? `${max.toFixed(1)}%` : '—';
  }, [filteredFixtures]);

  return (
    <div className="space-y-6">
      {/* 1. Dashboard Header Zone: Title & Description */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between border-b border-zinc-800 pb-4">
        <div>
          <div className="text-[11px] font-mono uppercase tracking-wider text-zinc-400">
            Probabilistic Analytics · Africa/Lagos (WAT, UTC+1)
          </div>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-white">
            Daily Validated Predictions
          </h1>
          <p className="mt-1 text-xs text-zinc-400">
            Calibrated predictive distributions backed by 5+ historical completed matches and rigorous validation gates.
          </p>
        </div>
      </div>

      {/* 2. Metrics KPI Row */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 font-mono">
        <div className="rounded-lg border border-zinc-800 bg-[#0d131f] p-3">
          <div className="text-[10px] uppercase tracking-wider text-zinc-400">Published Predictions</div>
          <div className="mt-1 flex items-baseline justify-between">
            <span className="text-xl font-bold text-white tabular-nums">{filteredFixtures.length}</span>
            <span className="text-[11px] text-zinc-500">Max 20</span>
          </div>
        </div>

        <div className="rounded-lg border border-zinc-800 bg-[#0d131f] p-3">
          <div className="text-[10px] uppercase tracking-wider text-zinc-400">Live In-Progress</div>
          <div className="mt-1 flex items-baseline justify-between">
            <span className="text-xl font-bold text-amber-400 tabular-nums">{liveFixtures.length}</span>
            <span className="text-[11px] text-zinc-500">Live Scores</span>
          </div>
        </div>

        <div className="rounded-lg border border-zinc-800 bg-[#0d131f] p-3">
          <div className="text-[10px] uppercase tracking-wider text-zinc-400">Top Model Edge</div>
          <div className="mt-1 flex items-baseline justify-between">
            <span className="text-xl font-bold text-emerald-400 tabular-nums">{topEdge}</span>
            <span className="text-[11px] text-zinc-500">Calibrated</span>
          </div>
        </div>

        <div className="rounded-lg border border-zinc-800 bg-[#0d131f] p-3">
          <div className="text-[10px] uppercase tracking-wider text-zinc-400">Active Leagues</div>
          <div className="mt-1 flex items-baseline justify-between">
            <span className="text-xl font-bold text-zinc-200 tabular-nums">{availableLeagues.length}</span>
            <span className="text-[11px] text-zinc-500">Filtered</span>
          </div>
        </div>
      </div>

      {/* 3. Date & Calendar Navigation Control Bar (Flexbox / Grid Layout) */}
      <div className="rounded-lg border border-zinc-800 bg-[#0d131f] p-2.5 sm:p-3 flex flex-col md:flex-row md:items-center justify-between gap-3">
        {/* Quick Date Carousel */}
        <div className="flex items-center gap-1.5 overflow-x-auto pb-1 md:pb-0 scrollbar-none max-w-full flex-1 min-w-0">
          <span className="text-xs font-medium text-zinc-500 shrink-0 mr-1 flex items-center gap-1">
            <Calendar className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
            <span>Date:</span>
          </span>
          <div className="flex items-center gap-1.5 shrink-0">
            {quickDates.map((item) => {
              const isSelected = selectedDate === item.ymd;
              const hasPubs = publishedDateSet.has(item.ymd);
              return (
                <button
                  key={item.ymd}
                  type="button"
                  onClick={() => setSelectedDate(item.ymd)}
                  className={`flex items-center justify-center gap-1.5 rounded-md border px-3 py-1.5 text-xs transition-all shrink-0 min-h-[36px] font-mono ${
                    isSelected
                      ? 'border-emerald-500/80 bg-emerald-950/50 text-emerald-300 font-semibold shadow-sm ring-1 ring-emerald-500/20'
                      : 'border-zinc-800/80 bg-zinc-900/60 text-zinc-300 hover:border-zinc-700 hover:bg-zinc-800/80'
                  }`}
                >
                  <span className="font-sans font-medium">{item.label}</span>
                  <span className="text-[10px] text-zinc-500">{item.ymd.slice(5)}</span>
                  {hasPubs && !isSelected && (
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 shrink-0" title="Predictions published" />
                  )}
                </button>
              );
            })}
          </div>
        </div>

        {/* Action Controls: Calendar Date Picker & Refresh Button */}
        <div className="flex items-center justify-between sm:justify-end gap-2 shrink-0 pt-2 md:pt-0 border-t md:border-t-0 border-zinc-800/80 w-full md:w-auto">
          <PredictionCalendar
            selectedDate={selectedDate}
            onSelectDate={(newDate) => {
              setSelectedDate(newDate);
            }}
            availableDates={availableDates}
            disabled={isCurrentLoading}
          />

          <button
            type="button"
            onClick={() => {
              handleManualRefresh();
            }}
            disabled={isCurrentLoading}
            className="flex h-8 items-center gap-1.5 rounded border border-zinc-700 bg-zinc-800 px-3 text-xs font-medium text-zinc-200 transition-colors hover:bg-zinc-700 hover:text-white disabled:opacity-50 shrink-0"
            title="Refresh prediction pipeline"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isCurrentLoading ? 'animate-spin text-emerald-400' : 'text-zinc-400'}`} />
            <span>{isManualRefreshing ? 'Refreshing...' : 'Refresh'}</span>
          </button>
        </div>
      </div>

      {/* 4. Sport & Secondary Filter Bar */}
      <div className="space-y-3 rounded-lg border border-zinc-800 bg-[#0d131f] p-3">
        {/* Sport Segmented Control */}
        <div className="flex items-center gap-1 overflow-x-auto pb-1 scrollbar-none">
          {SPORTS_OPTIONS.map((sport) => {
            const isSelected = selectedSport === sport.id;
            return (
              <button
                key={sport.id}
                onClick={() => {
                  setSelectedSport(sport.id);
                  setSelectedLeague('all');
                }}
                className={`rounded px-3 py-1.5 text-xs font-medium transition-colors whitespace-nowrap ${
                  isSelected
                    ? 'bg-zinc-200 text-zinc-900 font-semibold'
                    : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60'
                }`}
              >
                {sport.label}
              </button>
            );
          })}
        </div>

        {/* Secondary Filters: League & Status */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 border-t border-zinc-800/80 pt-2.5 text-xs">
          {/* League Dropdown */}
          <div className="flex items-center gap-2">
            <span className="text-zinc-400 font-medium shrink-0">League:</span>
            <select
              value={selectedLeague}
              onChange={(e) => setSelectedLeague(e.target.value)}
              className="rounded border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-xs text-zinc-200 outline-none focus:border-zinc-500 max-w-full truncate"
            >
              <option value="all">All Leagues ({availableLeagues.length})</option>
              {availableLeagues.map((league) => (
                <option key={league} value={league}>
                  {league}
                </option>
              ))}
            </select>
          </div>

          {/* Status Filter */}
          <div className="flex items-center gap-1 overflow-x-auto pb-0.5 scrollbar-none">
            <span className="text-zinc-400 font-medium mr-1 shrink-0">Status:</span>
            {(['all', 'upcoming', 'live', 'completed'] as const).map((status) => (
              <button
                key={status}
                onClick={() => setStatusFilter(status)}
                className={`rounded px-2.5 py-1 text-[11px] font-medium capitalize transition-colors shrink-0 ${
                  statusFilter === status
                    ? 'bg-zinc-700 text-white font-semibold'
                    : 'text-zinc-400 hover:text-zinc-200'
                }`}
              >
                {status}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Feed Error Notice */}
      {feedError && (
        <div className="rounded border border-rose-800/80 bg-rose-950/40 p-3 text-xs text-rose-200">
          <div className="font-semibold text-rose-300 mb-1">
            Pipeline Notice
          </div>
          <div className="text-zinc-300 font-mono text-[11px]">
            {feedError.message}
          </div>
        </div>
      )}

      {/* 5. Main Fixture Presentation (Live, Upcoming, Completed) */}
      {isCurrentLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      ) : filteredFixtures.length === 0 ? (
        <EmptyState
          title={emptyNotice || "No validated predictions available for this date/sport."}
          description={emptyNotice ? emptyNotice : `No fixtures meet the minimum 5+ completed match history and validation thresholds for ${formatLagosDateDisplay(
            selectedDate
          )} in Africa/Lagos (WAT). The statistical pipeline only publishes predictions that pass historical calibration gates.`}
          iconType={dateFixtures.length === 0 ? 'sync' : 'filter'}
          actionLabel={
            selectedSport !== 'all' || selectedLeague !== 'all' || statusFilter !== 'all'
              ? 'Reset Filters'
              : selectedDate !== todayLagos
              ? 'Jump to Today'
              : 'Refresh Predictions'
          }
          onAction={() => {
            if (selectedSport !== 'all' || selectedLeague !== 'all' || statusFilter !== 'all') {
              setSelectedSport('all');
              setSelectedLeague('all');
              setStatusFilter('all');
            } else if (selectedDate !== todayLagos) {
              setSelectedDate(todayLagos);
            } else {
              handleManualRefresh();
            }
          }}
        />
      ) : (
        <div className="space-y-8">
          {/* Section A: Live Matches (Actual live score first) */}
          {liveFixtures.length > 0 && (
            <section className="space-y-3">
              <div className="flex items-center justify-between border-b border-amber-900/40 pb-2">
                <div className="flex items-center gap-2">
                  <span className="h-2 w-2 rounded-full bg-amber-400 animate-pulse" />
                  <h2 className="text-xs font-bold uppercase tracking-wider text-amber-300">
                    Live Matches ({liveFixtures.length})
                  </h2>
                </div>
                <span className="text-[11px] font-mono text-zinc-400">
                  Live scores prominent
                </span>
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {liveFixtures.map((fixture) => (
                  <FixtureCard
                    key={fixture.id}
                    fixture={fixture}
                    onSelect={onSelectFixture}
                  />
                ))}
              </div>
            </section>
          )}

          {/* Section B: Upcoming Predictions (Strongest validated prediction first) */}
          {upcomingFixtures.length > 0 && (
            <section className="space-y-3">
              <div className="flex items-center justify-between border-b border-zinc-800 pb-2">
                <div className="flex items-center gap-2">
                  <TrendingUp className="h-3.5 w-3.5 text-emerald-400" />
                  <h2 className="text-xs font-bold uppercase tracking-wider text-zinc-200">
                    Upcoming Predictions ({upcomingFixtures.length})
                  </h2>
                </div>
                <span className="text-[11px] font-mono text-zinc-400">
                  Signal first · Maximum 15–20 published
                </span>
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {upcomingFixtures.map((fixture) => (
                  <FixtureCard
                    key={fixture.id}
                    fixture={fixture}
                    onSelect={onSelectFixture}
                  />
                ))}
              </div>
            </section>
          )}

          {/* Section C: Completed Matches (Actual final score first, evaluation separately) */}
          {completedFixtures.length > 0 && (
            <section className="space-y-3">
              <div className="flex items-center justify-between border-b border-zinc-800 pb-2">
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="h-3.5 w-3.5 text-zinc-400" />
                  <h2 className="text-xs font-bold uppercase tracking-wider text-zinc-400">
                    Completed Matches ({completedFixtures.length})
                  </h2>
                </div>
                <span className="text-[11px] font-mono text-zinc-400">
                  Official final scores prominent
                </span>
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {completedFixtures.map((fixture) => (
                  <FixtureCard
                    key={fixture.id}
                    fixture={fixture}
                    onSelect={onSelectFixture}
                  />
                ))}
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  );
};
