"""BLE protocol for Xiaomi Ab Wheel — Mi Standard Auth + MIoT Spec V2."""

import asyncio
import logging
import secrets
import struct
import time

from bleak import BleakClient
from bleak_retry_connector import establish_connection

# Set by coordinator before connect() — the BLEDevice from HA's bluetooth stack
_ble_device_provider = None

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.hmac import HMAC as CryptoHMAC
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

from .const import (
    CHAR_FW, CHAR_UPNP, CHAR_AVDTP, CHAR_SPEC_W, CHAR_SPEC_N,
    CMD_LOGIN, CMD_SEND_KEY, CMD_SEND_INFO, RCV_RDY, RCV_OK, CFM_LOGIN_OK,
    T_BOOL, T_UINT8, T_INT8, T_UINT16, T_INT16,
    T_UINT32, T_INT32, T_UINT64, T_INT64, T_FLOAT, T_STRING,
    SPEC_VERSION, TYPE_NAMES, TRAIN_STATES,
)

_LOGGER = logging.getLogger(__name__)


# ── Crypto ────────────────────────────────────────────────────────────────────

def _mi_derive_key(token: bytes, salt: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=64, salt=salt,
        info=b"mible-login-info", backend=default_backend(),
    ).derive(token)


def _mi_hash(key: bytes, data: bytes) -> bytes:
    h = CryptoHMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def _calc_login_info(token: bytes, rand_key: bytes, remote_key: bytes):
    salt = rand_key + remote_key
    salt_inv = remote_key + rand_key
    derived = _mi_derive_key(token, salt)
    keys = {
        "dev_key": derived[0:16], "app_key": derived[16:32],
        "dev_iv": derived[32:36], "app_iv": derived[36:40],
    }
    return _mi_hash(keys["app_key"], salt), _mi_hash(keys["dev_key"], salt_inv), keys


class MiCipher:
    """AES-128-CCM encrypt/decrypt using session keys from Mi Standard Auth."""

    def __init__(self, keys: dict):
        self.app_key = keys["app_key"]
        self.dev_key = keys["dev_key"]
        self.app_iv = keys["app_iv"]
        self.dev_iv = keys["dev_iv"]
        self.enc_counter = 0
        self.enc_overflow = 0
        self._enc = AESCCM(self.app_key, tag_length=4)
        self._dec = AESCCM(self.dev_key, tag_length=4)

    def _nonce(self, iv, clo, chi):
        return iv + b"\x00\x00\x00\x00" + struct.pack("<HH", clo, chi)

    def encrypt(self, pt: bytes) -> bytes:
        ctr = struct.pack("<H", self.enc_counter)
        nonce = self._nonce(self.app_iv, self.enc_counter, self.enc_overflow)
        ct = self._enc.encrypt(nonce, pt, None)
        old = (self.enc_counter & 0x8000) >> 15
        self.enc_counter = (self.enc_counter + 1) & 0xFFFF
        if old != ((self.enc_counter & 0x8000) >> 15):
            self.enc_overflow = (self.enc_overflow + 1) & 0xFFFF
        return ctr + ct

    def decrypt(self, data: bytes) -> bytes | None:
        if len(data) < 6:
            return None
        ctr = struct.unpack("<H", data[0:2])[0]
        nonce = self._nonce(self.dev_iv, ctr, 0)
        try:
            return self._dec.decrypt(nonce, data[2:], None)
        except Exception:
            return None


# ── MIoT Spec V2 packet builder ──────────────────────────────────────────────

