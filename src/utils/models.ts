import { ModelMetadata, PredictionModel } from '../types';

export const PREDICTION_MODELS: ModelMetadata[] = [
  {
    id: 'ELO',
    name: 'ELO Rating Engine',
    tier: 'production',
    description: 'Dynamic rating system calibrated on head-to-head encounter records, strength differential, and home advantage.',
    isProductionReady: true,
    version: 'v4.2-prod',
  },
  {
    id: 'POISSON',
    name: 'Poisson Distribution Engine',
    tier: 'production',
    description: 'Bivariate goal and point expectancy modeling attacking and defensive coefficients.',
    isProductionReady: true,
    version: 'v3.8-prod',
  },
  {
    id: 'ELO + POISSON',
    name: 'ELO + Poisson Hybrid Ensemble',
    tier: 'production',
    description: 'Dual-engine consensus blending relative rating disparities with discrete event distribution simulations.',
    isProductionReady: true,
    version: 'v5.1-prod',
  },
  {
    id: 'GLICKO2',
    name: 'Glicko-2 Uncertainty Model',
    tier: 'evaluation',
    description: 'Challenger framework incorporating rating deviation (RD) and rating volatility parameters.',
    isProductionReady: false,
    version: 'v1.4-eval',
  },
  {
    id: 'GRADIENT_BOOSTING',
    name: 'Gradient Boosting Regressor',
    tier: 'evaluation',
    description: 'Supervised multiclass Gradient Boosting trees fitted on rolling point-in-time form, goal differentials, and defensive records.',
    isProductionReady: false,
    version: 'v4.0-eval',
  },
  {
    id: 'GLICKO2 + GRADIENT_BOOSTING',
    name: 'Glicko-2 + Gradient Boosting Ensemble',
    tier: 'evaluation',
    description: 'Challenger hybrid pairing rating volatility scoring with non-linear feature boosting trees.',
    isProductionReady: false,
    version: 'v1.1-eval',
  },
  {
    id: 'ELO + POISSON + GLICKO2',
    name: 'Tri-Engine Consensus (ELO + Poisson + Glicko-2)',
    tier: 'evaluation',
    description: 'High-dimensional rating consensus synthesizing classical ratings, event probabilities, and variance spreads.',
    isProductionReady: false,
    version: 'v0.9-eval',
  },
  {
    id: 'ELO + POISSON + GLICKO2 + GRADIENT_BOOSTING',
    name: 'Full Quad-Ensemble Meta-Model',
    tier: 'evaluation',
    description: 'Comprehensive consensus pipeline combining all statistical engines and machine learning trees.',
    isProductionReady: false,
    version: 'v0.8-eval',
  },
];

export function getModelMetadata(modelId: PredictionModel): ModelMetadata {
  return (
    PREDICTION_MODELS.find((m) => m.id === modelId) || {
      id: modelId,
      name: modelId,
      tier: 'evaluation',
      description: 'Custom statistical prediction model',
      isProductionReady: false,
      version: 'v1.0',
    }
  );
}
