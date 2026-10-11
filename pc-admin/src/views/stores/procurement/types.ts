export interface ProcurementOrder {
 id:string; orderNo:string; storeId:string; storeName:string; status:string; revision:number
 goodsAmountFen:number; freightFen:number; payableFen:number; createdAt:string
 paymentInstructions:string; unitsChanged?:boolean; unitReviewRequired?:boolean
 paymentProof:null|{assetId:string;previewUrl:string}
 shipment:null|{carrier:string;tracking:string}
 lines:{id:string;skuId:string;productName:string;specLabel:string;saleUnit:string;quantity:number;purchaseCostFen:number;unitVersionId:string;conversionRatio:number;baseUnit:string}[]
 records:{id:string;kind:string;note:string;reference:string;actorName:string;createdAt:string;proofAssetId?:string;proofPreviewUrl?:string}[]
}
