#!/usr/bin/env python3
"""Offline tests for shaon_millim.py.

    python3 test_shaon_millim.py

No network and no panel needed. Every test renders in-process.
"""

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import time
import socket
import struct
import sys
import unittest
from datetime import datetime

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
        return (sm._encode_name(name)
                + struct.pack(">HHIH", rtype, 1, ttl, len(rdata))
                + rdata)

    @staticmethod
    def _txt(*pairs):
        out = b""
        for pair in pairs:
            chunk = pair.encode()
            out += bytes([len(chunk)]) + chunk
        return out

    @classmethod
    def _answer(cls, *records):
        """A response header claiming `records` as its answers."""
        return (struct.pack(">HHHHHH", 0, 0x8400, 0, len(records), 0, 0)
                + b"".join(records))

    INSTANCE = "Tuneshine 89B8._tuneshine._tcp.local"
    HOST = "tuneshine-89B8.local"

    def _packet(self):
        return self._answer(
            self._rr("_tuneshine._tcp.local", 12, sm._encode_name(self.INSTANCE)),
            self._rr(self.INSTANCE, 33,
                     struct.pack(">HHH", 0, 0, 80) + sm._encode_name(self.HOST)),
            self._rr(self.INSTANCE, 16, self._txt("deviceId=abc-123", "version=2.7.5")),
            self._rr(self.HOST, 1, bytes([192, 168, 1, 19])),
        )

    def test_a_well_formed_answer_parses(self):
        records = sm._parse_records(self._packet())
        by_type = {}
        for record in records:
            by_type.setdefault(record["type"], []).append(record)

        self.assertEqual(len(by_type["PTR"]), 1)
        self.assertEqual(by_type["PTR"][0]["value"], self.INSTANCE)
        self.assertEqual(by_type["SRV"][0]["port"], 80)
        self.assertEqual(by_type["SRV"][0]["target"], self.HOST)
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

    def test_a_reserved_label_type_ends_the_name(self):
        # 0x40 is a reserved label type. Read as a length it consumes 64 bytes of
        # whatever follows and returns garbage as a label.
        packet = struct.pack(">HHHHHH", 0, 0x8400, 0, 0, 0, 0) + b"\x40" + b"A" * 20
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
        answer = self._answer(
            self._rr("_tuneshine._tcp.local", 12, sm._encode_name(self.INSTANCE)),
            self._rr(self.INSTANCE, 33,
                     struct.pack(">HHH", 0, 0, 80) + sm._encode_name("nowhere.local")),
        )

        class FakeSock:
            def close(self):
                return None

        originals = (sm._mdns_socket, sm._mdns_query)
        sm._mdns_socket = lambda: FakeSock()
        sm._mdns_query = lambda sock, names, qtype, wait, stop_on=None: sm._parse_records(answer)
        try:
            self.assertEqual(sm.discover_mdns(0.01), [])
        finally:
            sm._mdns_socket, sm._mdns_query = originals

    def test_the_a_record_follow_up_asks_for_every_missing_host_at_once(self):
        instance = self.INSTANCE
        answer = self._answer(
            self._rr("_tuneshine._tcp.local", 12, sm._encode_name(instance)),
            self._rr(instance, 33,
                     struct.pack(">HHH", 0, 0, 80) + sm._encode_name("late.local")),
        )
        calls = []

        class FakeSock:
            def close(self):
                return None

        def fake_query(sock, names, qtype, wait, stop_on=None):
            calls.append((list(names), qtype, round(wait, 3), stop_on))
            if qtype == 12:
                return sm._parse_records(answer)
            return [{"name": "late.local", "type": "A", "value": "10.0.0.9"}]

        originals = (sm._mdns_socket, sm._mdns_query)
        sm._mdns_socket = lambda: FakeSock()
        sm._mdns_query = fake_query
        try:
            found = sm.discover_mdns(4.0)
        finally:
            sm._mdns_socket, sm._mdns_query = originals

        self.assertEqual(found[0]["host"], "10.0.0.9")
        # One browse, then ONE follow-up round carrying every missing name, on a
        # budget of its own rather than the full browse timeout.
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][0], ["late.local"])
        self.assertEqual(calls[1][2], sm.MDNS_FOLLOWUP_WAIT)
        self.assertEqual(calls[1][3], "A")


