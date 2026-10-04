import React from 'react';
import { ValidatedFixture } from '../../types';
import { formatLagosKickoff } from '../../utils/timezone';
import { ChevronRight } from 'lucide-react';

interface FixtureCardProps {
  fixture: ValidatedFixture;
  onSelect: (fixture: ValidatedFixture) => void;
}

export const FixtureCard: React.FC<FixtureCardProps> = ({ fixture, onSelect }) => {
  const { displayDate, displayTime } = formatLagosKickoff(fixture.kickoffUtc);

  // Capitalize sport for display
  const sportLabel =
    fixture.sport === 'football'
      ? 'Football'
      : fixture.sport === 'basketball'
      ? 'Basketball'
      : fixture.sport === 'baseball'
      ? 'Baseball'
      : fixture.sport === 'ice_hockey' || (fixture.sport as string) === 'hockey'
      ? 'Ice Hockey'
      : 'Formula 1';

  const stats = fixture.sportStats || {};
  const validatedMarketsCount = fixture.validatedMarkets?.length || 0;
  const isCompleted = fixture.status === 'completed';
  const isLive = fixture.status === 'live';
  const isUpcoming = fixture.status === 'upcoming';

  const pred = fixture.highestPercentagePrediction;
  const hasPrediction = pred && typeof pred.percentage === 'number';

  // Evaluate if outcome hit for completed matches
  let hitStatus: 'hit' | 'miss' | null = null;
  if (isCompleted && fixture.finalScore && pred) {
    const homeScore = fixture.finalScore.home;
    const awayScore = fixture.finalScore.away;
    const selLower = (pred.selection || '').toLowerCase();
    const marketLower = (pred.marketName || '').toLowerCase();

    if (marketLower.includes('1x2') || marketLower.includes('moneyline') || marketLower.includes('win')) {
      if (selLower.includes('home') || selLower.includes(fixture.homeTeam.toLowerCase())) {
        hitStatus = homeScore > awayScore ? 'hit' : 'miss';
      } else if (selLower.includes('away') || selLower.includes(fixture.awayTeam.toLowerCase())) {
        hitStatus = awayScore > homeScore ? 'hit' : 'miss';
      } else if (selLower.includes('draw')) {
        hitStatus = homeScore === awayScore ? 'hit' : 'miss';
      }
    }
  }

  return (
    <article
      onClick={() => onSelect(fixture)}
      className="group flex flex-col justify-between rounded-lg border border-zinc-800 bg-[#0d131f] p-3.5 sm:p-4 transition-all hover:border-zinc-700 hover:bg-[#111927] active:scale-[0.99] cursor-pointer"
    >
      <div>
        {/* 1. Header Metadata Bar (Zero-Pill Rule) */}
        <div className="flex items-center justify-between text-xs text-zinc-400 border-b border-zinc-800/80 pb-2.5">
          <div className="flex items-center gap-1.5 truncate">
            <span className="font-semibold text-zinc-200">{sportLabel}</span>
            <span aria-hidden="true" className="text-zinc-600">·</span>
            <span className="truncate text-zinc-400 font-medium">{fixture.league}</span>
          </div>

          {/* Status Display */}
          <div className="flex items-center gap-1.5 font-mono text-xs shrink-0 tabular-nums">
            {isLive && (
              <span className="flex items-center gap-1.5 text-amber-400 font-semibold">
                <span className="h-1.5 w-1.5 rounded-full bg-amber-400 animate-pulse" />
                LIVE {fixture.currentScore?.periodOrMinute ? `· ${fixture.currentScore.periodOrMinute}` : ''}
              </span>
            )}
            {isUpcoming && (
              <span className="text-zinc-400">
                {displayDate} · {displayTime}
              </span>
            )}
            {isCompleted && (
              <span className="text-zinc-400 font-medium">
                Final
              </span>
            )}
          </div>
        </div>

        {/* 2. Matchup Zone: Priority based on status */}
        {fixture.sport === 'formula_1' ? (
          <div className="py-3">
            <div className="flex items-baseline justify-between mb-2">
              <h3 className="text-sm font-bold text-zinc-100 group-hover:text-emerald-300 transition-colors truncate">
                {fixture.league || 'Formula 1 Grand Prix'}
              </h3>
              <span className="text-[10px] font-mono text-zinc-500 uppercase">
                {isLive ? 'In Progress' : isCompleted ? 'Official Result' : 'Grid Race'}
              </span>
            </div>

            {stats.f1Drivers && stats.f1Drivers.length > 0 ? (
              <div className="space-y-1.5 rounded border border-zinc-800/80 bg-zinc-900/60 p-2 text-xs">
                {stats.f1Drivers.slice(0, 3).map((driver, idx) => (
                  <div key={driver.driverId || idx} className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5 truncate">
                      <span className="font-mono text-zinc-500 text-[11px] w-3">{idx + 1}.</span>
                      <span className="font-medium text-zinc-200 truncate">{driver.driverName}</span>
                      <span className="text-[10px] text-zinc-500 truncate">({driver.constructorName})</span>
                    </div>
                    {driver.winProbability != null && (
                      <span className="font-mono text-xs font-semibold text-emerald-400 shrink-0">
                        {(driver.winProbability * 100).toFixed(1)}%
                      </span>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-xs text-zinc-400 py-1 font-mono">
                Multi-driver field · Validated probability lines active
              </div>
            )}
          </div>
        ) : (
          <div className="py-3">
            {/* Team A / Home */}
            <div className="flex items-center justify-between py-1">
              <span className={`text-sm tracking-tight truncate ${
                isCompleted && fixture.finalScore && fixture.finalScore.home > fixture.finalScore.away
                  ? 'font-bold text-white'
                  : 'font-medium text-zinc-200 group-hover:text-zinc-100'
              }`}>
                {fixture.homeTeam}
              </span>
              {isLive && fixture.currentScore && (
                <span className="font-mono text-base font-bold text-amber-300 tabular-nums ml-2">
                  {fixture.currentScore.home}
                </span>
              )}
              {isCompleted && fixture.finalScore && (
                <span className="font-mono text-base font-bold text-zinc-100 tabular-nums ml-2">
                  {fixture.finalScore.home}
                </span>
              )}
            </div>

            {/* Team B / Away */}
            <div className="flex items-center justify-between py-1">
              <span className={`text-sm tracking-tight truncate ${
                isCompleted && fixture.finalScore && fixture.finalScore.away > fixture.finalScore.home
                  ? 'font-bold text-white'
                  : 'font-medium text-zinc-200 group-hover:text-zinc-100'
              }`}>
                {fixture.awayTeam}
              </span>
              {isLive && fixture.currentScore && (
                <span className="font-mono text-base font-bold text-amber-300 tabular-nums ml-2">
                  {fixture.currentScore.away}
                </span>
              )}
              {isCompleted && fixture.finalScore && (
                <span className="font-mono text-base font-bold text-zinc-100 tabular-nums ml-2">
                  {fixture.finalScore.away}
                </span>
              )}
            </div>
          </div>
        )}

        {/* 3. Validated Prediction Panel (Always visible for all eligible fixtures) */}
        <div className={`rounded border p-2.5 ${
          isCompleted
            ? 'border-zinc-800/80 bg-zinc-900/40'
            : isLive
            ? 'border-zinc-800 bg-zinc-900/60'
            : 'border-zinc-800 bg-[#0f1726]'
        }`}>
          <div className="flex items-start justify-between gap-2">
            <div className="truncate">
              <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-zinc-400">
                <span>{isCompleted ? 'Historical Signal' : isLive ? 'Pre-Match Signal' : 'Validated Signal'}</span>
                <span className="normal-case text-zinc-500 font-normal">· {pred?.marketName || 'Primary Market'}</span>
                {hitStatus && (
                  <span className={`ml-1 text-[9px] font-bold px-1 rounded ${
                    hitStatus === 'hit' ? 'bg-emerald-950 text-emerald-300 border border-emerald-800/60' : 'bg-zinc-800 text-zinc-400'
                  }`}>
                    {hitStatus === 'hit' ? 'HIT' : 'MISS'}
                  </span>
                )}
              </div>
              <div className="text-xs font-bold text-zinc-100 mt-0.5 truncate">
                {pred?.selection || 'No validated prediction'}
              </div>
              <div className="text-[10px] font-mono text-zinc-500 mt-0.5">
                Model: <span className="text-zinc-400">{fixture.modelVersion || 'ELO + POISSON'}</span>
              </div>
            </div>

            <div className="text-right shrink-0">
              <div className="font-mono text-base font-bold text-emerald-400 tabular-nums">
                {hasPrediction ? `${pred!.percentage.toFixed(1)}%` : '—'}
              </div>
              <div className="text-[9px] uppercase tracking-wider text-zinc-500 font-mono">
                Probability
              </div>
            </div>
          </div>

          {/* Compact Probability Bar */}
          {hasPrediction && (
            <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-zinc-800">
              <div
                className="h-full bg-emerald-500 rounded-full"
                style={{ width: `${Math.min(100, Math.max(0, pred!.percentage))}%` }}
              />
            </div>
          )}
        </div>
      </div>

      {/* 4. Sport-Specific Key Indicator Footer */}
      <div className="mt-3 flex items-center justify-between border-t border-zinc-800/70 pt-2.5 text-[11px] text-zinc-400">
        <div className="truncate">
          {/* Football: xG form */}
          {fixture.sport === 'football' && stats.xGRecentHome != null && (
            <span>
              xG Form: <strong className="text-zinc-300 font-mono">{stats.xGRecentHome.toFixed(2)}</strong> - <strong className="text-zinc-300 font-mono">{stats.xGRecentAway?.toFixed(2) ?? '—'}</strong>
            </span>
          )}

          {/* Basketball: Pace / PPG */}
          {fixture.sport === 'basketball' && stats.pace != null && (
            <span>
              Pace: <strong className="text-zinc-300 font-mono">{stats.pace}</strong> · PPG: <strong className="text-zinc-300 font-mono">{stats.homePPG ?? '—'}</strong>
            </span>
          )}

          {/* Baseball: ERA */}
          {fixture.sport === 'baseball' && stats.homeERA != null && (
            <span>
              ERA: <strong className="text-zinc-300 font-mono">{stats.homeERA.toFixed(2)}</strong> vs <strong className="text-zinc-300 font-mono">{stats.awayERA?.toFixed(2) ?? '—'}</strong>
            </span>
          )}

          {/* Ice Hockey: Goals / Shots */}
          {(fixture.sport === 'ice_hockey' || (fixture.sport as string) === 'hockey') && (
            <span>
              GF/G: <strong className="text-zinc-300 font-mono">{stats.homeGoalsScoredAvg ?? stats.goalsPerGame ?? '—'}</strong> · Shots: <strong className="text-zinc-300 font-mono">{stats.homeShotsAvg ?? '—'}</strong>
            </span>
          )}

          {/* Formula 1: Grid position */}
          {fixture.sport === 'formula_1' && stats.gridPosition != null && (
            <span>
              Pole: <strong className="text-zinc-300 font-mono">P{stats.gridPosition}</strong> · Conv: <strong className="text-zinc-300 font-mono">{stats.poleConversionRate ?? '—'}%</strong>
            </span>
          )}
        </div>

        <div className="flex items-center gap-1 font-medium text-zinc-400 group-hover:text-emerald-400 transition-colors shrink-0 ml-2">
          <span>{validatedMarketsCount} {validatedMarketsCount === 1 ? 'market' : 'markets'}</span>
          <ChevronRight className="h-3 w-3" />
        </div>
      </div>
    </article>
  );
};
