import React from 'react';

export const SkeletonCard: React.FC = () => {
  return (
    <div className="animate-pulse rounded-xl border border-slate-800/80 bg-[#0e1420] p-5">
      {/* Header unboxed row skeleton */}
      <div className="flex items-center justify-between pb-3">
        <div className="flex items-center gap-2">
          <div className="h-3.5 w-24 rounded bg-slate-800" />
          <div className="h-3 w-3 rounded-full bg-slate-800" />
          <div className="h-3.5 w-32 rounded bg-slate-800" />
        </div>
        <div className="h-4 w-20 rounded bg-slate-800" />
      </div>

      {/* Matchup lines */}
      <div className="my-4 space-y-2.5">
        <div className="flex items-center justify-between">
          <div className="h-5 w-40 rounded bg-slate-700/60" />
          <div className="h-5 w-8 rounded bg-slate-800" />
        </div>
        <div className="flex items-center justify-between">
          <div className="h-5 w-36 rounded bg-slate-700/60" />
          <div className="h-5 w-8 rounded bg-slate-800" />
        </div>
      </div>

      {/* Prediction Highlight Bar */}
      <div className="mt-4 rounded-lg border border-slate-800 bg-slate-900/60 p-3.5">
        <div className="flex items-center justify-between">
          <div className="space-y-1.5">
            <div className="h-3 w-28 rounded bg-slate-800" />
            <div className="h-4 w-44 rounded bg-slate-700/80" />
          </div>
          <div className="h-7 w-14 rounded bg-slate-800" />
        </div>
      </div>

      {/* Stats footer skeleton */}
      <div className="mt-3.5 flex items-center justify-between pt-2">
        <div className="h-3 w-48 rounded bg-slate-800/60" />
        <div className="h-3 w-16 rounded bg-slate-800/60" />
      </div>
    </div>
  );
};
