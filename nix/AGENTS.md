# Nix infrastructure

The flake is intentionally small. It packages the locked Python application and
provides the same formatter, linter, and test entry points used outside Nix.

## Layout

- `flake.nix` owns inputs, supported systems, and explicit module imports.
- `modules/python.nix` translates `uv.lock` into runtime and development
  environments with uv2nix. The package and checks use immutable source; the
  development environment points to the live checkout.
- `modules/nixos.nix` exports `nixosModules.naust` with this flake's package
  as the default `services.naust.package`.
- `nixos/naust.nix` is the NixOS module: one `Type=notify` unit per world,
  steamcmd update, Steam FHS runtime with the PlayFab libraries, event sinks
  fed through systemd credentials, a unix socket for commands under
  `/run/naust`, a localhost metrics port, drain on stop, optional pre-start
  and post-drain hooks, optional poweroff after a verified drain. Usage is in
  `docs/nixos.md`; the contract it exposes is in `docs/architecture.md`.
- `modules/dev/shells.nix` defines `nix develop`.
- `modules/dev/checks.nix` defines `nix flake check`: the Python gate, and an
  evaluation-only NixOS configuration that asserts the module's generated
  units and settings on every platform.
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
  actually introduces and tests it. The NixOS module is the single-node
  deployment introduced with Project 2 (ADR 0003).
- The module cannot be run on macOS or aarch64; keep the evaluation check
  honest and test real behaviour on an x86_64-linux host.
- Keep module imports explicit. At this repository's size, auto-discovery hides
  more than it helps.
- Update `flake.lock` intentionally and review input changes.
