Bundled third-party packages (pure Python, unmodified) so the report builds with no `pip install`:

| Package | Version | Licence |
|---|---|---|
| openpyxl | 3.1.5 | MIT — licenses/openpyxl-LICENCE.rst |
| et_xmlfile | 2.0.0 | MIT — licenses/et_xmlfile-LICENCE.rst, licenses/et_xmlfile-LICENCE (PSF, for code derived from Python) |

The scripts use an installed openpyxl first and fall back to this copy only if none is installed.
