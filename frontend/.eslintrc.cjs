// ESLint 配置（eslint 8 flat-config 兼容形式）
module.exports = {
  root: true,
  env: { browser: true, es2022: true, node: true },
  parser: '@typescript-eslint/parser',
  parserOptions: {
    ecmaVersion: 'latest',
    sourceType: 'module',
    ecmaFeatures: { jsx: true },
  },
  settings: {
    react: { version: 'detect' },
  },
  plugins: ['@typescript-eslint', 'react-hooks', 'react-refresh'],
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
  ],
  rules: {
    // React Hooks 必须遵守调用规则
    'react-hooks/rules-of-hooks': 'error',
    'react-hooks/exhaustive-deps': 'warn',
    // Vite HMR 允许 export default const
    'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
    // 未使用变量告警（开发阶段不阻断，_ 前缀忽略）
    '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    // any 用 warn 而非 error（存量代码有 any，逐步收敛）
    '@typescript-eslint/no-explicit-any': 'warn',
    // 允许 ts 指令注释
    '@typescript-eslint/ban-ts-comment': 'warn',
    '@typescript-eslint/no-empty-function': 'off',
    '@typescript-eslint/no-empty-interface': 'off',
  },
  ignorePatterns: ['dist', 'node_modules', 'src-tauri', '*.config.ts', 'vite-env.d.ts'],
}
