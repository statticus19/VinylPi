# Optional E-paper assets: install or disable

VinylPi's public source archive **does not redistribute** Pokémon graphics or the University of Kentucky logo. They are optional personal customizations. The original working Pi may retain its existing locally installed assets unchanged. Running the installer is **opt-in** and downloads images directly from third parties; their rights and terms remain applicable.

## Choose which corner panels to display

In the Pi E-paper JSON config (`pi_epaper/config.json` in a standard project layout, or the path configured in the local service), change only the existing `corner` object. To omit the third-party imagery:

```json
"corner": {"enabled": true, "seconds": 900, "uk": false, "stats": true, "sun": true, "pokemon": false}
```

Set `"pokemon": true` to show the Pokémon panel and `"uk": true` to show the UK men's basketball panel after assets are installed. Set `"corner": {"enabled": false}` to disable **all** rotating corner panels, including Last.fm statistics and sun times. Changes are read when the E-paper service starts; restart that service only on a newly installed system or in a planned maintenance window. **Do not replace the whole config with this excerpt.**

The Pokémon panel additionally checks for local sprites and a names mapping, so missing sprites will normally suppress that card automatically. The UK card does not have the same file-existence prerequisite and can render a fallback instead of its logo; turn `uk` off if you do not want that panel or its network schedule lookup.

## Optional local image downloads

Sources identified in the existing VinylPi credits:

- Classic Generation I Red/Blue Pokémon sprites: [PokeAPI sprites](https://github.com/PokeAPI/sprites), under `sprites/pokemon/versions/generation-i/red-blue/transparent/`. The project reads `pi_epaper/assets/pokemon/001.png` through `151.png`.
- Pokémon names: [PokeAPI species CSV](https://github.com/PokeAPI/pokeapi/blob/master/data/v2/csv/pokemon_species.csv). The renderer reads `pi_epaper/assets/pokemon_names.json`, mapping Pokédex numbers to names.
- UK logo: [UK Athletics source image](https://storage.googleapis.com/ukathletics-com/2023/12/4696b854-kentucky_wildcats_logo.svg_.png), saved to `pi_epaper/assets/uk_logo.png`. UK Athletics retains its trademark rights.

The included optional helper can retrieve these assets **directly into your own Pi installation** (no assets are committed to Git):

```bash
cd /path/to/VinylPi
python3 tools/install-optional-epaper-art.py --pokemon
python3 tools/install-optional-epaper-art.py --uk
# Or both: --pokemon --uk
```

The helper requires Internet access, validates that returned images are PNGs, and may need maintenance if the providers change URLs or file formats. It is not part of automated setup. **Check the relevant third-party usage rights before downloading or using these assets.** Local installation does not grant permission to republish them.

## Font is a system dependency, not an optional panel

The display relies on the DejaVu Sans family for **regular, bold, italic and bold-italic text**, even with Pokémon and UK panels disabled. On Raspberry Pi OS / Debian, install the packaged fonts:

```bash
sudo apt update
sudo apt install fonts-dejavu-core
```

This supplies `DejaVuSans.ttf`, `DejaVuSans-Bold.ttf`, `DejaVuSans-Oblique.ttf`, and `DejaVuSans-BoldOblique.ttf` at `/usr/share/fonts/truetype/dejavu/`, which the actual E-paper renderer searches as a fallback. Source: [DejaVu Fonts](https://dejavu-fonts.github.io/). The font has its own license; use the distribution package, rather than copying an unattributed font binary into VinylPi.

No Pokémon sprites, UK logo, or font binary are included in the public candidate. This installation procedure has not yet been exercised end-to-end on a clean Pi.
