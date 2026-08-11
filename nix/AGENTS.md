# Nix infrastructure

The flake is intentionally small. It packages the locked Python application and
provides the same formatter, linter, and test entry points used outside Nix.

## Layout

- `flake.nix` owns inputs, supported systems, and explicit module imports.
- `modules/python.nix` translates `uv.lock` into runtime and development
  environments with uv2nix. The package and checks use immutable source; the
  development environment points to the live checkout.
- `modules/dev/shells.nix` defines `nix develop`.
- `modules/dev/checks.nix` defines the Python portion of `nix flake check`.
- `modules/dev/treefmt.nix` defines `nix fmt`.

## Commands

```sh
nix develop
nix run . -- --help
nix build
nix fmt
nix flake check
```

## Boundaries

- `pyproject.toml` and `uv.lock` are the Python dependency authorities. Do not
  duplicate application dependencies as hand-written nixpkgs lists.
- Python 3.12 is the compatibility floor and the version used for the Nix build.
- `.pre-commit-config.yaml` owns Git hooks; Nix only supplies the required tools.
- `.github/workflows/ci.yml` owns hosted CI. Flake checks mirror its Python gates
  for people and systems that use Nix.
- Add container, Kubernetes, or deployment tooling only when a project phase
  actually introduces and tests it.
- Keep module imports explicit. At this repository's size, auto-discovery hides
  more than it helps.
- Update `flake.lock` intentionally and review input changes.
