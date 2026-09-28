const { memberPage } = require('../../lib/member-page')
const { presentList } = require('../../lib/member')
Page(memberPage({ path: '/api/v1/app/member/points', present: presentList, paginated: true }))
