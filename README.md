# PredictPro 🏆⚡

> **Next-Generation Multi-Sport Quantitative Modeling, Validated Probability Calibration & Predictive Intelligence Platform**

[![Vite](https://img.shields.io/badge/Vite-6.0-646CFF?logo=vite&logoColor=white)](https://vitejs.dev/)
[![React](https://img.shields.io/badge/React-19.0-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.7-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![DuckDB](https://img.shields.io/badge/DuckDB-OLAP-FFF000?logo=duckdb&logoColor=black)](https://duckdb.org/)
[![Neon PostgreSQL](https://img.shields.io/badge/Neon-Serverless_PostgreSQL-00E599?logo=postgresql&logoColor=white)](https://neon.tech/)
[![Tailwind CSS](https://img.shields.io/badge/TailwindCSS-v4-06B6D4?logo=tailwindcss&logoColor=white)](https://tailwindcss.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## 📌 GitHub Repository Details

### Recommended GitHub Description
```text
Production-grade multi-sport quantitative modeling platform featuring dynamic ELO, Poisson distributions, strict probability calibration (ECE/Brier), sport-balanced Top-20 ranking, and a dual DuckDB-OLAP / Neon-PostgreSQL architecture.
```

### Recommended GitHub Topics / Tags
`sports-analytics` • `sports-betting-model` • `machine-learning` • `elo-rating` • `poisson-distribution` • `dixon-coles` • `probability-calibration` • `brier-score` • `duckdb` • `neon-postgres` • `react` • `typescript` • `expected-value` • `quantitative-finance`

---

## 📖 Table of Contents
- [Executive Overview](#-executive-overview)
- [System Architecture](#-system-architecture)
- [Key Engineering Pillars](#-key-engineering-pillars)
  - [1. Real Historical Fit & Zero-Synthetic Calibration](#1-real-historical-fit--zero-synthetic-calibration)
  - [2. Fail-Closed Prediction Pipeline](#2-fail-closed-prediction-pipeline)
  - [3. Sport-Balanced Top-20 Ranking](#3-sport-balanced-top-20-ranking)
  - [4. Dual Database Architecture: DuckDB OLAP + Neon PostgreSQL](#4-dual-database-architecture-duckdb-olap--neon-postgresql)
- [Sophisticated Improvements from Benchmark Repositories](#-sophisticated-improvements-from-benchmark-repositories)
  - [Survey of Benchmark Open-Source Projects](#survey-of-benchmark-open-source-projects)
  - [Architectural Upgrades Roadmap](#architectural-upgrades-roadmap)
- [Technology Stack](#-technology-stack)
- [Project Directory Structure](#-project-directory-structure)
- [Getting Started & Installation](#-getting-started--installation)
- [Testing & Quality Assurance](#-testing--quality-assurance)
- [License](#-license)

---

## 🚀 Executive Overview

**PredictPro** is a mission-critical sports forecasting and quantitative analytics system designed to replace naive heuristic models with statistically rigorous, calibrated probabilities across five major sports: **Football (Soccer)**, **Basketball (NBA/EuroLeague)**, **Baseball (MLB)**, **Hockey (NHL)**, and **Formula 1**.

Unlike typical prediction repos that publish uncalibrated raw model scores or use hardcoded placeholder probabilities, PredictPro enforces an uncompromising **fail-closed statistical pipeline**:
- Predictions require a minimum of 5 verified historical fixtures per contestant.
- Raw engine scores (ELO, Poisson, F1 rating algorithms) must undergo **strictly validated historical probability calibration** (Isotonic Regression or Platt Scaling with Brier and Expected Calibration Error [ECE] thresholds).
- If validated calibration curves are missing, expired, or unverified, the pipeline **abstains** (`status = "ABSTAINED"`), guaranteeing that zero synthetic or uncalibrated picks reach subscriber feeds.

---

## 🏗 System Architecture

```mermaid
flowchart TD
    subgraph DataIngestion[Data Ingestion & Historical Store]
        DataFeeds[Live Sports Providers / APIs] --> ProviderAdapter[Provider Normalization & Team Resolution]
        ProviderAdapter --> ParquetStore[(Cloudflare R2 / Parquet Archives)]
        ParquetStore --> DuckDBEngine[(Persistent DuckDB OLAP Engine)]
    end

    subgraph ModelingEngine[Statistical Modeling Engines]
        DuckDBEngine --> |Query Historical Matches >= 5| EloEngine[Dynamic ELO Engine]
        DuckDBEngine --> |Goal/Point Distribution| PoissonEngine[Poisson Scoring Engine]
        DuckDBEngine --> |Telemetry/Grid Positions| F1Engine[Formula 1 Rating & Prob Engine]
    end

    subgraph CalibrationPipeline[Calibration & Abstention Gate]
        EloEngine --> RawProb[Raw Model Probability]
        PoissonEngine --> RawProb
        F1Engine --> RawProb
        
        RawProb --> CalCheck{Validated Production<br/>Calibration Available?}
        CalCheck -->|No / Expired| AbstainGate[Mark ABSTAINED<br/>Reason: NO_VALIDATED_PRODUCTION_CALIBRATION<br/>Publishable = False]
        CalCheck -->|Yes| CalEngine[Isotonic / Platt Calibration]
        
        CalEngine --> ConfCalc[Confidence Score & Quality Gate]
        ConfCalc --> ValidationGate{Meets ECE / Brier /<br/>Confidence Thresholds?}
        ValidationGate -->|No| AbstainGate
        ValidationGate -->|Yes| ValidatedCandidate[Validated Prediction Candidate]
    end

    subgraph SelectionAndServing[Sport-Aware Ranking & Serving]
        ValidatedCandidate --> SportRanker[Sport-Balanced Best-Of-Day Selector<br/>Round-Robin Representation + Top-20 Cap]
        SportRanker --> NeonDB[(Neon Serverless PostgreSQL<br/>Operational Cache & Feed Store)]
        NeonDB --> RedisLayer[(Redis Hot Cache)]
        RedisLayer --> APIRoutes[Express API & Middleware Proxy]
        APIRoutes --> ReactUI[Vite + React 19 Frontend<br/>Interactive Multi-Sport Dashboard]
    end
```

---

## 🔑 Key Engineering Pillars

### 1. Real Historical Fit & Zero-Synthetic Calibration
- **No Synthetic Startup Seeding**: Hard-coded baseline calibrations (`approved_by="system_initialization_seed"`, synthetic Brier=0.1950, ECE=0.0450) have been permanently removed.
- **No Identity-Curve Fallback**: The pipeline rejects naive passthrough curves (`thresholds_x = [0..1]`, `thresholds_y = [0..1]`). If no genuine empirical calibration is fitted on out-of-fold historical outcomes, `CalibrationUnavailableError` is raised.
- **Per-Sport Model Independence**: Football, Basketball, Baseball, Hockey, and F1 each run their dedicated production model specifications and independent empirical calibration curves.

### 2. Fail-Closed Prediction Pipeline
A fixture candidate passes through sequential validation gates before publication:
```
Raw Model Probability 
  → Validated Production Calibration 
  → Empirical Confidence Metric 
  → Multi-Sport Abstention Checks 
  → Publication Gate 
  → Feed
```
When validation fails, candidates are tagged with structured rejection telemetry:
- `PROVIDER_NO_FIXTURE`
- `TEAM_NOT_MATCHED`
- `INSUFFICIENT_HISTORY` (Strict requirement of $\ge 5$ historical matches per team)
- `ENGINE_FAILURE`
- `CALIBRATION_UNAVAILABLE`
- `LOW_CONFIDENCE`
- `ABSTAINED`
- `NOT_PUBLISHABLE`

### 3. Sport-Balanced Top-20 Ranking
To prevent high-volume leagues (e.g. NHL hockey or MLB baseball) from crowding out other active sports:
1. **Guaranteed Sport Representation**: Every active sport with at least one genuinely validated prediction receives a guaranteed top slot in the daily board.
2. **Remaining Slots by Confidence**: Remaining slots up to the 20-fixture ceiling are populated strictly by calibrated percentage strength.
3. **Zero Fabrication**: If a sport has no validated candidates (e.g., offseason or insufficient team match history), **no placeholder or synthetic candidates are ever created**.

### 4. Dual Database Architecture: DuckDB OLAP + Neon PostgreSQL
- **DuckDB + Parquet (Analytical Store)**:
  - Backed by persistent storage (`predictpro_persistent.duckdb`) and Parquet historical partitions.
  - Handles all high-throughput historical queries, team form calculations, rating regressions, and calibration training without consuming transactional database connections.
  - Zero-dependency fallback ensures seamless execution in containerized environments.
- **Neon PostgreSQL (Operational Store)**:
  - Dedicated exclusively to live operational data: active fixture feeds, subscriber views, real-time match statuses, and account configurations.
  - Heavy OLAP queries are barred from Neon, protecting database compute budgets and preventing connection pool exhaustion.

---

## 🔬 Sophisticated Improvements from Benchmark Repositories

To benchmark PredictPro against the most sophisticated quantitative sports modeling systems in the open-source community, we analyzed top-performing repositories across GitHub:

### Survey of Benchmark Open-Source Projects

| Repository | Stars | Core Specialization | Architectural Strengths to Adopt |
| :--- | :---: | :--- | :--- |
| [**kyleskom/NBA-Machine-Learning-Sports-Betting**](https://github.com/kyleskom/NBA-Machine-Learning-Sports-Betting) | 1.7k⭐ | NBA Win/Spread/Totals modeling using XGBoost & Deep Neural Nets | • Automated line scraping from Pinnacle & DraftKings<br/>• Explicit **Expected Value (+EV)** calculations<br/>• Fractional **Kelly Criterion** stake sizing<br/>• Real-time Discord/Telegram alert webhooks |
| [**martineastwood/penaltyblog**](https://github.com/martineastwood/penaltyblog) | 230⭐ | High-performance Python football modeling suite | • **Dixon-Coles** bivariate Poisson with $\rho$-adjustment<br/>• Low-score dependence corrections (0-0, 1-0, 0-1, 1-1)<br/>• Implied probability de-biasing (Shin, Odds Ratio)<br/>• Brier score, RPS (Ranked Probability Score) & Log-Loss |
| [**ScottfreeLLC/AlphaPy**](https://github.com/ScottfreeLLC/AlphaPy) | 880⭐ | Quant algorithmic trading & sports framework (*SportFlow*) | • Out-of-fold cross-validation pipelines<br/>• Systematic feature-store architecture<br/>• Backtesting engine with drawdowns and Sharpe ratio |
| [**gmalbert/tennis-predictions**](https://github.com/gmalbert/tennis-predictions) | 120⭐ | Surface-adjusted ATP/WTA ELO & market edge tracking | • Surface-specific rating decay constants<br/>• **Closing Line Value (CLV)** tracking against Pinnacle closing prices<br/>• Probability calibration curves plotted over rolling windows |
| [**erikbohne/bettingAI**](https://github.com/erikbohne/bettingAI) | 150⭐ | Automated value-betting bot & hyperparameter tuning | • Real-time odds comparison against exchange order books<br/>• Bankroll risk management algorithms |

---

### Architectural Upgrades Roadmap

Based on this comparative analysis, the following enhancements represent immediate improvements for PredictPro:

```text
┌──────────────────────────────────────────────────────────────────────────────────┐
│                    PredictPro Advanced Enhancement Roadmap                       │
├───────────────────────────────┬──────────────────────────────────────────────────┤
│ 1. Dixon-Coles Bivariate      │ Replace independent Poisson with bivariate       │
│    Poisson Engine             │ distribution modeling goal correlation (rho)     │
├───────────────────────────────┼──────────────────────────────────────────────────┤
│ 2. Expected Goals (xG)        │ Ingest shot-quality xG and non-shot xG rather    │
│    Integration                │ than noisy raw scorelines for low-scoring sports  │
├───────────────────────────────┼──────────────────────────────────────────────────┤
│ 3. Closing Line Value (CLV)   │ Track model probability against Pinnacle/Betfair │
│    Benchmarking               │ closing lines to measure true predictive edge    │
├───────────────────────────────┼──────────────────────────────────────────────────┤
│ 4. Fractional Kelly Criterion │ Compute mathematical +EV and recommend optimal   │
│    & Value Sizing             │ bankroll fraction (quarter/half Kelly)           │
├───────────────────────────────┼──────────────────────────────────────────────────┤
│ 5. Conformal Prediction       │ Output guaranteed finite-sample coverage bands   │
│    Intervals                  │ instead of isolated point probabilities          │
├───────────────────────────────┼──────────────────────────────────────────────────┤
│ 6. Automated Bookmaker Odds   │ Compare model probabilities with live market     │
│    Scraper & Push Alerts      │ odds to automatically flag market inefficiencies │
└───────────────────────────────┴──────────────────────────────────────────────────┘
```

#### Detailed Breakdown of Upgrades:

#### 1. Dixon-Coles Bivariate Goal Dependency Correction
*Current:* Independent Poisson $P(X=x, Y=y) = \frac{\lambda^x e^{-\lambda}}{x!} \cdot \frac{\mu^y e^{-\mu}}{y!}$.  
*Improvement:* In football and hockey, low scores (0-0, 1-0, 0-1, 1-1) exhibit significant mutual correlation. Adopting the Dixon-Coles parameter $\rho$ introduces a bivariate adjustment factor $\tau_{\lambda, \mu}(x, y)$:
$$\tau_{\lambda, \mu}(0, 0) = 1 - \lambda \mu \rho$$
$$\tau_{\lambda, \mu}(1, 0) = 1 + \mu \rho$$
$$\tau_{\lambda, \mu}(0, 1) = 1 + \lambda \rho$$
$$\tau_{\lambda, \mu}(1, 1) = 1 - \rho$$
This eliminates systemic Poisson over-prediction of draws and 0-0 results.

#### 2. Expected Value (+EV) & Fractional Kelly Sizing
*Current:* Outputs calibrated winning percentage.  
*Improvement:* When live market odds $O$ are available, calculate Expected Value:
$$\text{EV} = (p_{\text{calibrated}} \times O) - 1$$
If $\text{EV} > \text{threshold}$, calculate optimal fractional Kelly stake:
$$f^* = c \cdot \frac{p \cdot O - 1}{O - 1} \quad (c = 0.25 \text{ or } 0.50)$$
This guarantees bankroll growth while avoiding catastrophic drawdown risk.

#### 3. Closing Line Value (CLV) Benchmarking
*Improvement:* In efficient betting markets, the sharp closing line (e.g. Pinnacle) represents the consensus market probability after all information is absorbed. Logging our pre-match model probabilities against closing prices provides an empirical validation benchmark independent of match variance.

---

## 💻 Technology Stack

### Frontend Application
- **Framework**: React 19 + TypeScript (Vite 6 build system)
- **Styling**: Tailwind CSS v4 + Lucide React Icons
- **Key Components**:
  - `HomeView`: Unified dashboard featuring sport filters, quick-date carousel, and top picks.
  - `PredictionCalendar`: Interactive date navigation with popover overflow protection.
  - `PredictionCard`: Dynamic fixture view showing win probabilities, confidence meters, team forms, and validation telemetry.
  - `CalibrationTelemetry`: Diagnostics panel displaying ECE, Brier scores, and abstention reasons.

### Backend Infrastructure
- **Server**: Node.js + Express with Vite SSR/development middlewares (`server.ts`)
- **Python Prediction Engine**: Python 3.10+
- **OLAP Engine**: DuckDB + Parquet analytical cache
- **Operational Database**: Neon PostgreSQL (Serverless connection-pooled)
- **Cache Layer**: Redis in-memory cache with graceful fallback
- **Statistical Libraries**: NumPy, SciPy (Optimization & Solvers), Scikit-Learn (Isotonic Regression)

---

## 📁 Project Directory Structure

```text
predictpro/
├── backend/
│   ├── app.py                         # FastAPI / Core backend entrypoint
│   ├── config.py                      # System settings & environment variables
│   ├── db/
│   │   ├── database_router.py         # Dual-DB query router (DuckDB vs Neon)
│   │   ├── duckdb_engine.py           # Persistent DuckDB OLAP engine & SQLite fallback
│   │   ├── neon_adapter.py            # Bounded Neon PostgreSQL operational adapter
│   │   └── neon_budget_guard.py       # Query complexity & budget protection guard
│   ├── engine/
│   │   ├── calibration.py             # Isotonic & Platt probability calibration (No identity fallback)
│   │   ├── pipeline.py                # Fail-closed prediction execution pipeline
│   │   ├── ranking.py                 # Sport-balanced Top-20 ranking & best-of-day selection
│   │   ├── validation_and_abstention.py # Quality thresholds & abstention reasons
│   │   ├── football_elo_engine.py     # Football dynamic ELO engine
│   │   ├── football_poisson_engine.py # Football Poisson scoring engine
│   │   ├── basketball_elo_engine.py   # Basketball ELO engine with margin-of-victory
│   │   ├── baseball_elo_engine.py     # Baseball ELO engine
│   │   ├── hockey_elo_engine.py       # Hockey ELO engine
│   │   └── formula1_engine.py         # Formula 1 rating & grid simulation engine
│   ├── services/
│   │   ├── feed_service.py            # Subscriber feed generation & caching
│   │   └── sync_service.py            # Fixture ingestion & data synchronization
│   └── tests/                         # Comprehensive unit & integration test suites
├── parquet_cache/                     # Partitioned Parquet archives for historical data
├── src/                               # React 19 Frontend application
│   ├── components/
│   │   ├── common/                    # Calendar, badges, cards, navigation
│   │   └── home/                      # Dashboard views, telemetry modals, filters
│   ├── hooks/                         # Custom React hooks
│   ├── types/                         # TypeScript interfaces and contracts
│   └── App.tsx                        # Root React component
├── server.ts                          # Express full-stack server & Neon proxy
├── metadata.json                      # AI Studio applet metadata
└── package.json                       # Dependencies & scripts
```

---

## 🛠 Getting Started & Installation

### Prerequisites
- **Node.js**: v18.0.0 or higher
- **Python**: v3.10 or higher
- **Package Manager**: npm or bun

### 1. Clone & Install Dependencies
```bash
# Clone the repository
git clone https://github.com/your-username/predictpro.git
cd predictpro

# Install Node dependencies
npm install

# (Optional) Install Python packages
pip install -r requirements.txt
```

### 2. Environment Configuration
Copy `.env.example` to `.env` and configure your credentials:
```bash
cp .env.example .env
```

Key environment variables:
```ini
PORT=3000
NODE_ENV=development

# Operational Database
NEON_DATABASE_URL=postgresql://user:password@ep-cool-project.region.neon.tech/predictpro?sslmode=require

# Analytical Database
DUCKDB_PATH=/app/backend/db/predictpro_persistent.duckdb
DUCKDB_PARQUET_PATH=parquet_cache

# Cache (Optional)
REDIS_URL=redis://localhost:6379
```

### 3. Launch Development Server
```bash
npm run dev
```
Open [http://localhost:3000](http://localhost:3000) in your browser.

---

## 🧪 Testing & Quality Assurance

The codebase includes an extensive suite of automated tests verifying:
- **No Synthetic Calibrations**: Asserts that uncalibrated or placeholder calibrations are rejected.
- **Fail-Closed Pipeline**: Verifies that fixtures with unvalidated calibration fail closed and mark the candidate as `ABSTAINED`.
- **DuckDB Persistence**: Validates that historical match queries read exclusively from DuckDB/Parquet without querying Neon.
- **Sport-Balanced Top-20**: Proves that no single sport saturates the feed, representation is preserved, and total daily candidates never exceed 20.
- **Minimum 5 Historical Matches**: Asserts that teams with fewer than 5 historical records are rejected with `INSUFFICIENT_HISTORY`.

To run the test suites:
```bash
# Run unit tests with Python
python3 -m unittest discover -s backend/tests -v

# Or run specific test modules
python3 -m unittest backend/tests/test_strict_database_rules.py -v
python3 -m unittest backend/tests/test_prediction_pipeline_audit.py -v
```

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