class SpecBuilder:
    """Builds MIoT BLE Spec V2 binary packets."""

    def __init__(self):
        self.serial = 0

    def _header(self, opcode: int, body: bytes) -> bytes:
        self.serial += 1
        total = 5 + len(body)
        fc = total | (SPEC_VERSION << 12)
        return struct.pack("<HHB", fc, self.serial, opcode) + body

    def proto_exchange(self) -> bytes:
        return self._header(0xF0, b"")

    def get_properties(self, props: list[tuple[int, int]]) -> bytes:
        body = struct.pack("<B", len(props))
        for siid, piid in props:
            body += struct.pack("<BH", siid, piid)
        return self._header(0x02, body)

    def do_action(self, siid: int, aiid: int, params: list | None = None) -> bytes:
        params = params or []
        body = struct.pack("<BBB", siid, aiid, len(params))
        for piid, type_id, value in params:
            type_len = (type_id << 12) | len(value)
            body += struct.pack("<HH", piid, type_len) + value
        return self._header(0x05, body)


# ── Spec response parsers ────────────────────────────────────────────────────

def _decode_value(type_id: int, raw: bytes):
    if type_id == T_BOOL:   return bool(raw[0])
    if type_id == T_UINT8:  return raw[0]
    if type_id == T_INT8:   return struct.unpack("<b", raw)[0]
    if type_id == T_UINT16: return struct.unpack("<H", raw)[0]
    if type_id == T_INT16:  return struct.unpack("<h", raw)[0]
    if type_id == T_UINT32: return struct.unpack("<I", raw)[0]
    if type_id == T_INT32:  return struct.unpack("<i", raw)[0]
    if type_id == T_UINT64: return struct.unpack("<Q", raw)[0]
    if type_id == T_INT64:  return struct.unpack("<q", raw)[0]
    if type_id == T_FLOAT:  return struct.unpack("<f", raw)[0]
    if type_id == T_STRING: return raw.decode("utf-8", errors="replace")
    return raw.hex()


def _read_piid(data, offset, version):
    if version == 1:
        return data[offset], offset + 1
    return struct.unpack("<H", data[offset:offset + 2])[0], offset + 2


def _read_params(data, offset, count, version):
    params = {}
    for _ in range(count):
        piid, offset = _read_piid(data, offset, version)
        tl = struct.unpack("<H", data[offset:offset + 2])[0]
        offset += 2
        tid = (tl >> 12) & 0xF
        vlen = tl & 0xFFF
        raw = data[offset:offset + vlen]
        offset += vlen
        params[piid] = (tid, raw, _decode_value(tid, raw))
    return params, offset


def parse_spec_packet(data: bytes) -> dict:
    fc = struct.unpack("<H", data[0:2])[0]
    return {
        "version": (fc >> 12) & 0xF,
        "total_len": fc & 0xFFF,
        "serial": struct.unpack("<H", data[2:4])[0],
        "opcode": data[4],
        "body": data[5:],
    }


def parse_action_resp(body: bytes, version: int = 2) -> tuple[int, dict]:
    resp_code = struct.unpack("<H", body[0:2])[0]
    params = {}
    if len(body) > 2:
        count = body[2]
        params, _ = _read_params(body, 3, count, version)
    return resp_code, params


def parse_event(body: bytes, version: int = 2) -> tuple[int, int, dict]:
    siid = body[0]
    eiid, offset = _read_piid(body, 1, version)
    count = body[offset]
    params, _ = _read_params(body, offset + 1, count, version)
    return siid, eiid, params


def parse_offline_summary(summary_str: str) -> list[dict]:
    records = []
    for rec in summary_str.split("_"):
        parts = rec.split(",")
        if len(parts) >= 13:
            records.append({
                "idx": int(parts[0]),
                "start_time": int(parts[1]),
                "end_time": int(parts[2]),
                "avg_freq": int(parts[3]),
                "max_freq": int(parts[4]),
                "mode": int(parts[5]),
                "reps": int(parts[6]),
                "duration": int(parts[7]),
                "calories": int(parts[8]),
                "breaks": int(parts[9]),
                "target_type": int(parts[10]),
                "target_value": int(parts[11]),
                "target_completed": int(parts[12]),
            })
    return records


