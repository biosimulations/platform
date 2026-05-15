export function normalize_text(str: string) {
  str = str.replace(/[-]/g, ' ').toLowerCase()

  // Capitalize letters after spaces
  str = str.replace(/\b\w+/g, function (word) {
    return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()
  })

  return str
}
