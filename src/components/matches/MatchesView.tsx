import React, { useState, useEffect, useCallback } from 'react';
import { SportType, ValidatedFixture, FixtureStatus, ApiErrorInfo } from '../../types';
import { EmptyState } from '../common/EmptyState';
import { SkeletonCard } from '../common/SkeletonCard';
import { getLagosTodayYmd, formatLagosDateDisplay, formatLagosTime } from '../../utils/timezone';
import { apiService } from '../../services/api';
import {
  Calendar,
  ChevronLeft,
  ChevronRight,
  Filter,
  Layers,
  RefreshCw,
  Sparkles,
  Trophy,
} from 'lucide-react';

interface MatchesViewProps {
  onSelectFixture: (fixture: ValidatedFixture) => void;
}

const SPORTS_OPTIONS: { id: 'all' | SportType; label: string }[] = [
  { id: 'all', label: 'All Sports' },
  { id: 'football', label: 'Football' },
  { id: 'basketball', label: 'Basketball' },
  { id: 'baseball', label: 'Baseball' },
  { id: 'ice_hockey', label: 'Ice Hockey' },
  { id: 'formula_1', label: 'Formula 1' },
];

export const MatchesView: React.FC<MatchesViewProps> = ({ onSelectFixture }) => {
  const todayLagos = getLagosTodayYmd();
  const [selectedDate, setSelectedDate] = useState<string>('all');
  const [selectedSport, setSelectedSport] = useState<'all' | SportType>('all');
  const [selectedStatus, setSelectedStatus] = useState<'all' | FixtureStatus>('all');
  const [page, setPage] = useState<number>(1);
  const [pageSize, setPageSize] = useState<number>(25);

  const [fixtures, setFixtures] = useState<ValidatedFixture[]>([]);
  const [totalCount, setTotalCount] = useState<number>(0);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [errorInfo, setErrorInfo] = useState<ApiErrorInfo | null>(null);

  // 7-day operational horizon in Lagos
  const horizonDates = React.useMemo(() => {
    const list: { ymd: string; label: string; dayName: string }[] = [
      { ymd: 'all', label: 'All 7 Days', dayName: 'Horizon' },
    ];
    const parts = todayLagos.split('-').map(Number);
    const baseDate = new Date(parts[0], parts[1] - 1, parts[2]);

    for (let i = 0; i < 7; i++) {
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

  const loadOperationalFixtures = useCallback(async (targetPage = 1) => {
    setIsLoading(true);
    setErrorInfo(null);
    try {
      const res = await apiService.fetchOperationalFixtures({
        sport: selectedSport !== 'all' ? selectedSport : undefined,
        status: selectedStatus !== 'all' ? selectedStatus : undefined,
        date: selectedDate !== 'all' ? selectedDate : undefined,
        limit: pageSize,
        page: targetPage,
      });

      if (res.errorInfo) {
        setErrorInfo(res.errorInfo);
        setFixtures([]);
        setTotalCount(0);
      } else {
        setFixtures(res.data || []);
        setTotalCount(res.total || 0);
        setPage(res.page || targetPage);
      }
    } catch (err) {
      setErrorInfo({
        status: 'NETWORK_ERROR',
        message: err instanceof Error ? err.message : 'Failed to load fixtures catalogue',
      });
      setFixtures([]);
      setTotalCount(0);
    } finally {
      setIsLoading(false);
    }
  }, [selectedSport, selectedStatus, selectedDate, pageSize]);

  useEffect(() => {
    setPage(1);
    loadOperationalFixtures(1);
  }, [selectedSport, selectedStatus, selectedDate, pageSize, loadOperationalFixtures]);

  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));

  return (
    <div className="space-y-6">
      {/* Header & Description */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between border-b border-zinc-800/80 pb-5">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold tracking-tight text-white sm:text-2xl">
              Operational Fixture Catalogue
            </h1>
            <span className="rounded bg-zinc-800 px-2 py-0.5 text-xs font-mono text-zinc-300">
              {totalCount} Total
            </span>
          </div>
          <p className="mt-1 text-xs text-zinc-400">
            Complete multi-sport operational window (Africa/Lagos UTC+1). Synchronized across MongoDB Primary and Neon Standby.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => loadOperationalFixtures(page)}
            disabled={isLoading}
            className="flex items-center gap-1.5 rounded border border-zinc-800 bg-[#0c111c] px-3 py-1.5 text-xs font-medium text-zinc-300 hover:border-zinc-700 hover:text-white transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? 'animate-spin text-emerald-400' : 'text-zinc-400'}`} />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {/* 7-Day Lagos Operational Date Horizon Carousel */}
      <div className="space-y-2">
        <div className="flex items-center gap-2 text-xs font-medium text-zinc-400">
          <Calendar className="h-3.5 w-3.5 text-emerald-400" />
          <span>Operational Window (7 Days):</span>
        </div>
        <div className="flex items-center gap-2 overflow-x-auto pb-2 scrollbar-none">
          {horizonDates.map((item) => {
            const isSelected = selectedDate === item.ymd;
            return (
              <button
                key={item.ymd}
                onClick={() => setSelectedDate(item.ymd)}
                className={`flex flex-col items-center justify-center rounded-lg border px-3.5 py-2 text-xs transition-all whitespace-nowrap min-w-[90px] ${
                  isSelected
                    ? 'border-emerald-500/50 bg-emerald-950/30 text-emerald-400 font-semibold shadow-sm'
                    : 'border-zinc-800/80 bg-[#0c111c] text-zinc-400 hover:border-zinc-700 hover:text-zinc-200'
                }`}
              >
                <span className="text-[10px] uppercase font-mono text-zinc-500">{item.dayName}</span>
                <span className="text-xs font-medium text-zinc-200 mt-0.5">{item.label}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Sport & Status Filter Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-lg border border-zinc-800/80 bg-[#090e18] p-3 text-xs">
        {/* Sport Pills */}
        <div className="flex items-center gap-1.5 overflow-x-auto pb-1 sm:pb-0 scrollbar-none">
          {SPORTS_OPTIONS.map((sport) => {
            const isSelected = selectedSport === sport.id;
            return (
              <button
                key={sport.id}
                onClick={() => setSelectedSport(sport.id)}
                className={`rounded px-2.5 py-1 text-xs font-medium transition-colors shrink-0 ${
                  isSelected
                    ? 'bg-zinc-700 text-white font-semibold'
                    : 'bg-zinc-800/50 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200'
                }`}
              >
                {sport.label}
              </button>
            );
          })}
        </div>

        {/* Status & Page Size controls */}
        <div className="flex flex-wrap items-center gap-2 sm:gap-3">
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] text-zinc-500 shrink-0">Status:</span>
            <select
              value={selectedStatus}
              onChange={(e) => setSelectedStatus(e.target.value as any)}
              className="rounded border border-zinc-800 bg-[#0c111c] px-2 py-1 text-xs text-zinc-300 focus:border-emerald-500 focus:outline-none"
            >
              <option value="all">All Statuses</option>
              <option value="upcoming">Upcoming</option>
              <option value="live">Live / In-Play</option>
              <option value="completed">Completed</option>
            </select>
          </div>

          <div className="flex items-center gap-1.5">
            <span className="text-[11px] text-zinc-500 shrink-0">Per Page:</span>
            <select
              value={pageSize}
              onChange={(e) => setPageSize(Number(e.target.value))}
              className="rounded border border-zinc-800 bg-[#0c111c] px-2 py-1 text-xs text-zinc-300 focus:border-emerald-500 focus:outline-none"
            >
              <option value={25}>25</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
            </select>
          </div>
        </div>
      </div>

      {/* Main Fixtures List */}
      {isLoading ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      ) : errorInfo ? (
        <EmptyState
          title="Unable to Load Fixtures"
          description={errorInfo.message}
          actionLabel="Retry"
          onAction={() => loadOperationalFixtures(page)}
        />
      ) : fixtures.length === 0 ? (
        <EmptyState
          title="No Fixtures Found"
          description={`No operational fixtures match the selected filters for ${selectedSport} on ${selectedDate}.`}
          actionLabel="Reset Filters"
          onAction={() => {
            setSelectedSport('all');
            setSelectedStatus('all');
            setSelectedDate('all');
          }}
        />
      ) : (
        <div className="space-y-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {fixtures.map((fixture) => {
              const hasPrediction = fixture.validationStatus === 'validated' && fixture.validatedMarkets && fixture.validatedMarkets.length > 0;
              const topPred = fixture.highestPercentagePrediction || (fixture.validatedMarkets && fixture.validatedMarkets[0]);
              const rawPct = topPred
                ? ('percentage' in topPred ? topPred.percentage : ('probabilityPercentage' in topPred ? topPred.probabilityPercentage : null))
                : null;

              return (
                <div
                  key={fixture.id}
                  onClick={() => onSelectFixture(fixture)}
                  className="group relative flex flex-col justify-between rounded-lg border border-zinc-800 bg-[#0a0f1d] p-4 text-left transition-all hover:border-zinc-700 hover:bg-[#0d1424] cursor-pointer"
                >
                  {/* Top Row: League & Sport Badge */}
                  <div className="flex items-center justify-between gap-2 border-b border-zinc-800/60 pb-2.5 text-[11px]">
                    <div className="flex items-center gap-1.5 truncate text-zinc-400">
                      <Trophy className="h-3.5 w-3.5 text-zinc-500 shrink-0" />
                      <span className="truncate font-medium">{fixture.league || 'Standard League'}</span>
                    </div>
                    <span className="rounded bg-zinc-800/80 px-2 py-0.5 font-mono text-[10px] uppercase text-zinc-400">
                      {fixture.sport}
                    </span>
                  </div>

                  {/* Middle Row: Matchup & Score */}
                  <div className="my-3 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-semibold text-white truncate max-w-[190px]">
                        {fixture.homeTeam}
                      </span>
                      {fixture.currentScore && (
                        <span className="font-mono text-xs font-bold text-zinc-300">
                          {fixture.currentScore.home}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-semibold text-white truncate max-w-[190px]">
                        {fixture.awayTeam}
                      </span>
                      {fixture.currentScore && (
                        <span className="font-mono text-xs font-bold text-zinc-300">
                          {fixture.currentScore.away}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Bottom Row: Prediction Status or Kickoff Info */}
                  <div className="mt-2 flex items-center justify-between pt-2 border-t border-zinc-800/60 text-[11px]">
                    <div className="text-zinc-500 font-mono">
                      {formatLagosTime(fixture.kickoffUtc)}
                    </div>

                    {hasPrediction && rawPct !== null ? (
                      <div className="flex items-center gap-1 rounded bg-emerald-950/60 px-2 py-0.5 text-[11px] font-semibold text-emerald-400 border border-emerald-500/20">
                        <Sparkles className="h-3 w-3 text-emerald-400" />
                        <span>
                          {typeof rawPct === 'number'
                            ? `${rawPct.toFixed(1)}%`
                            : 'Validated'}
                        </span>
                      </div>
                    ) : (
                      <div className="rounded bg-zinc-800/40 px-2 py-0.5 text-[10px] text-zinc-500">
                        Prediction unavailable
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Pagination Controls */}
          <div className="flex items-center justify-between border-t border-zinc-800/80 pt-4 text-xs text-zinc-400">
            <div>
              Showing <span className="text-zinc-200 font-medium">{(page - 1) * pageSize + 1}</span> to{' '}
              <span className="text-zinc-200 font-medium">{Math.min(page * pageSize, totalCount)}</span> of{' '}
              <span className="text-zinc-200 font-medium">{totalCount}</span> operational fixtures
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={() => loadOperationalFixtures(page - 1)}
                disabled={page <= 1 || isLoading}
                className="flex items-center gap-1 rounded border border-zinc-800 bg-[#0c111c] px-3 py-1.5 text-xs text-zinc-300 hover:border-zinc-700 hover:text-white disabled:opacity-40 disabled:hover:border-zinc-800 transition-colors"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
                <span>Prev</span>
              </button>

              <span className="px-2 font-mono text-zinc-300">
                {page} / {totalPages}
              </span>

              <button
                onClick={() => loadOperationalFixtures(page + 1)}
                disabled={page >= totalPages || isLoading}
                className="flex items-center gap-1 rounded border border-zinc-800 bg-[#0c111c] px-3 py-1.5 text-xs text-zinc-300 hover:border-zinc-700 hover:text-white disabled:opacity-40 disabled:hover:border-zinc-800 transition-colors"
              >
                <span>Next</span>
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
