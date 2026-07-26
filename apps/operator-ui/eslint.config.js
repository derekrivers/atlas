import js from '@eslint/js'
import pluginQuery from '@tanstack/eslint-plugin-query'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig } from 'eslint/config'
import tseslint from 'typescript-eslint'

const sharedStateImports = new Set([
  'ApiUnreachableState',
  'EmptyCollectionState',
  'LoadingState',
  'RequestErrorState',
])
const sharedStateImportPath = '@/components/states'

function normaliseFilename(filename) {
  return filename.replaceAll('\\', '/')
}

function isViewFile(filename) {
  const normalised = normaliseFilename(filename)
  return normalised.includes('/src/features/')
}

function importedName(specifier) {
  if (
    specifier.type === 'ImportSpecifier' &&
    specifier.imported.type === 'Identifier'
  ) {
    return specifier.imported.name
  }
  return undefined
}

function propertyName(node) {
  if (node.type === 'Identifier') {
    return node.name
  }
  if (node.type === 'Literal' && typeof node.value === 'string') {
    return node.value
  }
  return undefined
}

const atlasPlugin = {
  rules: {
    'no-ad-hoc-view-states': {
      meta: {
        type: 'problem',
        messages: {
          sharedState:
            'View state primitives must be imported from @/components/states.',
        },
      },
      create(context) {
        if (!isViewFile(context.filename)) {
          return {}
        }

        return {
          ImportDeclaration(node) {
            if (node.source.value === sharedStateImportPath) {
              return
            }

            for (const specifier of node.specifiers) {
              const name = importedName(specifier)
              if (name && sharedStateImports.has(name)) {
                context.report({ node: specifier, messageId: 'sharedState' })
              }
            }
          },
        }
      },
    },
    'no-view-polling-override': {
      meta: {
        type: 'problem',
        messages: {
          polling:
            'View files must use the shared Atlas query polling policy instead of setting refetchInterval.',
        },
      },
      create(context) {
        if (!isViewFile(context.filename)) {
          return {}
        }

        return {
          Property(node) {
            if (propertyName(node.key) === 'refetchInterval') {
              context.report({ node: node.key, messageId: 'polling' })
            }
          },
        }
      },
    },
  },
}

export default defineConfig(
  {
    ignores: [
      'dist',
      'node_modules',
      'coverage',
      'src/components/ui',
      'package-lock.json',
    ],
  },
  {
    extends: [
      js.configs.recommended,
      ...tseslint.configs.recommended,
      ...pluginQuery.configs['flat/recommended'],
    ],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    plugins: {
      atlas: atlasPlugin,
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
      'no-console': 'error',
      'no-unused-vars': 'off',
      '@typescript-eslint/no-unused-vars': [
        'error',
        {
          args: 'all',
          argsIgnorePattern: '^_',
          caughtErrors: 'all',
          caughtErrorsIgnorePattern: '^_',
          destructuredArrayIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          ignoreRestSiblings: true,
        },
      ],
      '@typescript-eslint/consistent-type-imports': [
        'error',
        {
          prefer: 'type-imports',
          fixStyle: 'inline-type-imports',
          disallowTypeAnnotations: false,
        },
      ],
      'no-duplicate-imports': 'error',
      'atlas/no-ad-hoc-view-states': 'error',
      'atlas/no-view-polling-override': 'error',
    },
  }
)
