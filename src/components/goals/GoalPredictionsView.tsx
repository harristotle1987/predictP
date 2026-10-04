import React, { useState } from 'react';
import { GoalPredictionItem } from '../../types';
import { EmptyState } from '../common/EmptyState';
import { SkeletonCard } from '../common/SkeletonCard';
import { GoalCard } from './GoalCard';
import { Activity, RefreshCw } from 'lucide-react';

interface GoalPredictionsViewProps {
  goalItems: GoalPredictionItem[];
  isLoading: boolean;
  onOpenFixture: (fixtureId: string) => void;
  onRefresh: () => void;
  previewMode?: boolean;
}

const GOAL_FILTERS: { id: 'all' | 'over_15' | 'over_25' | 'under_25' | 'btts'; label: string }[] = [
  { id: 'all', label: 'All Goal Signals' },
  { id: 'over_15', label: 'Over 1.5 Goals' },
  { id: 'over_25', label: 'Over 2.5 Goals' },
  { id: 'under_25', label: 'Under 2.5 Goals' },
  { id: 'btts', label: 'BTTS' },
];

export const GoalPredictionsView: React.FC<GoalPredictionsViewProps> = ({
  goalItems,
  isLoading,
  onOpenFixture,
  onRefresh,
}) => {
  const [activeFilter, setActiveFilter] = useState<'all' | 'over_15' | 'over_25' | 'under_25' | 'btts'>('all');

  const filteredItems = React.useMemo(() => {
    return goalItems.filter((item) => {
      const type = (item.marketType || '').toLowerCase();
      if (activeFilter === 'over_15') return type.includes('1.5');
      if (activeFilter === 'over_25') return type.includes('over 2.5') || (type.includes('2.5') && !type.includes('under'));
      if (activeFilter === 'under_25') return type.includes('under 2.5');
      if (activeFilter === 'btts') return type.includes('btts') || type.includes('both teams');
      return true;
    });
  }, [goalItems, activeFilter]);

  return (
    <div className="space-y-6">
      {/* 1. Header Zone */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between border-b border-zinc-800 pb-4">
        <div>
          <div className="text-[11px] font-mono uppercase tracking-wider text-zinc-400">
            Poisson Probability Engine · Football Dedicated
          </div>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-white">
            Goal Predictions & Totals
          </h1>
          <p className="mt-1 text-xs text-zinc-400">
            Calibrated expectancy metrics, combined xG form, and Both Teams To Score probabilities.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <span className="text-xs text-zinc-400 font-mono">
            <strong className="text-zinc-200">{filteredItems.length}</strong> signals
          </span>
          <button
            onClick={onRefresh}
            disabled={isLoading}
            className="flex h-8 items-center gap-1.5 rounded border border-zinc-700 bg-zinc-800 px-3 text-xs font-medium text-zinc-200 transition-colors hover:bg-zinc-700 hover:text-white disabled:opacity-50"
            title="Refresh prediction pipeline"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? 'animate-spin text-emerald-400' : 'text-zinc-400'}`} />
            <span>Refresh Predictions</span>
          </button>
        </div>
      </div>

      {/* 2. Filter Bar */}
      <div className="flex items-center gap-1 overflow-x-auto scrollbar-none rounded-lg border border-zinc-800 bg-[#0d131f] p-2">
        <span className="mr-2 text-xs font-medium text-zinc-500 pl-1 shrink-0">Market:</span>
        {GOAL_FILTERS.map((filter) => (
          <button
            key={filter.id}
            onClick={() => setActiveFilter(filter.id)}
            className={`rounded px-3 py-1.5 text-xs font-medium transition-colors whitespace-nowrap shrink-0 ${
              activeFilter === filter.id
                ? 'bg-zinc-200 text-zinc-900 font-semibold'
                : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60'
            }`}
          >
            {filter.label}
          </button>
        ))}
      </div>

      {/* 3. Grid of Goal Prediction Cards */}
      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      ) : filteredItems.length === 0 ? (
        <EmptyState
          title="No goal predictions matching filter"
          description="There are currently no validated goal predictions meeting the probability threshold for this filter."
          iconType="filter"
          actionLabel="Show All Goal Signals"
          onAction={() => setActiveFilter('all')}
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {filteredItems.map((item) => (
            <GoalCard
              key={item.id}
              item={item}
              onOpenFixture={onOpenFixture}
            />
          ))}
        </div>
      )}
    </div>
  );
};
