# GISDeploy for QGIS

Publish QGIS layers as a web map your clients open from one link, and keep that
link when the data changes.

[GISDeploy](https://gisdeploy.com) is a hosted map service operated by Aquedra
SpA. This plugin is free software (GPL-2.0-or-later). Using it requires a
GISDeploy account: the free plan includes 1 project, 5 layers and 2 GB, and paid
plans add capacity, branding and team work. Works with QGIS 3.22 or later,
including QGIS 4.

## What it does

- **Publish**: upload vector and raster layers (memory layers included) to a
  GISDeploy project. Vectors are sent as a zipped GeoPackage, rasters as
  GeoTIFF; files above 48 MiB upload in chunks with progress. Each layer is
  served as WMS/WMTS once processed.
- **Share**: *Publish map…* creates a web map that opens in any browser without
  an account, and opens its link.
- **Update without new links**: the dock shows each layer as `untracked`,
  `modified` (feature-level hash for vectors, file hash for rasters) or
  `synced`. Pushing a modified layer creates a new revision with your message;
  the map link and the WMS/WMTS addresses stay the same. Identical content is
  deduplicated server-side.
- **History and rollback**: every layer keeps a revision log (number, message,
  author, date). Rolling back creates a *new* revision with the earlier content,
  so nothing is deleted.
- **Styles**: on push the plugin exports the layer style as SLD and applies it
  to the hosted layer. SLD does not cover every QGIS symbology option.
- **Pull**: add any layer of the project back to QGIS as WMS. Private layers
  authenticate with your access token.

Server-side, a failed push never breaks the published layer: the previous
revision is restored automatically and keeps serving.

## Install

- **QGIS Plugin Manager**: *Plugins → Manage and Install Plugins*, search for
  GISDeploy.
- **From GISDeploy**: open *Dashboard → QGIS & API → Connect QGIS*, download
  the plugin ZIP, then in QGIS choose *Plugins → Manage and Install Plugins →
  Install from ZIP* and select the archive without extracting it.
- **From source**: `scripts/package.sh` builds
  `dist/gisdeploy-plugin-<version>.zip`; install it with *Install from ZIP*.

Flatpak QGIS (`org.qgis.qgis`):

```bash
unzip dist/gisdeploy-plugin-*.zip \
  -d ~/.var/app/org.qgis.qgis/data/QGIS/QGIS3/profiles/default/python/plugins/
```

Restart QGIS and enable **GISDeploy** in the Plugin Manager.

## Connect

1. In the GISDeploy dashboard, open **QGIS & API → Create access token** and
   choose the **Full (upload + maps)** preset (`agt_…`). Project-scoped tokens
   work too and confine the plugin to that project.
2. In QGIS, open the GISDeploy dock (globe toolbar icon) → **Connect…** → enter
   the server URL (`https://gisdeploy.com`) and the token.

Credentials are stored in QGIS's encrypted authentication database, protected by
its master password. Preferences and saved `.qgz` layer sources contain only an
`authcfg` reference. Reopening a shared project requires a local authentication
configuration; credentials do not travel with the project.

On upgrade from 0.1.0, the plugin removes its legacy plaintext token
preference. Reconnect with a newly issued token. Embedded credentials in loaded
legacy GISDeploy layers are removed; re-add private layers and save the project.
Revoke the old token if it was previously saved in a project or backup.

## Typical flow

1. Load or edit a layer in QGIS.
2. GISDeploy dock → the *Local* tab shows it as `untracked` (or `modified`).
3. **Push…** → pick layers, write a message → the plugin exports, uploads, waits
   for processing and applies your style.
4. **Publish map…** in the *Remote* tab → the web map opens; send its link to
   your client.
5. Edit the data and push again: the same link shows the new revision.
   **History…** lists revisions and **Rollback to selected** restores one.
6. **Add to QGIS** loads any layer of the project back as WMS.

## Data and privacy

Layers you push are uploaded to the GISDeploy server you configure
(`https://gisdeploy.com` by default) and stored there. Layers are private by
default; a public map link opens without login, so share it only with the
people who should see the map. The plugin talks only to the configured server
and never sends your token anywhere else.

The server keeps a bounded number of revisions per layer (Free 2, Basic 5,
Pro 20, Business 50, Enterprise unlimited), and retained revisions count toward
the storage quota.

## Languages

The interface follows the QGIS language: English and Spanish. To add or update
a translation, run `python3 scripts/update_translations.py` (it needs `lrelease`
from the Qt linguist tools), translate the new entries in
`gisdeploy/i18n/gisdeploy_<lang>.ts` and run it again to compile the `.qm`.

## Development

`gisdeploy/core/` is pure Python (stdlib only, no QGIS imports) and holds the
API client, hashing and sync-state logic. `gisdeploy/qgis_io/` is the thin layer
that touches the QGIS API (exports, custom properties, background tasks, WMS
URIs, authentication); `gisdeploy/ui/` is the dock and dialogs.

```bash
python3 -m pytest -q tests                                        # unit tests
python3 scripts/update_translations.py --check                    # .ts current and complete
QT_QPA_PLATFORM=offscreen python3 scripts/smoke_headless.py       # loads the plugin in QGIS
```

`scripts/e2e_live.py` runs the whole flow against a live GISDeploy server with
a test token; see its docstring.

`smoke_headless.py` needs the QGIS Python bindings. To check QGIS 4 (Qt6)
compatibility, run it, and the official migration checker, inside the
`ghcr.io/qgis/pyqgis4-checker:main-ubuntu` image (QGIS 4 with Qt6):

```bash
docker run --rm -v "$PWD":/src:ro ghcr.io/qgis/pyqgis4-checker:main-ubuntu bash -c \
  'cp -r /src /tmp/p && cd /tmp/p && pyqt5_to_pyqt6.py --dry_run --logfile /tmp/log gisdeploy; cat /tmp/log;
   QT_QPA_PLATFORM=offscreen python3 scripts/smoke_headless.py'
```

## Español

GISDeploy para QGIS publica tus capas como un mapa web que tus clientes abren
desde un enlace, y ese enlace no cambia cuando actualizas los datos. Sube capas
vectoriales y ráster con su estilo, crea el mapa con *Publicar mapa…*, guarda
cada actualización como una versión con su mensaje y restaura versiones
anteriores sin cambiar el enlace ni las direcciones WMS/WMTS. La interfaz está
en español cuando QGIS está en español.

Necesitas una cuenta de GISDeploy (servicio de Aquedra SpA): el plan gratuito
incluye 1 proyecto, 5 capas y 2 GB, y los planes de pago agregan capacidad. Crea
tu token en el panel de GISDeploy, en **QGIS y API → Crear token de acceso**
(perfil **Completo**), y pégalo en **Conectar…** dentro del panel del
complemento. El token se guarda en la base de datos de autenticación cifrada de
QGIS.

## License

Copyright (C) 2026 Aquedra SpA.

This plugin is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version. See [gisdeploy/LICENSE](gisdeploy/LICENSE). The GISDeploy service
itself is not covered by this license.
