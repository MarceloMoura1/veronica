"""Standalone physical audio-output probe for Windows/PortAudio.

Run outside Electron and Veronica:
    python audio_output_probe.py
"""

from __future__ import annotations

import argparse
import math
import re
import struct
import time

import pyaudio


TONE_HZ = 440
TONE_SECONDS = 3
PAUSE_SECONDS = 2
AMPLITUDE = int(32767 * 0.25)
REALTEK_FORMATS = (
    (24000, 1, "24k mono"),
    (44100, 2, "44.1k stereo"),
    (48000, 2, "48k stereo"),
)


TESTS = (
    (1, "Default PortAudio", None, None, 24000, 1),
    (2, "Default PortAudio", None, None, 44100, 1),
    (3, "Default PortAudio", None, None, 44100, 2),
    (4, "HyperX Cloud III", "hyperx cloud", "MME", 24000, 1),
    (5, "HyperX Cloud III", "hyperx cloud", "MME", 44100, 2),
    (6, "HyperX Cloud III", "hyperx cloud", "Windows DirectSound", 24000, 1),
    (7, "HyperX Cloud III", "hyperx cloud", "Windows DirectSound", 44100, 2),
    (8, "Realtek HD Audio — Headphones / 2nd output", "realtek hd audio 2nd output", "MME", 24000, 1),
    (9, "Realtek HD Audio — Headphones / 2nd output", "realtek hd audio 2nd output", "MME", 44100, 2),
    (10, "Realtek HD Audio — Headphones / 2nd output", "realtek hd audio 2nd output", "Windows DirectSound", 24000, 1),
    (11, "Realtek HD Audio — Headphones / 2nd output", "realtek hd audio 2nd output", "Windows DirectSound", 44100, 2),
)


def host_api_name(audio: pyaudio.PyAudio, device_info: dict) -> str:
    host_index = int(device_info["hostApi"])
    return str(audio.get_host_api_info_by_index(host_index).get("name", "unknown"))


def find_output(audio: pyaudio.PyAudio, name_fragment: str, wanted_host: str) -> dict:
    matches = []
    for index in range(audio.get_device_count()):
        info = audio.get_device_info_by_index(index)
        if int(info.get("maxOutputChannels", 0)) <= 0:
            continue
        host = host_api_name(audio, info)
        if name_fragment.casefold() in str(info.get("name", "")).casefold() and host.casefold() == wanted_host.casefold():
            matches.append(info)
    if len(matches) != 1:
        descriptions = [f"index={int(item['index'])} name={item.get('name')!r}" for item in matches]
        raise RuntimeError(
            f"expected one {wanted_host} output containing {name_fragment!r}; "
            f"found {len(matches)}: {descriptions}"
        )
    return matches[0]


def tone_pcm(rate: int, channels: int) -> bytes:
    frames = bytearray()
    for frame in range(rate * TONE_SECONDS):
        sample = int(AMPLITUDE * math.sin(2 * math.pi * TONE_HZ * frame / rate))
        packed = struct.pack("<h", sample)
        frames.extend(packed * channels)
    return bytes(frames)


def supports_format(audio: pyaudio.PyAudio, raw_index: int, rate: int, channels: int) -> tuple[bool, str]:
    try:
        audio.is_format_supported(
            rate,
            output_device=raw_index,
            output_channels=channels,
            output_format=pyaudio.paInt16,
        )
        return True, "supported"
    except Exception as exc:
        return False, f"unsupported ({type(exc).__name__}: {exc})"


def realtek_outputs(audio: pyaudio.PyAudio) -> list[dict]:
    outputs = []
    for index in range(audio.get_device_count()):
        info = audio.get_device_info_by_index(index)
        if int(info.get("maxOutputChannels", 0)) <= 0:
            continue
        if "realtek" not in str(info.get("name", "")).casefold():
            continue
        outputs.append(info)
    return outputs


