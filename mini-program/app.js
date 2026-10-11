const startup = require('./lib/startup')
const dining = require('./lib/dining-alliance')

App({
  onLaunch(options) { startup.captureLaunch(options) },
  onHide() { dining.close() },
  globalData: {
    // Local simulator only. Replace with an approved HTTPS API domain for device/release builds.
    apiBaseUrl: 'http://127.0.0.1:8000',
  },
})
