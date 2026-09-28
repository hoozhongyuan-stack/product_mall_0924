export interface StartupSelection {
  gifAssetId: string | null
  fallbackAssetId: string | null
}

export interface StartupDraft extends StartupSelection {
  revision: number
  publishedRevision: number | null
  publishedVersionId?: string | null
  publicationRevision?: number
}

export interface StartupPreview extends StartupSelection {
  revision: number
  gifUrl: string
  fallbackUrl: string
}

export interface StartupPublication {
  versionId: string
  revision: number
}

export interface StartupUpload {
  role: 'gif' | 'fallback'
  expectedAssetId: string | null
  assetId: string
}

/** Preserve the slot the operator selected while an asynchronous upload was running. */
export function applyStartupUpload<T extends StartupSelection>(current: T, upload: StartupUpload): T {
  const field = upload.role === 'gif' ? 'gifAssetId' : 'fallbackAssetId'
  return current[field] === upload.expectedAssetId
    ? { ...current, [field]: upload.assetId }
    : current
}
