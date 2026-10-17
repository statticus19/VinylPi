# Assets and redistribution review (v4)

The dashboard icons, branding and theme artwork are incorporated from the supplied Pi snapshot for functional review. **Ownership/licensing of each dashboard design has not been established; do not publish until verified.**

The following were deliberately withheld from this public staging candidate:
- 151 numbered Pokémon sprite PNGs: third-party Pokémon imagery; requires rights review or an opt-in user-provided asset install mechanism.
- `pi_epaper/assets/uk_logo.png`: university trademark/logo; requires permission or replacement.
- `pi_epaper/assets/fonts/DejaVuSans-Oblique.ttf`: redistribution license/attribution must be verified before adding; users can install a system font or use the appropriately licensed font. No font binary is included in this package.

The E-paper code still refers to the Pokémon sprites, university logo and names file, so those optional panels are **not yet self-contained** in this public package. Missing assets may require disabling the affected panels or supplying user-owned files in a local-only directory. Do not assert these features work in a clean installation until tested.

Album covers and additional streamed artwork remain dynamic user/runtime content, not bundled assets.

See [OPTIONAL-EPAPER-ASSETS.md](OPTIONAL-EPAPER-ASSETS.md) for source links, optional direct downloads, panel switches, and DejaVu system-font setup.

## Audfprint vendor source

`pi_epaper/learning/vendor/audfprint/` is sourced from `dpwe/audfprint` at commit `cb03ba99feafd41b8874307f0f4e808a6ce34362`. Its upstream MIT `LICENSE` was fetched and added here. Review other third-party licensing before publishing.
