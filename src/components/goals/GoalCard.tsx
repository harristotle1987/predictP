import React from 'react';
import { GoalPredictionItem } from '../../types';
import { formatLagosKickoff } from '../../utils/timezone';
import { ChevronRight } from 'lucide-react';

interface GoalCardProps {
  item: GoalPredictionItem;
  onOpenFixture: (fixtureId: string) => void;
}

export const GoalCard: React.FC<GoalCardProps> = ({ item, onOpenFixture }) => {
  const { displayDate, displayTime } = formatLagosKickoff(item.kickoffUtc);

  const pctDisplay = typeof item.percentage === 'number' ? `${item.percentage.toFixed(1)}%` : '—';
  const xgDisplay = typeof item.xGCombined === 'number' ? item.xGCombined.toFixed(2) : '—';
  const avgScored =
    typeof item.homeAvgScored === 'number' && typeof item.awayAvgScored === 'number'
      ? (item.homeAvgScored + item.awayAvgScored).toFixed(1)
      : '—';
  const bttsDisplay =
    typeof item.bothTeamsScoredRecentRate === 'number'
      ? item.bothTeamsScoredRecentRate <= 1
        ? `${Math.round(item.bothTeamsScoredRecentRate * 100)}%`
        : `${Math.round(item.bothTeamsScoredRecentRate)}%`
      : '—';

  return (
    <article
      onClick={() => onOpenFixture(item.fixtureId)}
      className="group flex flex-col justify-between rounded-lg border border-zinc-800 bg-[#0d131f] p-4 transition-colors hover:border-zinc-700 hover:bg-[#111927] cursor-pointer"
    >
      <div>
        {/* 1. Header Metadata Bar */}
        <div className="flex items-center justify-between text-xs text-zinc-400 border-b border-zinc-800/80 pb-2.5">
          <div className="flex items-center gap-1.5 truncate">
            <span className="font-semibold text-zinc-200">Football Goals</span>
            <span aria-hidden="true" className="text-zinc-600">·</span>
            <span className="truncate text-zinc-400 font-medium">{item.league}</span>
          </div>

          <div className="font-mono text-xs text-zinc-400 shrink-0 tabular-nums">
            {displayDate} · {displayTime}
          </div>
        </div>

        {/* 2. Matchup */}
        <div className="py-3">
          <h3 className="text-sm font-semibold text-zinc-100 group-hover:text-emerald-300 transition-colors">
            {item.homeTeam} <span className="text-zinc-500 font-normal">vs</span> {item.awayTeam}
          </h3>
        </div>

        {/* 3. Goal Prediction Signal Box */}
        <div className="rounded border border-zinc-800 bg-[#0f1726] p-2.5">
          <div className="flex items-start justify-between gap-2">
            <div>
              <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400">
                Market · {item.marketType}
              </div>
              <div className="text-xs font-bold text-zinc-100 mt-0.5">
                {item.predictedOutcome}
              </div>
            </div>
            <div className="text-right shrink-0">
              <div className="font-mono text-base font-bold text-emerald-400 tabular-nums">
                {pctDisplay}
              </div>
              <div className="text-[9px] uppercase tracking-wider text-zinc-500 font-mono">
                Probability
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 4. Goal Metrics Footer */}
      <div className="mt-3 grid grid-cols-3 gap-2 border-t border-zinc-800/70 pt-2.5 text-center text-xs font-mono">
        <div className="rounded bg-zinc-900/50 p-1.5 border border-zinc-800/60">
          <div className="text-[10px] text-zinc-400 uppercase tracking-wider">Combined xG</div>
          <div className="font-semibold text-zinc-200 tabular-nums mt-0.5">{xgDisplay}</div>
        </div>
        <div className="rounded bg-zinc-900/50 p-1.5 border border-zinc-800/60">
          <div className="text-[10px] text-zinc-400 uppercase tracking-wider">Avg Goals</div>
          <div className="font-semibold text-zinc-200 tabular-nums mt-0.5">{avgScored}</div>
        </div>
        <div className="rounded bg-zinc-900/50 p-1.5 border border-zinc-800/60">
          <div className="text-[10px] text-zinc-400 uppercase tracking-wider">BTTS Form</div>
          <div className="font-semibold text-zinc-200 tabular-nums mt-0.5">{bttsDisplay}</div>
        </div>
      </div>
    </article>
  );
};
