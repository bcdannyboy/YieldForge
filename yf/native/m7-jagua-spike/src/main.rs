use std::collections::HashSet;
use std::io::{self, Read};
use std::time::Instant;

use anyhow::{Context, Result, ensure};
use jagua_rs::collision_detection::hazards::filter::NoFilter;
use jagua_rs::collision_detection::{CDEConfig, CDEngine};
use jagua_rs::geometry::Transformation;
use jagua_rs::geometry::fail_fast::SPSurrogateConfig;
use jagua_rs::geometry::geo_traits::Transformable;
use jagua_rs::geometry::primitives::SPolygon;
use jagua_rs::io::ext_repr::{ExtContainer, ExtPolygon, ExtSPolygon, ExtShape};
use jagua_rs::io::import::{Importer, import_simple_polygon};
use serde::{Deserialize, Serialize};

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    schema_version: String,
    outer: Vec<[f64; 2]>,
    #[serde(default)]
    holes: Vec<Vec<[f64; 2]>>,
    layouts: Vec<Layout>,
    queries: Vec<Query>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Layout {
    layout_id: String,
    polygons: Vec<Vec<[f64; 2]>>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Query {
    layout_index: usize,
    translation: [f64; 2],
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SearchRequest {
    schema_version: String,
    outer: Vec<[f64; 2]>,
    #[serde(default)]
    holes: Vec<Vec<[f64; 2]>>,
    parent_vertex_bits: Vec<[u64; 2]>,
    parent_bounds_bits: [u64; 4],
    layouts: Vec<SearchLayout>,
    search_config: SearchConfig,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SearchLayout {
    layout_id: String,
    polygons: Vec<Vec<[f64; 2]>>,
    vertex_bits: Vec<[u64; 2]>,
    bounds_bits: [u64; 4],
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SearchConfig {
    grid_columns: usize,
    grid_rows: usize,
    maximum_candidates: usize,
    coordinate_tolerance: f64,
    candidate_source_order: [String; 3],
}

#[derive(Debug, Serialize)]
struct Response {
    schema_version: &'static str,
    backend: &'static str,
    backend_version: &'static str,
    coordinate_precision: &'static str,
    build_microseconds: u128,
    query_microseconds: u128,
    results: Vec<QueryResult>,
}

#[derive(Debug, Serialize)]
struct QueryResult {
    layout_id: String,
    collides: bool,
}

#[derive(Debug, Serialize)]
struct SearchResponse {
    schema_version: &'static str,
    backend: &'static str,
    backend_version: &'static str,
    coordinate_precision: &'static str,
    build_microseconds: u128,
    generation_microseconds: u128,
    query_microseconds: u128,
    searches: Vec<SearchResult>,
}

#[derive(Debug, Serialize)]
struct SearchResult {
    layout_id: String,
    generated_candidate_count: usize,
    duplicate_candidate_count: usize,
    budget_truncated: bool,
    translations: Vec<[f64; 2]>,
    collisions: Vec<bool>,
}

fn points(value: &[[f64; 2]]) -> Result<ExtSPolygon> {
    ensure!(value.len() >= 3, "polygon requires at least three points");
    let converted = value
        .iter()
        .map(|point| {
            ensure!(
                point[0].is_finite() && point[1].is_finite(),
                "polygon coordinates must be finite"
            );
            let x = point[0] as f32;
            let y = point[1] as f32;
            ensure!(
                x.is_finite() && y.is_finite(),
                "coordinate exceeds f32 range"
            );
            Ok((x, y))
        })
        .collect::<Result<Vec<_>>>()?;
    Ok(ExtSPolygon(converted))
}

fn engine(outer: &[[f64; 2]], holes: &[Vec<[f64; 2]>]) -> Result<CDEngine> {
    let container = ExtContainer {
        id: 0,
        shape: ExtShape::Polygon(ExtPolygon {
            outer: points(outer)?,
            inner: holes
                .iter()
                .map(|hole| points(hole))
                .collect::<Result<Vec<_>>>()?,
        }),
        zones: vec![],
    };
    let importer = Importer::new(
        CDEConfig {
            quadtree_depth: 4,
            cd_threshold: 16,
            item_surrogate_config: SPSurrogateConfig::none(),
        },
        None,
        None,
        None,
    );
    Ok(importer
        .import_container(&container)?
        .base_cde
        .as_ref()
        .clone())
}

fn layouts(request: &Request) -> Result<Vec<Vec<SPolygon>>> {
    request
        .layouts
        .iter()
        .map(|layout| {
            ensure!(!layout.layout_id.is_empty(), "layout ID cannot be empty");
            ensure!(
                !layout.polygons.is_empty(),
                "layout requires at least one polygon"
            );
            layout
                .polygons
                .iter()
                .map(|polygon| import_simple_polygon(&points(polygon)?))
                .collect::<Result<Vec<_>>>()
        })
        .collect()
}

fn search_layouts(request: &SearchRequest) -> Result<Vec<Vec<SPolygon>>> {
    request
        .layouts
        .iter()
        .map(|layout| {
            ensure!(!layout.layout_id.is_empty(), "layout ID cannot be empty");
            ensure!(
                !layout.polygons.is_empty(),
                "layout requires at least one polygon"
            );
            layout
                .polygons
                .iter()
                .map(|polygon| import_simple_polygon(&points(polygon)?))
                .collect::<Result<Vec<_>>>()
        })
        .collect()
}

fn evaluate(request: Request) -> Result<Response> {
    ensure!(
        request.schema_version == "yieldforge.m7-jagua-spike-request.v1",
        "unsupported request schema"
    );
    let build_started = Instant::now();
    let cde = engine(&request.outer, &request.holes)?;
    let base_layouts = layouts(&request)?;
    let build_microseconds = build_started.elapsed().as_micros();
    let query_started = Instant::now();
    let mut results = Vec::with_capacity(request.queries.len());
    for query in &request.queries {
        let polygons = base_layouts
            .get(query.layout_index)
            .context("query layout index is out of range")?;
        let translation = (query.translation[0] as f32, query.translation[1] as f32);
        ensure!(
            translation.0.is_finite() && translation.1.is_finite(),
            "translation exceeds f32 range"
        );
        let transformation = Transformation::from_translation(translation);
        let collides = polygons.iter().any(|polygon| {
            let mut moved = polygon.clone();
            moved.transform(&transformation);
            cde.detect_poly_collision(&moved, &NoFilter)
        });
        results.push(QueryResult {
            layout_id: request.layouts[query.layout_index].layout_id.clone(),
            collides,
        });
    }
    Ok(Response {
        schema_version: "yieldforge.m7-jagua-spike-response.v1",
        backend: "jagua-rs",
        backend_version: "0.7.0",
        coordinate_precision: "f32",
        build_microseconds,
        query_microseconds: query_started.elapsed().as_micros(),
        results,
    })
}

fn finite_bounds(bounds: &[f64; 4]) -> bool {
    bounds.iter().all(|value| value.is_finite()) && bounds[0] <= bounds[2] && bounds[1] <= bounds[3]
}

fn normalized(value: f64) -> f64 {
    if value == 0.0 { 0.0 } else { value }
}

fn grid(start: f64, stop: f64, count: usize) -> Vec<f64> {
    if count == 2 {
        return vec![start, stop];
    }
    let step = (stop - start) / (count - 1) as f64;
    let mut values = (0..count)
        .map(|index| normalized(start + index as f64 * step))
        .collect::<Vec<_>>();
    values[count - 1] = stop;
    values
}

fn generate_translations(
    parent_vertices: &[[f64; 2]],
    parent_bounds: [f64; 4],
    layout: &SearchLayout,
    config: &SearchConfig,
) -> Result<(Vec<[f64; 2]>, usize, usize, bool)> {
    let layout_vertices = layout
        .vertex_bits
        .iter()
        .map(|point| [f64::from_bits(point[0]), f64::from_bits(point[1])])
        .collect::<Vec<_>>();
    let layout_bounds = layout.bounds_bits.map(f64::from_bits);
    ensure!(finite_bounds(&parent_bounds), "parent bounds are invalid");
    ensure!(finite_bounds(&layout_bounds), "layout bounds are invalid");
    ensure!(
        config.grid_columns >= 2 && config.grid_rows >= 2 && config.maximum_candidates >= 1,
        "search counts are invalid"
    );
    ensure!(
        config.coordinate_tolerance.is_finite() && config.coordinate_tolerance > 0.0,
        "coordinate tolerance is invalid"
    );
    ensure!(
        config.candidate_source_order == ["bbox_alignments", "vertex_alignments", "uniform_grid"],
        "candidate source order differs from M7"
    );
    ensure!(
        parent_vertices
            .iter()
            .chain(layout_vertices.iter())
            .flatten()
            .all(|value| value.is_finite()),
        "search vertices must be finite"
    );
    let min_x = parent_bounds[0] - layout_bounds[0];
    let max_x = parent_bounds[2] - layout_bounds[2];
    let min_y = parent_bounds[1] - layout_bounds[1];
    let max_y = parent_bounds[3] - layout_bounds[3];
    if min_x > max_x + config.coordinate_tolerance || min_y > max_y + config.coordinate_tolerance {
        return Ok((vec![], 0, 0, false));
    }
    let mut unique = Vec::new();
    let mut seen = HashSet::new();
    let mut duplicates = 0;
    let mut truncated = false;
    let add = |x: f64,
               y: f64,
               unique: &mut Vec<[f64; 2]>,
               seen: &mut HashSet<(u64, u64)>,
               duplicates: &mut usize,
               truncated: &mut bool| {
        let point = [normalized(x), normalized(y)];
        let key = (point[0].to_bits(), point[1].to_bits());
        if !seen.insert(key) {
            *duplicates += 1;
            return false;
        }
        unique.push(point);
        if unique.len() > config.maximum_candidates {
            *truncated = true;
            return true;
        }
        false
    };
    for [x, y] in [
        [min_x, min_y],
        [min_x, max_y],
        [max_x, min_y],
        [max_x, max_y],
    ] {
        if add(
            x,
            y,
            &mut unique,
            &mut seen,
            &mut duplicates,
            &mut truncated,
        ) {
            break;
        }
    }
    'vertices: for parent in parent_vertices {
        if truncated {
            break;
        }
        for foot in &layout_vertices {
            let x = parent[0] - foot[0];
            let y = parent[1] - foot[1];
            if x >= min_x - config.coordinate_tolerance
                && x <= max_x + config.coordinate_tolerance
                && y >= min_y - config.coordinate_tolerance
                && y <= max_y + config.coordinate_tolerance
                && add(
                    x,
                    y,
                    &mut unique,
                    &mut seen,
                    &mut duplicates,
                    &mut truncated,
                )
            {
                break 'vertices;
            }
        }
    }
    if !truncated {
        'grid: for x in grid(min_x, max_x, config.grid_columns) {
            for y in grid(min_y, max_y, config.grid_rows) {
                if add(
                    x,
                    y,
                    &mut unique,
                    &mut seen,
                    &mut duplicates,
                    &mut truncated,
                ) {
                    break 'grid;
                }
            }
        }
    }
    let generated = unique.len();
    unique.truncate(config.maximum_candidates);
    Ok((unique, generated, duplicates, truncated))
}

fn evaluate_search(request: SearchRequest) -> Result<SearchResponse> {
    ensure!(
        request.schema_version == "yieldforge.m7-jagua-search-request.v1",
        "unsupported search request schema"
    );
    let build_started = Instant::now();
    let cde = engine(&request.outer, &request.holes)?;
    let base_layouts = search_layouts(&request)?;
    let build_microseconds = build_started.elapsed().as_micros();
    let generation_started = Instant::now();
    let parent_vertices = request
        .parent_vertex_bits
        .iter()
        .map(|point| [f64::from_bits(point[0]), f64::from_bits(point[1])])
        .collect::<Vec<_>>();
    let parent_bounds = request.parent_bounds_bits.map(f64::from_bits);
    let generated = request
        .layouts
        .iter()
        .map(|layout| {
            generate_translations(
                &parent_vertices,
                parent_bounds,
                layout,
                &request.search_config,
            )
        })
        .collect::<Result<Vec<_>>>()?;
    let generation_microseconds = generation_started.elapsed().as_micros();
    let query_started = Instant::now();
    let searches = request
        .layouts
        .iter()
        .zip(base_layouts)
        .zip(generated)
        .map(
            |((layout, polygons), (translations, generated_count, duplicates, truncated))| {
                let collisions = translations
                    .iter()
                    .map(|translation| {
                        let transformation = Transformation::from_translation((
                            translation[0] as f32,
                            translation[1] as f32,
                        ));
                        polygons.iter().any(|polygon| {
                            let mut moved = polygon.clone();
                            moved.transform(&transformation);
                            cde.detect_poly_collision(&moved, &NoFilter)
                        })
                    })
                    .collect();
                SearchResult {
                    layout_id: layout.layout_id.clone(),
                    generated_candidate_count: generated_count,
                    duplicate_candidate_count: duplicates,
                    budget_truncated: truncated,
                    translations,
                    collisions,
                }
            },
        )
        .collect();
    Ok(SearchResponse {
        schema_version: "yieldforge.m7-jagua-search-response.v1",
        backend: "jagua-rs",
        backend_version: "0.7.0",
        coordinate_precision: "f32",
        build_microseconds,
        generation_microseconds,
        query_microseconds: query_started.elapsed().as_micros(),
        searches,
    })
}

fn run() -> Result<()> {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input)?;
    let payload: serde_json::Value =
        serde_json::from_str(&input).context("invalid spike request JSON")?;
    let schema = payload
        .get("schema_version")
        .and_then(|value| value.as_str())
        .context("request is missing schema version")?;
    let output = match schema {
        "yieldforge.m7-jagua-spike-request.v1" => {
            let request: Request = serde_json::from_value(payload)?;
            serde_json::to_string(&evaluate(request)?)?
        }
        "yieldforge.m7-jagua-search-request.v1" => {
            let request: SearchRequest = serde_json::from_value(payload)?;
            serde_json::to_string(&evaluate_search(request)?)?
        }
        _ => anyhow::bail!("unsupported request schema"),
    };
    println!("{output}");
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("{error:#}");
        std::process::exit(2);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request(translation: [f64; 2]) -> Request {
        Request {
            schema_version: "yieldforge.m7-jagua-spike-request.v1".into(),
            outer: vec![[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]],
            holes: vec![],
            layouts: vec![Layout {
                layout_id: "square".into(),
                polygons: vec![vec![[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]]],
            }],
            queries: vec![Query {
                layout_index: 0,
                translation,
            }],
        }
    }

    #[test]
    fn distinguishes_contained_and_outside_layouts() {
        assert!(!evaluate(request([1.0, 1.0])).unwrap().results[0].collides);
        assert!(evaluate(request([9.0, 9.0])).unwrap().results[0].collides);
    }

    #[test]
    fn generates_registered_translation_order() {
        let layout = SearchLayout {
            layout_id: "square".into(),
            polygons: vec![vec![[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]]],
            vertex_bits: vec![
                [0.0f64.to_bits(), 0.0f64.to_bits()],
                [0.0f64.to_bits(), 2.0f64.to_bits()],
                [2.0f64.to_bits(), 0.0f64.to_bits()],
                [2.0f64.to_bits(), 2.0f64.to_bits()],
            ],
            bounds_bits: [
                0.0f64.to_bits(),
                0.0f64.to_bits(),
                2.0f64.to_bits(),
                2.0f64.to_bits(),
            ],
        };
        let config = SearchConfig {
            grid_columns: 5,
            grid_rows: 5,
            maximum_candidates: 8,
            coordinate_tolerance: 1e-7,
            candidate_source_order: [
                "bbox_alignments".into(),
                "vertex_alignments".into(),
                "uniform_grid".into(),
            ],
        };
        let (translations, _, _, _) = generate_translations(
            &[[10.0, 10.0], [10.0, 14.0], [14.0, 10.0], [14.0, 14.0]],
            [10.0, 10.0, 14.0, 14.0],
            &layout,
            &config,
        )
        .unwrap();
        assert_eq!(
            &translations[..4],
            &[[10.0, 10.0], [10.0, 12.0], [12.0, 10.0], [12.0, 12.0]]
        );
    }
}
