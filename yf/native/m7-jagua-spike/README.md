# M7 Jagua differential spike

This bounded Rust executable extends the collision engine embedded by Spyrrow with batched M7
rigid-layout queries. Jagua uses `f32` collision geometry and does not replace Shapely residual
overlays, material accounting, accepted-witness validation, or differential auditing.

The executable accepts one `yieldforge.m7-jagua-spike-request.v1` JSON object on standard input and
emits one response object. A request contains one remnant polygon, cached complete layouts expressed
as simple placed-part polygons, and a batch of translation queries.

The search request additionally generates the frozen bbox/vertex/grid translation sequence in
Rust. Candidate-generation vertices and bounds cross the process boundary as raw IEEE-754 `f64`
bit patterns so exact duplicate detection and ordering match Python. The collision container is
guarded by one source unit to avoid Jagua's boundary-contact rejection; every possible fit still
passes through Shapely.
