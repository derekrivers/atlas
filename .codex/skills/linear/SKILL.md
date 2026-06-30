---
name: linear
description: |
  Use Symphony's `linear_graphql` client tool for raw Linear GraphQL
  operations: querying issues, reading team workflow states, and moving an
  issue between states. State transitions in this Atlas workflow are performed
  by you, the agent, via this tool.
---

# Linear GraphQL

Use this skill for all Linear reads and writes during a Symphony app-server
session. The `linear_graphql` client tool is exposed by Symphony and reuses
Symphony's configured Linear auth for the session — never read raw tokens.

## Tool input

```json
{ "query": "query or mutation document", "variables": { "optional": "object" } }
```

- One GraphQL operation per call.
- A top-level `errors` array means the operation failed even if the call returned.
- Ask only for the fields you need.

## Moving a ticket between states (the core Atlas operation)

The Atlas prompt routes by, and asks you to move to, states **by display name**
("In Progress", "PR Open", "Review Required"). Linear's `issueUpdate` mutation
takes a **`stateId` (UUID)**, not a name. Always resolve the name to its
`stateId` first — never hardcode a name inside a mutation.

1. Read the issue's team workflow states:

```graphql
query IssueTeamStates($id: String!) {
  issue(id: $id) {
    id
    team { id states { nodes { id name type } } }
  }
}
```

2. Pick the node whose `name` equals the target display name and take its `id`.
3. Move the issue:

```graphql
mutation MoveIssueToState($id: String!, $stateId: String!) {
  issueUpdate(id: $id, input: { stateId: $stateId }) {
    success
    issue { id identifier state { id name } }
  }
}
```

## Querying an issue

Use the narrowest lookup you can: key → identifier filter → internal id.

```graphql
query IssueByKey($key: String!) {
  issue(id: $key) {
    id identifier title description url
    state { id name type }
    project { id name }
  }
}
```

For an unfamiliar mutation or input type, introspect through `linear_graphql`:

```graphql
query ListMutations { __type(name: "Mutation") { fields { name } } }
```

## Comments (out-of-scope findings, blockers)

```graphql
mutation CreateComment($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) {
    success comment { id url }
  }
}
```

## Usage rules

- Use `linear_graphql` for every Linear read/write; do not introduce raw-token
  shell helpers for GraphQL access.
- For state transitions, fetch team states first and use the exact `stateId`.
- Example ticket key in this repo: `ATLAS-NN`.
