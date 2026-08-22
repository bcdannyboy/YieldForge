import type { CandidateSummary } from "../contracts";

export type ReconciledCandidate = CandidateSummary | { candidate_id: string; live_only: true };

export function reconcileCandidates(
  liveCandidateIds: readonly string[],
  terminalCandidates: readonly CandidateSummary[],
): ReconciledCandidate[] {
  const terminalById = new Map(terminalCandidates.map((item) => [item.candidate_id, item]));
  const result: ReconciledCandidate[] = liveCandidateIds.map(
    (candidateId) => terminalById.get(candidateId) ?? { candidate_id: candidateId, live_only: true },
  );
  const seen = new Set(liveCandidateIds);
  for (const candidate of terminalCandidates) {
    if (!seen.has(candidate.candidate_id)) result.push(candidate);
  }
  return result;
}
