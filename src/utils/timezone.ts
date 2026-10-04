/**
 * Timezone utilities configured strictly for Africa/Lagos (WAT, UTC+1)
 */

export const LAGOS_TIMEZONE = 'Africa/Lagos';

/**
 * Returns today's date in Africa/Lagos timezone as YYYY-MM-DD
 */
export function getLagosTodayYmd(): string {
  try {
    const now = new Date();
    const lagosFormatter = new Intl.DateTimeFormat('en-CA', {
      timeZone: LAGOS_TIMEZONE,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    });
    return lagosFormatter.format(now);
  } catch {
    const d = new Date();
    return d.toISOString().split('T')[0];
  }
}

/**
 * Converts any UTC ISO timestamp to YYYY-MM-DD in Africa/Lagos timezone
 */
export function getLagosDateYmd(isoString: string): string {
  try {
    const date = new Date(isoString);
    const lagosFormatter = new Intl.DateTimeFormat('en-CA', {
      timeZone: LAGOS_TIMEZONE,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    });
    return lagosFormatter.format(date);
  } catch {
    return isoString.slice(0, 10);
  }
}

/**
 * Checks if a given YYYY-MM-DD date is strictly in the past relative to today in Africa/Lagos
 */
export function isDateInPastLagos(ymdString: string): boolean {
  try {
    const todayYmd = getLagosTodayYmd();
    return ymdString < todayYmd;
  } catch {
    return false;
  }
}

/**
 * Formats a YYYY-MM-DD string into a human-readable title (e.g. "Today, 23 Sep 2026", "Tomorrow", "Sat, 27 Sep")
 */
export function formatLagosDateDisplay(ymdString: string): string {
  try {
    const todayYmd = getLagosTodayYmd();
    const parts = ymdString.split('-').map(Number);
    if (parts.length !== 3) return ymdString;

    // Construct local midnight representation
    const date = new Date(parts[0], parts[1] - 1, parts[2]);

    // Calculate tomorrow
    const now = new Date();
    const tomorrowDate = new Date(now.getTime() + 24 * 60 * 60 * 1000);
    const lagosFormatter = new Intl.DateTimeFormat('en-CA', {
      timeZone: LAGOS_TIMEZONE,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    });
    const tomorrowYmd = lagosFormatter.format(tomorrowDate);

    if (ymdString === todayYmd) {
      const monthDay = new Intl.DateTimeFormat('en-GB', {
        month: 'short',
        day: 'numeric',
      }).format(date);
      return `Today (${monthDay})`;
    }
    if (ymdString === tomorrowYmd) {
      const monthDay = new Intl.DateTimeFormat('en-GB', {
        month: 'short',
        day: 'numeric',
      }).format(date);
      return `Tomorrow (${monthDay})`;
    }

    return new Intl.DateTimeFormat('en-GB', {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    }).format(date);
  } catch {
    return ymdString;
  }
}

/**
 * Formats an ISO UTC date string to West Africa Time (WAT)
 */
export function formatLagosTime(isoString: string): string {
  try {
    const date = new Date(isoString);
    return (
      new Intl.DateTimeFormat('en-GB', {
        timeZone: LAGOS_TIMEZONE,
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).format(date) + ' WAT'
    );
  } catch {
    return 'TBD';
  }
}

/**
 * Formats date and time in Africa/Lagos for kickoff headers
 */
export function formatLagosKickoff(isoString: string): {
  displayDate: string;
  displayTime: string;
  isToday: boolean;
  isTomorrow: boolean;
} {
  try {
    const date = new Date(isoString);
    const now = new Date();

    const lagosFormatter = new Intl.DateTimeFormat('en-CA', {
      timeZone: LAGOS_TIMEZONE,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    });

    const targetYmd = lagosFormatter.format(date);
    const nowYmd = lagosFormatter.format(now);

    // Calculate tomorrow in Lagos
    const tomorrowDate = new Date(now.getTime() + 24 * 60 * 60 * 1000);
    const tomorrowYmd = lagosFormatter.format(tomorrowDate);

    const isToday = targetYmd === nowYmd;
    const isTomorrow = targetYmd === tomorrowYmd;

    const timeStr = new Intl.DateTimeFormat('en-GB', {
      timeZone: LAGOS_TIMEZONE,
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(date);

    let displayDate = '';
    if (isToday) {
      displayDate = 'Today';
    } else if (isTomorrow) {
      displayDate = 'Tomorrow';
    } else {
      displayDate = new Intl.DateTimeFormat('en-GB', {
        timeZone: LAGOS_TIMEZONE,
        weekday: 'short',
        day: 'numeric',
        month: 'short',
      }).format(date);
    }

    return {
      displayDate,
      displayTime: `${timeStr} WAT`,
      isToday,
      isTomorrow,
    };
  } catch {
    return {
      displayDate: 'Upcoming',
      displayTime: '--:-- WAT',
      isToday: false,
      isTomorrow: false,
    };
  }
}

/**
 * Verifies if fixture kickoff in Africa/Lagos is today or future.
 * Past completed fixtures that kicked off earlier in the day are allowed if today,
 * but fixtures older than today in Lagos are excluded.
 */
export function isTodayOrFutureInLagos(isoString: string): boolean {
  try {
    const date = new Date(isoString);
    const now = new Date();

    const lagosFormatter = new Intl.DateTimeFormat('en-CA', {
      timeZone: LAGOS_TIMEZONE,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    });

    const targetYmd = lagosFormatter.format(date);
    const nowYmd = lagosFormatter.format(now);

    return targetYmd >= nowYmd;
  } catch {
    return false;
  }
}
