import React from 'react';
import { MarketItem, ValidatedFixture } from '../../types';
import { formatLagosKickoff } from '../../utils/timezone';
import { MarketSection } from './MarketSection';
import {
  ArrowLeft,
  ChevronRight,
  Cpu,
  ShieldCheck,
} from 'lucide-react';

interface MatchDetailsViewProps {
  fixture: ValidatedFixture;
  onBack: () => void;
}

export const MatchDetailsView: React.FC<MatchDetailsViewProps> = ({ fixture, onBack }) => {
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

  const isCompleted = fixture.status === 'completed';
  const isLive = fixture.status === 'live';
  const isUpcoming = fixture.status === 'upcoming';
  const pred = fixture.highestPercentagePrediction;

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

  // Strict sport-specific market filtering: NEVER display football markets on basketball/baseball/hockey
  const marketGroups = React.useMemo(() => {
    const groups: { title: string; markets: MarketItem[] }[] = [];
    const validMarkets = fixture.validatedMarkets || [];

    if (fixture.sport === 'football') {
      const matchResult = validMarkets.filter(
        (m) =>
          m.marketName.toLowerCase().includes('win') ||
          m.marketName.toLowerCase().includes('1x2') ||
          m.marketName.toLowerCase().includes('draw')
      );
      const goalsMarkets = validMarkets.filter(
        (m) =>
          m.marketName.toLowerCase().includes('over') ||
          m.marketName.toLowerCase().includes('under') ||
          m.marketName.toLowerCase().includes('btts') ||
          m.marketName.toLowerCase().includes('both teams')
      );
      const cornersAndOther = validMarkets.filter(
        (m) =>
          m.marketName.toLowerCase().includes('corner') ||
          m.marketName.toLowerCase().includes('double chance')
      );

      if (matchResult.length > 0) groups.push({ title: 'Full Time Outcome (1X2)', markets: matchResult });
      if (goalsMarkets.length > 0) groups.push({ title: 'Goal Totals & Both Teams To Score', markets: goalsMarkets });
      if (cornersAndOther.length > 0) groups.push({ title: 'Corners & Secondary Markets', markets: cornersAndOther });

      const caughtIds = new Set([
        ...matchResult.map((m) => m.id),
        ...goalsMarkets.map((m) => m.id),
        ...cornersAndOther.map((m) => m.id),
      ]);
      const remaining = validMarkets.filter((m) => !caughtIds.has(m.id));
      if (remaining.length > 0) groups.push({ title: 'Additional Football Lines', markets: remaining });
    } else if (fixture.sport === 'basketball') {
      const basketballOnly = validMarkets.filter(
        (m) =>
          !m.marketName.toLowerCase().includes('1x2') &&
          !m.marketName.toLowerCase().includes('draw') &&
          !m.marketName.toLowerCase().includes('btts') &&
          !m.marketName.toLowerCase().includes('both teams') &&
          !m.marketName.toLowerCase().includes('clean sheet')
      );

      const primary = basketballOnly.filter(
        (m) =>
          m.marketName.toLowerCase().includes('moneyline') ||
          m.marketName.toLowerCase().includes('spread')
      );
      const totalsAndHalves = basketballOnly.filter(
        (m) =>
          m.marketName.toLowerCase().includes('total') ||
          m.marketName.toLowerCase().includes('points') ||
          m.marketName.toLowerCase().includes('half')
      );

      if (primary.length > 0) groups.push({ title: 'Moneyline & Point Spread', markets: primary });
      if (totalsAndHalves.length > 0) groups.push({ title: 'Total Points & Halves', markets: totalsAndHalves });

      const caughtIds = new Set([...primary.map((m) => m.id), ...totalsAndHalves.map((m) => m.id)]);
      const remaining = basketballOnly.filter((m) => !caughtIds.has(m.id));
      if (remaining.length > 0) groups.push({ title: 'Additional Basketball Markets', markets: remaining });
    } else if (fixture.sport === 'baseball') {
      const baseballOnly = validMarkets.filter(
        (m) =>
          !m.marketName.toLowerCase().includes('1x2') &&
          !m.marketName.toLowerCase().includes('draw') &&
          !m.marketName.toLowerCase().includes('btts') &&
          !m.marketName.toLowerCase().includes('both teams')
      );

      const primary = baseballOnly.filter(
        (m) =>
          m.marketName.toLowerCase().includes('moneyline') ||
          m.marketName.toLowerCase().includes('run line')
      );
      const runs = baseballOnly.filter(
        (m) =>
          m.marketName.toLowerCase().includes('total runs') ||
          m.marketName.toLowerCase().includes('innings')
      );

      if (primary.length > 0) groups.push({ title: 'Moneyline & Run Line', markets: primary });
      if (runs.length > 0) groups.push({ title: 'Total Runs & Inning Lines', markets: runs });

      const caughtIds = new Set([...primary.map((m) => m.id), ...runs.map((m) => m.id)]);
      const remaining = baseballOnly.filter((m) => !caughtIds.has(m.id));
      if (remaining.length > 0) groups.push({ title: 'Additional Baseball Markets', markets: remaining });
    } else if (fixture.sport === 'ice_hockey' || (fixture.sport as string) === 'hockey') {
      const hockeyOnly = validMarkets.filter(
        (m) =>
          !m.marketName.toLowerCase().includes('1x2') &&
          !m.marketName.toLowerCase().includes('draw') &&
          !m.marketName.toLowerCase().includes('btts')
      );

      const primary = hockeyOnly.filter(
        (m) =>
          m.marketName.toLowerCase().includes('moneyline') ||
          m.marketName.toLowerCase().includes('puck line')
      );
      const totals = hockeyOnly.filter(
        (m) =>
          m.marketName.toLowerCase().includes('total') ||
          m.marketName.toLowerCase().includes('over/under')
      );

      if (primary.length > 0) groups.push({ title: 'Moneyline & Puck Line', markets: primary });
      if (totals.length > 0) groups.push({ title: 'Goal Totals & Margins', markets: totals });

      const caughtIds = new Set([...primary.map((m) => m.id), ...totals.map((m) => m.id)]);
      const remaining = hockeyOnly.filter((m) => !caughtIds.has(m.id));
      if (remaining.length > 0) groups.push({ title: 'Additional Hockey Markets', markets: remaining });
    } else {
      if (validMarkets.length > 0) {
        groups.push({ title: `Validated ${sportLabel} Markets`, markets: validMarkets });
      }
    }

    return groups;
  }, [fixture, sportLabel]);

  return (
    <div className="space-y-6">
      {/* 1. Breadcrumb Header */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between border-b border-zinc-800 pb-3">
        <div className="flex items-center gap-2 text-xs">
          <button
            onClick={onBack}
            className="flex items-center gap-1.5 font-medium text-zinc-400 hover:text-white transition-colors"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            <span>Dashboard</span>
          </button>
          <ChevronRight className="h-3.5 w-3.5 text-zinc-600" />
          <span className="text-zinc-400">{sportLabel}</span>
          <ChevronRight className="h-3.5 w-3.5 text-zinc-600" />
          <span className="font-semibold text-zinc-200">{fixture.league}</span>
        </div>

        <div className="flex items-center gap-2 text-xs font-mono text-zinc-400">
          <ShieldCheck className="h-3.5 w-3.5 text-emerald-400" />
          <span>Active Model:</span>
          <span className="text-zinc-200 font-semibold">{fixture.modelVersion}</span>
        </div>
      </div>

      {/* 2. Main Matchup Header */}
      <section className="rounded-lg border border-zinc-800 bg-[#0d131f] p-5 sm:p-6">
        {/* Unboxed Metadata */}
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-800/80 pb-3 text-xs text-zinc-400">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-zinc-200">{sportLabel}</span>
            <span aria-hidden="true" className="text-zinc-600">·</span>
            <span className="text-zinc-400 font-medium">{fixture.league}</span>
          </div>

          <div className="flex items-center gap-2 font-mono text-xs tabular-nums">
            {isLive && (
              <span className="flex items-center gap-1.5 text-amber-400 font-semibold">
                <span className="h-1.5 w-1.5 rounded-full bg-amber-400 animate-pulse" />
                LIVE {fixture.currentScore?.periodOrMinute ? `· ${fixture.currentScore.periodOrMinute}` : ''}
              </span>
            )}
            {isUpcoming && (
              <span className="text-zinc-400">
                Kickoff: {displayDate} · {displayTime}
              </span>
            )}
            {isCompleted && (
              <span className="text-zinc-400 font-medium">
                Official Final Result
              </span>
            )}
          </div>
        </div>

        {/* Scoreboard / Matchup */}
        {fixture.sport === 'formula_1' ? (
          <div className="my-5 space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-lg font-bold text-white">{fixture.league || 'Formula 1 Grand Prix'}</h2>
                <p className="text-xs text-zinc-400">20-Driver Championship Field</p>
              </div>
              <span className="text-xs font-mono text-zinc-400 border border-zinc-800 px-2 py-1 rounded bg-zinc-900">
                F1 Multi-Driver Engine
              </span>
            </div>

            {fixture.sportStats?.f1Drivers && fixture.sportStats.f1Drivers.length > 0 && (
              <div className="overflow-x-auto scrollbar-none rounded border border-zinc-800 bg-zinc-900/40">
                <table className="w-full text-left text-xs min-w-[480px]">
                  <thead className="bg-zinc-900 border-b border-zinc-800 text-[10px] font-semibold uppercase tracking-wider text-zinc-400">
                    <tr>
                      <th className="py-2 px-3 w-8">#</th>
                      <th className="py-2 px-3">Driver & Team</th>
                      <th className="py-2 px-3 text-right">Rating</th>
                      <th className="py-2 px-3 text-right">Win %</th>
                      <th className="py-2 px-3 text-right">Podium %</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/60 font-mono">
                    {fixture.sportStats.f1Drivers.map((driver, idx) => (
                      <tr key={driver.driverId || idx} className="hover:bg-zinc-800/30">
                        <td className="py-2 px-3 text-zinc-500">{idx + 1}</td>
                        <td className="py-2 px-3 font-sans">
                          <span className="font-semibold text-zinc-200">{driver.driverName}</span>
                          <span className="text-[10px] text-zinc-500 ml-1.5 font-normal">({driver.constructorName})</span>
                        </td>
                        <td className="py-2 px-3 text-right text-zinc-300">
                          {driver.rating ? driver.rating.toFixed(0) : '—'}
                        </td>
                        <td className="py-2 px-3 text-right font-bold text-emerald-400">
                          {driver.winProbability != null ? `${(driver.winProbability * 100).toFixed(1)}%` : '—'}
                        </td>
                        <td className="py-2 px-3 text-right text-zinc-300">
                          {driver.podiumProbability != null ? `${(driver.podiumProbability * 100).toFixed(0)}%` : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ) : (
          <div className="my-6 grid grid-cols-1 items-center gap-4 sm:grid-cols-5 text-center">
            {/* Home Team */}
            <div className="sm:col-span-2 sm:text-right">
              <div className="text-xl sm:text-2xl font-bold text-white tracking-tight">
                {fixture.homeTeam}
              </div>
              <div className="text-[10px] uppercase tracking-wider text-zinc-500 mt-0.5">Home</div>
            </div>

            {/* Center Score / State (Dominates completed/live matches) */}
            <div className="sm:col-span-1 flex flex-col items-center justify-center">
              {isLive && fixture.currentScore && (
                <div className="rounded border border-amber-600/40 bg-zinc-900 px-4 py-1.5 font-mono text-2xl font-bold text-amber-300 tabular-nums">
                  {fixture.currentScore.home} - {fixture.currentScore.away}
                </div>
              )}

              {isCompleted && fixture.finalScore && (
                <div className="rounded border border-zinc-700 bg-zinc-900/80 px-4 py-1.5 font-mono text-2xl font-bold text-white tabular-nums">
                  {fixture.finalScore.home} - {fixture.finalScore.away}
                </div>
              )}

              {isUpcoming && (
                <div className="rounded bg-zinc-800 px-3 py-1 font-mono text-xs font-semibold text-zinc-400 uppercase tracking-wider">
                  VS
                </div>
              )}
            </div>

            {/* Away Team */}
            <div className="sm:col-span-2 sm:text-left">
              <div className="text-xl sm:text-2xl font-bold text-white tracking-tight">
                {fixture.awayTeam}
              </div>
              <div className="text-[10px] uppercase tracking-wider text-zinc-500 mt-0.5">Away</div>
            </div>
          </div>
        )}

        {/* Primary Validated Prediction Signal Block */}
        <div className="rounded border border-zinc-800 bg-[#0f1726] p-3.5">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
            <div>
              <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-zinc-400">
                <span>{isCompleted ? 'Historical Best Signal' : isLive ? 'Pre-Match Signal' : 'Primary Validated Signal'}</span>
                <span className="normal-case text-zinc-500 font-normal">· {pred?.marketName}</span>
                {hitStatus && (
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                    hitStatus === 'hit' ? 'bg-emerald-950 text-emerald-300 border border-emerald-800' : 'bg-zinc-800 text-zinc-400'
                  }`}>
                    Evaluation: {hitStatus === 'hit' ? 'HIT' : 'MISS'}
                  </span>
                )}
              </div>
              <div className="text-sm font-bold text-white mt-0.5">
                {pred?.selection || 'No prediction available'}
              </div>
            </div>

            <div className="flex items-baseline gap-2 sm:text-right">
              <span className="font-mono text-2xl font-bold text-emerald-400 tabular-nums">
                {pred?.percentage != null
                  ? `${pred.percentage.toFixed(1)}%`
                  : '—'}
              </span>
              <span className="text-[10px] uppercase tracking-wider text-zinc-500 font-mono">
                Calibrated Probability
              </span>
            </div>
          </div>
        </div>

        {/* Sport-Specific Statistical Indicators */}
        <div className="mt-5 border-t border-zinc-800/80 pt-4">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400 mb-2">
            Key Indicators · {sportLabel}
          </div>

          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 text-xs font-mono">
            {fixture.sport === 'football' && (
              <>
                <div className="rounded border border-zinc-800/80 bg-zinc-900/50 p-2">
                  <span className="text-zinc-500 block text-[10px] uppercase tracking-wider">Home xG Form</span>
                  <span className="text-sm font-semibold text-zinc-200 tabular-nums mt-0.5 block">
                    {fixture.sportStats?.xGRecentHome != null ? fixture.sportStats.xGRecentHome.toFixed(2) : '—'}
                  </span>
                </div>
                <div className="rounded border border-zinc-800/80 bg-zinc-900/50 p-2">
                  <span className="text-zinc-500 block text-[10px] uppercase tracking-wider">Away xG Form</span>
                  <span className="text-sm font-semibold text-zinc-200 tabular-nums mt-0.5 block">
                    {fixture.sportStats?.xGRecentAway != null ? fixture.sportStats.xGRecentAway.toFixed(2) : '—'}
                  </span>
                </div>
                <div className="rounded border border-zinc-800/80 bg-zinc-900/50 p-2">
                  <span className="text-zinc-500 block text-[10px] uppercase tracking-wider">Clean Sheets</span>
                  <span className="text-sm font-semibold text-zinc-200 tabular-nums mt-0.5 block">
                    {fixture.sportStats?.homeCleanSheets != null && fixture.sportStats?.awayCleanSheets != null
                      ? `${fixture.sportStats.homeCleanSheets} - ${fixture.sportStats.awayCleanSheets}`
                      : '—'}
                  </span>
                </div>
                <div className="rounded border border-zinc-800/80 bg-zinc-900/50 p-2">
                  <span className="text-zinc-500 block text-[10px] uppercase tracking-wider">Avg Corners</span>
                  <span className="text-sm font-semibold text-zinc-200 tabular-nums mt-0.5 block">
                    {fixture.sportStats?.avgMatchCorners != null ? fixture.sportStats.avgMatchCorners.toFixed(1) : '—'}
                  </span>
                </div>
              </>
            )}

            {fixture.sport === 'basketball' && (
              <>
                <div className="rounded border border-zinc-800/80 bg-zinc-900/50 p-2">
                  <span className="text-zinc-500 block text-[10px] uppercase tracking-wider">Pace Factor</span>
                  <span className="text-sm font-semibold text-zinc-200 tabular-nums mt-0.5 block">
                    {fixture.sportStats?.pace != null ? fixture.sportStats.pace.toFixed(1) : '—'}
                  </span>
                </div>
                <div className="rounded border border-zinc-800/80 bg-zinc-900/50 p-2">
                  <span className="text-zinc-500 block text-[10px] uppercase tracking-wider">Home PPG</span>
                  <span className="text-sm font-semibold text-zinc-200 tabular-nums mt-0.5 block">
                    {fixture.sportStats?.homePPG != null ? fixture.sportStats.homePPG.toFixed(1) : '—'}
                  </span>
                </div>
                <div className="rounded border border-zinc-800/80 bg-zinc-900/50 p-2">
                  <span className="text-zinc-500 block text-[10px] uppercase tracking-wider">Away PPG</span>
                  <span className="text-sm font-semibold text-zinc-200 tabular-nums mt-0.5 block">
                    {fixture.sportStats?.awayPPG != null ? fixture.sportStats.awayPPG.toFixed(1) : '—'}
                  </span>
                </div>
                <div className="rounded border border-zinc-800/80 bg-zinc-900/50 p-2">
                  <span className="text-zinc-500 block text-[10px] uppercase tracking-wider">Rebound Diff</span>
                  <span className="text-sm font-semibold text-zinc-200 tabular-nums mt-0.5 block">
                    {fixture.sportStats?.reboundDifferential != null
                      ? `${fixture.sportStats.reboundDifferential > 0 ? '+' : ''}${fixture.sportStats.reboundDifferential.toFixed(1)}`
                      : '—'}
                  </span>
                </div>
              </>
            )}

            {fixture.sport === 'baseball' && (
              <>
                <div className="rounded border border-zinc-800/80 bg-zinc-900/50 p-2">
                  <span className="text-zinc-500 block text-[10px] uppercase tracking-wider">Home ERA</span>
                  <span className="text-sm font-semibold text-zinc-200 tabular-nums mt-0.5 block">
                    {fixture.sportStats?.homeERA != null ? fixture.sportStats.homeERA.toFixed(2) : '—'}
                  </span>
                </div>
                <div className="rounded border border-zinc-800/80 bg-zinc-900/50 p-2">
                  <span className="text-zinc-500 block text-[10px] uppercase tracking-wider">Away ERA</span>
                  <span className="text-sm font-semibold text-zinc-200 tabular-nums mt-0.5 block">
                    {fixture.sportStats?.awayERA != null ? fixture.sportStats.awayERA.toFixed(2) : '—'}
                  </span>
                </div>
                <div className="rounded border border-zinc-800/80 bg-zinc-900/50 p-2">
                  <span className="text-zinc-500 block text-[10px] uppercase tracking-wider">Bullpen WHIP</span>
                  <span className="text-sm font-semibold text-zinc-200 tabular-nums mt-0.5 block">
                    {fixture.sportStats?.bullpenWHIP != null ? fixture.sportStats.bullpenWHIP.toFixed(2) : '—'}
                  </span>
                </div>
                <div className="rounded border border-zinc-800/80 bg-zinc-900/50 p-2">
                  <span className="text-zinc-500 block text-[10px] uppercase tracking-wider">Team Batting</span>
                  <span className="text-sm font-semibold text-zinc-200 tabular-nums mt-0.5 block">
                    {fixture.sportStats?.battingAvg != null ? `.${Math.round(fixture.sportStats.battingAvg * 1000)}` : '—'}
                  </span>
                </div>
              </>
            )}

            {(fixture.sport === 'ice_hockey' || (fixture.sport as string) === 'hockey') && (
              <>
                <div className="rounded border border-zinc-800/80 bg-zinc-900/50 p-2">
                  <span className="text-zinc-500 block text-[10px] uppercase tracking-wider">Goals For / G</span>
                  <span className="text-sm font-semibold text-zinc-200 tabular-nums mt-0.5 block">
                    {fixture.sportStats?.homeGoalsScoredAvg != null
                      ? fixture.sportStats.homeGoalsScoredAvg.toFixed(2)
                      : fixture.sportStats?.goalsPerGame != null
                      ? fixture.sportStats.goalsPerGame.toFixed(2)
                      : '—'}
                  </span>
                </div>
                <div className="rounded border border-zinc-800/80 bg-zinc-900/50 p-2">
                  <span className="text-zinc-500 block text-[10px] uppercase tracking-wider">Goals Against / G</span>
                  <span className="text-sm font-semibold text-zinc-200 tabular-nums mt-0.5 block">
                    {fixture.sportStats?.homeGoalsConcededAvg != null
                      ? fixture.sportStats.homeGoalsConcededAvg.toFixed(2)
                      : '—'}
                  </span>
                </div>
                <div className="rounded border border-zinc-800/80 bg-zinc-900/50 p-2">
                  <span className="text-zinc-500 block text-[10px] uppercase tracking-wider">Shots / Game</span>
                  <span className="text-sm font-semibold text-zinc-200 tabular-nums mt-0.5 block">
                    {fixture.sportStats?.homeShotsAvg != null ? fixture.sportStats.homeShotsAvg.toFixed(1) : '—'}
                  </span>
                </div>
                <div className="rounded border border-zinc-800/80 bg-zinc-900/50 p-2">
                  <span className="text-zinc-500 block text-[10px] uppercase tracking-wider">Recent Form</span>
                  <span className="text-xs font-semibold text-zinc-200 mt-0.5 block truncate">
                    {fixture.sportStats?.homeRecentForm ?? 'Standard'}
                  </span>
                </div>
              </>
            )}

            {fixture.sport === 'formula_1' && (
              <>
                <div className="rounded border border-zinc-800/80 bg-zinc-900/50 p-2">
                  <span className="text-zinc-500 block text-[10px] uppercase tracking-wider">Grid Position</span>
                  <span className="text-sm font-semibold text-zinc-200 tabular-nums mt-0.5 block">
                    {fixture.sportStats?.gridPosition != null ? `P${fixture.sportStats.gridPosition}` : '—'}
                  </span>
                </div>
                <div className="rounded border border-zinc-800/80 bg-zinc-900/50 p-2">
                  <span className="text-zinc-500 block text-[10px] uppercase tracking-wider">Standing</span>
                  <span className="text-xs font-semibold text-zinc-200 mt-0.5 block truncate">
                    {fixture.sportStats?.constructorStanding ?? '—'}
                  </span>
                </div>
                <div className="rounded border border-zinc-800/80 bg-zinc-900/50 p-2">
                  <span className="text-zinc-500 block text-[10px] uppercase tracking-wider">Pole Conv. Rate</span>
                  <span className="text-sm font-semibold text-zinc-200 tabular-nums mt-0.5 block">
                    {fixture.sportStats?.poleConversionRate != null ? `${fixture.sportStats.poleConversionRate.toFixed(1)}%` : '—'}
                  </span>
                </div>
                <div className="rounded border border-zinc-800/80 bg-zinc-900/50 p-2">
                  <span className="text-zinc-500 block text-[10px] uppercase tracking-wider">Validation State</span>
                  <span className="text-xs font-semibold text-emerald-400 mt-0.5 block">
                    Validated
                  </span>
                </div>
              </>
            )}
          </div>
        </div>
      </section>

      {/* 3. Validated Markets Sections */}
      <section className="space-y-4">
        <div className="flex items-center justify-between border-b border-zinc-800 pb-2">
          <h2 className="text-sm font-semibold tracking-wide uppercase text-zinc-300">
            Validated Markets ({sportLabel})
          </h2>
          <span className="text-xs font-mono text-zinc-400">
            {(fixture.validatedMarkets || []).length} {(fixture.validatedMarkets || []).length === 1 ? 'line' : 'lines'}
          </span>
        </div>

        {marketGroups.map((group, idx) => (
          <MarketSection
            key={idx}
            title={group.title}
            markets={group.markets}
            sport={fixture.sport}
          />
        ))}
      </section>

      {/* 4. Model Architecture & Calibration Audit Note */}
      <section className="rounded-lg border border-zinc-800/80 bg-zinc-900/30 p-4 text-xs font-mono text-zinc-400">
        <div className="flex items-center gap-2 text-zinc-300 font-semibold mb-1">
          <Cpu className="h-3.5 w-3.5 text-emerald-400" />
          <span>Model Architecture & Calibration Audit</span>
        </div>
        <p className="text-[11px] leading-relaxed text-zinc-500">
          Predictions are computed strictly via historical backtests with zero future-data leakage. Calibrated through isotonic regression with probabilities bounded in [0.01, 0.99]. Fixtures with insufficient historical matches (&lt;5 past fixtures) are automatically barred from publication.
        </p>
      </section>
    </div>
  );
};
