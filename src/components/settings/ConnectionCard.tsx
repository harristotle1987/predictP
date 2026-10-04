import React from 'react';
import { ConnectionState } from '../../types';

interface ConnectionCardProps {
  name: string;
  category: string;
  status: ConnectionState;
  latencyMs: number;
  details: { label: string; value: string | number }[];
  description: string;
  error?: string;
  actionButton?: React.ReactNode;
}

export const ConnectionCard: React.FC<ConnectionCardProps> = ({
  name,
  category,
  status,
  latencyMs,
  details,
  description,
  error,
  actionButton,
}) => {
  const isConnected = status === 'connected';
  const isDegraded = status === 'degraded';
  const isConnecting = status === 'connecting';

  return (
    <div className="rounded-lg border border-zinc-800 bg-[#0d131f] p-4 transition-colors hover:border-zinc-700 flex flex-col justify-between">
      <div>
        {/* Top row: Name & Category */}
        <div className="flex items-start justify-between">
          <div>
            <div className="text-[10px] uppercase tracking-wider font-semibold text-zinc-400">{category}</div>
            <h3 className="text-sm font-bold text-zinc-100 mt-0.5">{name}</h3>
          </div>

          {/* Status Indicator */}
          <div className="flex items-center gap-1.5 font-mono text-[11px] tabular-nums shrink-0 ml-2">
            {isConnected && (
              <>
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                <span className="font-semibold text-emerald-400">Connected ({latencyMs}ms)</span>
              </>
            )}
            {isDegraded && (
              <>
                <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
                <span className="font-semibold text-amber-400">Degraded {latencyMs > 0 ? `(${latencyMs}ms)` : ''}</span>
              </>
            )}
            {(isConnecting || status === 'disconnected') && (
              <>
                <span className="h-1.5 w-1.5 rounded-full bg-zinc-500 animate-pulse" />
                <span className="font-medium text-zinc-400">
                  {isConnecting ? 'Handshake' : 'Disconnected'}
                </span>
              </>
            )}
          </div>
        </div>

        <p className="mt-2 text-xs text-zinc-400 leading-relaxed">{description}</p>

        {error && status !== 'connected' && (
          <div className="mt-2 rounded bg-rose-950/30 border border-rose-800/40 p-2 text-[11px] font-mono text-rose-300 break-words">
            <span className="text-rose-400 font-semibold">Reason: </span>
            {error}
          </div>
        )}

        {actionButton && <div className="mt-2.5">{actionButton}</div>}
      </div>

      {/* Metrics Row */}
      <div className="mt-3 grid grid-cols-2 gap-2 border-t border-zinc-800/80 pt-2.5 text-xs font-mono">
        {details.map((item, idx) => (
          <div key={idx} className="text-[11px]">
            <span className="text-zinc-500">{item.label}: </span>
            <span className="font-semibold text-zinc-200 tabular-nums">
              {item.value}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
};
