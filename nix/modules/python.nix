# Build the locked uv project once, then share its runtime and development
# environments with packages, apps, checks, and the development shell.
{ inputs, ... }:
{
  perSystem =
    { pkgs, lib, ... }:
    let
      python = pkgs.python312;
      workspace = inputs.uv2nix.lib.workspace.loadWorkspace {
        workspaceRoot = ../..;
      };
      workspaceOverlay = workspace.mkPyprojectOverlay {
        sourcePreference = "wheel";
      };
      editableOverlay = workspace.mkEditablePyprojectOverlay {
        root = "$NAUST_REPO_ROOT";
      };
      pythonSet =
        (pkgs.callPackage inputs.pyproject-nix.build.packages { inherit python; }).overrideScope
          (
            lib.composeManyExtensions [
              inputs.pyproject-build-systems.overlays.default
              workspaceOverlay
            ]
          );
      runtimeEnv = pythonSet.mkVirtualEnv "naust-env" workspace.deps.default;
      checkEnv = pythonSet.mkVirtualEnv "naust-check-env" workspace.deps.all;
      editablePythonSet = pythonSet.overrideScope editableOverlay;
      developmentEnv = editablePythonSet.mkVirtualEnv "naust-dev-env" workspace.deps.all;
    in
    {
      _module.args = {
        inherit checkEnv developmentEnv;
      };

      packages = {
        default = runtimeEnv;
        naust = runtimeEnv;
      };

      apps = {
        default = {
          type = "app";
          program = "${runtimeEnv}/bin/naust";
          meta.description = "Run the Naust CLI";
        };
        naust = {
          type = "app";
          program = "${runtimeEnv}/bin/naust";
          meta.description = "Run the Naust CLI";
        };
      };
    };
}
