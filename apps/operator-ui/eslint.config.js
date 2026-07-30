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
const overviewDashboardPath = '/src/features/overview/overview-dashboard.tsx'
const overviewQueryResponseSurfaces = new Map([
  ['useTicketsQuery', new Set(['tickets'])],
  ['useReviewsQuery', new Set(['reviews'])],
  ['useDependencyCriticalPathQuery', new Set(['steps', 'total_effort'])],
])
const overviewSharedSelectors = [
  {
    importPath: '@/features/tickets/ticket-board-state',
    name: 'selectTicketStatusDistribution',
  },
  {
    importPath: '@/features/tickets/ticket-board-state',
    name: 'selectTicketStatusDistributionTotal',
  },
  {
    importPath: '@/features/reviews/selectors',
    name: 'selectReviewQueueDepth',
  },
  {
    importPath: '@/features/critical-path/selectors',
    name: 'selectCriticalPathHead',
  },
  {
    importPath: '@/features/critical-path/selectors',
    name: 'selectCriticalPathTotalEffort',
  },
]
const overviewCollectionDerivationMethods = new Set([
  'filter',
  'forEach',
  'map',
  'reduce',
])
const overviewCollectionResponseProperties = new Set([
  'tickets',
  'reviews',
  'steps',
])
const overviewScalarResponseProperties = new Set([
  'total_effort',
])
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

function isOverviewDashboardFile(filename) {
  return normaliseFilename(filename).endsWith(overviewDashboardPath)
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

function unwrapChain(node) {
  return node.type === 'ChainExpression' ? node.expression : node
}

function isQueryHookName(name) {
  return name === 'useQuery' || /^use[A-Z].*Query$/.test(name)
}

export const atlasPlugin = {
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
    'overview-shared-derivations': {
      meta: {
        type: 'problem',
        messages: {
          duplicate:
            'Overview dashboard aggregates must use board, review queue, and critical path selectors instead of local derivations.',
          missing:
            'Overview dashboard must import {{name}} from {{importPath}}.',
        },
      },
      create(context) {
        if (!isOverviewDashboardFile(context.filename)) {
          return {}
        }

        const imports = new Map()
        const overviewQueryHookLocals = new Map()
        const overviewQueryHookNamespaces = new Set()
        const queryResultAliases = new Map()
        const responseDataAliases = new Map()
        const responseCollectionAliases = new Set()

        function addImport(importPath, name) {
          const names = imports.get(importPath) ?? new Set()
          names.add(name)
          imports.set(importPath, names)
        }

        function hasImport(importPath, name) {
          return imports.get(importPath)?.has(name) ?? false
        }

        function reportDuplicate(node) {
          context.report({ node, messageId: 'duplicate' })
        }

        function queryHookNameFromCall(node) {
          const expression = unwrapChain(node)
          if (expression.type !== 'CallExpression') {
            return undefined
          }

          const callee = unwrapChain(expression.callee)
          if (callee.type === 'Identifier') {
            return overviewQueryHookLocals.get(callee.name)
          }

          if (
            callee.type === 'MemberExpression' &&
            callee.object.type === 'Identifier' &&
            overviewQueryHookNamespaces.has(callee.object.name)
          ) {
            const name = propertyName(callee.property)
            if (name && overviewQueryResponseSurfaces.has(name)) {
              return name
            }
          }

          return undefined
        }

        function queryHookNameFromDataExpression(node) {
          const expression = unwrapChain(node)
          if (
            expression.type === 'Identifier' &&
            responseDataAliases.has(expression.name)
          ) {
            return responseDataAliases.get(expression.name)
          }

          if (expression.type !== 'MemberExpression') {
            return undefined
          }

          if (propertyName(expression.property) !== 'data') {
            return undefined
          }

          const object = unwrapChain(expression.object)
          if (object.type !== 'Identifier') {
            return undefined
          }

          return queryResultAliases.get(object.name)
        }

        function responseSurfaceFromExpression(node) {
          const expression = unwrapChain(node)
          if (expression.type !== 'MemberExpression') {
            return undefined
          }

          const property = propertyName(expression.property)
          if (!property) {
            return undefined
          }

          const hookName = queryHookNameFromDataExpression(expression.object)
          if (!hookName) {
            return undefined
          }

          const surfaces = overviewQueryResponseSurfaces.get(hookName)
          if (!surfaces?.has(property)) {
            return undefined
          }

          return { hookName, property }
        }

        function isOwnedResponseCollection(node) {
          const expression = unwrapChain(node)
          if (
            expression.type === 'Identifier' &&
            responseCollectionAliases.has(expression.name)
          ) {
            return true
          }

          const surface = responseSurfaceFromExpression(expression)
          return (
            surface !== undefined &&
            overviewCollectionResponseProperties.has(surface.property)
          )
        }

        function addResponseDataAlias(pattern, hookName) {
          if (pattern.type !== 'ObjectPattern') {
            return
          }

          for (const property of pattern.properties) {
            if (property.type !== 'Property') {
              continue
            }

            if (
              propertyName(property.key) === 'data' &&
              property.value.type === 'Identifier'
            ) {
              responseDataAliases.set(property.value.name, hookName)
            }
          }
        }

        return {
          'Program:exit'(node) {
            for (const selector of overviewSharedSelectors) {
              if (!hasImport(selector.importPath, selector.name)) {
                context.report({
                  node,
                  messageId: 'missing',
                  data: selector,
                })
              }
            }
          },
          ImportDeclaration(node) {
            if (typeof node.source.value !== 'string') {
              return
            }

            for (const specifier of node.specifiers) {
              const name = importedName(specifier)
              if (name) {
                addImport(node.source.value, name)
              }

              if (node.source.value === atlasQueryHooksImportPath) {
                const local = localName(specifier)
                if (local && name && overviewQueryResponseSurfaces.has(name)) {
                  overviewQueryHookLocals.set(local, name)
                }

                const namespace = namespaceName(specifier)
                if (namespace) {
                  overviewQueryHookNamespaces.add(namespace)
                }
              }
            }
          },
          VariableDeclarator(node) {
            if (!node.init) {
              return
            }

            const hookName = queryHookNameFromCall(node.init)
            if (hookName) {
              if (node.id.type === 'Identifier') {
                queryResultAliases.set(node.id.name, hookName)
              } else {
                addResponseDataAlias(node.id, hookName)
              }
              return
            }

            if (node.id.type !== 'Identifier') {
              return
            }

            const dataHookName = queryHookNameFromDataExpression(node.init)
            if (dataHookName) {
              responseDataAliases.set(node.id.name, dataHookName)
              return
            }

            if (isOwnedResponseCollection(node.init)) {
              responseCollectionAliases.add(node.id.name)
            }
          },
          CallExpression(node) {
            if (node.callee.type !== 'MemberExpression') {
              return
            }

            const method = propertyName(node.callee.property)
            if (
              method &&
              overviewCollectionDerivationMethods.has(method) &&
              isOwnedResponseCollection(node.callee.object)
            ) {
              reportDuplicate(node.callee.property)
            }
          },
          MemberExpression(node) {
            const property = propertyName(node.property)
            if (!property) {
              return
            }

            if (
              property === 'length' &&
              isOwnedResponseCollection(node.object)
            ) {
              reportDuplicate(node.property)
              return
            }

            if (
              overviewScalarResponseProperties.has(property) &&
              responseSurfaceFromExpression(node)
            ) {
              reportDuplicate(node.property)
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
      'atlas/overview-shared-derivations': 'error',
      'atlas/no-view-polling-override': 'error',
    },
  }
)
