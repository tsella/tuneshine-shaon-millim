#!/usr/bin/env python3
"""Offline tests for shaon_millim.py.

    python3 test_shaon_millim.py

No network and no panel needed. Every test renders in-process.
"""

import contextlib
import importlib.util
import io
import os
import socket
import struct
import sys
import unittest

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "shaon_millim", os.path.join(HERE, "shaon_millim.py"))
sm = importlib.util.module_from_spec(_spec)
sys.modules["shaon_millim"] = sm
_spec.loader.exec_module(sm)

ALL_MINUTES = [(h, m) for h in range(24) for m in range(60)]
SAMPLE = [(h, m) for h in range(24) for m in (0, 47)]


def plain(hour, minute):
    return sm.strip_nikkud(" ".join(t.text for t in sm.phrase(hour, minute)))


def ink(img, background):
    """Count pixels that are not the background."""
    raw = img.convert("RGB").tobytes()
    bg = bytes(background)
    return sum(1 for i in range(0, len(raw), 3) if raw[i:i + 3] != bg)


class Base(unittest.TestCase):
    def setUp(self):
        self.bg = (0, 0, 0)


class TestPhrase(Base):
    """The phrase table is the part a wrong edit breaks silently, so it is
    checked over every minute of the day rather than a sample."""

    def test_every_minute_produces_tokens(self):
        for hour, minute in ALL_MINUTES:
            tokens = sm.phrase(hour, minute)
            self.assertTrue(tokens, "%02d:%02d produced nothing" % (hour, minute))

    def test_no_token_is_empty(self):
        for hour, minute in ALL_MINUTES:
            for token in sm.phrase(hour, minute):
                self.assertTrue(token.text.strip(),
                                "%02d:%02d has an empty token" % (hour, minute))

    def test_no_token_carries_latin_characters_or_digits(self):
        for hour, minute in ALL_MINUTES:
            for token in sm.phrase(hour, minute):
                self.assertFalse(
                    any(ch.isascii() and ch.isalnum() for ch in token.text),
                    "%02d:%02d: %r is not Hebrew" % (hour, minute, token.text))

    def test_every_minute_ends_with_a_day_period_except_midnight(self):
        for hour, minute in ALL_MINUTES:
            tokens = sm.phrase(hour, minute)
            if hour == 0:
                self.assertNotIn("period", [t.role for t in tokens[1:]])
            else:
                self.assertIn("period", [t.role for t in tokens])

    def test_on_the_hour_has_no_minute_tokens(self):
        for hour in range(24):
            self.assertNotIn("min", [t.role for t in sm.phrase(hour, 0)])

    def test_one_minute_is_singular(self):
        self.assertEqual(plain(9, 1), "תשע ודקה בבוקר")

    def test_two_minutes_uses_the_dual(self):
        self.assertIn("שתי דקות", plain(9, 2))

    def test_half_past_uses_וחצי(self):
        self.assertIn("וחצי", plain(9, 30))

    def test_twenty_past_spells_the_tens(self):
        self.assertIn("ועשרים", plain(9, 20))

    def test_twenty_five_past_uses_the_plain_tens_plus_ones(self):
        self.assertIn("ועשרים וחמש", plain(9, 25))

    def test_midnight_is_חצות(self):
        self.assertIn("חצות", plain(0, 5))
        self.assertEqual(sm.strip_nikkud(sm.phrase(0, 5)[0].text), "חצות")

    def test_afternoon_abbreviates_only_when_the_sentence_is_long(self):
        self.assertIn("אחה״צ", plain(13, 47))
        self.assertNotIn("אחה״צ", plain(13, 20))

    def test_hours_past_twelve_are_spoken_as_twelve_hour_clock(self):
        self.assertIn("שלוש", plain(15, 0))
        self.assertNotIn("חמש עשרה", plain(15, 0))

    def test_noon_is_twelve_and_צהרים(self):
        self.assertIn("שתים עשרה", plain(12, 0))
        self.assertIn("בצהרים", plain(12, 0))

    def test_strip_nikkud_removes_every_combining_mark(self):
        for hour, minute in ALL_MINUTES:
            for token in sm.phrase(hour, minute):
                for mark in sm.NIKKUD_RE.findall(token.text):
                    self.assertNotIn(mark, sm.strip_nikkud(token.text))


