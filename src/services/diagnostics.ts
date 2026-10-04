/**
 * PredictPro Deep Subsystem & Database Diagnostics Service
 *
 * Retrieves verbose connection error details and diagnostics from the backend
 * (/api/health/diagnostics) to pinpoint DNS, TCP, SSL/TLS, Driver, Auth, Schema,
 * or Budget Guard anomalies—specifically for Neon PostgreSQL and failover subsystems.
 */

export interface DnsDiagnostic {
  resolved: boolean;
  ips: string[];
  latencyMs: number;
  error?: string | null;
}

export interface TcpDiagnostic {
  reachable: boolean;
  latencyMs: number;
  error?: string | null;
}

export interface DriverDiagnostic {
  name: string;
  installed: boolean;
  version?: string | null;
  error?: string | null;
}

export interface QueryTestDiagnostic {
  executed: boolean;
  success: boolean;
  latencyMs: number;
  serverVersion?: string | null;
  error?: string | null;
  traceback?: string | null;
}

export interface NeonDiagnostics {
  configured: boolean;
  status: 'connected' | 'disconnected' | 'connecting' | 'degraded';
  connectionStringMasked?: string | null;
  host?: string | null;
  port?: number;
  database?: string | null;
  user?: string | null;
  sslMode?: string | null;
  dns?: DnsDiagnostic;
  tcp?: TcpDiagnostic;
  driver?: DriverDiagnostic;
  queryTest?: QueryTestDiagnostic;
  tablesFound?: string[];
  schemaCompatible?: boolean;
  failoverReady?: boolean;
  safetyState?: string;
  suggestedRemediation?: string | null;
  budgetGuardMetrics?: Record<string, any>;
}

export interface SubsystemHealthInfo {
  status: string;
  latencyMs?: number;
  error?: string | null;
  details?: string | null;
  [key: string]: any;
}

export interface HealthDiagnosticsResponse {
  status: 'healthy' | 'degraded' | 'unhealthy' | 'initializing';
  timestamp: string;
  service: string;
  diagnostics: {
    neon?: NeonDiagnostics;
    mongodb?: SubsystemHealthInfo;
    redis?: SubsystemHealthInfo;
    r2Storage?: SubsystemHealthInfo;
    duckDb?: SubsystemHealthInfo;
    sportsSkills?: SubsystemHealthInfo;
    databaseRouter?: Record<string, any>;
    replication?: Record<string, any>;
    [key: string]: any;
  };
  summary: {
    neonConnected: boolean;
    neonLatencyMs?: number;
    activePrimaryStore?: string;
    totalSubsystemsHealthy?: number;
    fastapiReady?: boolean;
    [key: string]: any;
  };
}

export interface DiagnosticLogEntry {
  timestamp: string;
  layer: 'CONFIGURATION' | 'DNS' | 'TCP_SOCKET' | 'DRIVER' | 'TLS_AUTH' | 'SQL_QUERY' | 'SCHEMA' | 'BUDGET_GUARD';
  level: 'INFO' | 'WARN' | 'ERROR' | 'SUCCESS';
  message: string;
  details?: any;
}

class DiagnosticsService {
  private lastReport: HealthDiagnosticsResponse | null = null;
  private isRunning = false;

  /**
   * Retrieves verbose diagnostics from the backend /api/health/diagnostics endpoint.
   */
  public async fetchHealthDiagnostics(): Promise<HealthDiagnosticsResponse> {
    try {
      const res = await fetch('/api/health/diagnostics', {
        headers: {
          Accept: 'application/json',
          'Cache-Control': 'no-cache',
        },
      });

      if (!res.ok) {
        throw new Error(`Diagnostics endpoint returned HTTP ${res.status}: ${res.statusText}`);
      }

      const data: HealthDiagnosticsResponse = await res.json();
      this.lastReport = data;
      return data;
    } catch (err: any) {
      console.warn('[Diagnostics] Failed to fetch backend diagnostics:', err.message);
      const fallback: HealthDiagnosticsResponse = {
        status: 'unhealthy',
        timestamp: new Date().toISOString(),
        service: 'predictpro-client',
        diagnostics: {
          neon: {
            configured: false,
            status: 'disconnected',
            suggestedRemediation: `Could not reach diagnostics endpoint: ${err.message}. Ensure backend is running.`,
          },
        },
        summary: {
          neonConnected: false,
          fastapiReady: false,
        },
      };
      this.lastReport = fallback;
      return fallback;
    }
  }

