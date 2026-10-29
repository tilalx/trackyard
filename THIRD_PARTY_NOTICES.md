# Third-party notices

trackyard itself is licensed under the [MIT License](LICENSE). It bundles the
following third-party material in `trackyard/static/`:

| Component | Files | Copyright | License |
|---|---|---|---|
| [IBM Plex](https://github.com/IBM/plex) typeface (Sans Light/Regular/SemiBold, Mono Regular; Latin-1 subsets as published by IBM, unmodified) | `IBMPlex*-Latin1.woff2` | © 2017–2018 IBM Corp., Reserved Font Name "Plex" | SIL Open Font License 1.1, see [`IBM-Plex-OFL-1.1.txt`](trackyard/static/IBM-Plex-OFL-1.1.txt) |
| Colour token values from [Carbon Design System](https://github.com/carbon-design-system/carbon) (`@carbon/themes`, `@carbon/colors`) | `app.css` | © IBM Corp. | Apache License 2.0, see [`Carbon-Apache-2.0.txt`](trackyard/static/Carbon-Apache-2.0.txt) |

The dashboard's look and feel follows Carbon's public design guidelines. This
project is not affiliated with, sponsored by or endorsed by IBM. "IBM", "Carbon"
and "IBM Plex" are trademarks of IBM Corp.

## Runtime dependencies

Installed from PyPI, not redistributed in this repository. All are under
permissive licenses at the time of writing:

| Package | License |
|---|---|
| bleak | MIT |
| typer | MIT |
| rich | MIT |
| PyYAML | MIT |
| fastapi | MIT |
| uvicorn | BSD-3-Clause |
| httpx | BSD-3-Clause |

Transitive dependencies (starlette, pydantic, anyio, click, pyobjc on macOS, …)
are MIT, BSD, Apache-2.0, ISC or PSF licensed; `certifi` is MPL-2.0 (used
unmodified as a library).