class TestDayPeriod(Base):
    def test_boundaries(self):
        cases = {0: "", 1: "בַּלַּיְלָה", 4: "בַּלַּיְלָה", 5: "בַּבּוֹקֶר",
                 11: "בַּבּוֹקֶר", 12: "בַּצָּהֳרַיִם", 13: "אַחַר הַצָּהֳרַיִם",
                 16: "אַחַר הַצָּהֳרַיִם", 17: "בָּעֶרֶב", 20: "בָּעֶרֶב",
                 21: "בַּלַּיְלָה", 23: "בַּלַּיְלָה"}
        for hour, expected in cases.items():
            self.assertEqual(sm.day_period(hour), expected, "hour %d" % hour)


class TestRender(Base):
    def test_output_is_a_64px_panel(self):
        img, _ = sm.render(9, 41, {})
        self.assertEqual(img.size, (64, 64))
        self.assertEqual(img.mode, "RGB")

    def test_fit_always_seats_the_phrase(self):
        for hour, minute in SAMPLE:
            _, info = sm.render(hour, minute, {"render": {"sizing": "fit"}})
            self.assertTrue(info["fits"], "%02d:%02d did not fit" % (hour, minute))
            self.assertLessEqual(info["widest"], info["limit"])

    def test_static_at_twelve_sixteen_seats_every_sampled_phrase(self):
        cfg = {"render": {"sizing": "static", "size": 12, "line_height": 16}}
        for hour, minute in SAMPLE:
            _, info = sm.render(hour, minute, cfg)
            self.assertTrue(info["fits"], "%02d:%02d did not fit" % (hour, minute))

    def test_static_too_large_reports_that_it_does_not_fit(self):
        cfg = {"render": {"sizing": "static", "size": 40, "line_height": 44}}
        _, info = sm.render(9, 41, cfg)
        self.assertFalse(info["fits"])

    def test_info_names_the_settings_used(self):
        _, info = sm.render(9, 41, {"render": {"sizing": "static", "size": 12}})
        self.assertEqual(info["sizing"], "static")
        self.assertEqual(info["size"], 12)
        self.assertGreaterEqual(info["lines"], 1)

    def test_nikkud_puts_more_ink_on_the_panel_at_a_fixed_size(self):
        # In fit mode dropping the points lets the text grow, so the whole frame
        # can carry more ink with nikkud off. Hold the size to compare fairly.
        base = {"sizing": "static", "size": 12, "line_height": 16}
        none, _ = sm.render(9, 41, {"render": dict(base, nikkud="none")})
        full, _ = sm.render(9, 41, {"render": dict(base, nikkud="full")})
        bg = img_bg(none)
        self.assertLess(ink(none, bg), ink(full, bg))

    def test_antialias_off_uses_only_palette_colours(self):
        img, _ = sm.render(9, 41, {"render": {"antialias": False},
                                   "colors": {"preset": "oled-warm"}})
        raw = img.tobytes()
        distinct = {raw[i:i + 3] for i in range(0, len(raw), 3)}
        self.assertLessEqual(len(distinct), 6)


def img_bg(img):
    return img.getpixel((0, 0))


class TestColours(Base):
    def test_every_preset_resolves_to_four_colours(self):
        for name in sm.PRESET_NAMES:
            pal = sm.colors_from_config({"colors": {"preset": name}}, 9)
            for value in (pal.background, pal.letter, pal.nikkud, pal.period):
                self.assertEqual(len(value), 3)
                self.assertTrue(all(0 <= c <= 255 for c in value))
            self.assertNotEqual(pal.background, pal.letter, name)

    def test_custom_hex_overrides_the_preset(self):
        pal = sm.colors_from_config(
            {"colors": {"preset": "oled-warm", "letter": "#FF0000"}}, 9)
        self.assertEqual(pal.letter, (255, 0, 0))

    def test_short_hex_is_accepted(self):
        pal = sm.colors_from_config({"colors": {"letter": "#F00"}}, 9)
        self.assertEqual(pal.letter, (255, 0, 0))

    def test_unknown_preset_is_refused(self):
        with self.assertRaises(ValueError):
            sm.colors_from_config({"colors": {"preset": "nope"}}, 9)

    def test_bad_hex_is_refused(self):
        with self.assertRaises(ValueError):
            sm.colors_from_config({"colors": {"letter": "#zzz"}}, 9)


