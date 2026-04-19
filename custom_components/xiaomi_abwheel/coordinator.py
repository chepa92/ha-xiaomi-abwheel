"""Data coordinator for Xiaomi Ab Wheel."""

import asyncio
import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone

from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, TRAIN_STATES, EVENT_WORKOUT_COMPLETED, EVENT_OFFLINE_WORKOUT
from .protocol import AbWheelDevice, parse_realtime_event, parse_summary_event

_LOGGER = logging.getLogger(__name__)

POLL_INTERVAL = timedelta(minutes=5)
IDLE_DISCONNECT_SECONDS = 300  # 5 minutes


class AbWheelCoordinator(DataUpdateCoordinator):
    """Coordinator that manages BLE connection and data for the Ab Wheel."""

    def __init__(self, hass: HomeAssistant, entry, mac: str, token: bytes):
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=POLL_INTERVAL, config_entry=entry)
        self.device = AbWheelDevice(mac, token)
        self._mac = mac
        self._today_date: str = date.today().isoformat()
        self._needs_sync = True  # sync device info on next successful connect
        self._last_activity: float = 0.0  # monotonic time of last reps change
        self._last_reps_seen: int = 0  # reps value at last check

        self.data = {
            "connected": False,
            "firmware": "",
            "serial": "",
            "battery": None,
            "offline_count": 0,
            "train_state": "idle",
            # Real-time workout
            "reps": 0,
            "calories": 0,
            "duration": 0,
            "frequency": 0,
            "breaks": 0,
            # Last completed workout summary
            "last_reps": 0,
            "last_calories": 0,
            "last_duration": 0,
            "last_avg_freq": 0,
            "last_max_freq": 0,
            "last_start_time": 0,
            # Today's totals (reset at midnight)
            "today_reps": 0,
            "today_calories": 0,
            "today_duration": 0,
            "today_workouts": 0,
            # Offline records
            "offline_records": [],
            # Journal
            "journal_entries": 0,
        }

    def _reset_today_if_needed(self) -> None:
        """Reset today_* counters if the date has changed."""
        today = date.today().isoformat()
        if today != self._today_date:
            self._today_date = today
            self.data["today_reps"] = 0
            self.data["today_calories"] = 0
            self.data["today_duration"] = 0
            self.data["today_workouts"] = 0

    def _add_to_today(self, reps: int, calories: int, duration: int, start_time: int) -> None:
        """Add a workout to today's totals if it happened today."""
        self._reset_today_if_needed()
        # Only count workouts that started today (local time)
        try:
            workout_date = datetime.fromtimestamp(start_time).date().isoformat()
        except (ValueError, OSError, TypeError):
            workout_date = self._today_date  # fallback: count it
        if workout_date == self._today_date:
            self.data["today_reps"] += reps
            self.data["today_calories"] += calories
            self.data["today_duration"] += duration
            self.data["today_workouts"] += 1

    async def _async_update_data(self) -> dict:
        """Periodic poll: reconnect if needed, sync info + offline."""
        self._reset_today_if_needed()

        if not self.device.connected:
            self._needs_sync = True  # full sync on reconnect
            self._last_activity = 0.0  # reset idle timer
            try:
                ble_device = async_ble_device_from_address(self.hass, self._mac, connectable=True)
                if ble_device:
                    self.device._ble_device = ble_device
                else:
                    _LOGGER.debug("BLE device %s not seen by adapter – will retry", self._mac)
                    self.data["connected"] = False
                    return self.data
                ok = await self.device.connect()
                if not ok:
                    _LOGGER.debug("BLE connect to %s returned False \u2013 will retry", self._mac)
                    self.data["connected"] = False
                    return self.data
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("BLE connect to %s failed: %s \u2013 will retry", self._mac, exc)
                self.data["connected"] = False
                return self.data

        self.data["connected"] = True

        # Check idle timeout — disconnect if no activity for 5 min
        if self._last_activity > 0:
            idle = time.monotonic() - self._last_activity
            if idle >= IDLE_DISCONNECT_SECONDS:
                _LOGGER.info(
                    "No activity for %ds — disconnecting to save battery", int(idle)
                )
                await self.device.disconnect()
                self.data["connected"] = False
                self.data["train_state"] = "idle"
                return self.data

        # Only sync device info + offline records right after (re)connect
        # to avoid stealing packets from the real-time event listener queue
        if self._needs_sync:
            self._needs_sync = False

            # Sync device info (also returns offline count)
            try:
                info = await self.device.sync_device_info()
                self.data["firmware"] = info.get("firmware", "")
                self.data["serial"] = info.get("serial", "")
                self.data["offline_count"] = info.get("offline_count", 0)
                if "battery" in info:
                    self.data["battery"] = info["battery"]
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("sync_device_info failed: %s", exc)

            # Get offline records
            if self.data["offline_count"] > 0:
                try:
                    records = await self.device.get_offline_records()
                    if records:
                        self.data["offline_records"] = records
                        synced_ids = []
                        for rec in records:
                            self.hass.bus.async_fire(EVENT_OFFLINE_WORKOUT, {
                                "idx": rec["idx"],
                                "reps": rec["reps"],
                                "calories": rec["calories"],
                                "duration": rec["duration"],
                                "avg_freq": rec["avg_freq"],
                                "max_freq": rec["max_freq"],
                                "start_time": rec["start_time"],
                                "end_time": rec["end_time"],
                            })
                            # Save offline record to journal
                            await self._save_to_journal(rec)
                            # Update "Last" sensors from offline record
                            self.data["last_reps"] = rec["reps"]
                            self.data["last_calories"] = rec["calories"]
                            self.data["last_duration"] = rec["duration"]
                            self.data["last_avg_freq"] = rec["avg_freq"]
                            self.data["last_max_freq"] = rec["max_freq"]
                            self.data["last_start_time"] = rec["start_time"]
                            # Add to today's totals
                            self._add_to_today(
                                rec["reps"], rec["calories"],
                                rec["duration"], rec["start_time"],
                            )
                            synced_ids.append(rec["idx"])
                        # Clear stale real-time values
                        self.data["reps"] = 0
                        self.data["calories"] = 0
                        self.data["duration"] = 0
                        self.data["frequency"] = 0
                        self.data["breaks"] = 0
                        self.data["train_state"] = "idle"
                        # Records already deleted by get_offline_records()
                        self.data["offline_count"] = 0
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.debug("get_offline_records failed: %s", exc)

            # Load journal entry count
            try:
                self.data["journal_entries"] = await self.hass.async_add_executor_job(
                    self._journal_count_blocking
                )
            except Exception:  # noqa: BLE001
                pass

        # Start event listener for real-time data
        self.device.set_event_callback(self._on_event)
        self.device.start_event_listener()

        # Mark connection time as initial activity
        if self._last_activity == 0.0:
            self._last_activity = time.monotonic()

        return self.data

    @callback
    def _on_event(self, siid: int, eiid: int, params: dict):
        """Handle real-time events from the device."""
        key = f"event.{siid}.{eiid}"

        if key == "event.4.1":  # Train state
            val = params.get(2, (0, b"", 0))[2]
            self.data["train_state"] = TRAIN_STATES.get(val, str(val))
            self.async_set_updated_data(self.data)

        elif key == "event.5.1":  # Realtime
            val = params.get(1, (0, b"", ""))[2]
            rt = parse_realtime_event(str(val))
            if rt:
                # Track reps changes for idle disconnect
                if rt["total_reps"] != self._last_reps_seen:
                    self._last_reps_seen = rt["total_reps"]
                    self._last_activity = time.monotonic()
                self.data["reps"] = rt["total_reps"]
                self.data["calories"] = rt["calories"]
                self.data["duration"] = rt["duration"]
                self.data["frequency"] = rt["frequency"]
                self.data["breaks"] = rt["breaks"]
                self.async_set_updated_data(self.data)

        elif key == "event.5.2":  # Summary (workout complete)
            val = params.get(1, (0, b"", ""))[2]
            sm = parse_summary_event(str(val))
            if sm:
                self.data["last_reps"] = sm["reps"]
                self.data["last_calories"] = sm["calories"]
                self.data["last_duration"] = sm["duration"]
                self.data["last_avg_freq"] = sm["avg_freq"]
                self.data["last_max_freq"] = sm["max_freq"]
                self.data["last_start_time"] = sm["start_time"]
                self.data["train_state"] = "idle"
                # Add to today's totals
                self._add_to_today(
                    sm["reps"], sm["calories"], sm["duration"], sm["start_time"]
                )
                # Reset realtime counters
                self.data["reps"] = 0
                self.data["calories"] = 0
                self.data["duration"] = 0
                self.data["frequency"] = 0
                self.data["breaks"] = 0
                self.async_set_updated_data(self.data)
                # Fire event for other integrations (e.g. Garmin sync)
                self.hass.bus.async_fire(EVENT_WORKOUT_COMPLETED, {
                    "reps": sm["reps"],
                    "calories": sm["calories"],
                    "duration": sm["duration"],
                    "avg_freq": sm["avg_freq"],
                    "max_freq": sm["max_freq"],
                    "start_time": sm["start_time"],
                    "end_time": sm["end_time"],
                })
                # Save to journal
                self.hass.async_create_task(
                    self._save_to_journal(sm), eager_start=False,
                )

        elif key == "event.3.1":  # Battery level
            val = params.get(1, (0, b"", 0))[2]
            levels = {0: 100, 1: 30, 2: 5}
            self.data["battery"] = levels.get(val, 50)
            self.async_set_updated_data(self.data)

    async def async_manual_connect(self) -> bool:
        """Trigger an immediate connection attempt (for the Connect button)."""
        _LOGGER.info("Manual connect requested for %s", self._mac)
        await self.async_request_refresh()
        return self.data.get("connected", False)

    async def async_start_exercise(self) -> bool:
        """Service to start a new exercise session."""
        if not self.device.connected:
            return False
        return await self.device.start_exercise()

    # ── Journal ───────────────────────────────────────────────────────────

    @property
    def _journal_path(self) -> str:
        mac_clean = self._mac.replace(":", "").upper()
        return self.hass.config.path(f"xiaomi_abwheel_journal_{mac_clean}.json")

    def _journal_write_blocking(self, record: dict) -> int:
        """Append record to journal JSON (blocking – run in executor)."""
        path = self._journal_path
        journal: list[dict] = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    journal = json.load(f)
            except (json.JSONDecodeError, OSError):
                journal = []
        # Dedup by start_time
        existing = {str(r.get("start_time")) for r in journal}
        if str(record.get("start_time")) in existing:
            return len(journal)
        journal.append(record)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(journal, f, ensure_ascii=False, indent=2)
        return len(journal)

    async def _save_to_journal(self, workout: dict) -> None:
        """Save a completed workout to the journal file."""
        try:
            st = int(float(workout.get("start_time", 0)))
            dt = datetime.fromtimestamp(st) if st else datetime.now()
            training_date = dt.strftime("%Y-%m-%d")
            training_time = dt.strftime("%H:%M:%S")
        except (ValueError, OSError, TypeError):
            training_date = date.today().isoformat()
            training_time = ""

        record = {
            "training_date": training_date,
            "training_time": training_time,
            "start_time": workout.get("start_time"),
            "end_time": workout.get("end_time"),
            "reps": workout.get("reps", 0),
            "calories": workout.get("calories", 0),
            "duration_sec": workout.get("duration", 0),
            "avg_freq": workout.get("avg_freq", 0),
            "max_freq": workout.get("max_freq", 0),
            "mode": workout.get("mode", 0),
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            total = await self.hass.async_add_executor_job(
                self._journal_write_blocking, record
            )
            self.data["journal_entries"] = total
            _LOGGER.debug("Journal: saved workout, total=%d", total)
        except Exception as exc:
            _LOGGER.warning("Journal save failed: %s", exc)

    async def async_clear_journal(self) -> None:
        """Delete the journal file."""
        try:
            await self.hass.async_add_executor_job(os.remove, self._journal_path)
            _LOGGER.info("Journal cleared: %s", self._journal_path)
        except FileNotFoundError:
            pass
        except Exception as exc:
            _LOGGER.warning("Journal clear failed: %s", exc)
        self.data["journal_entries"] = 0
        self.async_set_updated_data(self.data)

    def _journal_count_blocking(self) -> int:
        """Count existing journal entries (blocking)."""
        path = self._journal_path
        if not os.path.exists(path):
            return 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                journal = json.load(f)
            return len(journal)
        except (json.JSONDecodeError, OSError):
            return 0

    async def async_shutdown(self):
        """Disconnect on HA shutdown."""
        await self.device.disconnect()