class TestMdnsTimeout(Base):
    def test_a_number_is_used(self):
        self.assertEqual(sm.mdns_timeout(1.5), 1.5)
        self.assertEqual(sm.mdns_timeout("2"), 2.0)

    def test_nothing_configured_uses_the_default(self):
        self.assertEqual(sm.mdns_timeout(None), sm.MDNS_TIMEOUT)
        self.assertEqual(sm.mdns_timeout(""), sm.MDNS_TIMEOUT)

    def test_junk_warns_and_falls_back(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(sm.mdns_timeout("soon"), sm.MDNS_TIMEOUT)
        self.assertIn("not a number", err.getvalue())

    def test_zero_and_negative_fall_back(self):
        self.assertEqual(sm.mdns_timeout(0), sm.MDNS_TIMEOUT)
        self.assertEqual(sm.mdns_timeout(-3), sm.MDNS_TIMEOUT)


class TestDiscoverCli(Base):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="shaon-cli-")
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)

    def _main(self, argv, found):
        seen = {}

        def fake_discover(wait=sm.MDNS_TIMEOUT):
            seen["wait"] = wait
            return found

        original, saved_argv = sm.discover_mdns, sys.argv
        sm.discover_mdns = fake_discover
        sys.argv = ["shaon_millim.py"] + argv
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out, \
                    contextlib.redirect_stderr(io.StringIO()):
                code = sm.main()
        finally:
            sm.discover_mdns, sys.argv = original, saved_argv
        return code, out.getvalue(), seen

    def test_a_panel_found_exits_zero(self):
        code, out, _ = self._main(["--discover"], [
            {"name": "Tuneshine 89B8", "host": "192.168.1.19",
             "txt": {"version": "2.7.5"}}])
        self.assertEqual(code, 0)
        self.assertIn("192.168.1.19", out)
        self.assertIn("version=2.7.5", out)

    def test_finding_nothing_exits_one(self):
        # A wrapper script has to be able to tell a working network from a
        # blocked one.
        code, out, _ = self._main(["--discover"], [])
        self.assertEqual(code, 1)
        self.assertIn("no Tuneshine found", out)

    def test_discover_honours_mdns_timeout_from_the_config(self):
        path = os.path.join(self.tmp, "fast.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"mdns_timeout": 1.5}, fh)
        code, _out, seen = self._main(["--discover", "--config", path], [])
        self.assertEqual(code, 1)
        self.assertEqual(seen["wait"], 1.5)


class TestExampleConfig(Base):
    """config.example.json is a copy of the defaults, so it drifts silently.
    These tests make drift a failure instead of a surprise."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "config.example.json"), encoding="utf-8") as fh:
            cls.cfg = json.load(fh)

    def test_mdns_timeout_matches_the_constant(self):
        self.assertEqual(self.cfg["mdns_timeout"], sm.MDNS_TIMEOUT)

    def test_render_defaults_match_the_code(self):
        for key, expected in sm.RENDER_DEFAULTS.items():
            self.assertEqual(self.cfg["render"].get(key), expected, key)

    def test_colour_defaults_match_the_code(self):
        for key, expected in sm.DEFAULT_COLORS.items():
            self.assertEqual(self.cfg["colors"].get(key), expected, key)

    def test_the_example_has_every_documented_key(self):
        expected = {"devices", "mdns_timeout", "label", "timezone", "respect_playback",
                    "colors", "render", "fonts", "location", "latitude", "longitude",
                    "night_dim"}
        self.assertEqual(set(self.cfg), expected)

    def test_night_defaults_match_the_code(self):
        for key, expected in sm.NIGHT_DIM_DEFAULT.items():
            self.assertEqual(self.cfg["night_dim"].get(key), expected, key)

    def test_the_example_is_valid_for_the_renderer(self):
        img, info = sm.render(9, 41, {"colors": self.cfg["colors"],
                                      "render": self.cfg["render"]})
        self.assertTrue(info["fits"])


class TestPushUsesDiscovery(Base):
    def _run(self, found, config=None):
        seen = {}

        def fake_discover(wait=sm.MDNS_TIMEOUT):
            seen["discovered"] = True
            seen["wait"] = wait
            return found

        def fake_upload(host, payload, phrase, hhmm, label="", timeout=0):
            seen.setdefault("uploads", []).append(host)
            return "ok"

        originals = (sm.discover_mdns, sm.upload_frame)
        sm.discover_mdns, sm.upload_frame = fake_discover, fake_upload
        try:
            img = Image.new("RGB", (64, 64), (0, 0, 0))
            cfg = {"respect_playback": False}
            cfg.update(config or {})
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = sm.push_to_devices(cfg, img, "x", "09:41")
        finally:
            sm.discover_mdns, sm.upload_frame = originals
        return code, seen

    def test_an_empty_device_list_discovers_and_pushes(self):
        code, seen = self._run([{"name": "x", "host": "10.0.0.5", "txt": {}}])
        self.assertEqual(code, 0)
        self.assertEqual(seen["uploads"], ["10.0.0.5"])

    def test_a_non_default_port_is_kept(self):
        code, seen = self._run([{"name": "x", "host": "10.0.0.5:8080", "txt": {}}])
        self.assertEqual(code, 0)
        self.assertEqual(seen["uploads"], ["10.0.0.5:8080"])

    def test_finding_nothing_fails_without_raising(self):
        code, seen = self._run([])
        self.assertEqual(code, 1)
        self.assertNotIn("uploads", seen)

    def test_a_configured_device_skips_discovery(self):
        code, seen = self._run([], {"devices": ["192.168.1.19"]})
        self.assertEqual(code, 0)
        self.assertEqual(seen["uploads"], ["192.168.1.19"])
        self.assertNotIn("discovered", seen)

    def test_the_configured_mdns_timeout_is_used(self):
        _code, seen = self._run([{"name": "x", "host": "10.0.0.5", "txt": {}}],
                                {"mdns_timeout": 2.5})
        self.assertEqual(seen["wait"], 2.5)


class TestNightDim(Base):
    """Sunset and sunrise scale the frame's pixels. Device luminance is never touched."""

    SUN = {"sunrise": "2026-10-19T07:11", "sunset": "2026-10-19T18:09", "date": "2026-10-19"}

    def setUp(self):
        super().setUp()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        original_dir, original_file = sm.STATE_DIR, sm.SUN_STATE
        sm.STATE_DIR = self.dir.name
        sm.SUN_STATE = os.path.join(self.dir.name, "sun.json")
        self.addCleanup(setattr, sm, "STATE_DIR", original_dir)
        self.addCleanup(setattr, sm, "SUN_STATE", original_file)

    def _bright(self, at, **night):
        cfg = {"night_dim": {"enabled": True, "percent": 10, **night}}
        return sm.night_brightness(cfg, self.SUN, datetime(*at))

    def _stub_fetch(self, result):
        original = sm.fetch_sun
        calls = []

        def fake(lat, lon):
            calls.append((lat, lon))
            return result

        sm.fetch_sun = fake
        self.addCleanup(setattr, sm, "fetch_sun", original)
        return calls

    def test_daylight_is_full_brightness(self):
        for at in ((2026, 10, 19, 12, 0), (2026, 10, 19, 7, 11), (2026, 10, 19, 18, 8)):
            self.assertEqual(self._bright(at), (100, False), at)

    def test_night_is_the_configured_percent(self):
        for at in ((2026, 10, 19, 18, 9), (2026, 10, 19, 23, 30),
                   (2026, 10, 19, 0, 30), (2026, 10, 19, 7, 10)):
            self.assertEqual(self._bright(at), (10, True), at)

    def test_sunset_is_inclusive_and_sunrise_is_exclusive(self):
        self.assertEqual(self._bright((2026, 10, 19, 18, 9)), (10, True))
        self.assertEqual(self._bright((2026, 10, 19, 7, 11)), (100, False))

    def test_switching_it_off_leaves_the_frame_alone(self):
        cfg = {"night_dim": {"enabled": False, "percent": 10}}
        self.assertEqual(sm.night_brightness(cfg, self.SUN, datetime(2026, 10, 19, 23, 30)),
                         (100, False))

    def test_missing_or_unparsable_times_never_dim(self):
        cfg = {"night_dim": {"enabled": True, "percent": 10}}
        at = datetime(2026, 10, 19, 23, 30)
        self.assertEqual(sm.night_brightness(cfg, None, at), (100, False))
        self.assertEqual(sm.night_brightness(cfg, {"sunrise": None, "sunset": "x"}, at), (100, False))
        self.assertEqual(sm.night_brightness(cfg, {"sunrise": "nope", "sunset": "also"}, at),
                         (100, False))

    def test_percent_is_clamped_and_junk_falls_back_to_full(self):
        self.assertEqual(self._bright((2026, 10, 19, 23, 30), percent=-20), (0, True))
        self.assertEqual(self._bright((2026, 10, 19, 23, 30), percent=250), (100, True))
        self.assertEqual(self._bright((2026, 10, 19, 23, 30), percent="dark"), (100, False))

    def test_dimming_a_frame_scales_every_channel(self):
        img = Image.new("RGB", (1, 1))
        img.putpixel((0, 0), (200, 100, 50))
        self.assertEqual(sm.dim_frame(img, 50).getpixel((0, 0)), (100, 50, 25))
        self.assertIs(sm.dim_frame(img, 100), img)

    def test_a_heavily_dimmed_frame_still_draws_the_text(self):
        """Dimming the palette used to erase the frame: _line_tile cuts its mask at
        an absolute luminance, so a dim palette fell under it and every line was
        dropped. Dimming the finished frame keeps the geometry."""
        cfg = {"colors": {"preset": "shabbat"}, "render": {"sizing": "static", "size": 12}}
        full, _ = sm.render(0, 14, cfg, brightness=100)
        dim, _ = sm.render(0, 14, cfg, brightness=10)
        self.assertGreater(full.convert("L").getextrema()[1], 100)
        self.assertGreater(dim.convert("L").getextrema()[1], 0, "text vanished")
        self.assertLess(dim.convert("L").getextrema()[1], full.convert("L").getextrema()[1] // 3)

    def test_a_dimmed_frame_carries_less_light(self):
        cfg = {"colors": {"preset": "shabbat"}, "render": {"sizing": "static", "size": 12}}

        def total(img):
            return sum(sum(px) for px in img.convert("RGB").getdata())

        bright, _ = sm.render(9, 41, cfg, brightness=100)
        dim, info = sm.render(9, 41, cfg, brightness=20)
        self.assertEqual(info["brightness"], 20)
        self.assertLess(total(dim), total(bright) * 0.35)
        self.assertGreater(total(dim), 0, "a dim frame is not a blank frame")

    def test_todays_times_are_served_from_the_cache_without_a_request(self):
        sm.save_sun_state(dict(self.SUN))
        self._stub_fetch(self.fail)     # any fetch is a bug
        self.assertEqual(sm.sun_times({}, now=datetime(2026, 10, 19, 12, 0)), self.SUN)

    def test_a_failed_fetch_keeps_the_previous_days_times(self):
        sm.save_sun_state({"sunrise": "2026-10-18T07:07", "sunset": "2026-10-18T18:15",
                           "date": "2026-10-18", "latitude": 40.7, "longitude": -74.0,
                           "located_at": time.time()})
        self._stub_fetch(None)
        got = sm.sun_times({}, now=datetime(2026, 10, 19, 12, 0))
        self.assertEqual((got["sunrise"], got["sunset"]), ("2026-10-18T07:07", "2026-10-18T18:15"))

    def test_a_stale_cache_is_refetched_and_written_back(self):
        sm.save_sun_state({"sunrise": "2026-10-18T07:07", "sunset": "2026-10-18T18:15",
                           "date": "2026-10-18", "latitude": 40.7, "longitude": -74.0,
                           "located_at": time.time()})
        self._stub_fetch({"sunrise": "2026-10-19T07:11", "sunset": "2026-10-19T18:09",
                          "date": "2026-10-19"})
        got = sm.sun_times({}, now=datetime(2026, 10, 19, 12, 0))
        self.assertEqual(got["sunrise"], "2026-10-19T07:11")
        self.assertEqual(sm.load_sun_state()["sunrise"], "2026-10-19T07:11")

    def test_location_off_disables_the_lookup(self):
        self._stub_fetch(self.fail)
        self.assertIsNone(sm.sun_times({"location": "off"}, now=datetime(2026, 10, 19, 12, 0)))


class TestSpacing(Base):
    """Letter and word spacing. Both default to 0, which must draw exactly what the
    single-string path drew before these knobs existed."""

    CFG = {"colors": {"preset": "shabbat"},
           "render": {"sizing": "static", "size": 12, "line_height": 16}}

    def _render(self, **render):
        cfg = dict(self.CFG, render=dict(self.CFG["render"], **render))
        return sm.render(9, 41, cfg)

    def test_tracking_widens_the_line(self):
        widths = [self._render(tracking=t)[1]["widest"] for t in (0, 1, 2)]
        self.assertEqual(widths, sorted(widths))
        self.assertLess(widths[0], widths[-1])

    def test_tracking_zero_draws_the_same_pixels_as_no_option(self):
        plain, _ = self._render()
        zero, _ = self._render(tracking=0)
        self.assertEqual(list(plain.getdata()), list(zero.getdata()))

    def test_word_gap_cannot_widen_a_line_that_holds_one_word(self):
        # At 12px every word of 09:41 gets its own line, so there is no gap to widen.
        plain, plain_info = self._render()
        _gapped, gapped_info = self._render(word_gap=4)
        self.assertEqual(plain_info["widest"], gapped_info["widest"])

    def test_nonsense_spacing_is_refused(self):
        for block in ({"tracking": 99}, {"tracking": -99}, {"word_gap": 99}, {"word_gap": -99}):
            with self.assertRaises(ValueError, msg=block):
                sm.resolve_render({"render": block})

    def test_overwide_tracking_is_reported_rather_than_clipped(self):
        _img, info = self._render(tracking=6)
        self.assertGreater(info["widest"], sm.MAX_W)
        self.assertFalse(info["fits"], "an overflowing line has to say so")

    def test_margin_tightness_is_not_reported_as_a_clipping_failure(self):
        """At tracking 1, 54 of the 1440 minutes come out 60px: inside the 64px panel,
        past the 59px margin. Only the first can clip anything."""
        cfg = dict(self.CFG, render=dict(self.CFG["render"], tracking=1))
        _img, tight = sm.render(1, 8, cfg)
        self.assertEqual(tight["widest"], 60)
        self.assertTrue(tight["fits"], "60px is inside the panel")
        self.assertFalse(tight["margin_ok"], "60px is past the 59px margin")


if __name__ == "__main__":
    unittest.main(verbosity=2)
