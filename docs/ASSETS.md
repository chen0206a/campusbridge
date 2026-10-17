# Assets and provenance

| Asset | Source | License / verification |
|---|---|---|
| CampusBridge UI and icons | Original React/CSS/SVG implementation in frontend/src | Project MIT; no external icon/image pack |
| Architecture PNG/SVG/drawio | Original TaskPlanner/Runtime diagram authored with draw.io primitives | Project MIT; editable source included |
| New showcase captures | Local browser rendering of CampusBridge with MockModel, dynamic mode and fictional demo accounts | Project MIT; no real student records |
| Original desktop case captures | Existing Stage3 browser evidence, copied byte-for-byte when used | Project MIT; source paths and SHA in asset manifest |
| Campus knowledge | Eight original fictional campus-support texts in resources/knowledge-v2.json | MIT metadata per document; sources carry revisions and hashes |
| Fixed cases and experiment inputs | Original synthetic development/evaluation scenarios | Project MIT; original reference bytes and histories retained |

No model weights or downloaded datasets are distributed. DeepSeek results contain synthetic inputs and publicly inspectable development traces; archive checksums preserve the recorded results.

`docs/assets/showcase/manifest.json` records each captured/copied image, its SHA-256, source and use. Captures are published as JPEG or losslessly encoded PNG, as recorded in the manifest. There is no photo editing or generated image substitution.

The Release Candidate adds `campusbridge-release.drawio/png/svg`: an updated version of the project's Stage 6 diagram, authored with draw.io primitives. It shows MySQL business storage, optional SQLite development, the independent knowledge index and Redis. Original Stage 6 assets and existing screenshot bytes are retained. RC asset hashes are recorded with the acceptance evidence.

The 2026-10-17 UI refresh adds original browser JPEG captures in `docs/assets/showcase-20261017/`. Its separate manifest records byte hashes, dimensions-independent capture descriptions and synthetic demo provenance. Earlier screenshot bytes remain unchanged.
