# Hermetic equivalents of the Python checks run by GitHub Actions.
{ ... }:
{
  perSystem =
    {
      pkgs,
      checkEnv,
      ...
    }:
    {
      checks.python = pkgs.runCommand "naust-python-checks" { nativeBuildInputs = [ checkEnv ]; } ''
        export PYTHONDONTWRITEBYTECODE=1
        ruff format --no-cache --check ${../../..}/src ${../../..}/tests
        ruff check --no-cache ${../../..}/src ${../../..}/tests
        pytest -p no:cacheprovider ${../../..}/tests
        touch $out
      '';
    };
}
