/** The admin asset DTO shared by catalog and page editors. */
export interface Asset {
  assetId: string
  kind: 'IMAGE' | 'VIDEO' | 'GIF'
  contentType: string
  byteSize: number
  width?: number
  height?: number
  adminUrl: string
}
