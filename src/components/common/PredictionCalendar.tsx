import React, { useState } from 'react';
import {
  getLagosTodayYmd,
  isDateInPastLagos,
  formatLagosDateDisplay,
} from '../../utils/timezone';
import { Calendar as CalendarIcon, ChevronLeft, ChevronRight } from 'lucide-react';

interface PredictionCalendarProps {
  selectedDate: string; // YYYY-MM-DD in Lagos
  onSelectDate: (dateYmd: string) => void;
  availableDates?: string[]; // Dates with published predictions (YYYY-MM-DD)
  disabled?: boolean;
}

export const PredictionCalendar: React.FC<PredictionCalendarProps> = ({
  selectedDate,
  onSelectDate,
  availableDates = [],
  disabled = false,
}) => {
  const todayYmd = getLagosTodayYmd();
  const [isOpen, setIsOpen] = useState(false);

  // Initialize viewing year/month from selectedDate or today
  const initialDateParts = (selectedDate || todayYmd).split('-').map(Number);
  const [viewYear, setViewYear] = useState<number>(initialDateParts[0] || 2026);
  const [viewMonth, setViewMonth] = useState<number>((initialDateParts[1] || 9) - 1); // 0-indexed

  // Today parts in Lagos
  const todayParts = todayYmd.split('-').map(Number);
  const currentLagosYear = todayParts[0];
  const currentLagosMonth = todayParts[1] - 1;

  // Month navigation: prevent navigating to months entirely in the past
  const isPreviousMonthDisabled =
    viewYear < currentLagosYear ||
    (viewYear === currentLagosYear && viewMonth <= currentLagosMonth);

  const handlePrevMonth = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (isPreviousMonthDisabled) return;
    if (viewMonth === 0) {
      setViewMonth(11);
      setViewYear(viewYear - 1);
    } else {
      setViewMonth(viewMonth - 1);
    }
  };

  const handleNextMonth = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (viewMonth === 11) {
      setViewMonth(0);
      setViewYear(viewYear + 1);
    } else {
      setViewMonth(viewMonth + 1);
    }
  };

  const handleJumpToToday = (e: React.MouseEvent) => {
    e.stopPropagation();
    setViewYear(currentLagosYear);
    setViewMonth(currentLagosMonth);
    onSelectDate(todayYmd);
    setIsOpen(false);
  };

  // Build calendar matrix for viewYear, viewMonth
  const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
  const firstDayOfWeek = new Date(viewYear, viewMonth, 1).getDay(); // 0 = Sunday

  // Month title
  const monthName = new Intl.DateTimeFormat('en-GB', {
    month: 'long',
    year: 'numeric',
  }).format(new Date(viewYear, viewMonth, 1));

  // Quick helper to format day into YYYY-MM-DD
  const formatYmd = (day: number): string => {
    const mm = String(viewMonth + 1).padStart(2, '0');
    const dd = String(day).padStart(2, '0');
    return `${viewYear}-${mm}-${dd}`;
  };

  const daysArray = Array.from({ length: daysInMonth }, (_, i) => i + 1);
  const paddingArray = Array.from({ length: firstDayOfWeek }, (_, i) => i);

  // Predictions set for fast lookup
  const publishedDateSet = new Set(availableDates);

  return (
    <div className="relative inline-block text-left">
      {/* Calendar Toggle Button */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={disabled}
          onClick={() => setIsOpen(!isOpen)}
          className={`flex items-center gap-2 rounded border px-3 py-1.5 text-xs font-medium transition-colors ${
            isOpen
              ? 'border-emerald-500/80 bg-[#111927] text-white ring-1 ring-emerald-500/30'
              : 'border-zinc-700 bg-zinc-800/80 text-zinc-200 hover:border-zinc-600 hover:bg-zinc-700 hover:text-white'
          }`}
          aria-haspopup="dialog"
          aria-expanded={isOpen}
        >
          <CalendarIcon className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
          <span className="font-semibold text-zinc-100">
            {formatLagosDateDisplay(selectedDate)}
          </span>
          <span className="font-mono text-[10px] text-zinc-400 uppercase tracking-wider">
            (WAT)
          </span>
          <ChevronRight
            className={`h-3 w-3 text-zinc-400 transition-transform ${isOpen ? 'rotate-90' : ''}`}
          />
        </button>

        {/* Quick Today Button if looking at a future date */}
        {selectedDate !== todayYmd && (
          <button
            type="button"
            onClick={handleJumpToToday}
            className="rounded border border-emerald-500/30 bg-emerald-950/40 px-2 py-1 text-[11px] font-semibold text-emerald-300 transition-colors hover:bg-emerald-900/60 hover:text-emerald-200"
            title="Jump to Today in Lagos"
          >
            Today
          </button>
        )}
      </div>

      {/* Calendar Dropdown Modal / Popover */}
      {isOpen && (
        <>
          {/* Backdrop dismiss */}
          <div
            className="fixed inset-0 z-30"
            onClick={() => setIsOpen(false)}
          />

          <div className="absolute left-1/2 -translate-x-1/2 sm:left-auto sm:right-0 sm:translate-x-0 z-40 mt-2 w-[calc(100vw-2rem)] sm:w-80 max-w-xs sm:max-w-none rounded-lg border border-zinc-700 bg-[#0d131f] p-3.5 shadow-2xl animate-in fade-in zoom-in-95 duration-150">
            {/* Header: Month / Year Navigation */}
            <div className="flex items-center justify-between border-b border-zinc-800 pb-2.5">
              <button
                type="button"
                onClick={handlePrevMonth}
                disabled={isPreviousMonthDisabled}
                className={`rounded p-1 text-zinc-400 transition-colors ${
                  isPreviousMonthDisabled
                    ? 'cursor-not-allowed opacity-30'
                    : 'hover:bg-zinc-800 hover:text-white'
                }`}
                title="Previous Month"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>

              <div className="text-center">
                <div className="text-xs font-bold text-zinc-100">{monthName}</div>
                <div className="text-[10px] text-zinc-500 font-mono">
                  Africa/Lagos (UTC+1)
                </div>
              </div>

              <button
                type="button"
                onClick={handleNextMonth}
                className="rounded p-1 text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-white"
                title="Next Month"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>

            {/* Day of Week Headers */}
            <div className="mt-2.5 grid grid-cols-7 gap-1 text-center font-mono text-[10px] font-semibold text-zinc-500 uppercase">
              <span>Su</span>
              <span>Mo</span>
              <span>Tu</span>
              <span>We</span>
              <span>Th</span>
              <span>Fr</span>
              <span>Sa</span>
            </div>

            {/* Days Grid */}
            <div className="mt-1 grid grid-cols-7 gap-1">
              {/* Leading blanks for alignment */}
              {paddingArray.map((_, idx) => (
                <div key={`pad-${idx}`} className="h-7" />
              ))}

              {/* Month Days */}
              {daysArray.map((day) => {
                const dateYmd = formatYmd(day);
                const isPast = isDateInPastLagos(dateYmd);
                const isSelected = dateYmd === selectedDate;
                const isToday = dateYmd === todayYmd;
                const hasPredictions = publishedDateSet.has(dateYmd);

                return (
                  <button
                    key={dateYmd}
                    type="button"
                    disabled={isPast || disabled}
                    onClick={() => {
                      if (isPast) return;
                      onSelectDate(dateYmd);
                      setIsOpen(false);
                    }}
                    className={`relative flex h-7 w-7 mx-auto items-center justify-center rounded text-xs font-mono transition-colors ${
                      isSelected
                        ? 'bg-emerald-500 font-bold text-slate-950'
                        : isPast
                        ? 'cursor-not-allowed text-zinc-600 opacity-40 line-through'
                        : isToday
                        ? 'border border-emerald-500/50 bg-emerald-950/40 text-emerald-300 font-bold'
                        : hasPredictions
                        ? 'bg-zinc-800/90 text-zinc-100 font-semibold hover:bg-zinc-700 hover:text-emerald-300'
                        : 'text-zinc-300 hover:bg-zinc-800 hover:text-white'
                    }`}
                  >
                    <span>{day}</span>

                    {/* Indicator Dot for Dates with Published Predictions */}
                    {hasPredictions && !isSelected && (
                      <span className="absolute bottom-0.5 left-1/2 h-1 w-1 -translate-x-1/2 rounded-full bg-emerald-400" />
                    )}
                  </button>
                );
              })}
            </div>

            {/* Footer Legend & Jump Actions */}
            <div className="mt-3 flex items-center justify-between border-t border-zinc-800 pt-2 text-[10px] text-zinc-400">
              <div className="flex items-center gap-1.5">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                <span>Signals available</span>
              </div>

              <button
                type="button"
                onClick={handleJumpToToday}
                className="font-semibold text-emerald-400 hover:text-emerald-300"
              >
                Today ({todayYmd.slice(5)})
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
};
