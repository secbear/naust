# `nix fmt` covers every language currently present in the repository.
{ ... }:
{
  perSystem =
    { ... }:
    {
      treefmt = {
        projectRootFile = "flake.nix";
        programs = {
          nixfmt.enable = true;
          ruff-check.enable = true;
          ruff-format.enable = true;
          shfmt.enable = true;
        };
      };
    };
}