class TestRenderOptions(Base):
    def test_unknown_sizing_is_refused(self):
        with self.assertRaises(ValueError):
            sm.resolve_render({"render": {"sizing": "weird"}})

    def test_unknown_nikkud_mode_is_refused(self):
        with self.assertRaises(ValueError):
            sm.resolve_render({"render": {"nikkud": "weird"}})

    def test_defaults_match_the_documented_example(self):
        opts = sm.resolve_render({})
        self.assertEqual(opts["sizing"], "fit")
        self.assertEqual(opts["nikkud"], "color")
        self.assertEqual(opts["align"], "right")
        self.assertTrue(opts["antialias"])


class TestFont(Base):
    def test_the_bundled_font_is_present(self):
        self.assertTrue(os.path.exists(sm.FONT_PATH),
                        "missing font: %s" % sm.FONT_PATH)

    def test_a_missing_font_file_is_refused_by_name(self):
        with self.assertRaises(FileNotFoundError):
            sm.set_font("/nonexistent/font.ttf")

    def test_setting_a_font_twice_is_harmless(self):
        original = sm.FONT_PATH
        try:
            sm.set_font(original)
            self.assertEqual(sm.FONT_PATH, original)
        finally:
            sm.set_font(original)


class TestDiscovery(Base):
    """The DNS parser reads packets from other machines, so it has to survive
    malformed input as well as parse a good one."""

    @staticmethod
    def _rr(name, rtype, rdata, ttl=120):
        encoded = sm._encode_name(name)
        return (encoded
                + struct.pack(">HHIH", rtype, 1, ttl, len(rdata))
                + rdata)

    @staticmethod
    def _txt(*pairs):
        out = b""
        for pair in pairs:
            chunk = pair.encode()
            out += bytes([len(chunk)]) + chunk
        return out

    def _packet(self):
        instance = "Tuneshine 89B8._tuneshine._tcp.local"
        host = "tuneshine-89B8.local"
        records = [
            self._rr("_tuneshine._tcp.local", 12, sm._encode_name(instance)),
            self._rr(instance, 33,
                     struct.pack(">HHH", 0, 0, 80) + sm._encode_name(host)),
            self._rr(instance, 16, self._txt("deviceId=abc-123", "version=2.7.5")),
            self._rr(host, 1, bytes([192, 168, 1, 19])),
        ]
        header = struct.pack(">HHHHHH", 0, 0x8400, 0, len(records), 0, 0)
        return header + b"".join(records)

    def test_a_well_formed_answer_parses(self):
        records = sm._parse_records(self._packet())
        by_type = {}
        for record in records:
            by_type.setdefault(record["type"], []).append(record)

        self.assertEqual(len(by_type["PTR"]), 1)
        self.assertEqual(by_type["PTR"][0]["value"],
                         "Tuneshine 89B8._tuneshine._tcp.local")
        self.assertEqual(by_type["SRV"][0]["port"], 80)
        self.assertEqual(by_type["SRV"][0]["target"], "tuneshine-89B8.local")
        self.assertEqual(by_type["A"][0]["value"], "192.168.1.19")

    def test_txt_keys_are_read_from_length_prefixed_strings(self):
        # Splitting on null instead of the length byte loses every key.
        records = sm._parse_records(self._packet())
        txt = [r for r in records if r["type"] == "TXT"][0]["value"]
        self.assertEqual(txt["deviceId"], "abc-123")
        self.assertEqual(txt["version"], "2.7.5")

    def test_a_self_pointing_compression_pointer_terminates(self):
        # Byte 12 is a pointer back to byte 12. Without a jump bound this loops
        # forever; the call returning at all is the assertion.
        packet = struct.pack(">HHHHHH", 0, 0x8400, 0, 0, 0, 0) + b"\xc0\x0c"
        name, _off = sm._parse_name(packet, 12)
        self.assertEqual(name, "")

    def test_a_truncated_packet_does_not_raise(self):
        for cut in range(len(self._packet())):
            sm._parse_records(self._packet()[:cut])

    def test_a_short_packet_returns_nothing(self):
        self.assertEqual(sm._parse_records(b""), [])
        self.assertEqual(sm._parse_records(b"\x00" * 5), [])

    def test_an_instance_without_an_a_record_is_dropped(self):
        # PTR and SRV present, no A: there is no address to push to.
        instance = "Tuneshine 89B8._tuneshine._tcp.local"
        records = [
            self._rr("_tuneshine._tcp.local", 12, sm._encode_name(instance)),
            self._rr(instance, 33,
                     struct.pack(">HHH", 0, 0, 80) + sm._encode_name("nowhere.local")),
        ]
        header = struct.pack(">HHHHHH", 0, 0x8400, 0, len(records), 0, 0)
        original = sm._mdns_socket

        class FakeSock:
            def sendto(self, *a):
                return None

            def settimeout(self, *a):
                return None

            def recvfrom(self, *a):
                raise socket.timeout()

            def close(self):
                return None

        try:
            sm._mdns_socket = lambda: FakeSock()
            sm._mdns_query = lambda sock, name, qtype, wait: sm._parse_records(
                header + b"".join(records))
            self.assertEqual(sm.discover_mdns(0.01), [])
        finally:
            sm._mdns_socket = original


