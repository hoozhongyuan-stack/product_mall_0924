export interface CampaignForm {code:string;title:string;kind:string;minimum:string;discount:string;productIds:string[];redeemEligible:boolean;validFrom:string;validUntil:string;totalQuantity:string;selfClaimLimit:string;claimMode:string;issuanceEnabled:boolean}
export interface PendingCoupon {key:string;path:string;method:'POST'|'PUT';body:Record<string,unknown>;action:string}
export function campaignError(form:CampaignForm):string
export function campaignBody(form:CampaignForm,revision?:number):Record<string,unknown>
export function issueError(memberId:string,quantity:string,reason:string,canRepeat:boolean):string
export function pendingCoupon(storage:Storage,actor:string,target:string,value?:PendingCoupon|null):PendingCoupon|null
