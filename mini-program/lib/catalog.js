function money(fen) {
  if (!Number.isSafeInteger(fen) || fen < 0) return '价格待确认'
  const yuan = (fen / 100).toFixed(2)
  return `¥${yuan.endsWith('.00') ? yuan.slice(0, -3) : yuan}`
}

function mediaUrl(baseUrl, path) {
  if (!path || typeof path !== 'string' || !path.startsWith('/api/v1/app/assets/')) return ''
  return `${baseUrl.replace(/\/$/, '')}${path}`
}

function categoryTree(items) {
  const active = (Array.isArray(items) ? items : []).filter((item) => item.status === 'ACTIVE')
  return active.filter((item) => !item.parentId).map((parent) => ({
    ...parent,
    children: active.filter((item) => item.parentId === parent.id)
      .sort((left, right) => left.sortOrder - right.sortOrder || left.name.localeCompare(right.name)),
  }))
}

function productCard(item, baseUrl) {
  return {
    id: item.productId,
    name: item.name,
    imageUrl: mediaUrl(baseUrl, item.mainImageUrl),
    price: money(item.minListPriceFen),
    specsPreview: item.specsPreview || '默认规格',
    fulfillment: item.fulfillmentKind === 'REDEEM' ? '到店核销' : '快递发货',
    availability: item.availabilityCode === 'STOCK_NOT_READY' ? '暂不可购买' : '',
  }
}

function skuLabel(sku) {
  if (!Array.isArray(sku.specs) || !sku.specs.length) return '默认规格'
  return sku.specs.map((spec) => `${spec.name}：${spec.value}`).join(' · ')
}

function productDetail(item, baseUrl) {
  const images = [item.mainImageUrl, ...(item.galleryImageUrls || [])]
    .map((path) => mediaUrl(baseUrl, path)).filter(Boolean)
  const skus = (item.skus || []).map((sku) => ({
    id: sku.skuId,
    label: skuLabel(sku),
    priceFen: Number.isSafeInteger(sku.applicablePriceFen) ? sku.applicablePriceFen : sku.listPriceFen,
    price: money(Number.isSafeInteger(sku.applicablePriceFen) ? sku.applicablePriceFen : sku.listPriceFen),
    priceSource: sku.priceSource || 'DAILY',
    availableQuantity: sku.availableQuantity || 0,
    cartEligible: sku.cartEligible === true,
    unit: sku.unit && sku.unit.saleUnit ? sku.unit.saleUnit : '件',
  }))
  const cheapest = skus.reduce((best, sku) => !best || sku.priceFen < best.priceFen ? sku : best, null)
  return {
    id: item.productId,
    name: item.name,
    fulfillmentKind: item.fulfillmentKind,
    fulfillment: item.fulfillmentKind === 'REDEEM' ? '到店核销' : '快递发货',
    redeemValidUntil: item.fulfillmentKind === 'REDEEM' && typeof item.redeemValidUntil === 'string' ? item.redeemValidUntil : '',
    descriptionHtml: item.descriptionHtml || '',
    images,
    videoUrl: mediaUrl(baseUrl, item.videoUrl),
    skus,
    defaultSkuId: cheapest ? cheapest.id : '',
    price: cheapest ? cheapest.price : '价格待确认',
    availability: item.availabilityMessage || '暂无可售库存。',
    purchasable: item.purchasable === true,
  }
}

module.exports = { money, mediaUrl, categoryTree, productCard, productDetail, skuLabel }