def browser_label_correspondence(raw_name: str, browser_labels: list[str]) -> list[str]:
    """Report textual overlap only; never assert that browser and PortAudio identities are equal."""
    raw_words = set(re.findall(r"[a-z0-9]+", raw_name.casefold()))
    matches = []
    for label in browser_labels:
        label_words = set(re.findall(r"[a-z0-9]+", label.casefold()))
        # Connector-significant terms must agree. Generic "Realtek Audio" overlap
        # must not equate Speakers with the separate Headphones/2nd output.
        if ("2nd" in raw_words) != ("2nd" in label_words):
            continue
        if ("speaker" in raw_words or "speakers" in raw_words) and "2nd" in label_words:
            continue
        if {"realtek", "audio"}.issubset(raw_words & label_words):
            matches.append(label)
    return matches


def print_realtek_inventory(
    audio: pyaudio.PyAudio, outputs: list[dict], browser_labels: list[str]
) -> dict[int, list[tuple[int, int, str]]]:
    print("\n=== BROWSER OUTPUTS (supplied from navigator.mediaDevices) ===", flush=True)
    if browser_labels:
        for label in browser_labels:
            print(f"- {label}", flush=True)
    else:
        print("(none supplied; use --browser-label once per real browser label)", flush=True)

    supported_by_device = {}
    print("\n=== PORTAUDIO REALTEK OUTPUTS ===", flush=True)
    for info in outputs:
        raw_index = int(info["index"])
        host_index = int(info["hostApi"])
        host_info = audio.get_host_api_info_by_index(host_index)
        default_index = int(host_info.get("defaultOutputDevice", -1))
        supported = []
        print(f"\nraw_index={raw_index}", flush=True)
        print(f"raw_name={info.get('name')}", flush=True)
        print(f"host_api={host_info.get('name', 'unknown')}", flush=True)
        print(f"maxOutputChannels={int(info.get('maxOutputChannels', 0))}", flush=True)
        print(f"defaultSampleRate={info.get('defaultSampleRate')}", flush=True)
        print(f"host_api_default={raw_index == default_index}", flush=True)
        for rate, channels, label in REALTEK_FORMATS:
            ok, detail = supports_format(audio, raw_index, rate, channels)
            print(f"pcm_int16_{label.replace(' ', '_')}={detail}", flush=True)
            if ok:
                supported.append((rate, channels, label))
        overlaps = browser_label_correspondence(str(info.get("name", "")), browser_labels)
        print(f"browser_label_text_matches={overlaps or 'none/unknown'}", flush=True)
        supported_by_device[raw_index] = supported
    return supported_by_device


def play_realtek_tests(
    audio: pyaudio.PyAudio, outputs: list[dict], supported_by_device: dict[int, list[tuple[int, int, str]]]
) -> None:
    tests = []
    for info in outputs:
        raw_index = int(info["index"])
        supported = supported_by_device[raw_index]
        preferred = next((item for item in supported if item[:2] == (24000, 1)), None)
        formats = [preferred] if preferred else [item for item in supported if item[:2] in {(44100, 2), (48000, 2)}]
        tests.extend((info, item) for item in formats if item is not None)

    for position, (info, (rate, channels, _label)) in enumerate(tests, start=1):
        raw_index = int(info["index"])
        print(f"\n=== REALTEK TEST {position} ===", flush=True)
        print(f"raw_index={raw_index}", flush=True)
        print(f"raw_name={info.get('name')}", flush=True)
        print(f"host_api={host_api_name(audio, info)}", flush=True)
        print(f"rate={rate}", flush=True)
        print(f"channels={channels}", flush=True)
        stream = None
        try:
            stream = audio.open(
                format=pyaudio.paInt16, channels=channels, rate=rate,
                output=True, output_device_index=raw_index,
            )
            print(f"open=success active={stream.is_active()}", flush=True)
            pcm = tone_pcm(rate, channels)
            stream.write(pcm)
            print(f"write=success bytes={len(pcm)}", flush=True)
        except Exception as exc:
            print(f"ERROR open/write: {type(exc).__name__}: {exc}", flush=True)
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                except Exception as exc:
                    print(f"stop_error={type(exc).__name__}: {exc}", flush=True)
                try:
                    stream.close()
                except Exception as exc:
                    print(f"close_error={type(exc).__name__}: {exc}", flush=True)
        if position < len(tests):
            print(f"Waiting {PAUSE_SECONDS} seconds before next test...", flush=True)
            time.sleep(PAUSE_SECONDS)


