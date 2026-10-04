import React, { useState } from 'react';
import { Database, Key, CheckCircle2, AlertCircle, Loader2, X, Shield, Lock } from 'lucide-react';
import { apiService } from '../../services/api';

interface NeonConfigModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

export const NeonConfigModal: React.FC<NeonConfigModalProps> = ({ isOpen, onClose, onSuccess }) => {
  const [neonUrl, setNeonUrl] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [showPassword, setShowPassword] = useState(false);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!neonUrl.trim()) {
      setErrorMsg('Please enter a valid NEON_DATABASE_URL connection string.');
      return;
    }

    setIsSaving(true);
    setErrorMsg(null);
    setSuccessMsg(null);

    try {
      const res = await apiService.updateNeonUrl(neonUrl.trim());
      if (res.success) {
        setSuccessMsg(res.data?.message || 'NEON_DATABASE_URL saved & connected successfully!');
        setTimeout(() => {
          setIsSaving(false);
          setSuccessMsg(null);
          onSuccess();
          onClose();
        }, 1200);
      } else {
        setIsSaving(false);
        setErrorMsg(res.error || 'Failed to establish Neon PostgreSQL connection.');
      }
    } catch (err) {
      setIsSaving(false);
      setErrorMsg(err instanceof Error ? err.message : 'Unexpected connection error');
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 animate-in fade-in duration-200">
      <div className="relative w-full max-w-lg rounded-xl border border-zinc-800 bg-[#0d131f] shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-zinc-800/80 px-5 py-4 bg-zinc-900/50">
          <div className="flex items-center gap-2.5">
            <div className="rounded-lg bg-emerald-500/10 p-2 text-emerald-400 border border-emerald-500/20">
              <Database className="h-5 w-5" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-white">Configure Environment Variable</h3>
              <p className="text-xs text-zinc-400">Set primary operational store connection key</p>
            </div>
          </div>
          <button
            onClick={onClose}
            disabled={isSaving}
            className="rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-white transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body Form */}
        <form onSubmit={handleSubmit} className="p-5 space-y-4 text-xs">
          {/* Environment Variable Name Field */}
          <div className="space-y-1.5">
            <label className="block text-[11px] font-mono font-medium text-zinc-300 uppercase tracking-wider">
              Environment Variable Name
            </label>
            <div className="relative flex items-center">
              <Key className="absolute left-3 h-4 w-4 text-emerald-400" />
              <input
                type="text"
                value="NEON_DATABASE_URL"
                readOnly
                className="w-full rounded-lg border border-zinc-800 bg-zinc-900/90 pl-9 pr-3 py-2.5 font-mono text-xs font-bold text-emerald-400 select-all cursor-not-allowed focus:outline-none"
              />
              <span className="absolute right-3 text-[10px] font-mono text-zinc-500 bg-zinc-800/80 px-2 py-0.5 rounded">
                REQUIRED
              </span>
            </div>
          </div>

          {/* Environment Variable Value Field */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <label className="block text-[11px] font-mono font-medium text-zinc-300 uppercase tracking-wider">
                Environment Variable Value
              </label>
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="text-[10px] font-mono text-zinc-400 hover:text-zinc-200 underline"
              >
                {showPassword ? 'Hide Connection String' : 'Show Connection String'}
              </button>
            </div>
            <div className="relative">
              <textarea
                value={neonUrl}
                onChange={(e) => setNeonUrl(e.target.value)}
                placeholder="postgresql://user:password@ep-xyz.neon.tech/neondb?sslmode=require"
                rows={3}
                spellCheck={false}
                required
                className={`w-full rounded-lg border bg-zinc-950 p-3 font-mono text-xs text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:ring-1 transition-all ${
                  showPassword ? 'tracking-normal' : ''
                } ${
                  errorMsg
                    ? 'border-rose-500/50 focus:ring-rose-500'
                    : 'border-zinc-800 focus:border-emerald-500/50 focus:ring-emerald-500'
                }`}
                style={!showPassword && neonUrl ? ({ WebkitTextSecurity: 'disc' } as any) : {}}
              />
            </div>
            <p className="text-[11px] text-zinc-500">
              Format: <code className="font-mono text-zinc-400">postgresql://user:password@ep-xyz.neon.tech/neondb?sslmode=require</code>
            </p>
          </div>

          {/* Security Notice */}
          <div className="flex items-center gap-2 rounded-lg border border-zinc-800/80 bg-zinc-900/40 p-2.5 text-[11px] text-zinc-400">
            <Shield className="h-4 w-4 text-emerald-400 shrink-0" />
            <span>Connection string is saved securely to <code className="font-mono text-zinc-300">.env</code> and encrypted in server memory.</span>
          </div>

          {/* Feedback messages */}
          {errorMsg && (
            <div className="flex items-center gap-2 rounded-lg border border-rose-500/20 bg-rose-500/10 p-3 text-rose-300 animate-in fade-in">
              <AlertCircle className="h-4 w-4 text-rose-400 shrink-0" />
              <span>{errorMsg}</span>
            </div>
          )}

          {successMsg && (
            <div className="flex items-center gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/10 p-3 text-emerald-300 animate-in fade-in">
              <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0" />
              <span>{successMsg}</span>
            </div>
          )}

          {/* Actions */}
          <div className="flex items-center justify-end gap-2.5 pt-2 border-t border-zinc-800/80">
            <button
              type="button"
              onClick={onClose}
              disabled={isSaving}
              className="rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-2 font-medium text-zinc-300 hover:bg-zinc-800 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSaving || !neonUrl.trim()}
              className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 font-semibold text-white hover:bg-emerald-500 disabled:opacity-50 transition-colors shadow-lg shadow-emerald-950/30"
            >
              {isSaving ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  <span>Connecting Database...</span>
                </>
              ) : (
                <>
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  <span>Save & Connect Database</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
