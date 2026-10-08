import { ValidatedFixture, ScoreState } from '../types';

/**
 * STRICT RULE: Only live and completed fixtures should display or have a score.
 * Upcoming matches must NEVER have any currentScore or finalScore.
 */
function parseScore(score: any): ScoreState | undefined {
  if (!score) return undefined;
  if (typeof score === 'string') {
    try {
      score = JSON.parse(score);
    } catch {
      return undefined;
    }
  }
  if (typeof score === 'object' && score !== null) {
    const h = typeof score.home === 'number' ? score.home : typeof score.home === 'string' && score.home !== '' ? Number(score.home) : null;
    const a = typeof score.away === 'number' ? score.away : typeof score.away === 'string' && score.away !== '' ? Number(score.away) : null;
    if (h !== null && !isNaN(h) && a !== null && !isNaN(a)) {
      return {
        home: h,
        away: a,
        display: score.display || `${h} - ${a}`,
        periodOrMinute: score.periodOrMinute || score.period || score.clock || score['period/clock'],
      };
    }
  }
  return undefined;
}

export function sanitizeSingleFixture<T extends ValidatedFixture>(f: T): T {
  if (!f || typeof f !== 'object') return f;

  const nowMs = Date.now();
  const kickoffMs = f.kickoffUtc ? new Date(f.kickoffUtc).getTime() : 0;
  const isFuture = kickoffMs > nowMs + 5 * 60 * 1000;
  const rawStatus = String(f.status || '').toLowerCase().trim();

  const isComp = rawStatus === 'completed' || rawStatus === 'finished' || rawStatus === 'ft' || rawStatus === 'ended' || rawStatus === 'final';
  const isLiveMatch = !isComp && !isFuture && (rawStatus === 'live' || rawStatus === 'in_progress' || rawStatus === 'halftime' || rawStatus === '1st_half' || rawStatus === '2nd_half');

  if (isComp) {
    const score = parseScore(f.finalScore) || parseScore(f.currentScore) || parseScore((f as any).current_score) || parseScore((f as any).final_score);
    return {
      ...f,
      status: 'completed' as const,
      currentScore: score,
      finalScore: score,
    };
  } else if (isLiveMatch) {
    const score = parseScore(f.currentScore) || parseScore((f as any).current_score) || parseScore(f.finalScore);
    return {
      ...f,
      status: 'live' as const,
      currentScore: score,
      finalScore: undefined,
    };
  } else {
    // Upcoming fixture: Strip any scores completely
    const sanitized: any = {
      ...f,
      status: 'upcoming' as const,
      currentScore: undefined,
      finalScore: undefined,
    };
    delete sanitized.current_score;
    delete sanitized.final_score;
    delete sanitized.home_score;
    delete sanitized.away_score;
    return sanitized as T;
  }
}

export function sanitizeFixtureScores<T extends ValidatedFixture>(fixtures: T[]): T[] {
  if (!Array.isArray(fixtures)) return [];
  return fixtures.map(sanitizeSingleFixture);
}