def run_test(audio: pyaudio.PyAudio, spec: tuple, *, list_only: bool) -> None:
    number, label, fragment, wanted_host, rate, channels = spec
    if fragment is None:
        info = audio.get_default_output_device_info()
    else:
        info = find_output(audio, fragment, wanted_host)
    raw_index = int(info["index"])
    actual_host = host_api_name(audio, info)

    print(f"\n=== TEST {number} ===", flush=True)
    print(f"Device: {label}", flush=True)
    print(f"Raw name: {info.get('name')}", flush=True)
    print(f"Raw index: {raw_index}", flush=True)
    print(f"Host API: {actual_host}", flush=True)
    print(f"Rate: {rate}", flush=True)
    print(f"Channels: {channels}", flush=True)
    print(f"Default sample rate: {info.get('defaultSampleRate')}", flush=True)
    print(f"Max output channels: {info.get('maxOutputChannels')}", flush=True)

    if list_only:
        print("Result: listed only (no playback)", flush=True)
        return

    stream = None
    try:
        audio.is_format_supported(
            rate,
            output_device=raw_index,
            output_channels=channels,
            output_format=pyaudio.paInt16,
        )
        kwargs = {
            "format": pyaudio.paInt16,
            "channels": channels,
            "rate": rate,
            "output": True,
        }
        # Tests 1-3 intentionally let PortAudio choose its default output.
        if fragment is not None:
            kwargs["output_device_index"] = raw_index
        stream = audio.open(**kwargs)
        print(f"Open: success; active={stream.is_active()}", flush=True)
        print(
            f"Mode: blocking; frames_per_buffer=unspecified; "
            f"output_latency={stream.get_output_latency()}", flush=True,
        )
        pcm = tone_pcm(rate, channels)
        frames = len(pcm) // (2 * channels)
        available_before = stream.get_write_available()
        stream_time_before = stream.get_time()
        write_started = time.perf_counter()
        stream.write(pcm)
        elapsed = time.perf_counter() - write_started
        print(f"Write: success; bytes={len(pcm)}", flush=True)
        print(
            f"Write timing: frames={frames}; expected={frames / rate:.3f}s; "
            f"elapsed={elapsed:.6f}s; stream_time_before={stream_time_before}; "
            f"stream_time_after={stream.get_time()}; "
            f"write_available_before={available_before}; "
            f"write_available_after={stream.get_write_available()}", flush=True,
        )
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", flush=True)
    finally:
        if stream is not None:
            try:
                stream.stop_stream()
            except Exception as exc:
                print(f"Stop error: {type(exc).__name__}: {exc}", flush=True)
            try:
                stream.close()
            except Exception as exc:
                print(f"Close error: {type(exc).__name__}: {exc}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Play controlled tones through current PortAudio outputs.")
    parser.add_argument("--list-only", action="store_true", help="Resolve and print all endpoints without playing tones.")
    parser.add_argument("--realtek-only", action="store_true", help="Inventory and probe every raw Realtek output.")
    parser.add_argument(
        "--browser-label", action="append", default=[],
        help="Real audiooutput label from navigator.mediaDevices; repeat for multiple labels.",
    )
    args = parser.parse_args()
    audio = pyaudio.PyAudio()
    try:
        if args.realtek_only:
            outputs = realtek_outputs(audio)
            supported = print_realtek_inventory(audio, outputs, args.browser_label)
            if not outputs:
                print("\nNo PortAudio output containing 'Realtek' was found.", flush=True)
            elif not args.list_only:
                play_realtek_tests(audio, outputs, supported)
            return
        for position, spec in enumerate(TESTS):
            try:
                run_test(audio, spec, list_only=args.list_only)
            except Exception as exc:
                print(f"\n=== TEST {spec[0]} ===", flush=True)
                print(f"ERROR resolving endpoint: {type(exc).__name__}: {exc}", flush=True)
            if not args.list_only and position < len(TESTS) - 1:
                print(f"Waiting {PAUSE_SECONDS} seconds before next test...", flush=True)
                time.sleep(PAUSE_SECONDS)
    except KeyboardInterrupt:
        print("\nProbe interrupted by user.", flush=True)
    finally:
        audio.terminate()


if __name__ == "__main__":
    main()