  /**
   * Deep health check logger:
   * Inspects and logs verbose, color-coded connection analysis to the developer console
   * to pinpoint why Neon or any subsystem is returning as disconnected.
   */
  public async logNeonConnectionDiagnostics(verbose = true): Promise<DiagnosticLogEntry[]> {
    if (this.isRunning) return [];
    this.isRunning = true;

    const entries: DiagnosticLogEntry[] = [];
    const timestamp = new Date().toISOString();

    const addEntry = (
      layer: DiagnosticLogEntry['layer'],
      level: DiagnosticLogEntry['level'],
      message: string,
      details?: any
    ) => {
      entries.push({ timestamp, layer, level, message, details });
    };

    try {
      const report = await this.fetchHealthDiagnostics();
      const neon = report.diagnostics?.neon;

      if (verbose) {
        console.group('%c[PredictPro Diagnostics] Deep Neon Connection Health Check', 'color: #38bdf8; font-weight: bold; font-size: 13px;');
        console.log(`%cTimestamp: %c${report.timestamp}`, 'font-weight: bold', 'color: #94a3b8');
        console.log(`%cOverall Health: %c${report.status.toUpperCase()}`, 'font-weight: bold', report.status === 'healthy' ? 'color: #4ade80' : 'color: #f87171');
      }

      // Layer 1: Configuration
      if (!neon || !neon.configured) {
        addEntry('CONFIGURATION', 'ERROR', 'NEON_DATABASE_URL is not configured in backend environment or /.env file.');
        if (verbose) {
          console.error('%c[Config Error]%c NEON_DATABASE_URL is missing in environment.', 'color: #ef4444; font-weight: bold;', 'color: inherit;');
          console.info('%c[Remediation]%c Add NEON_DATABASE_URL=postgresql://user:pass@ep-xyz.aws.neon.tech/neondb?sslmode=require to /.env', 'color: #38bdf8; font-weight: bold;', 'color: inherit;');
        }
      } else {
        addEntry('CONFIGURATION', 'SUCCESS', `Configured with masked URI: ${neon.connectionStringMasked || '***'}`);
        if (verbose) {
          console.log(`%c[Config OK]%c Host: ${neon.host || 'unknown'} | Port: ${neon.port || 5432} | DB: ${neon.database || 'default'} | User: ${neon.user || 'owner'}`, 'color: #4ade80; font-weight: bold;', 'color: inherit;');
        }
      }

      // Layer 2: DNS Resolution
      if (neon?.dns) {
        if (neon.dns.resolved) {
          addEntry('DNS', 'SUCCESS', `Host '${neon.host}' resolved to [${neon.dns.ips.join(', ')}] in ${neon.dns.latencyMs}ms`);
          if (verbose) {
            console.log(`%c[DNS OK]%c Resolved ${neon.host} -> ${neon.dns.ips.join(', ')} (${neon.dns.latencyMs}ms)`, 'color: #4ade80; font-weight: bold;', 'color: inherit;');
          }
        } else {
          addEntry('DNS', 'ERROR', `DNS resolution failed: ${neon.dns.error}`);
          if (verbose) {
            console.error(`%c[DNS Failure]%c Failed to resolve hostname: ${neon.dns.error}`, 'color: #ef4444; font-weight: bold;', 'color: inherit;');
          }
        }
      }

      // Layer 3: TCP Socket Reachability
      if (neon?.tcp) {
        if (neon.tcp.reachable) {
          addEntry('TCP_SOCKET', 'SUCCESS', `TCP port ${neon.port || 5432} reachable in ${neon.tcp.latencyMs}ms`);
          if (verbose) {
            console.log(`%c[TCP OK]%c Socket established with ${neon.host}:${neon.port || 5432} (${neon.tcp.latencyMs}ms)`, 'color: #4ade80; font-weight: bold;', 'color: inherit;');
          }
        } else {
          addEntry('TCP_SOCKET', 'ERROR', `TCP socket connection failed: ${neon.tcp.error}`);
          if (verbose) {
            console.error(`%c[TCP Failure]%c Socket connection failed: ${neon.tcp.error}`, 'color: #ef4444; font-weight: bold;', 'color: inherit;');
          }
        }
      }

      // Layer 4: Driver Availability
      if (neon?.driver) {
        if (neon.driver.installed) {
          addEntry('DRIVER', 'SUCCESS', `Driver '${neon.driver.name}' version ${neon.driver.version || 'installed'}`);
          if (verbose) {
            console.log(`%c[Driver OK]%c Python ${neon.driver.name} driver loaded (${neon.driver.version})`, 'color: #4ade80; font-weight: bold;', 'color: inherit;');
          }
        } else {
          addEntry('DRIVER', 'ERROR', `Driver '${neon.driver.name}' not available: ${neon.driver.error}`);
          if (verbose) {
            console.error(`%c[Driver Missing]%c Driver unavailable: ${neon.driver.error}`, 'color: #ef4444; font-weight: bold;', 'color: inherit;');
          }
        }
      }

      // Layer 5 & 6: SQL Ping & Server Connection
      if (neon?.queryTest) {
        if (neon.queryTest.success) {
          addEntry('SQL_QUERY', 'SUCCESS', `Query ping roundtrip completed in ${neon.queryTest.latencyMs}ms.`);
          if (verbose) {
            console.log(`%c[Query OK]%c Ping roundtrip latency: ${neon.queryTest.latencyMs}ms | Server: ${neon.queryTest.serverVersion || 'PostgreSQL'}`, 'color: #4ade80; font-weight: bold;', 'color: inherit;');
          }
        } else {
          addEntry('SQL_QUERY', 'ERROR', `Query ping failed: ${neon.queryTest.error}`, { traceback: neon.queryTest.traceback });
          if (verbose) {
            console.error(`%c[Query Failure]%c Error: ${neon.queryTest.error}`, 'color: #ef4444; font-weight: bold;', 'color: inherit;');
            if (neon.queryTest.traceback) {
              console.warn('%c[Traceback]%c\n' + neon.queryTest.traceback, 'color: #f59e0b; font-weight: bold;', 'color: inherit;');
            }
          }
        }
      }

      // Layer 7: Schema Compatibility
      if (neon?.schemaCompatible !== undefined) {
        if (neon.schemaCompatible) {
          addEntry('SCHEMA', 'SUCCESS', `All required tables present (${neon.tablesFound?.length || 0} tables: ${neon.tablesFound?.join(', ') || 'verified'}).`);
          if (verbose) {
            console.log(`%c[Schema OK]%c Tables: ${neon.tablesFound?.join(', ') || 'All 9 operational tables active'}`, 'color: #4ade80; font-weight: bold;', 'color: inherit;');
          }
        } else {
          addEntry('SCHEMA', 'WARN', 'Schema is not fully compatible or tables are missing.');
          if (verbose) {
            console.warn('%c[Schema Warning]%c Some operational tables were not detected.', 'color: #f59e0b; font-weight: bold;', 'color: inherit;');
          }
        }
      }

      // Layer 8: Budget Guard & Safety
      if (neon?.safetyState) {
        const isOptimal = neon.safetyState === 'OPTIMAL';
        addEntry('BUDGET_GUARD', isOptimal ? 'SUCCESS' : 'WARN', `Neon Safety State: ${neon.safetyState}`);
        if (verbose) {
          console.log(`%c[Budget Guard]%c Safety state: ${neon.safetyState} | Failover Ready: ${neon.failoverReady ? 'YES' : 'NO'}`, isOptimal ? 'color: #4ade80; font-weight: bold;' : 'color: #f59e0b; font-weight: bold;', 'color: inherit;');
        }
      }

      // Suggested Remediation
      if (neon?.suggestedRemediation && verbose) {
        console.info(`%c[Action Needed]%c ${neon.suggestedRemediation}`, 'color: #38bdf8; font-weight: bold; background: rgba(56, 189, 248, 0.1); padding: 2px 6px; border-radius: 4px;', 'color: inherit;');
      }

      if (verbose) {
        console.groupEnd();
      }

      return entries;
    } catch (err: any) {
      console.error('[Diagnostics Logger Error]:', err);
      addEntry('CONFIGURATION', 'ERROR', `Diagnostic run failed: ${err.message}`);
      return entries;
    } finally {
      this.isRunning = false;
    }
  }

  /**
   * Quick helper to get cached or fresh diagnostic summary
   */
  public async getQuickStatus(): Promise<{
    neonConnected: boolean;
    neonLatencyMs: number;
    failoverReady: boolean;
    status: string;
    details?: string;
  }> {
    const report = this.lastReport || (await this.fetchHealthDiagnostics());
    const neon = report.diagnostics?.neon;

    return {
      neonConnected: neon?.status === 'connected',
      neonLatencyMs: neon?.queryTest?.latencyMs || neon?.tcp?.latencyMs || 0,
      failoverReady: Boolean(neon?.failoverReady),
      status: neon?.status || 'disconnected',
      details: neon?.suggestedRemediation || (neon?.status === 'connected' ? 'Neon PostgreSQL operational' : 'Disconnected'),
    };
  }
}

export const diagnosticsService = new DiagnosticsService();
export default diagnosticsService;
