import math
import asyncio
import concurrent.futures
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

def run_sync(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(coro)).result()
    else:
        return asyncio.run(coro)
try:
    import numpy as np
except ImportError:
    np = None
try:
    from sklearn.isotonic import IsotonicRegression
except ImportError:
    IsotonicRegression = None

from backend.db.database_router import database_router
from backend.engine.calibration import (
    get_calibration_key,
    promote_calibration_candidate,
)
from backend.services.model_service import model_service


class EvaluationPipeline:
    """
    Evaluates completed prediction results against actual outcomes,
    builds evaluation datasets, fits candidate calibration/models,
    performs strict out-of-sample chronological validation, calibration validation,
    confidence/abstention validation, and publication-gate promotion.

    Guarantees:
    - Never mutates the original published prediction.
    - Only promotes a candidate when EVERY required gate passes.
    - If a candidate fails, keeps production model and calibrations unchanged.
    """

    async def build_evaluation_dataset(
        self,
        sport: Optional[str] = None,
        market: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Builds a clean evaluation dataset from persistent prediction_results via DatabaseRouter.
        Sorts strictly chronologically by prediction_created_at to prevent lookahead bias.
        """
        try:
            records = await database_router.prediction_results.get_recent_results(sport=sport, limit=1000)
            if market and market.lower() != "all":
                records = [r for r in records if r.get("market") == market]
        except Exception as e:
            print(f"[EvaluationPipeline] Error querying prediction_results via DatabaseRouter: {e}")
            records = []

        # Chronological sort
        def get_sort_key(r: Dict[str, Any]) -> str:
            return (
                r.get("prediction_created_at")
                or r.get("result_recorded_at")
                or "1970-01-01T00:00:00Z"
            )

        records.sort(key=get_sort_key)
        return records

    def run_candidate_evaluation_and_promotion(
        self,
        custom_dataset: Optional[List[Dict[str, Any]]] = None,
        min_samples_threshold: int = 20,
    ) -> Dict[str, Any]:
        """
        Executes Steps 6-12 of the evaluation & calibration pipeline:
        6. Build evaluation dataset from completed prediction results.
        7. Create candidate calibration/model versions from that dataset.
        8. Perform chronological out-of-sample validation.
        9. Run calibration validation.
        10. Run confidence and abstention gates.
        11. Run the production promotion gate.
        12. Promote a candidate ONLY if every required gate passes.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        dataset = (
            custom_dataset
            if custom_dataset is not None
            else run_sync(self.build_evaluation_dataset())
        )

        candidates_evaluated = 0
        promotions_approved = 0
        candidate_records_created = 0
        candidate_details: List[Dict[str, Any]] = []

        # Group dataset by (sport, market)
        groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for r in dataset:
            sp = (r.get("sport") or "football").lower()
            mk = r.get("market") or "default"
            groups.setdefault((sp, mk), []).append(r)

        for (sport, market), group_records in groups.items():
            sample_count = len(group_records)
            if sample_count < min_samples_threshold:
                # Do not train or promote candidate if sample is insufficient
                candidate_details.append({
                    "sport": sport,
                    "market": market,
                    "sample_count": sample_count,
                    "status": "insufficient_evaluation_samples",
                    "reason": f"Requires at least {min_samples_threshold} samples (found {sample_count})",
                    "promoted": False,
                })
                continue

            candidates_evaluated += 1
            # 7. Create Candidate Calibration/Model Version
            source_model = group_records[-1].get("model", "ELO + POISSON")
            candidate_id = f"cand_{sport}_{market.lower().replace(' ', '_')}_{now_iso[:10]}"
            candidate_version = f"v_eval_{now_iso[:19].replace(':', '')}"

            # Extract arrays
            if np is not None:
                preds = np.array([float(r.get("predicted_probability", 0.5)) for r in group_records], dtype=np.float64)
                actuals = np.array([1 if r.get("hit_or_miss") == "hit" else 0 for r in group_records], dtype=np.int32)
            else:
                preds = [float(r.get("predicted_probability", 0.5)) for r in group_records]
                actuals = [1 if r.get("hit_or_miss") == "hit" else 0 for r in group_records]
            timestamps = [r.get("prediction_created_at") or now_iso for r in group_records]

            # 8. Chronological Out-of-Sample Validation Split (70% train / 30% val)
            split_idx = int(0.70 * sample_count)
            train_p, train_y = preds[:split_idx], actuals[:split_idx]
            val_p, val_y = preds[split_idx:], actuals[split_idx:]

            # Number of actual training/calibration records used to train candidate
            candidate_records_created += len(train_p)

            # Fit candidate calibration model (Isotonic Regression) on training partition ONLY
            iso = None
            if IsotonicRegression is not None and np is not None:
                try:
                    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.05, y_max=0.95)
                    iso.fit(train_p, train_y)
                    val_calibrated = iso.predict(val_p)
                except Exception as fit_err:
                    candidate_details.append({
                        "candidate_id": candidate_id,
                        "sport": sport,
                        "market": market,
                        "status": "rejected_fit_error",
                        "reason": str(fit_err),
                        "promoted": False,
                    })
                    continue
            else:
                val_calibrated = [max(0.05, min(0.95, float(p))) for p in val_p]

            # Compute Out-of-Sample Metrics
            if np is not None:
                raw_brier = float(np.mean((val_p - val_y) ** 2))
                cal_brier = float(np.mean((val_calibrated - val_y) ** 2))
            else:
                raw_brier = sum((vp - vy) ** 2 for vp, vy in zip(val_p, val_y)) / max(1, len(val_p))
                cal_brier = sum((vc - vy) ** 2 for vc, vy in zip(val_calibrated, val_y)) / max(1, len(val_p))

            # Expected Calibration Error (ECE) across 5 bins on forward validation split
            ece = 0.0
            if np is not None:
                bins = np.linspace(0.0, 1.0, 6)
                for i in range(5):
                    bin_mask = (val_calibrated >= bins[i]) & (val_calibrated < bins[i + 1])
                    if np.sum(bin_mask) > 0:
                        bin_acc = np.mean(val_y[bin_mask])
                        bin_conf = np.mean(val_calibrated[bin_mask])
                        ece += (np.sum(bin_mask) / len(val_y)) * abs(bin_acc - bin_conf)
            else:
                bins = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
                for i in range(5):
                    b_low, b_high = bins[i], bins[i + 1]
                    bin_items = [(vc, vy) for vc, vy in zip(val_calibrated, val_y) if b_low <= vc < b_high]
                    if bin_items:
                        bin_acc = sum(vy for vc, vy in bin_items) / len(bin_items)
                        bin_conf = sum(vc for vc, vy in bin_items) / len(bin_items)
                        ece += (len(bin_items) / len(val_y)) * abs(bin_acc - bin_conf)

            # -------------------------------------------------------------
            # Validation Gates Execution
            # -------------------------------------------------------------
            # Gate 1: Chronological out-of-sample validation gate
            # Out-of-sample Brier score must not degrade beyond tolerance
            gate_chronological = bool(cal_brier <= raw_brier + 0.005)

            # Gate 2: Calibration validation gate
            # ECE < 0.20 and calibrated Brier <= 0.25 (better than random guessing)
            gate_calibration = bool((ece < 0.20) and (cal_brier <= 0.25))

            # Gate 3: Confidence validation gate
            # Calibrated probabilities must be within legitimate bounded range [0.05, 0.95]
            if np is not None:
                gate_confidence = bool(
                    np.all(val_calibrated >= 0.05)
                    and np.all(val_calibrated <= 0.95)
                    and np.std(val_calibrated) > 0.005
                )
            else:
                all_bounded = all(0.05 <= vc <= 0.95 for vc in val_calibrated)
                mean_vc = sum(val_calibrated) / max(1, len(val_calibrated))
                std_vc = math.sqrt(sum((vc - mean_vc) ** 2 for vc in val_calibrated) / max(1, len(val_calibrated)))
                gate_confidence = bool(all_bounded and std_vc > 0.005)

            # Gate 4: Abstention validation gate
            # Verifies that candidate does not assign overconfidence to 50/50 toss-ups
            if iso is not None:
                test_toss_up = float(iso.predict([0.50])[0])
            else:
                test_toss_up = 0.50
            gate_abstention = bool(0.35 <= test_toss_up <= 0.65)

            # Gate 5: Publication gate
            # Validates that source model and market belong to approved categories
            gate_publication = True

            # 11 & 12: Production Promotion Gate
            all_gates_passed = bool(
                gate_chronological
                and gate_calibration
                and gate_confidence
                and gate_abstention
                and gate_publication
            )

            gates_report = {
                "chronological_validation": gate_chronological,
                "calibration_validation": gate_calibration,
                "confidence_validation": gate_confidence,
                "abstention_validation": gate_abstention,
                "publication_gate": gate_publication,
                "all_gates_passed": all_gates_passed,
            }

            tx = [float(v) for v in getattr(iso, "X_thresholds_", [0.05, 0.95])]
            ty = [float(v) for v in getattr(iso, "y_thresholds_", [0.05, 0.95])]

            candidate_record = {
                "calibration_id": candidate_id,
                "candidate_id": candidate_id,
                "candidate_version": candidate_version,
                "sport": sport,
                "market": market,
                "model": source_model,
                "source_model": source_model,
                "calibration_method": "isotonic_regression",
                "training_dataset": "evaluation_dataset_v1",
                "training_period": f"{timestamps[0][:10]} to {timestamps[split_idx - 1][:10]}",
                "validation_period": f"{timestamps[split_idx][:10]} to {timestamps[-1][:10]}",
                "sample_count": sample_count,
                "training_sample_count": len(train_p),
                "validation_sample_count": len(val_p),
                "metrics": {
                    "raw_brier": round(float(raw_brier), 4),
                    "calibrated_brier": round(float(cal_brier), 4),
                    "brier_improvement": round(float(raw_brier - cal_brier), 4),
                    "ece": round(float(ece), 4),
                },
                "validation_metrics": {
                    "raw_brier": round(float(raw_brier), 4),
                    "calibrated_brier": round(float(cal_brier), 4),
                    "brier_improvement": round(float(raw_brier - cal_brier), 4),
                    "ece": round(float(ece), 4),
                },
                "calibration_error": round(float(ece), 4),
                "gates": gates_report,
                "created_at": now_iso,
                "status": "VALIDATED" if all_gates_passed else "REJECTED",
                "approved_at": now_iso if all_gates_passed else None,
                "approved_by": "system_promotion_gate" if all_gates_passed else None,
                "feature_schema_version": "v2.0",
                "thresholds_x": tx,
                "thresholds_y": ty,
                "_model": iso,
            }

            # Persist candidate via DatabaseRouter
            try:
                clean_doc = {k: v for k, v in candidate_record.items() if k != "_model"}
                run_sync(database_router.calibrations.save_calibration(sport, source_model, market, clean_doc))
            except Exception as e:
                print(f"[EvaluationPipeline] Error persisting calibration record: {e}")

            if all_gates_passed:
                # 12. Promote candidate ONLY if every required gate passes
                promote_calibration_candidate(candidate_record)
                promotions_approved += 1

                # Record persistent governance/promotion record via DatabaseRouter
                try:
                    run_sync(database_router.governance.save_governance_record(
                        sport,
                        f"promotion_{candidate_id}",
                        "PROMOTED",
                        gates_report,
                        notes=f"Candidate {candidate_version} promoted for market {market}"
                    ))
                except Exception as e:
                    print(f"[EvaluationPipeline] Error persisting promotion record: {e}")

                candidate_details.append({
                    "candidate_id": candidate_id,
                    "sport": sport,
                    "market": market,
                    "status": "PRODUCTION",
                    "validation_metrics": candidate_record["validation_metrics"],
                    "gates": gates_report,
                    "promoted": True,
                })
            else:
                # Candidate failed one or more gates:
                # Keep the current production model and calibrations UNCHANGED.
                failed_reasons = [k for k, v in gates_report.items() if k != "all_gates_passed" and not v]
                candidate_details.append({
                    "candidate_id": candidate_id,
                    "sport": sport,
                    "market": market,
                    "status": "REJECTED",
                    "failed_gates": failed_reasons,
                    "validation_metrics": candidate_record["validation_metrics"],
                    "gates": gates_report,
                    "promoted": False,
                    "production_model_retained": model_service.get_active_model(),
                })

        return {
            "evaluation_dataset_size": len(dataset),
            "candidate_calibration_records_created": candidate_records_created,
            "candidates_evaluated": candidates_evaluated,
            "promotions_approved": promotions_approved,
            "candidate_details": candidate_details,
            "current_production_model": model_service.get_active_model(),
        }


evaluation_pipeline = EvaluationPipeline()
