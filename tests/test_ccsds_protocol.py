import struct

import pytest

from orbitlink import protocol as P
from orbitlink.ccsds import Deframer, DecodeError, Frame, PacketType, SpacePacket, crc16, split_packets

# Frame produced by the flight software's C++ encoder (ccsds::encode_packet +
# encode_frame) for a PING TC, APID 0x064, seq 1234, frame count 7.
FLIGHT_VECTOR = "1acffc1d02c50007000d1064c4d2000607deadbeef8cb52e54"


def test_crc_check_value():
    assert crc16(b"123456789") == 0x29B1


def test_bit_exact_with_flight_encoder():
    pkt = SpacePacket(apid=0x064, seq=1234, data=bytes([0x07, 0xDE, 0xAD, 0xBE, 0xEF]), ptype=PacketType.TC)
    frame = Frame(P.SPACECRAFT_ID, 0, 7, pkt.encode()).encode()
    assert frame.hex() == FLIGHT_VECTOR


def test_packet_roundtrip_and_errors():
    pkt = SpacePacket(apid=0x010, seq=0x3FFF, data=b"\x01\x02\x03")
    raw = pkt.encode()
    back, n = SpacePacket.decode(raw)
    assert n == len(raw) and back == pkt
    for bit in range(len(raw) * 8):
        bad = bytearray(raw)
        bad[bit // 8] ^= 1 << (bit % 8)
        with pytest.raises(DecodeError):
            SpacePacket.decode(bytes(bad))
    with pytest.raises(ValueError):
        SpacePacket(apid=0x800, seq=0, data=b"").encode()


def test_deframer_handles_noise_split_and_corruption():
    good = Frame(P.SPACECRAFT_ID, 0, 1, b"hello").encode()
    bad = bytearray(Frame(P.SPACECRAFT_ID, 0, 2, b"world").encode())
    bad[12] ^= 0x01
    stream = b"\x00\x1a\xcf" + good + bytes(bad) + good
    d = Deframer()
    frames = []
    for i in range(0, len(stream), 5):  # arbitrary chunking
        frames += d.feed(stream[i : i + 5])
    assert [f.data for f in frames] == [b"hello", b"hello"]
    assert d.stats.crc_errors == 1


def test_split_packets_stops_at_garbage():
    a = SpacePacket(P.APID_HK, 1, b"\x00" * 4).encode()
    b = SpacePacket(P.APID_ACK, 2, b"\x01" * 10).encode()
    pkts = split_packets(a + b + b"\xff\xff")
    assert [p.apid for p in pkts] == [P.APID_HK, P.APID_ACK]


def test_hk_roundtrip_and_flags():
    fields = {"time_ms": 123456, "mode": 2, "batt_v": 7.412, "current_a": 1.27, "temp_c": -3.25,
              "imu_flags": 0b1101, "fdir_flags": 0b1001, "boot_count": 3, "reset_cause": 1,
              "lat_deg": -33.8688, "lon_deg": 151.2093}
    hk = P.decode_hk(P.build_hk(fields))
    assert hk["mode_name"] == "PAYLOAD"
    assert hk["batt_v"] == pytest.approx(7.412)
    assert hk["temp_c"] == pytest.approx(-3.25)
    assert hk["lat_deg"] == pytest.approx(-33.8688, abs=1e-6)
    assert hk["imu_attitude_valid"] and hk["imu_active"] == "B"
    assert hk["fdir_active"] == ["undervoltage", "i2c_fault"]
    assert hk["reset_cause_name"] == "watchdog"


def test_command_args_encoding():
    assert P.command_args("set_mode", {"mode": "payload"}) == (P.Func.SET_MODE, b"\x02")
    func, args = P.command_args("SET_PARAM", {"id": 2, "value": 1000})
    assert func == P.Func.SET_PARAM and args == struct.pack(">Bi", 2, 1000)
    assert P.command_args("power_rail", {"rail": "gnss", "on": False})[1] == b"\x03\x00"
    with pytest.raises(ValueError):
        P.command_args("self_destruct", {})
    with pytest.raises(ValueError):
        P.command_args("capture", {"frames": 0})


def test_ack_codec():
    ack = P.Ack(P.APID_TC_CORE, 77, P.Func.PING, P.AckStatus.OK, 0xCAFEBABE)
    assert P.decode_ack(P.encode_ack(ack)) == ack
