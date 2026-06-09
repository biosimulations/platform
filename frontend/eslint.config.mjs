// @ts-check
import withNuxt from './.nuxt/eslint.config.mjs'

export default withNuxt(
  {
    rules: {
      'vue/html-closing-bracket-newline': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/multiline-html-element-content-newline': 'off',
      'vue/max-attributes-per-line': 'off',
      'vue/html-indent': 'off',
      'vue/html-self-closing': 'off',
      'vue/mustache-interpolation-spacing': 'off',
      'vue/html-closing-bracket-spacing': 'off',
      'vue/attributes-order': 'off',
      'vue/html-comment-content-spacing': 'off',
      'vue/block-tag-newline': 'off',
      'vue/padding-line-between-blocks': 'off',
      '@stylistic/comma-dangle': 'off',
      '@stylistic/quotes': 'off',
      '@stylistic/semi': 'off',
      '@stylistic/indent': 'off',
      '@stylistic/object-curly-spacing': 'off',
      '@stylistic/member-delimiter-style': 'off',
      '@stylistic/spaced-comment': 'off',
      '@typescript-eslint/no-explicit-any': 'off',
      'vue/multi-word-component-names': 'off',
      '@stylistic/no-multiple-empty-lines': 'off',
      'vue/object-curly-spacing': 'off',
      '@typescript-eslint/no-unused-vars': 'warn',
      '@stylistic/quote-props': 'off',
      '@stylistic/padded-blocks': 'off',
      'vue/comma-dangle': 'off',
      'vue/first-attribute-linebreak': 'off'
    }
  }
)
