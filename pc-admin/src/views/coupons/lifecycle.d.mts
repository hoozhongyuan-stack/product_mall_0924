import type { Campaign } from './types'
export interface CampaignLifecycle { label: string; active: boolean; reason: string }
export function campaignLifecycle(campaign: Campaign, now?: number): CampaignLifecycle
export function issuanceReason(campaign: Campaign, now?: number): string
