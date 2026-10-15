# Third-party notices

CampusBridge original code, Prompts, synthetic datasets, UI icons and diagrams are licensed under MIT. Dependencies are installed through their package managers and retain their upstream licenses. The public source export contains no vendored dependency code, fonts, model weights or third-party training corpus.

## Python locked dependencies

| Package | Version | License |
|---|---|---|
| [annotated-doc](https://pypi.org/project/annotated-doc/0.0.5/) | 0.0.5 | MIT |
| [annotated-types](https://pypi.org/project/annotated-types/0.8.0/) | 0.8.0 | MIT |
| [anyio](https://pypi.org/project/anyio/4.15.1/) | 4.15.1 | MIT |
| [certifi](https://pypi.org/project/certifi/2026.7.22/) | 2026.7.22 | MPL-2.0 |
| [click](https://pypi.org/project/click/8.5.0/) | 8.5.0 | BSD-3-Clause |
| [colorama](https://pypi.org/project/colorama/0.4.6/) | 0.4.6 | BSD-3-Clause |
| [fastapi](https://pypi.org/project/fastapi/0.141.1/) | 0.141.1 | MIT |
| [greenlet](https://pypi.org/project/greenlet/3.5.6/) | 3.5.6 | MIT AND PSF-2.0 |
| [h11](https://pypi.org/project/h11/0.16.0/) | 0.16.0 | MIT |
| [httpcore](https://pypi.org/project/httpcore/1.0.9/) | 1.0.9 | BSD-3-Clause |
| [httpx](https://pypi.org/project/httpx/0.28.1/) | 0.28.1 | BSD-3-Clause |
| [idna](https://pypi.org/project/idna/3.20/) | 3.20 | BSD-3-Clause |
| [iniconfig](https://pypi.org/project/iniconfig/2.3.1/) | 2.3.1 | MIT |
| [packaging](https://pypi.org/project/packaging/26.3/) | 26.3 | Apache-2.0 OR BSD-2-Clause |
| [pluggy](https://pypi.org/project/pluggy/1.6.0/) | 1.6.0 | MIT |
| [pydantic](https://pypi.org/project/pydantic/2.13.5/) | 2.13.5 | MIT |
| [pydantic_core](https://pypi.org/project/pydantic_core/2.46.5/) | 2.46.5 | MIT |
| [Pygments](https://pypi.org/project/Pygments/2.21.0/) | 2.21.0 | BSD-2-Clause |
| [pytest](https://pypi.org/project/pytest/9.1.1/) | 9.1.1 | MIT |
| [SQLAlchemy](https://pypi.org/project/SQLAlchemy/2.0.54/) | 2.0.54 | MIT |
| [starlette](https://pypi.org/project/starlette/1.7.0/) | 1.7.0 | BSD-3-Clause |
| [typing-inspection](https://pypi.org/project/typing-inspection/0.4.4/) | 0.4.4 | MIT |
| [typing_extensions](https://pypi.org/project/typing_extensions/4.16.0/) | 4.16.0 | PSF-2.0 |
| [uvicorn](https://pypi.org/project/uvicorn/0.52.4/) | 0.52.4 | BSD-3-Clause |

certifi uses MPL-2.0; greenlet uses MIT AND PSF-2.0; packaging uses Apache-2.0 OR BSD-2-Clause. Their installed distributions include upstream notices. The project MIT license applies to CampusBridge code.

## Optional local RAG dependencies and model

BGE-small-zh-v1.5 is published by BAAI under MIT: [official model card](https://huggingface.co/BAAI/bge-small-zh-v1.5). Stage B pins revision `7999e1d3359715c523056ef9478215996d62a620`. Weights and upstream model files are downloaded separately and are excluded from the repository. Their upstream notices remain in the external snapshot.

Optional runtime packages are pinned in `requirements-rag.lock`; [installed metadata and upstream links](docs/RAG_DEPENDENCIES.json) record the complete added package set. Primary packages: Torch (BSD-3-Clause), Transformers and Hugging Face Hub (Apache-2.0), NumPy and psutil (BSD-3-Clause). Transitive packages retain their own notices, including regex (Apache-2.0 AND CNRI-Python) and tqdm (MPL-2.0 AND MIT). The base dependency lock is unchanged; these distributions are installed, not vendored.

## Business database dependencies

Stage D1 adds Alembic 1.20.0 (MIT), Mako 1.4.3 (MIT) and MarkupSafe 3.0.3 (BSD-3-Clause) to the base lock. Optional MySQL dependencies are in requirements-mysql.lock: PyMySQL (MIT), cryptography (Apache-2.0 OR BSD-3-Clause), cffi (MIT) and pycparser (BSD-3-Clause). [Installed metadata](docs/DATABASE_DEPENDENCIES.json) records upstream links and versions. The locally installed MySQL Community Server 8.0.42 used for acceptance retains its GPL license and is not bundled in the source repository.

## Optional Redis infrastructure

Stage D2 uses redis-py 7.4.1 (MIT), pinned in requirements-redis.lock. [Upstream client](https://github.com/redis/redis-py). Redis Server is operated separately and is not distributed with CampusBridge. Local D2 acceptance used the Ubuntu Redis 7.0.15 binary (BSD-3-Clause) in an external temporary directory; production operators should choose a maintained security-patched server distribution and review its applicable license. No server binaries, snapshots or credentials are included here.

## Frontend dependency graph

212 locked package entries, including development/build/test dependencies and platform-specific optional packages. Each package version, license and registry source is recorded in [DEPENDENCY_LICENSES.json](docs/DEPENDENCY_LICENSES.json) and frontend/package-lock.json.

License identifiers: Apache-2.0, BSD-2-Clause, BSD-3-Clause, BlueOak-1.0.0, CC-BY-4.0, CC0-1.0, ISC, MIT, MIT-0.

React / React DOM: MIT; Vite: MIT; TypeScript: Apache-2.0; Vitest: MIT; Testing Library: MIT. Original icons are in frontend/src/icons.tsx. There are no downloaded icon packs or image libraries in the product.

## Tooling used for project assets

`caniuse-lite` packages Can I Use compatibility data under CC-BY-4.0. Attribution: Can I Use contributors; caniuse-lite package metadata credits Ben Briggs; [data source](https://github.com/Fyrd/caniuse), [package source](https://github.com/browserslist/caniuse-lite), [license](https://creativecommons.org/licenses/by/4.0/). It is a build dependency installed through npm and is not vendored in this source export. `mdn-data` uses CC0-1.0; some `lru-cache` entries use BlueOak-1.0.0. Their precise locked versions and registry locations are recorded in the dependency JSON.

GitHub Actions checkout/setup-python/setup-node are referenced by official commit SHA. They execute in CI and are not bundled into this repository.

The editable architecture was authored using draw.io primitives and exported by the local draw.io tool. draw.io itself is not redistributed. UI screenshots depict the original CampusBridge application with fictional demonstration accounts. See [asset provenance](docs/ASSETS.md).
