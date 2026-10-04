import React, { useEffect, useState } from 'react';
import { PredictionModel } from '../../types';
import { LAGOS_TIMEZONE } from '../../utils/timezone';
import {
  Activity,
  BarChart3,
  Calendar,
  Cpu,
  Layers,
  Menu,
  ShieldCheck,
  X,
} from 'lucide-react';

export type ActivePage = 'home' | 'matches' | 'goals' | 'settings' | 'details' | 'diagnostics';

interface TopBarProps {
  activePage: ActivePage;
  onNavigate: (page: ActivePage) => void;
  activeModel: PredictionModel;
  neonStatus?: 'connected' | 'disconnected' | 'degraded' | 'not_configured' | 'connecting';
}

export const TopBar: React.FC<TopBarProps> = ({
  activePage,
  onNavigate,
  activeModel,
  neonStatus,
}) => {
  const [lagosTime, setLagosTime] = useState<string>('');
  const [mobileMenuOpen, setMobileMenuOpen] = useState<boolean>(false);

  useEffect(() => {
    const updateTime = () => {
      try {
        const now = new Date();
        const formatted = new Intl.DateTimeFormat('en-GB', {
          timeZone: LAGOS_TIMEZONE,
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
          hour12: false,
        }).format(now);
        setLagosTime(`${formatted} WAT`);
      } catch {
        setLagosTime('WAT (UTC+1)');
      }
    };

    updateTime();
    const interval = setInterval(updateTime, 1000);
    return () => clearInterval(interval);
  }, []);

  const navItems: {
    id: ActivePage;
    label: string;
    shortLabel: string;
    icon: React.ComponentType<{ className?: string }>;
    badge?: React.ReactNode;
  }[] = [
    { id: 'home', label: 'Predictions', shortLabel: 'Predictions', icon: BarChart3 },
    { id: 'matches', label: 'Operational Fixtures', shortLabel: 'Fixtures', icon: Calendar },
    { id: 'goals', label: 'Goal Signals', shortLabel: 'Goals', icon: Activity },
    {
      id: 'diagnostics',
      label: 'Diagnostics',
      shortLabel: 'Diagnostics',
      icon: ShieldCheck,
      badge: (
        <span
          className={`h-2 w-2 rounded-full ${
            neonStatus === 'connected'
              ? 'bg-emerald-400'
              : neonStatus === 'degraded'
              ? 'bg-amber-400'
              : neonStatus === 'not_configured'
              ? 'bg-zinc-500'
              : neonStatus === 'connecting'
              ? 'bg-blue-400 animate-pulse'
              : 'bg-red-400'
          }`}
          title={`Neon Status: ${neonStatus || 'disconnected'}`}
        />
      ),
    },
    { id: 'settings', label: 'Settings & Analytics', shortLabel: 'Settings', icon: Cpu },
  ];

  return (
    <>
      <header className="sticky top-0 z-40 w-full border-b border-zinc-800 bg-[#080c14]/95 backdrop-blur-sm">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-3 sm:px-6 lg:px-8">
          {/* Zone 1: Brand Identity */}
          <div className="flex items-center gap-4 sm:gap-6">
            <button
              onClick={() => {
                onNavigate('home');
                setMobileMenuOpen(false);
              }}
              className="flex items-center gap-2 sm:gap-2.5 text-left transition-opacity hover:opacity-90 min-h-[44px]"
            >
              <div className="flex h-7 w-7 items-center justify-center rounded border border-emerald-500/30 bg-emerald-950/40 text-emerald-400">
                <Layers className="h-4 w-4" />
              </div>
              <div className="flex items-baseline gap-1">
                <span className="text-base font-bold tracking-tight text-white">Predict</span>
                <span className="text-base font-bold tracking-tight text-emerald-400">Pro</span>
                <span className="hidden text-[10px] font-mono tracking-wider text-zinc-500 sm:inline ml-1 uppercase">
                  Analytics
                </span>
              </div>
            </button>

            {/* Zone 2: Primary Nav Links (Desktop & Tablet) */}
            <nav className="hidden md:flex items-center gap-1 text-xs">
              {navItems.map((item) => {
                const Icon = item.icon;
                const isActive = activePage === item.id || (item.id === 'home' && activePage === 'details');
                return (
                  <button
                    key={item.id}
                    onClick={() => onNavigate(item.id)}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                      isActive
                        ? 'bg-zinc-800 text-white font-semibold'
                        : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/40'
                    }`}
                  >
                    <Icon className={`h-3.5 w-3.5 ${isActive ? 'text-emerald-400' : 'text-zinc-500'}`} />
                    <span>{item.label}</span>
                    {item.badge && <span className="ml-1 flex items-center">{item.badge}</span>}
                  </button>
                );
              })}
            </nav>
          </div>

          {/* Zone 3: Telemetry & Model Quick-Access */}
          <div className="flex items-center gap-2 sm:gap-3">
            {/* Lagos Time indicator (Tablet & Desktop) */}
            <div className="hidden lg:flex items-center gap-1.5 text-xs font-mono text-zinc-400 tabular-nums border-r border-zinc-800 pr-3">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              <span>{lagosTime}</span>
            </div>

            {/* Active Model Indicator */}
            <button
              onClick={() => onNavigate('settings')}
              className="flex items-center gap-1.5 rounded border border-zinc-800 bg-[#0d131f] px-2 sm:px-2.5 py-1 text-xs font-mono text-zinc-300 hover:border-zinc-700 hover:text-white transition-colors min-h-[36px]"
              title="Active Prediction Engine Configuration"
            >
              <ShieldCheck className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
              <span className="hidden sm:inline text-zinc-500 text-[10px] uppercase">Engine:</span>
              <span className="font-semibold text-emerald-400 text-[11px] truncate max-w-[85px] sm:max-w-[130px] md:max-w-none">
                {activeModel}
              </span>
            </button>

            {/* Mobile Menu Button (Mobile viewports) */}
            <button
              type="button"
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
              className="md:hidden flex h-9 w-9 items-center justify-center rounded border border-zinc-800 bg-zinc-900 text-zinc-400 hover:text-white transition-colors"
              aria-label="Toggle navigation menu"
              aria-expanded={mobileMenuOpen}
            >
              {mobileMenuOpen ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
            </button>
          </div>
        </div>

        {/* Mobile Navigation Drawer Overlay */}
        {mobileMenuOpen && (
          <>
            <div
              className="fixed inset-0 top-14 z-30 bg-black/60 backdrop-blur-sm md:hidden"
              onClick={() => setMobileMenuOpen(false)}
            />
            <div className="relative z-40 md:hidden border-b border-zinc-800 bg-[#0b0f17] px-4 py-3 space-y-2 animate-in slide-in-from-top-2 duration-150 shadow-2xl">
              <div className="flex items-center justify-between text-xs font-mono text-zinc-400 pb-2 border-b border-zinc-800/80">
                <span>Africa/Lagos (WAT, UTC+1)</span>
                <span className="text-emerald-400 font-semibold">{lagosTime}</span>
              </div>
              <div className="space-y-1">
                {navItems.map((item) => {
                  const Icon = item.icon;
                  const isActive = activePage === item.id || (item.id === 'home' && activePage === 'details');
                  return (
                    <button
                      key={item.id}
                      onClick={() => {
                        onNavigate(item.id);
                        setMobileMenuOpen(false);
                      }}
                      className={`w-full flex items-center justify-between px-3 py-2.5 rounded-lg text-xs font-medium transition-colors min-h-[44px] ${
                        isActive
                          ? 'bg-zinc-800 text-white font-semibold'
                          : 'text-zinc-400 hover:text-white hover:bg-zinc-800/50'
                      }`}
                    >
                      <div className="flex items-center gap-2.5">
                        <Icon className={`h-4 w-4 ${isActive ? 'text-emerald-400' : 'text-zinc-400'}`} />
                        <span>{item.label}</span>
                      </div>
                      {item.badge}
                    </button>
                  );
                })}
              </div>
            </div>
          </>
        )}
      </header>

      {/* Mobile Fixed Bottom Navigation Bar (Thumb-Zone Ergonomics) */}
      <nav
        aria-label="Mobile Bottom Navigation"
        className="fixed bottom-0 left-0 right-0 z-40 md:hidden border-t border-zinc-800 bg-[#080c14]/95 backdrop-blur-md px-1 py-1.5 shadow-2xl"
      >
        <div className="grid grid-cols-5 items-center justify-around">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = activePage === item.id || (item.id === 'home' && activePage === 'details');
            return (
              <button
                key={item.id}
                onClick={() => onNavigate(item.id)}
                className={`flex flex-col items-center justify-center py-1 px-1 rounded transition-colors min-h-[48px] ${
                  isActive ? 'text-emerald-400 font-semibold' : 'text-zinc-400 hover:text-zinc-200'
                }`}
              >
                <div className="relative">
                  <Icon className={`h-5 w-5 ${isActive ? 'text-emerald-400' : 'text-zinc-400'}`} />
                  {item.id === 'diagnostics' && (
                    <span
                      className={`absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full ${
                        neonStatus === 'connected'
                          ? 'bg-emerald-400 ring-2 ring-[#080c14]'
                          : neonStatus === 'degraded'
                          ? 'bg-amber-400 ring-2 ring-[#080c14]'
                          : 'bg-red-400 ring-2 ring-[#080c14]'
                      }`}
                    />
                  )}
                </div>
                <span className="text-[10px] tracking-tight mt-0.5 leading-none truncate max-w-[62px]">
                  {item.shortLabel}
                </span>
              </button>
            );
          })}
        </div>
      </nav>
    </>
  );
};
