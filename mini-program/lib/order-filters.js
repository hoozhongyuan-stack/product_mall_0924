// Shared by the member shortcuts and the paginated order list. The server owns counts and matching.
const FULFILLMENT_FILTERS = Object.freeze(['WAITING_SHIPMENT', 'IN_TRANSIT', 'WAITING_REDEMPTION', 'AFTER_SALE'])
function orderFilters(value = {}) {
  return {
    status: ['PENDING_PAYMENT', 'PAID', 'CLOSED'].includes(value.status) ? value.status : '',
    fulfillment: FULFILLMENT_FILTERS.includes(value.fulfillment) ? value.fulfillment : '',
  }
}
module.exports = { orderFilters }
