# Vellum application authoring

Use this skill for the installed Vellum project lifecycle. Its contract is
`vellum.agent-instructions.v1`; the adjacent `manifest.v1.json` is the
machine-readable authority.

## Before changing an application

1. Read `app.toml`, `framework.lock`, `AGENTS.md`, and
   `.vellum/agent-instructions.md`.
2. Run `vellum --json doctor --fix`. `--fix` creates safe project-local
   cache/state directories and materializes the exact SDK-provided UI package;
   it does not install system software.
3. Inspect the JSON `ok`, `status`, `diagnostics`, and capability checks. If a
   requested capability is unavailable, stop and report it. Do not bypass the
   lock, invent output, or patch framework code into the application.

Install only an immutable SDK artifact with its matching checksum manifest:

```sh
./scripts/install.sh --archive "$sdk_archive" --checksums "$sdk_checksums" --install-dir "$prefix"
```

Do not treat a local-development install as verified or assume that a hosted
release exists.

## Lifecycle

Use JSON mode for automation and preserve the exact artifact identity written
to the project lock.

```sh
vellum --json create "My App" --directory "$app" --template basic
vellum --json create "Imported App" --directory "$app" --template basic --from figma "$figma_export" --as main
vellum --json doctor --fix --project "$app"
vellum --json import "$figma_export" --source-type figma --as main --project "$app"
vellum --json reimport --source "$updated_export" --as main --project "$app"
vellum --json build --target macos --project "$app"
vellum --json run --target macos --project "$app"
vellum --json test --scenario smoke --project "$app"
vellum --json capture --scenario smoke --output "$app/artifacts/smoke.png" --target macos --project "$app"
vellum --json capture --matrix tests/capture-matrix.json --montage --output artifacts/montage.png --target macos --project "$app"
vellum --json package --target macos --output "$app/dist" --project "$app"
```

The only supported import source types are `figma` and `design-ir`. The first
means Vellum's bounded, credential-free plugin-export JSON or its actual
`.pulp.zip` container, not an arbitrary design file. Pass the ZIP directly; do
not unpack it yourself. Vellum retains the archive and scene as immutable
snapshots and binds revision identity to the archive SHA-256. Consult conversion
diagnostics after every import or reimport. Do not invent another route or
silently discard unsupported properties.

For editable native UI, use controlled `TextInput` v1 from `@vellum/ui` with a
stable ID, string `value`, and `onChange`. Scenarios may use bounded `input` and
named `key` actions; they are retained-tree actions, not DOM or arbitrary
keyboard automation. To persist the versioned whole-app snapshot on macOS,
explicitly set `persistence = "state-v1"` in `[capabilities]` and keep
`createApp({id,stateVersion,...})` stable. Do not claim IME, selection/caret,
clipboard editing, migration, database, sync, accessibility text, or mobile
support from this lane.

## Ownership and maintenance

- Tool-owned: `framework.lock`, `sources/imported/`, `design/ir/`, `design/generated/`,
  `tokens/imported/`, `assets/generated/`, and `ui/generated/`. Change these
  only by import/reimport.
- Developer-owned: `app.toml`, `package.json`, `package-lock.json`, `src/`,
  `components/`, `design/overlays/`, editable theme
  tokens and assets, `native/`, `platforms/`, `tests/`, and `packaging/`.
- Keep behavior, state, navigation, services, and overrides in developer-owned
  files. Preserve stable imported identities and review orphan/conflict reports.
- After reimport, inspect the diff, run interaction scenarios, capture affected
  screens, and package only after tests pass.
- When the framework needs a fix, change Vellum in its own repository, verify
  and publish a new immutable SDK artifact, then update the application lock.
  Never vendor or patch framework internals inside an application.

`capability_unavailable` with exit code 4 is an honest terminal result for that
operation. A developer or agent may install the required verified artifact, or
report the missing capability; it must not substitute a different runtime.
