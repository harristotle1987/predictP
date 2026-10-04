import React from 'react';
import { MarketItem } from '../../types';

interface MarketSectionProps {
  title: string;
  markets: MarketItem[];
  sport: string;
}

export const MarketSection: React.FC<MarketSectionProps> = ({ title, markets }) => {
  if (markets.length === 0) return null;

  return (
    <div className="rounded-lg border border-zinc-800 bg-[#0d131f] p-4">
      <div className="flex items-center justify-between border-b border-zinc-800/80 pb-2.5">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-300">{title}</h3>
        <span className="text-[11px] font-mono text-zinc-500 tabular-nums">
          {markets.length} {markets.length === 1 ? 'Market' : 'Markets'}
        </span>
      </div>

      <div className="mt-3 grid grid-cols-1 gap-2.5 sm:grid-cols-2">
        {markets.map((market) => (
          <div
            key={market.id}
            className="flex flex-col justify-between rounded border border-zinc-800/80 bg-zinc-900/50 p-3 transition-colors hover:border-zinc-700"
          >
            <div>
              <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400">
                {market.marketName}
              </div>
              <div className="mt-0.5 text-xs font-bold text-zinc-100">
                {market.selection}
              </div>
            </div>

            <div className="mt-2.5">
              <div className="flex items-center justify-between text-xs">
                <span className="text-zinc-500 text-[11px]">Calibrated Probability</span>
                <span className="font-mono text-sm font-bold text-emerald-400 tabular-nums">
                  {market.probabilityPercentage.toFixed(1)}%
                </span>
              </div>

              {/* Compact probability bar */}
              <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-zinc-800">
                <div
                  className="h-full bg-emerald-500 rounded-full"
                  style={{ width: `${Math.min(100, Math.max(0, market.probabilityPercentage))}%` }}
                />
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
