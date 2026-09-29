const http = require('node:http')

const request = http.get('http://127.0.0.1:8787/healthz', { timeout: 2000 }, (response) => {
  response.resume()
  if (response.statusCode !== 200) process.exitCode = 1
})
request.on('timeout', () => request.destroy())
request.on('error', () => { process.exitCode = 1 })