def parse_realtime_event(val: str) -> dict | None:
    """Parse event.5.1 comma-separated string."""
    parts = val.split(",") if isinstance(val, str) else []
    if len(parts) < 10:
        return None
    return {
        "mode": int(parts[0]),
        "group_idx": int(parts[1]),
        "group_duration": int(parts[2]),
        "group_count": int(parts[3]),
        "total_reps": int(parts[4]),
        "calories": int(parts[5]),
        "duration": int(parts[6]),
        "frequency": int(parts[7]),
        "breaks": int(parts[8]),
        "start_time": int(parts[9]),
    }


def parse_summary_event(val: str) -> dict | None:
    """Parse event.5.2 comma-separated string."""
    parts = val.split(",") if isinstance(val, str) else []
    if len(parts) < 10:
        return None
    return {
        "mode": int(parts[0]),
        "reps": int(parts[1]),
        "calories": int(parts[2]),
        "duration": int(parts[3]),
        "avg_freq": int(parts[4]),
        "start_time": int(parts[5]),
        "end_time": int(parts[6]),
        "breaks": int(parts[7]),
        "segments": int(parts[8]),
        "max_freq": int(parts[9]),
    }


# ── Parcel protocol ──────────────────────────────────────────────────────────

async def _parcel_send(client: BleakClient, q: asyncio.Queue, char: str,
                       data: bytes) -> bool:
    chunks = [data[i:i + 18] for i in range(0, len(data), 18)]
    hdr = struct.pack("<HHH", 0, 0, len(chunks))
    await client.write_gatt_char(char, hdr, response=False)

    while True:
        msg = await asyncio.wait_for(q.get(), timeout=10)
        if msg == RCV_RDY:
            break

    for idx, chunk in enumerate(chunks):
        await client.write_gatt_char(
            char, struct.pack("<H", idx + 1) + chunk, response=False,
        )
        await asyncio.sleep(0.05)

    while True:
        msg = await asyncio.wait_for(q.get(), timeout=10)
        if msg == RCV_OK:
            return True
        if msg == b"\x00\x00\x01\x03":
            return False


async def _parcel_receive(client: BleakClient, q: asyncio.Queue, char: str,
                          timeout: float = 15) -> bytes:
    while True:
        msg = await asyncio.wait_for(q.get(), timeout=timeout)
        frm = struct.unpack("<H", msg[0:2])[0]
        if frm == 0 and len(msg) >= 6:
            expected = msg[4] | (msg[5] << 8)
            break

    if expected == 0:
        return msg

    await client.write_gatt_char(char, RCV_RDY, response=False)
    buf = b""
    for _ in range(expected):
        msg = await asyncio.wait_for(q.get(), timeout=10)
        buf += msg[2:]
    await client.write_gatt_char(char, RCV_OK, response=False)
    return buf


# ── High-level device API ────────────────────────────────────────────────────

