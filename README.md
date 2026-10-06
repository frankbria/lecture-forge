# lecture-forge

Turn textbook chapters into Feynman-style, chalkboard-free audio lectures you can listen to while driving.

PDF / Markdown / text → episode plan → script (written, then critiqued) → ElevenLabs MP3 in your Dropbox.

**Status:** requirements defined, implementation not started. See [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md).

## Planned usage

```bash
uv sync
lecture-forge plan book.pdf --series topology   # proposes series/topology/plan.yaml; edit it
lecture-forge write topology --all              # script + critique per episode; review script.txt
lecture-forge render topology --all             # MP3s to D:\Dropbox\Lectures\topology
lecture-forge run chapter3.pdf --series topo3 --auto   # everything, no stops
```

The writing rules are in [prompts/style-guide.md](prompts/style-guide.md).
