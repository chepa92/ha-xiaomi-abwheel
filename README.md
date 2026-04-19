# Xiaomi Ab Wheel — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/chepa92/ha-xiaomi-abwheel)](https://github.com/chepa92/ha-xiaomi-abwheel/releases)

Home Assistant custom integration for the **Xiaomi/Mijia Smart Ab Wheel** (model `jfl001`, product ID `0x4B94`).

Connects via Bluetooth Low Energy using the Mi Standard Auth protocol, provides real-time workout tracking, offline record sync, and an optional Garmin Connect integration for automatic activity uploads.

## Features

- **Real-time workout data** — reps, calories, duration, frequency, breaks
- **Daily totals** — today's reps, calories, duration, and workout count (resets at midnight)
- **Last workout summary** — stats from the most recent completed session
- **Offline record sync** — automatically fetches and deletes stored workouts from the device on connect
- **Battery monitoring** — retrieved on every connection
- **Workout journal** — persistent JSON log of all workouts
- **Idle disconnect** — automatically disconnects after 5 minutes of inactivity to save device battery
- **Manual connect button** — trigger a BLE connection on demand
- **Garmin Connect sync** — optional, via the companion [garmin_hydration_sync](https://github.com/chepa92/ha-xiaomi-abwheel) integration

## Entities

### Sensors

| Sensor | Unit | Description |
|--------|------|-------------|
| Workout State | — | Current state: `idle`, `training`, `paused`, etc. |
| Workout Reps | reps | Live rep count during a session |
| Workout Calories | cal | Live calorie count |
| Workout Duration | s | Live duration |
| Workout Frequency | rpm | Current rolling frequency |
| Workout Breaks | — | Number of pauses |
| Today Reps | reps | Total reps today |
| Today Calories | cal | Total calories today |
| Today Duration | s | Total exercise time today |
| Today Workouts | — | Number of sessions today |
| Last Reps | reps | Reps in the last completed workout |
| Last Calories | cal | Calories in the last completed workout |
| Last Duration | s | Duration of the last completed workout |
| Last Avg Freq | rpm | Average frequency |
| Last Max Freq | rpm | Peak frequency |
| Last Time | timestamp | When the last workout started |
| Battery | % | Device battery level (diagnostic) |
| Offline Records | — | Pending offline records on device (diagnostic) |
| Journal Entries | entries | Total workouts in the journal file (diagnostic) |

### Binary Sensor

| Entity | Description |
|--------|-------------|
| Connected | BLE connection status |

### Buttons

| Entity | Description |
|--------|-------------|
| Connect | Trigger manual BLE connection |
| Clear Journal | Delete all entries from the local workout journal |

## Requirements

- Home Assistant **2024.12+** (tested on 2026.3)
- Bluetooth adapter accessible to HA
- Xiaomi Ab Wheel device credentials (MAC address + Mi token)

## Getting Your Token

The integration requires the BLE token from Xiaomi's cloud. You can extract it using:

1. **[Xiaomi Cloud Tokens Extractor](https://github.com/PiotrMachworski/xiaomi-cloud-tokens-extractor)** — run the script, log in with your Mi account, find the ab wheel device, and copy the 24-character hex token.
2. **Mi Home app database** — on a rooted Android device, the token is stored in the Mi Home app's local database.

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click **⋮** → **Custom repositories**
3. Add `https://github.com/chepa92/ha-xiaomi-abwheel` with category **Integration**
4. Search for **Xiaomi Ab Wheel** and install
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/xiaomi_abwheel` folder to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings** → **Devices & Services** → **Add Integration**
2. Search for **Xiaomi Ab Wheel**
3. Enter your device's MAC address (e.g. `AA:BB:CC:DD:EE:FF`) and token (24 hex characters)

The integration will auto-discover the device via Bluetooth advertisements and connect when in range.

## How It Works

### BLE Protocol

The integration implements the full Xiaomi Mi Standard Auth (LOGIN mode) protocol:

- **Authentication**: ECDH key exchange + HKDF-SHA256 + HMAC-SHA256
- **Encryption**: AES-128-CCM for all data after auth
- **MIoT Spec V2**: Property get/set, action invocation, and event subscription

### Connection Lifecycle

1. HA discovers the device via BLE advertisements (service UUID `0xFE95`)
2. On poll interval (5 min), connects if not already connected
3. Authenticates and subscribes to real-time events
4. Syncs device info and fetches/deletes offline records
5. Listens for workout events (start, real-time data, summary)
6. Disconnects after 5 minutes of no reps activity

### Events

The integration fires Home Assistant events that other integrations can listen to:

- `xiaomi_abwheel_workout_completed` — fired when a live workout finishes
- `xiaomi_abwheel_offline_workout` — fired for each offline record synced from the device

Event data includes: `reps`, `calories`, `duration`, `avg_freq`, `max_freq`, `start_time`, `end_time`.

## Troubleshooting

- **Device not found**: Ensure Bluetooth is working in HA. Check `Settings → System → Hardware` for your BLE adapter.
- **Auth failed**: Verify the token is correct (24 hex chars). Re-extract from Xiaomi cloud if needed.
- **Disconnects frequently**: This is by design — the integration disconnects after 5 min idle to save the device's battery. Press the "Connect" button or start a workout to reconnect.
- **Battery shows unknown**: Battery is read on each connection. If it shows unknown, the device hasn't connected yet.

## License

MIT

## Credits

- Protocol reverse-engineered from the Xiaomi Home Android app (BLE Standard Auth + MIoT Spec V2)
- Built for the Xiaomi/Mijia Smart Ab Wheel (jfl001) with BK3633 chip
