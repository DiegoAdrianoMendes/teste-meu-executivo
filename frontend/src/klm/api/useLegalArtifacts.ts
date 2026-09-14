import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  klmApi,
  type KlmLegalArtifactGenerateInput,
  type KlmLegalArtifactKind,
  type KlmLegalArtifactSectionGenerateInput,
} from '@/klm/api';

/**
 * Hooks do editor jurídico de uma demanda — aba "Peças" (Onda 6, Sprint A
 * UI). Consomem `/api/v1/legal/demands/{id}/artifacts/*`
 * (`app/api/v1/endpoints/legal_editor.py` + `legal_demands.py`) via
 * `klmApi` (nunca `fetch` cru). Ver `LegalArtifactsTab`.
 */

const LEGAL_ARTIFACT_VERSIONS_KEY = ['klm', 'legal', 'artifact-versions'] as const;
const LEGAL_ARTIFACT_CHECKLIST_KEY = ['klm', 'legal', 'artifact-checklist'] as const;

export function useLegalArtifactVersions(demandId: string | undefined, kind?: KlmLegalArtifactKind) {
  return useQuery({
    queryKey: [...LEGAL_ARTIFACT_VERSIONS_KEY, demandId, kind ?? 'todas'],
    queryFn: () => klmApi.listLegalArtifactEditorVersions(demandId as string, kind),
    enabled: Boolean(demandId),
  });
}

export function useLegalArtifactChecklist(demandId: string | undefined, artifactId: string | undefined) {
  return useQuery({
    queryKey: [...LEGAL_ARTIFACT_CHECKLIST_KEY, demandId, artifactId],
    queryFn: () => klmApi.getLegalArtifactChecklist(demandId as string, artifactId as string),
    enabled: Boolean(demandId) && Boolean(artifactId),
  });
}

function invalidateArtifactQueries(
  queryClient: ReturnType<typeof useQueryClient>,
  demandId: string,
  artifactId?: string,
) {
  void queryClient.invalidateQueries({ queryKey: [...LEGAL_ARTIFACT_VERSIONS_KEY, demandId] });
  if (artifactId) {
    void queryClient.invalidateQueries({ queryKey: [...LEGAL_ARTIFACT_CHECKLIST_KEY, demandId, artifactId] });
  }
}

export function useGenerateLegalArtifact(demandId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: KlmLegalArtifactGenerateInput) => klmApi.generateLegalArtifact(demandId, payload),
    onSuccess: () => invalidateArtifactQueries(queryClient, demandId),
  });
}

export function useGenerateLegalArtifactSection(demandId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ artifactId, payload }: { artifactId: string; payload: KlmLegalArtifactSectionGenerateInput }) =>
      klmApi.generateLegalArtifactSection(demandId, artifactId, payload),
    onSuccess: (_data, variables) => invalidateArtifactQueries(queryClient, demandId, variables.artifactId),
  });
}

export function useSubmitLegalArtifact(demandId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (artifactId: string) => klmApi.submitLegalArtifactEditorVersion(demandId, artifactId),
    onSuccess: (_data, artifactId) => invalidateArtifactQueries(queryClient, demandId, artifactId),
  });
}

export function useVerifyLegalArtifactCitations(demandId: string) {
  return useMutation({
    mutationFn: (artifactId: string) => klmApi.verifyLegalArtifactCitations(demandId, artifactId),
  });
}

export function useReviewLegalArtifact(demandId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (artifactId: string) => klmApi.reviewLegalArtifact(demandId, artifactId),
    onSuccess: (_data, artifactId) => invalidateArtifactQueries(queryClient, demandId, artifactId),
  });
}

export function useExportLegalArtifactDocx(demandId: string) {
  return useMutation({
    mutationFn: (artifactId: string) => klmApi.exportLegalArtifactDocx(demandId, artifactId),
  });
}

export function useReimportLegalArtifactDocx(demandId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ artifactId, expectedVersion, file }: { artifactId: string; expectedVersion: number; file: File }) =>
      klmApi.reimportLegalArtifactDocx(demandId, artifactId, expectedVersion, file),
    onSuccess: (_data, variables) => invalidateArtifactQueries(queryClient, demandId, variables.artifactId),
  });
}