class AbWheelDevice:
    """High-level API for the Xiaomi Ab Wheel BLE device."""

    def __init__(self, mac: str, token: bytes):
        self.mac = mac
        self.token = token
        self._client: BleakClient | None = None
        self._ble_device = None  # BLEDevice from HA bluetooth stack
        self._cipher: MiCipher | None = None
        self._builder = SpecBuilder()
        self._w_q: asyncio.Queue = asyncio.Queue()
        self._n_q: asyncio.Queue = asyncio.Queue()
        self._avdtp_q: asyncio.Queue = asyncio.Queue()
        self._upnp_q: asyncio.Queue = asyncio.Queue()
        self._connected = False
        self._event_callback = None
        self._event_task: asyncio.Task | None = None
        self.firmware: str = ""
        self.serial: str = ""

    @property
    def connected(self) -> bool:
        return self._connected and self._client is not None and self._client.is_connected

    def set_event_callback(self, callback):
        """Set callback(siid, eiid, params) for real-time events."""
        self._event_callback = callback

    def _on_w(self, _s, d):
        self._w_q.put_nowait(bytes(d))

    def _on_n(self, _s, d):
        self._n_q.put_nowait(bytes(d))

    def _on_avdtp(self, _s, d):
        self._avdtp_q.put_nowait(bytes(d))

    def _on_upnp(self, _s, d):
        self._upnp_q.put_nowait(bytes(d))

    async def connect(self) -> bool:
        """Connect, authenticate, and initialize."""
        try:
            if self._ble_device is not None:
                self._client = await establish_connection(
                    BleakClient, self._ble_device, self.mac, max_attempts=3,
                )
            else:
                self._client = BleakClient(self.mac, timeout=20)
                await self._client.connect()
            _LOGGER.info("Connected to %s", self.mac)

            # Windows BLE needs time for GATT services to settle
            await asyncio.sleep(0.8)

            # Subscribe all notification chars with retry
            for char, cb in [
                (CHAR_UPNP, self._on_upnp),
                (CHAR_AVDTP, self._on_avdtp),
                (CHAR_SPEC_N, self._on_n),
                (CHAR_SPEC_W, self._on_w),
            ]:
                for attempt in range(3):
                    try:
                        await self._client.start_notify(char, cb)
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                        await asyncio.sleep(0.5)

            # Authenticate
            keys = await self._do_auth()
            if keys is None:
                _LOGGER.error("Authentication failed")
                await self._client.disconnect()
                return False

            self._cipher = MiCipher(keys)
            self._connected = True

            # Read firmware
            fw = await self._client.read_gatt_char(CHAR_FW)
            self.firmware = bytes(fw).decode("utf-8", errors="replace").rstrip("\x00")
            await asyncio.sleep(0.3)

            # Drain announcements
            while not self._n_q.empty():
                self._n_q.get_nowait()
            while not self._w_q.empty():
                self._w_q.get_nowait()

            # Protocol exchange
            pkt = self._builder.proto_exchange()
            resp = await self._send_and_recv(pkt)
            if not resp or resp["opcode"] != 0xF0:
                _LOGGER.warning("Protocol exchange unexpected: %s", resp)

            return True

        except Exception as exc:
            _LOGGER.debug("Connection failed: %s", exc)
            if self._client:
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
            self._connected = False
            return False

    async def disconnect(self):
        """Disconnect from device."""
        self._connected = False
        if self._event_task and not self._event_task.done():
            self._event_task.cancel()
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass

    async def _do_auth(self) -> dict | None:
        """Perform Mi Standard Auth LOGIN."""
        q = self._avdtp_q
        rand_key = secrets.token_bytes(16)
        await self._client.write_gatt_char(CHAR_UPNP, CMD_LOGIN, response=False)
        await self._client.write_gatt_char(CHAR_AVDTP, CMD_SEND_KEY, response=False)

        msg = await asyncio.wait_for(q.get(), timeout=5)
        if msg != RCV_RDY:
            return None
        for idx, i in enumerate(range(0, len(rand_key), 18)):
            await self._client.write_gatt_char(
                CHAR_AVDTP, struct.pack("<H", idx + 1) + rand_key[i:i + 18],
                response=False,
            )
            await asyncio.sleep(0.05)
        if (await asyncio.wait_for(q.get(), timeout=5)) != RCV_OK:
            return None

        # Receive remote key
        msg = await asyncio.wait_for(q.get(), timeout=5)
        expected = msg[4] | (msg[5] << 8) if len(msg) >= 6 else 0
        await self._client.write_gatt_char(CHAR_AVDTP, RCV_RDY, response=False)
        remote_key = b""
        for _ in range(expected):
            msg = await asyncio.wait_for(q.get(), timeout=5)
            remote_key += msg[2:]
        await self._client.write_gatt_char(CHAR_AVDTP, RCV_OK, response=False)

        # Receive remote info
        msg = await asyncio.wait_for(q.get(), timeout=5)
        expected = msg[4] | (msg[5] << 8) if len(msg) >= 6 else 0
        await self._client.write_gatt_char(CHAR_AVDTP, RCV_RDY, response=False)
        remote_info = b""
        for _ in range(expected):
            msg = await asyncio.wait_for(q.get(), timeout=5)
            remote_info += msg[2:]
        await self._client.write_gatt_char(CHAR_AVDTP, RCV_OK, response=False)

        our_info, expected_info, keys = _calc_login_info(self.token, rand_key, remote_key)
        if remote_info != expected_info:
            return None

        # Send our info
        await self._client.write_gatt_char(CHAR_AVDTP, CMD_SEND_INFO, response=False)
        msg = await asyncio.wait_for(q.get(), timeout=5)
        if msg != RCV_RDY:
            return None
        chunks = [our_info[i:i + 18] for i in range(0, len(our_info), 18)]
        for idx, chunk in enumerate(chunks):
            await self._client.write_gatt_char(
                CHAR_AVDTP, struct.pack("<H", idx + 1) + chunk, response=False,
            )
            await asyncio.sleep(0.05)
        await asyncio.wait_for(q.get(), timeout=5)

        confirm = await asyncio.wait_for(self._upnp_q.get(), timeout=5)
        if confirm != CFM_LOGIN_OK:
            return None

        _LOGGER.info("Auth OK")
        return keys

    async def _send_and_recv(self, pkt: bytes) -> dict | None:
        """Encrypt, send via parcel on 0x001A, receive response from 0x001B."""
        enc = self._cipher.encrypt(pkt)
        ok = await _parcel_send(self._client, self._w_q, CHAR_SPEC_W, enc)
        if not ok:
            return None
        raw = await _parcel_receive(self._client, self._n_q, CHAR_SPEC_N)
        pt = self._cipher.decrypt(raw)
        if pt is None:
            return None
        return parse_spec_packet(pt)

    async def _recv_event(self, timeout: float = 15) -> dict | None:
        raw = await _parcel_receive(self._client, self._n_q, CHAR_SPEC_N, timeout=timeout)
        pt = self._cipher.decrypt(raw)
        if pt is None:
            return None
        return parse_spec_packet(pt)

    # ── Public commands ───────────────────────────────────────────────────

    async def sync_device_info(self) -> dict:
        """syncDeviceInfo(siid=3, aiid=1) → returns offline count, serial, etc."""
        ts = int(time.time())
        info_str = f"0,700,1750,30,{ts},1"
        pkt = self._builder.do_action(3, 1, [
            (1, T_STRING, info_str.encode("utf-8")),
        ])
        resp = await self._send_and_recv(pkt)
        result = {"offline_count": 0, "serial": "", "firmware": self.firmware}
        if resp and resp["opcode"] == 0x06:
            code, params = parse_action_resp(resp["body"], resp["version"])
            if code == 0:
                for piid, (tid, raw, val) in params.items():
                    if piid == 2:
                        result["offline_count"] = val
                    elif piid == 3:
                        result["serial"] = str(val)
                        self.serial = str(val)
                    elif piid == 5:
                        result["firmware"] = str(val)
                    elif piid == 6:
                        # Battery: 0=full(100%), 1=low(30%), 2=empty(5%)
                        levels = {0: 100, 1: 30, 2: 5}
                        result["battery"] = levels.get(val, 50)
        return result

    async def get_offline_records(self) -> list[dict]:
        """Retrieve all offline training summaries (loops if device paginates)."""
        all_records: list[dict] = []
        seen_idx: set[int] = set()

        for _round in range(10):  # safety cap
            # Get count
            pkt = self._builder.do_action(6, 1)
            resp = await self._send_and_recv(pkt)
            count = 0
            if resp and resp["opcode"] == 0x06:
                code, params = parse_action_resp(resp["body"], resp["version"])
                if code == 0 and 3 in params:
                    count = params[3][2]
            if count == 0:
                break

            # Get index list
            pkt = self._builder.do_action(6, 5)
            resp = await self._send_and_recv(pkt)
            idx_list_str = ""
            if resp and resp["opcode"] == 0x06:
                code, params = parse_action_resp(resp["body"], resp["version"])
                if code == 0 and 7 in params:
                    idx_list_str = str(params[7][2])
            if not idx_list_str:
                break

            await asyncio.sleep(0.3)

            # Request summaries
            pkt = self._builder.do_action(6, 2, [
                (7, T_STRING, idx_list_str.encode("utf-8")),
            ])
            resp = await self._send_and_recv(pkt)

            # Wait for event.6.1
            batch: list[dict] = []
            try:
                evt = await self._recv_event(timeout=10)
                if evt and evt["opcode"] == 0x07:
                    siid, eiid, params = parse_event(evt["body"], evt["version"])
                    if siid == 6 and eiid == 1:
                        summary_str = params.get(1, (0, b"", ""))[2]
                        if isinstance(summary_str, str):
                            batch = parse_offline_summary(summary_str)
            except asyncio.TimeoutError:
                break

            if not batch:
                break

            # Delete this batch from device immediately
            batch_ids = [r["idx"] for r in batch]
            new_records = [r for r in batch if r["idx"] not in seen_idx]
            if not new_records:
                break  # no new records — stop

            for r in new_records:
                seen_idx.add(r["idx"])
                all_records.append(r)

            await asyncio.sleep(0.3)
            await self.delete_offline_records(batch_ids)

            # If we got everything, stop
            if len(all_records) >= count:
                break

            await asyncio.sleep(0.5)

        return all_records

    async def delete_offline_records(self, idx_list: list[int]) -> bool:
        """Delete offline records from device (siid=6, aiid=4)."""
        if not idx_list:
            return True
        idx_str = ",".join(str(i) for i in idx_list)
        pkt = self._builder.do_action(6, 4, [
            (7, T_STRING, idx_str.encode("utf-8")),
        ])
        resp = await self._send_and_recv(pkt)
        if resp and resp["opcode"] == 0x06:
            code, _ = parse_action_resp(resp["body"], resp["version"])
            if code == 0:
                _LOGGER.info("Deleted offline records: %s", idx_str)
                return True
        _LOGGER.warning("Failed to delete offline records: %s", idx_str)
        return False

    async def start_exercise(self) -> bool:
        """Start free exercise and begin monitoring."""
        pkt = self._builder.do_action(4, 1, [
            (1, T_UINT8, b"\x00"),
            (3, T_STRING, b"0,0,0,0,0,0"),
        ])
        resp = await self._send_and_recv(pkt)
        if not resp or resp["opcode"] != 0x06:
            return False
        code, _ = parse_action_resp(resp["body"], resp["version"])
        if code != 0:
            return False

        await asyncio.sleep(0.3)
        pkt = self._builder.do_action(4, 2, [
            (2, T_UINT8, b"\x01"),
        ])
        resp = await self._send_and_recv(pkt)
        if resp and resp["opcode"] == 0x06:
            code, _ = parse_action_resp(resp["body"], resp["version"])
            return code == 0
        return False

    async def listen_events(self):
        """Listen for real-time events. Calls event_callback(siid, eiid, params)."""
        while self.connected:
            try:
                evt = await self._recv_event(timeout=5)
                if evt and evt["opcode"] == 0x07:
                    siid, eiid, params = parse_event(evt["body"], evt["version"])
                    if self._event_callback:
                        self._event_callback(siid, eiid, params)
            except asyncio.TimeoutError:
                pass
            except Exception as exc:
                _LOGGER.debug("Event listen error: %s", exc)
                break

    def start_event_listener(self):
        """Start background event listener task."""
        if self._event_task and not self._event_task.done():
            return
        self._event_task = asyncio.create_task(self.listen_events())
