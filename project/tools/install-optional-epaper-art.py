#!/usr/bin/env python3
"""Opt-in retrieval of third-party visual art for a personal E-paper installation.

Artwork is fetched from its original providers; it is NOT bundled with VinylPi.
Run on the user's own machine with network access. Respect third-party terms.
"""
import argparse
import csv
import io
import json
from pathlib import Path
from urllib.request import Request, urlopen

POKEMON_NAMES = "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/pokemon_species.csv"
SPRITE_BASE = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/versions/generation-i/red-blue/transparent"
UK_LOGO = "https://storage.googleapis.com/ukathletics-com/2023/12/4696b854-kentucky_wildcats_logo.svg_.png"

def get(url):
    with urlopen(Request(url, headers={"User-Agent": "VinylPi-optional-asset-installer/1.0"}), timeout=25) as response:
        return response.read()

def install_pokemon(assets):
    # English species names are provided through PokeAPI's CSV.
    data = get(POKEMON_NAMES).decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(data)))
    names = {str(int(row["id"])):row["identifier"].replace("-", " ").title()
             for row in rows if row.get("id", "").isdigit() and 1 <= int(row["id"]) <= 151}
    if len(names) != 151:
        raise RuntimeError("Source did not contain all 151 species; no names file was written")
    images = assets / "pokemon"
    images.mkdir(parents=True, exist_ok=True)
    ok = 0
    for n in range(1, 152):
        try:
            payload = get(f"{SPRITE_BASE}/{n}.png")
            if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("response was not a PNG")
            (images / f"{n:03d}.png").write_bytes(payload)
            ok += 1
        except Exception as exc:
            print(f"Sprite {n} unavailable: {type(exc).__name__}")
    (assets / "pokemon_names.json").write_text(json.dumps(names,indent=2)+"\n", encoding="utf-8")
    print(f"Retrieved {ok}/151 Pokémon sprites into {images}")
    if ok != 151:
        print("WARNING: Some sprites are missing; retry when online")

def install_uk(assets):
    payload = get(UK_LOGO)
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("UK logo response was not a PNG")
    (assets / "uk_logo.png").write_bytes(payload)
    print("Retrieved UK logo into", assets / "uk_logo.png")

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--assets-dir", type=Path, default=Path(__file__).resolve().parents[1]/"pi_epaper/assets")
    p.add_argument("--pokemon",action="store_true")
    p.add_argument("--uk",action="store_true")
    args=p.parse_args()
    if not (args.pokemon or args.uk):
        p.error("Choose --pokemon, --uk, or both")
    args.assets_dir.mkdir(parents=True, exist_ok=True)
    if args.pokemon: install_pokemon(args.assets_dir)
    if args.uk: install_uk(args.assets_dir)

if __name__ == "__main__": main()
