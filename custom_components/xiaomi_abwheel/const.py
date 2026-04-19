"""Constants for Xiaomi Ab Wheel integration."""

DOMAIN = "xiaomi_abwheel"

CONF_MAC = "mac"
CONF_TOKEN = "token"

# BLE GATT UUIDs (service 0xFE95)
CHAR_FW     = "00000004-0000-1000-8000-00805f9b34fb"
CHAR_UPNP   = "00000010-0000-1000-8000-00805f9b34fb"
CHAR_AVDTP  = "00000019-0000-1000-8000-00805f9b34fb"
CHAR_SPEC_W = "0000001a-0000-1000-8000-00805f9b34fb"
CHAR_SPEC_N = "0000001b-0000-1000-8000-00805f9b34fb"

# Auth protocol
CMD_LOGIN     = b"\x24\x00\x00\x00"
CMD_SEND_KEY  = b"\x00\x00\x00\x0b\x01\x00"
CMD_SEND_INFO = b"\x00\x00\x00\x0a\x02\x00"
RCV_RDY       = b"\x00\x00\x01\x01"
RCV_OK        = b"\x00\x00\x01\x00"
CFM_LOGIN_OK  = b"\x21\x00\x00\x00"

# MIoT Spec type IDs
T_BOOL   = 0
T_UINT8  = 1
T_INT8   = 2
T_UINT16 = 3
T_INT16  = 4
T_UINT32 = 5
T_INT32  = 6
T_UINT64 = 7
T_INT64  = 8
T_FLOAT  = 9
T_STRING = 10

SPEC_VERSION = 2

TYPE_NAMES = {
    0: "Bool", 1: "Uint8", 2: "Int8", 3: "Uint16", 4: "Int16",
    5: "Uint32", 6: "Int32", 7: "Uint64", 8: "Int64", 9: "Float", 10: "String",
}

TRAIN_STATES = {1: "start", 2: "pause", 3: "training", 4: "rest", 5: "stop"}

# HA bus events fired by this integration for other integrations to consume
EVENT_WORKOUT_COMPLETED = f"{DOMAIN}_workout_completed"
EVENT_OFFLINE_WORKOUT   = f"{DOMAIN}_offline_workout"
