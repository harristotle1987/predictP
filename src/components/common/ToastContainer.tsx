import React from 'react';
import { AlertCircle, AlertTriangle, CheckCircle2, Info, X, ExternalLink, Activity } from 'lucide-react';

export interface ToastItem {
  id: string;
  type: 'error' | 'warning' | 'info' | 'success';
  title: string;
  message: string;
  code?: string;
  actionLabel?: string;
  onAction?: () => void;
  timestamp?: string;
  autoClose?: boolean;
  duration?: number;
}

interface ToastContainerProps {
  toasts: ToastItem[];
  onDismiss: (id: string) => void;
}

export const ToastContainer: React.FC<ToastContainerProps> = ({ toasts, onDismiss }) => {
  if (toasts.length === 0) return null;

  return (
    <div
      aria-live="assertive"
      className="fixed bottom-4 right-4 z-50 flex max-w-md w-full flex-col gap-2.5 pointer-events-none px-4 sm:px-0"
    >
      {toasts.map((toast) => {
        const isError = toast.type === 'error';
        const isWarning = toast.type === 'warning';
        const isSuccess = toast.type === 'success';

        let borderClass = 'border-blue-500/40 bg-zinc-950/95 text-blue-200';
        let iconColor = 'text-blue-400';
        let IconComponent = Info;

        if (isError) {
          borderClass = 'border-red-500/50 bg-red-950/90 text-red-200 shadow-lg shadow-red-950/50';
          iconColor = 'text-red-400';
          IconComponent = AlertCircle;
        } else if (isWarning) {
          borderClass = 'border-amber-500/50 bg-amber-950/90 text-amber-200 shadow-lg shadow-amber-950/50';
          iconColor = 'text-amber-400';
          IconComponent = AlertTriangle;
        } else if (isSuccess) {
          borderClass = 'border-emerald-500/50 bg-emerald-950/90 text-emerald-200 shadow-lg shadow-emerald-950/50';
          iconColor = 'text-emerald-400';
          IconComponent = CheckCircle2;
        }

        return (
          <div
            key={toast.id}
            role="alert"
            className={`pointer-events-auto relative flex flex-col rounded-lg border p-3.5 backdrop-blur-md transition-all duration-300 animate-in fade-in slide-in-from-bottom-3 ${borderClass}`}
          >
            <div className="flex items-start gap-3">
              <div className={`mt-0.5 flex-shrink-0 ${iconColor}`}>
                <IconComponent className="h-5 w-5" />
              </div>

              <div className="flex-1 min-w-0 pr-2">
                <div className="flex items-center gap-2 flex-wrap">
                  <h4 className="text-xs font-semibold text-white tracking-tight">{toast.title}</h4>
                  {toast.code && (
                    <span className="rounded bg-black/40 px-1.5 py-0.5 text-[10px] font-mono text-zinc-300 border border-white/10">
                      {toast.code}
                    </span>
                  )}
                </div>
                <p className="mt-1 text-xs leading-relaxed text-zinc-300 font-normal">
                  {toast.message}
                </p>

                {toast.actionLabel && toast.onAction && (
                  <div className="mt-2.5 flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        toast.onAction?.();
                        onDismiss(toast.id);
                      }}
                      className="inline-flex items-center gap-1.5 rounded bg-emerald-500 hover:bg-emerald-400 px-2.5 py-1 text-[11px] font-semibold text-zinc-950 transition-colors shadow-sm"
                    >
                      <Activity className="h-3.5 w-3.5" />
                      <span>{toast.actionLabel}</span>
                      <ExternalLink className="h-3 w-3 ml-0.5" />
                    </button>
                  </div>
                )}
              </div>

              <button
                type="button"
                onClick={() => onDismiss(toast.id)}
                className="text-zinc-400 hover:text-white transition-colors p-0.5 -mr-1 -mt-1 rounded focus:outline-none"
                aria-label="Dismiss notification"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
};
