# Housekeeper on TrueNAS SCALE

TrueNAS SCALE 24.10 ("Electric Eel") and newer uses a Docker-based app system.
Housekeeper installs cleanly as a **Custom App**.

## Prerequisites

1. A published Docker image, e.g. `ghcr.io/nickym/housekeeper:latest`.
   (Build it yourself with `docker build -t housekeeper .` and push, or use a
   public image once it's available.)
2. A dataset on TrueNAS for persistent state, e.g.
   `tank/apps/housekeeper/data`. Create it under **Datasets → Add Dataset**.

## Install via the Apps UI

1. **Apps → Discover Apps → Custom App** (top-right).
2. Fill in:
   - **Application Name**: `housekeeper`
   - **Image Repository**: `ghcr.io/nickym/housekeeper`
   - **Image Tag**: `latest`
3. **Container Configuration**
   - **Container Entrypoint**: leave blank (the image's CMD is correct).
   - **Container Environment Variables**:
     - `HOUSEKEEPER_DATA_DIR` = `/data`
     - `TZ` = your zone, e.g. `Europe/Copenhagen`
4. **Networking → Port Forwarding**
   - **Container Port**: `8765`
   - **Node Port**: `30876` (or any free port ≥ 9000 per TrueNAS conventions)
   - **Protocol**: TCP
5. **Storage → Add Storage**
   - **Type**: Host Path
   - **Mount Path**: `/data`
   - **Host Path**: `/mnt/tank/apps/housekeeper/data` (your dataset)
   - **Read-only**: off
6. **Save** and wait for the app to reach `running`.

Open `http://<truenas-ip>:30876/` and go to **Settings** to enter your Radarr,
Sonarr, TMDB and (optionally) Plex details. They're stored in SQLite under
`/data/housekeeper.db` on the dataset you mounted, so they survive app updates,
re-installs, and container restarts.

## Notes

- **Permissions**: the container runs as root by default, which is fine for a
  TrueNAS-managed dataset. If you set the dataset ACL to a non-root owner, also
  set the **Run As User / Group** under "User and Group Configuration" so the
  app can write to `/data`.
- **Reaching Radarr/Sonarr/Plex on the same host**: from the app's perspective
  `localhost` is the container itself, not the TrueNAS host. Use the TrueNAS
  LAN IP (e.g. `http://192.168.1.50:7878`) in the Settings page.
- **Updates**: bump the tag (or re-pull `:latest`) under
  **Apps → Installed → housekeeper → Edit**. Your data is preserved because
  it lives on the mounted dataset.
