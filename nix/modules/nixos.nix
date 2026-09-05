# Expose the NixOS module, defaulting its package to this flake's build.
{ self, ... }:
{
  flake.nixosModules = {
    naust =
      { pkgs, lib, ... }:
      {
        imports = [ ../nixos/naust.nix ];
        services.naust.package = lib.mkDefault self.packages.${pkgs.stdenv.hostPlatform.system}.naust;
      };
    default = self.nixosModules.naust;
  };
}
