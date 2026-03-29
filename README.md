# Film Dust Remover — Lightroom Classic Plugin

A Lightroom Classic plugin that automatically detects and removes dust particles from film scan images using computer vision. Built specifically for high-resolution scans captured with cameras like the **Sony A7IV** at near-full-frame coverage (~190 px/mm on 35 mm film).

> **Non-destructive** — your originals are never touched. A cleaned TIFF is saved alongside the original and imported into your catalog.

---

## Features

- Works with **any format Lightroom supports** — ARW, DNG, RAW, TIFF, JPEG, etc.
- Tuned for **high-resolution camera scans** (Sony A7IV and similar)
- Adjustable **sensitivity slider** (1–100) to dial in aggression
- Processes **one photo or a whole batch** at once
- **16-bit TIFF output** — full tonal precision preserved
- Only replaces detected dust pixels; all other pixels are untouched

---

## Requirements

- **Lightroom Classic** (any recent version)
- **Python 3** — [python.org/downloads](https://www.python.org/downloads/)
- **OpenCV** and **NumPy** Python packages (installed via `setup.sh`)

---

## Installation

### Step 1 — Download the plugin

**Option A: Clone with Git**
```bash
git clone https://github.com/YOUR_USERNAME/film-dust-remover.git
```

**Option B: Download ZIP**
1. Click the green **Code** button on this page
2. Choose **Download ZIP**
3. Unzip the downloaded file somewhere permanent (e.g. `~/Documents/Lightroom Plugins/`)

### Step 2 — Install Python dependencies

Open **Terminal** and run:
```bash
cd path/to/film-dust-remover
bash FilmDustRemover.lrplugin/setup.sh
```

You should see:
```
✓ Python found: python3
✓ opencv-python-headless installed
✓ numpy installed
All dependencies ready.
```

> If you see errors, make sure Python 3 is installed first from [python.org](https://www.python.org/downloads/)

### Step 3 — Add the plugin to Lightroom

1. Open **Lightroom Classic**
2. Go to **File → Plug-in Manager**
3. Click **Add**
4. Navigate to the downloaded folder and select **`FilmDustRemover.lrplugin`**
5. Click **Add Plug-in**
6. The status should show **Installed and running**

---

## Usage

1. In the **Library** module, select one or more film scan photos
2. Go to **File → Plug-in Extras → Remove Film Dust…**
3. Adjust the **Detection Sensitivity** slider:
   - `20–40` — conservative, only obvious large dust
   - `50–65` — balanced, good starting point ✓
   - `70–100` — aggressive, catches subtle specs (may catch fine grain at very high values)
4. Set an **Output Suffix** (default: `_clean`)
5. Click **Remove Dust**

The plugin will:
1. Render each photo to a 16-bit TIFF via Lightroom (applies your develop settings)
2. Run dust detection and removal
3. Save the cleaned TIFF alongside your original
4. Import it into your catalog automatically

---

## Tips

- **Start at sensitivity 50** and compare the result with the original before going higher
- Use **Compare mode** (`C` key in Library) to flip between original and cleaned
- Zoom to **100%** (`Z` key) to inspect dust removal quality
- If you see any smearing or ghosting, **lower the sensitivity**
- The `_clean` suffix keeps originals and cleaned versions easy to tell apart

---

## How It Works

The detection algorithm:

1. **Light Gaussian blur** — removes single-pixel film grain noise without blurring dust
2. **Median reference** — builds a "what this area should look like without dust" reference using a resolution-scaled median filter
3. **Difference map** — finds pixels significantly darker or brighter than their surroundings
4. **Size filtering** — keeps only regions within realistic dust-particle bounds for the scan resolution. At ~190 px/mm (Sony A7IV on 35 mm film):
   - Ignores anything < 3 px² (film grain)
   - Catches particles up to ~1.9 mm physical diameter at full sensitivity
   - Ignores anything larger (real image content)
5. **Navier-Stokes inpainting** — fills detected regions with a 4 px radius for smooth, grain-like fills with no ghosting

Hair/squiggle detection is intentionally not included — elongated shape heuristics cannot reliably distinguish a film artifact from actual hair on a subject.

---

## What May Need Work

This plugin is a work in progress and does a solid job on most film scans — but there are known situations where it still struggles:

**Portraits with fine hair, eyelashes, and eyebrows**
The biggest ongoing challenge. Even with a circularity filter in place to reject elongated shapes, certain fine facial details — particularly stray hairs across skin, thick eyelashes, or eyebrow edges — can still register as dust-like anomalies depending on the lighting and background tone of the scan. We're aware of this and it's being actively mulled over. For now, portraits with a lot of fine hair detail are best run at a lower sensitivity (30–45) or skipped until a better solution is worked out.

**Subjects with hair against light backgrounds**
Similar to the above — dark hair strands against a bright background (sky, white wall, etc.) can occasionally fool the detector into thinking they're dust. Again, lower sensitivity helps here.

Both of these come down to a hard fundamental problem: dust and fine dark detail can look nearly identical to a computer vision algorithm. Solving it properly likely means introducing some scene-awareness (knowing where faces and hair are before deciding what's dust) — which is on the roadmap.

---

## Troubleshooting

**Plugin doesn't appear in File → Plug-in Extras**
- Open Plug-in Manager and check the plugin shows a green status dot
- If there's an error, reload the plugin with the **Reload Plug-in** button
- Make sure you selected the `FilmDustRemover.lrplugin` folder (not its parent)

**"Python not found" error**
- Install Python 3 from [python.org](https://www.python.org/downloads/)
- Make sure to check **"Add Python to PATH"** during installation
- Restart Lightroom after installing

**"Missing dependencies" error**
- Run `bash FilmDustRemover.lrplugin/setup.sh` in Terminal again
- Or manually: `pip3 install opencv-python-headless numpy`

**Result looks the same as original**
- Zoom to 100% — dust removal is subtle by design
- Try increasing sensitivity
- Enable debug mask: run `dust_remover.py` directly with `--debug` to see what's being detected

---

## License

MIT License — free to use, modify, and distribute.
