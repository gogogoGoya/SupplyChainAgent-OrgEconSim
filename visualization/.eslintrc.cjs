module.exports = {
  root: true,
  env: {browser: true, es2022: true, node: true},
  parserOptions: {ecmaVersion: 'latest', sourceType: 'module', ecmaFeatures: {jsx: true}},
  plugins: ['react', 'react-hooks'],
  settings: {react: {version: 'detect'}},
  ignorePatterns: ['dist/', 'node_modules/'],
  rules: {'no-unreachable': 'error', 'no-dupe-args': 'error', 'no-dupe-keys': 'error',
    'no-constant-condition': ['error',{checkLoops:false}], 'react-hooks/rules-of-hooks':'error'},
};
