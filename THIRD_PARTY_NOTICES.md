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

## Frontend dependency graph

212 locked package entries, including development/build/test dependencies and platform-specific optional packages. Each package version, license and registry source is recorded in [DEPENDENCY_LICENSES.json](docs/DEPENDENCY_LICENSES.json) and frontend/package-lock.json.

License identifiers: Apache-2.0, BSD-2-Clause, BSD-3-Clause, BlueOak-1.0.0, CC-BY-4.0, CC0-1.0, ISC, MIT, MIT-0.

React / React DOM: MIT; Vite: MIT; TypeScript: Apache-2.0; Vitest: MIT; Testing Library: MIT. Original icons are in frontend/src/icons.tsx. There are no downloaded icon packs or image libraries in the product.

## Tooling used for project assets

`caniuse-lite` packages Can I Use compatibility data under CC-BY-4.0. Attribution: Can I Use contributors; caniuse-lite package metadata credits Ben Briggs; [data source](https://github.com/Fyrd/caniuse), [package source](https://github.com/browserslist/caniuse-lite), [license](https://creativecommons.org/licenses/by/4.0/). It is a build dependency installed through npm and is not vendored in this source export. `mdn-data` uses CC0-1.0; some `lru-cache` entries use BlueOak-1.0.0. Their precise locked versions and registry locations are recorded in the dependency JSON.

GitHub Actions checkout/setup-python/setup-node are referenced by official commit SHA. They execute in CI and are not bundled into this repository.

The editable architecture was authored using draw.io primitives and exported by the local draw.io tool. draw.io itself is not redistributed. UI screenshots depict the original CampusBridge application with fictional demonstration accounts. See [asset provenance](docs/ASSETS.md).
