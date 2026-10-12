Component({
  options: { multipleSlots: true },
  properties: { line: { type: Object, value: {} } },
  data: { failedImage: false },
  observers: { 'line.image': function () { this.setData({ failedImage: false }) } },
  methods: { imageError(event) {
    if (event.currentTarget.dataset.url !== (this.data.line || {}).image) return
    this.setData({ failedImage: true })
  } },
})
