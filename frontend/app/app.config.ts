export default defineAppConfig({
  ui: {
    colors: {
      primary: 'blue',
      neutral: 'slate'
    },
    select: {
      slots: {
        viewport: 'lenis-prevent'
      }
    },
    selectMenu: {
      slots: {
        viewport: 'lenis-prevent',
        content: 'w-auto min-w-(--reka-combobox-trigger-width) max-w-[min(400px,calc(100vw-2rem))]'
      }
    },
    table: {
      slots: {
        root: 'lenis-prevent'
      }
    },
    modal: {
      slots: {
        content: 'lenis-prevent'
      }
    },
    drawer: {
      slots: {
        content: 'lenis-prevent'
      }
    },
    slideover: {
      slots: {
        content: 'lenis-prevent'
      }
    }
  }
})
