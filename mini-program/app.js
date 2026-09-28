const startup = require('./lib/startup')

App({
  onLaunch(options) { startup.captureLaunch(options) },
  globalData: {
    // Local simulator only. Replace with an approved HTTPS API domain for device/release builds.
    apiBaseUrl: 'http://127.0.0.1:8000',
  },
})
