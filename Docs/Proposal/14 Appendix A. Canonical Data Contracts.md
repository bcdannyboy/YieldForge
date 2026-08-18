---
source: YieldForge Proposal v1
status: source
converted: 2026-08-17
---

> [!note] Source status
> This note is a working Markdown conversion of the original DOCX. The preserved DOCX remains the source artifact.

# Appendix A. Canonical Data Contracts

Indicative schemas for the validation system; implementation types may change without changing semantics.

## A.1 Part

| Part {<br>id: string<br>polygon: exact Polygon2D<br>allowed_rotations_deg: list[float]<br>clearance: float<br>order_id: string<br>material_key: optional string   # fixed in v1<br>quality_requirements: optional  # reserved, unused in v1<br>} |
| --- |

## A.2 Order event

| OrderEvent {<br>id: string<br>timestamp: datetime<br>release_timestamp: datetime<br>due_timestamp: optional datetime<br>part_instances: list[PartID]<br>benchmark_family: hidden metadata<br>} |
| --- |

## A.3 Stock piece and remnant

| StockPiece {<br>id: string<br>outer_polygon: exact Polygon2D<br>source_type: FULL_SHEET \| REMNANT<br>parent_stock_id: optional string<br>created_at: datetime<br>acquisition_cost: float<br>scrap_value_per_area: float<br>storage_location: optional string<br>material_key: string<br>quality_regions: optional list[Region]  # reserved<br>} |
| --- |

## A.4 Candidate nest

| CandidateNest {<br>id: string<br>stock_piece_id: string<br>placements: list[Placement]<br>sparrow_commit: string<br>solver_seed: int<br>solver_config_hash: string<br>immediate_cost: float<br>residual_components: list[Polygon2D]<br>retained_remnants: list[StockPiece]<br>scrap_components: list[Polygon2D]<br>descriptors: map[string, float]<br>} |
| --- |

## A.5 Inventory and decision state

| InventoryState {<br>event_index: int<br>available_stock: canonical list[StockPiece]<br>cumulative_purchase_cost: float<br>cumulative_storage_cost: float<br>cumulative_handling_cost: float<br>cumulative_scrap_proceeds: float<br>history_hash: string<br>}<br>DecisionRecord {<br>policy_id: string<br>information_cutoff: datetime<br>candidate_archive_hash: string<br>chosen_candidate_id: string<br>policy_score: float<br>rejected_candidate_scores: list<br>} |
| --- |
