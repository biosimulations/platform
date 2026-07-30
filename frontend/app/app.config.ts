export default defineAppConfig({
  ui: {
    colors: {
      primary: 'blue',
      neutral: 'slate'
    },
    selectMenu: {
      slots: {
        viewport: 'lenis-prevent',
        content: 'w-auto min-w-(--reka-combobox-trigger-width) max-w-[min(400px,calc(100vw-2rem))]'
      }
    }
  }
})
