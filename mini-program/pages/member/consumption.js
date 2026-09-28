const { memberPage } = require('../../lib/member-page')
const { presentList, presentConsumption } = require('../../lib/member')
Page(memberPage({ path: '/api/v1/app/member/consumption', present: (value, page) => presentList(value, page, presentConsumption), paginated: true }))
