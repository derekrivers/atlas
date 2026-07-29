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
const atlasQueryHooksImportPath = '@/api/query-hooks'
const tanstackQueryImportPath = '@tanstack/react-query'
const viewPollingOverrideProperties = new Set([
  'refetchInterval',
  'refetchIntervalInBackground',
])

function normaliseFilename(filename) {
  return filename.replaceAll('\\', '/')
}

function isViewFile(filename) {
  // These view rules intentionally scope to src/features. Later view tickets
  // should place views there rather than bypassing the shared-state contract.
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

function localName(specifier) {
  if (
    (specifier.type === 'ImportSpecifier' ||
      specifier.type === 'ImportDefaultSpecifier') &&
    specifier.local.type === 'Identifier'
  ) {
    return specifier.local.name
  }
  return undefined
}

function namespaceName(specifier) {
  if (
    specifier.type === 'ImportNamespaceSpecifier' &&
    specifier.local.type === 'Identifier'
  ) {
    return specifier.local.name
  }
  return undefined
}

function isRuntimeImport(node, specifier) {
  return node.importKind !== 'type' && specifier.importKind !== 'type'
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

function isQueryHookName(name) {
  return name === 'useQuery' || /^use[A-Z].*Query$/.test(name)
}

const atlasPlugin = {
  rules: {
    'no-ad-hoc-view-states': {
      meta: {
        type: 'problem',
        messages: {
          missingSharedStateImport:
            'Feature views that use Atlas query hooks must import shared state primitives from @/components/states.',
          sharedState:
            'View state primitives must be imported from @/components/states.',
        },
      },
      create(context) {
        if (!isViewFile(context.filename)) {
          return {}
        }

        const atlasQueryHookLocals = new Set()
        const atlasQueryHookNamespaces = new Set()
        const tanstackUseQueryLocals = new Set()
        let hasSharedStateImport = false
        let firstQueryHookCall

        return {
          'Program:exit'() {
            if (firstQueryHookCall && !hasSharedStateImport) {
              context.report({
                node: firstQueryHookCall,
                messageId: 'missingSharedStateImport',
              })
            }
          },
          ImportDeclaration(node) {
            if (node.source.value === sharedStateImportPath) {
              hasSharedStateImport =
                hasSharedStateImport ||
                node.specifiers.some((specifier) =>
                  isRuntimeImport(node, specifier)
                )
              return
            }

            if (node.source.value === atlasQueryHooksImportPath) {
              for (const specifier of node.specifiers) {
                if (!isRuntimeImport(node, specifier)) {
                  continue
                }

                const imported = importedName(specifier)
                const local = localName(specifier)
                if (imported && local && isQueryHookName(imported)) {
                  atlasQueryHookLocals.add(local)
                }

                const namespace = namespaceName(specifier)
                if (namespace) {
                  atlasQueryHookNamespaces.add(namespace)
                }
              }
              return
            }

            if (node.source.value === tanstackQueryImportPath) {
              for (const specifier of node.specifiers) {
                if (!isRuntimeImport(node, specifier)) {
                  continue
                }

                const imported = importedName(specifier)
                const local = localName(specifier)
                if (imported === 'useQuery' && local) {
                  tanstackUseQueryLocals.add(local)
                }
              }
              return
            }

            for (const specifier of node.specifiers) {
              const name = importedName(specifier)
              if (name && sharedStateImports.has(name)) {
                context.report({ node: specifier, messageId: 'sharedState' })
              }
            }
          },
          CallExpression(node) {
            if (firstQueryHookCall) {
              return
            }

            if (
              node.callee.type === 'Identifier' &&
              (atlasQueryHookLocals.has(node.callee.name) ||
                tanstackUseQueryLocals.has(node.callee.name))
            ) {
              firstQueryHookCall = node.callee
              return
            }

            if (
              node.callee.type === 'MemberExpression' &&
              node.callee.object.type === 'Identifier' &&
              atlasQueryHookNamespaces.has(node.callee.object.name)
            ) {
              const member = propertyName(node.callee.property)
              if (member && isQueryHookName(member)) {
                firstQueryHookCall = node.callee.property
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
            'View files must use the shared Atlas query polling policy instead of setting refetchInterval or refetchIntervalInBackground.',
        },
      },
      create(context) {
        if (!isViewFile(context.filename)) {
          return {}
        }

        return {
          Property(node) {
            if (viewPollingOverrideProperties.has(propertyName(node.key))) {
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