class TestPushUsesDiscovery(Base):
    def _run(self, found):
        seen = {}

        def fake_discover(wait=sm.MDNS_TIMEOUT):
            seen["wait"] = wait
            return found

        def fake_upload(host, payload, phrase, hhmm, label="", timeout=0):
            seen.setdefault("uploads", []).append(host)
            return "ok"

        originals = (sm.discover_mdns, sm.upload_frame)
        sm.discover_mdns, sm.upload_frame = fake_discover, fake_upload
        try:
            img = Image.new("RGB", (64, 64), (0, 0, 0))
            quiet = (contextlib.redirect_stdout(io.StringIO()),
                     contextlib.redirect_stderr(io.StringIO()))
            with quiet[0], quiet[1]:
                code = sm.push_to_devices({"respect_playback": False}, img, "x", "09:41")
        finally:
            sm.discover_mdns, sm.upload_frame = originals
        return code, seen

    def test_an_empty_device_list_discovers_and_pushes(self):
        code, seen = self._run([{"host": "10.0.0.5", "ip": "10.0.0.5",
                                 "port": 80, "name": "x", "txt": {}}])
        self.assertEqual(code, 0)
        self.assertEqual(seen["uploads"], ["10.0.0.5"])

    def test_a_non_default_port_is_kept(self):
        code, seen = self._run([{"host": "10.0.0.5:8080", "ip": "10.0.0.5",
                                 "port": 8080, "name": "x", "txt": {}}])
        self.assertEqual(code, 0)
        self.assertEqual(seen["uploads"], ["10.0.0.5:8080"])

    def test_finding_nothing_fails_without_raising(self):
        code, seen = self._run([])
        self.assertEqual(code, 1)
        self.assertNotIn("uploads", seen)

    def test_a_configured_device_skips_discovery(self):
        seen = {}

        def fake_upload(host, payload, phrase, hhmm, label="", timeout=0):
            seen["uploads"] = [host]
            return "ok"

        def explosion(wait=0):
            raise AssertionError("discovery must not run when a device is configured")

        originals = (sm.discover_mdns, sm.upload_frame)
        sm.discover_mdns, sm.upload_frame = explosion, fake_upload
        try:
            img = Image.new("RGB", (64, 64), (0, 0, 0))
            quiet = (contextlib.redirect_stdout(io.StringIO()),
                     contextlib.redirect_stderr(io.StringIO()))
            with quiet[0], quiet[1]:
                code = sm.push_to_devices({"devices": ["192.168.1.19"],
                                           "respect_playback": False}, img, "x", "09:41")
        finally:
            sm.discover_mdns, sm.upload_frame = originals
        self.assertEqual(code, 0)
        self.assertEqual(seen["uploads"], ["192.168.1.19"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
