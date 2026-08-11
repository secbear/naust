# Small shell for working on the Python application and its Nix wrapper.
{ ... }:
{
  perSystem =
    {
      pkgs,
      developmentEnv,
      ...
    }:
    {
      devShells.default = pkgs.mkShell {
        name = "naust";
        packages = [
          developmentEnv
          pkgs.uv
          pkgs.nixfmt
          pkgs.treefmt
        ];
        env = {
          PYTHONDONTWRITEBYTECODE = "1";
          UV_NO_SYNC = "1";
          UV_PYTHON_DOWNLOADS = "never";
        };
        shellHook = ''
          unset PYTHONPATH
          export NAUST_REPO_ROOT="$(git rev-parse --show-toplevel)"
          echo "naust dev shell: pytest | ruff check . | nix flake check"
        '';
      };
    };
}
