import asyncio
import json
from types import SimpleNamespace

import pytest

import ada
from audio_outputs import AudioOutput, AudioOutputService


class FakeProvider:
    def __init__(self, outputs=None, failure=None):
        self.outputs = outputs or [
            AudioOutput(2, "Speakers (Realtek Audio)", True),
            AudioOutput(7, "Headphones (Arctis Nova Pro Wireless)"),
        ]
        self.failure = failure
        self.validated = []

    def list_outputs(self):
        if self.failure == "list":
            raise OSError("provider details must not leak")
        return self.outputs

    def validate_output(self, device_index):
        if self.failure == "validate":
            raise OSError("driver details must not leak")
        self.validated.append(device_index)


PERSONAL_ALIASES = {
    "personal_headset": {
        "target": "HyperX Cloud III",
        "aliases": [
            "meu headset", "headset", "meu fone", "fone", "meus fones", "fones",
            "hyperx", "hyperx cloud", "hyperx cloud 3", "hyperx cloud iii", "cloud 3", "cloud iii",
        ],
    },
    "personal_speakers": {
        "target": "Realtek HD Audio — Headphones / 2nd output",
        "aliases": [
            "minha caixa de som", "caixa de som", "caixa", "alto-falante", "alto falante",
            "alto-falantes", "alto falantes", "speaker", "speakers", "realtek", "realtek hd",
            "saída realtek",
        ],
    },
}


def personal_outputs(include_hyperx=True, include_realtek=True):
    outputs = [AudioOutput(3, "27G2G4 (NVIDIA High Definition Audio)", False, display_name="27G2G4 — NVIDIA High Definition Audio")]
    if include_hyperx:
        outputs.append(AudioOutput(7, "Fones de ouvido (HyperX Cloud III)", True, display_name="HyperX Cloud III"))
    if include_realtek:
        outputs.append(AudioOutput(14, "Headphones (Realtek HD Audio 2nd output)", False, display_name="Realtek HD Audio — Headphones / 2nd output"))
    return outputs


def test_list_marks_default_current_and_sanitizes_payload():
    service = AudioOutputService(FakeProvider(), selected_name="Headphones (Arctis Nova Pro Wireless)")

    result = service.list_outputs()

    assert result["success"] is True
    assert result["outputs"][0]["is_default"] is True
    assert result["outputs"][1]["is_current"] is True
    assert set(result["outputs"][0]) == {"id", "name", "is_default", "is_current"}
    assert result["outputs"][0]["id"].startswith("output_")
    assert "device_index" not in result["outputs"][0]


def test_set_valid_output_updates_current_and_persists():
    persisted = []
    provider = FakeProvider()
    service = AudioOutputService(provider, persist=persisted.append)
    target = service.list_outputs()["outputs"][1]

    result = service.set_output(target["id"])

    assert result == {"success": True, "output": {"id": target["id"], "name": target["name"]}}
    assert provider.validated == [7]
    assert persisted[-1] == {"audio_output_id": target["id"], "audio_output_name": target["name"]}
    assert service.get_current_output()["output"]["id"] == target["id"]


@pytest.mark.parametrize("spoken_name", ["meu fone", "meu headset"])
def test_friendly_headset_names_resolve_without_hardcoded_device(spoken_name):
    service = AudioOutputService(FakeProvider())

    result = service.set_output(spoken_name)

    assert result["success"] is True
    assert result["output"]["name"] == "Headphones (Arctis Nova Pro Wireless)"


@pytest.mark.parametrize("spoken_name", ["caixa de som", "alto-falante do computador"])
def test_friendly_speaker_names_resolve_without_global_audio_change(spoken_name):
    service = AudioOutputService(FakeProvider())

    result = service.set_output(spoken_name)

    assert result["success"] is True
    assert result["output"]["name"] == "Speakers (Realtek Audio)"


def test_missing_and_ambiguous_outputs_never_select_arbitrarily():
    provider = FakeProvider([
        AudioOutput(1, "Headset USB", True),
        AudioOutput(2, "Headset Bluetooth"),
    ])
    service = AudioOutputService(provider)

    missing = service.set_output("television")
    ambiguous = service.set_output("headset")

    assert missing == {"success": False, "error": "audio_output_not_found"}
    assert ambiguous["success"] is False
    assert ambiguous["error"] == "ambiguous_audio_output"
    assert [item["name"] for item in ambiguous["candidates"]] == ["Headset USB", "Headset Bluetooth"]
    assert provider.validated == []


def test_provider_failure_returns_safe_error_without_false_success():
    service = AudioOutputService(FakeProvider(failure="validate"))

    result = service.set_output("Speakers (Realtek Audio)")

    assert result == {"success": False, "error": "audio_output_activation_failed"}


def test_disconnected_selection_falls_back_to_default_and_persists():
    persisted = []
    service = AudioOutputService(
        FakeProvider(), selected_id="output_disconnected", selected_name="Missing Headset",
        persist=persisted.append,
    )

    current = service.get_current_output()

    assert current["output"]["name"] == "Speakers (Realtek Audio)"
    assert current["output"]["is_default"] is True
    assert persisted[-1]["audio_output_name"] == "Speakers (Realtek Audio)"


def test_legacy_playback_opens_once_and_writes_all_chunks(monkeypatch):
    asyncio.run(_assert_legacy_playback_opens_once(monkeypatch))


async def _assert_legacy_playback_opens_once(monkeypatch):
    streams = []

    class Stream:
        def __init__(self, index):
            self.index = index
            self.writes = []
            self.closed = False

        def write(self, data):
            self.writes.append(data)

        def is_active(self):
            return not self.closed

        def stop_stream(self):
            pass

        def close(self):
            self.closed = True

    def open_stream(**kwargs):
        stream = Stream(kwargs["output_device_index"])
        streams.append(stream)
        return stream

    monkeypatch.setattr(ada.pya, "open", open_stream)
    monkeypatch.setattr(ada.AudioLoop, "_resolve_audio_device", lambda *_args, **_kwargs: 3)
    loop = object.__new__(ada.AudioLoop)
    loop.output_device_index = None
    loop.output_device_name = "Fones de ouvido (HyperX Cloud III)"
    loop.audio_in_queue = asyncio.Queue()
    loop.on_audio_data = None
    loop.on_error = None
    loop._voice_output_logged = False
    task = asyncio.create_task(loop.play_audio())
    await loop.audio_in_queue.put(b"first")
    await loop.audio_in_queue.put(b"second")
    for _ in range(50):
        if streams and streams[0].writes == [b"first", b"second"]:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert [stream.index for stream in streams] == [3]
    assert streams[0].closed is True
    assert streams[0].writes == [b"first", b"second"]


def test_windows_enumeration_uses_one_playable_host_api_and_keeps_real_endpoints():
    raw = [
        AudioOutput(1, "Mapeador de som da Microsoft - Output", False, "MME", 2, "mme:mapper", is_accessible=True),
        AudioOutput(2, "Fones de ouvido (HyperX Cloud III)", True, "MME", 2, "mme:hyperx", is_accessible=True),
        AudioOutput(3, "27G264 (NVIDIA High Definition Audio)", False, "MME", 2, "mme:nvidia", is_accessible=True),
        AudioOutput(4, "Driver de som primário", False, "Windows DirectSound", 2, "ds:default", is_accessible=True),
        AudioOutput(5, "Fones de ouvido (HyperX Cloud III)", True, "Windows DirectSound", 2, "ds:hyperx", "HyperX Cloud III", True),
        AudioOutput(6, "27G264 (NVIDIA High Definition Audio)", False, "Windows DirectSound", 2, "ds:nvidia", "27G264 - NVIDIA High Definition Audio", True),
        AudioOutput(7, "Speakers (Realtek HD Audio output)", False, "Windows DirectSound", 2, "ds:realtek-speakers", "Realtek HD Audio - Speakers", True),
        AudioOutput(8, "Headphones (Realtek HD Audio 2nd output)", False, "Windows DirectSound", 2, "ds:realtek-headphones", "Realtek HD Audio - Headphones / 2nd output", True),
        AudioOutput(9, "JBL TUNE 510BT Stereo", False, "Windows DirectSound", 2, "ds:jbl-stereo", is_accessible=True),
        AudioOutput(10, "JBL TUNE 510BT Hands-Free", False, "Windows DirectSound", 1, "ds:jbl-hfp", is_accessible=True),
        AudioOutput(11, "", False, "Windows DirectSound", 2, "ds:empty", is_accessible=True),
        AudioOutput(12, "Output ()", False, "Windows DirectSound", 2, "ds:broken", is_accessible=True),
        AudioOutput(13, "Capture only", False, "Windows DirectSound", 0, "ds:capture", is_accessible=True),
        AudioOutput(14, "Fones de ouvido (HyperX Cloud III)", False, "Windows WASAPI", 2, "wasapi:hyperx", is_accessible=False),
        AudioOutput(15, "Fones de ouvido (HyperX Cloud III)", False, "Windows WDM-KS", 2, "wdm:hyperx", is_accessible=False),
    ]
    service = AudioOutputService(FakeProvider(raw))

    first = service.list_outputs()
    second = service.list_outputs()
    names = [item["name"] for item in first["outputs"]]

    assert names == [
        "HyperX Cloud III", "27G264 - NVIDIA High Definition Audio",
        "Realtek HD Audio - Speakers", "Realtek HD Audio - Headphones / 2nd output",
        "JBL TUNE 510BT Stereo", "JBL TUNE 510BT Hands-Free",
    ]
    assert sum(item["is_current"] for item in first["outputs"]) == 1
    assert sum(item["is_default"] for item in first["outputs"]) == 1
    assert next(item for item in first["outputs"] if item["is_default"])["name"] == "HyperX Cloud III"
    assert [item["id"] for item in first["outputs"]] == [item["id"] for item in second["outputs"]]


def test_saved_raw_name_migrates_to_clean_display_name():
    output = AudioOutput(
        5, "Fones de ouvido (HyperX Cloud III)", True, "Windows DirectSound", 2,
        "ds:hyperx", "HyperX Cloud III", True,
    )
    persisted = []
    service = AudioOutputService(
        FakeProvider([output]), selected_name="Fones de ouvido (HyperX Cloud III)",
        persist=persisted.append,
    )

    current = service.get_current_output()

    assert current["output"]["name"] == "HyperX Cloud III"
    assert current["output"]["is_current"] is True


def test_current_endpoint_keeps_raw_portaudio_identity_behind_opaque_id():
    raw = AudioOutput(
        9, "Fones de ouvido (HyperX Cloud III)", True, "Windows DirectSound", 2,
        "ds:hyperx", "HyperX Cloud III", True,
    )
    service = AudioOutputService(FakeProvider([raw]))

    public = service.list_outputs()["outputs"][0]
    endpoint = service.current_endpoint()

    assert public["id"].startswith("output_")
    assert endpoint is raw
    assert endpoint.device_index == 9
    assert service.current_device_index() == 9


def test_generic_mapper_filter_tolerates_portaudio_unicode_spacing():
    service = AudioOutputService(FakeProvider([
        AudioOutput(1, "Driver de som prima rio", False, "Windows DirectSound", 2),
        AudioOutput(2, "HyperX Cloud III", True, "Windows DirectSound", 2),
    ]))

    assert [item["name"] for item in service.list_outputs()["outputs"]] == ["HyperX Cloud III"]


@pytest.mark.parametrize("alias", [
    "meu headset", "headset", "meu fone", "fone", "meus fones", "fones", "hyperx",
    "hyperx cloud", "hyperx cloud 3", "hyperx cloud iii", "cloud 3", "cloud iii",
])
def test_personal_headset_aliases_resolve_deterministically(alias):
    service = AudioOutputService(FakeProvider(personal_outputs()), aliases=PERSONAL_ALIASES)

    assert service.set_output(alias)["output"]["name"] == "HyperX Cloud III"


@pytest.mark.parametrize("alias", [
    "minha caixa de som", "caixa de som", "caixa", "alto-falante", "alto falante",
    "alto-falantes", "alto falantes", "speaker", "speakers", "realtek", "realtek hd",
    "saída realtek", "  ALTO--FALANTE! ",
])
def test_personal_speaker_aliases_resolve_deterministically(alias):
    service = AudioOutputService(FakeProvider(personal_outputs()), aliases=PERSONAL_ALIASES)

    assert service.set_output(alias)["output"]["name"] == "Realtek HD Audio — Headphones / 2nd output"


@pytest.mark.parametrize("alias", ["meu headset", "hyperx", "cloud iii"])
def test_missing_personal_headset_never_falls_back(alias):
    provider = FakeProvider(personal_outputs(include_hyperx=False))
    service = AudioOutputService(provider, aliases=PERSONAL_ALIASES)

    assert service.set_output(alias) == {
        "success": False, "error": "audio_output_unavailable", "target": "HyperX Cloud III",
    }
    assert provider.validated == []


@pytest.mark.parametrize("alias", ["caixa de som", "speaker", "realtek"])
def test_missing_personal_speakers_never_fall_back_to_nvidia_or_hyperx(alias):
    provider = FakeProvider(personal_outputs(include_realtek=False))
    service = AudioOutputService(provider, aliases=PERSONAL_ALIASES)

    result = service.set_output(alias)

    assert result["success"] is False
    assert result["error"] == "audio_output_unavailable"
    assert result["target"] == "Realtek HD Audio — Headphones / 2nd output"
    assert provider.validated == []


def test_personal_alias_configuration_survives_service_recreation(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"audio_output_aliases": PERSONAL_ALIASES}), encoding="utf-8")

    first_config = json.loads(settings_path.read_text(encoding="utf-8"))["audio_output_aliases"]
    first = AudioOutputService(FakeProvider(personal_outputs()), aliases=first_config)
    assert first.set_output("meu headset")["output"]["name"] == "HyperX Cloud III"
    del first

    second_config = json.loads(settings_path.read_text(encoding="utf-8"))["audio_output_aliases"]
    second = AudioOutputService(FakeProvider(personal_outputs()), aliases=second_config)
    assert second.set_output("caixa de som")["output"]["name"] == "Realtek HD Audio — Headphones / 2nd output"


@pytest.mark.parametrize("failure_stage", ["open", "write"])
def test_playback_open_and_write_errors_are_reported_and_propagated(monkeypatch, failure_stage):
    asyncio.run(_assert_playback_error_is_visible(monkeypatch, failure_stage))


async def _assert_playback_error_is_visible(monkeypatch, failure_stage):
    errors = []
    service = AudioOutputService(FakeProvider())

    class BrokenStream:
        def is_active(self):
            return True

        def write(self, _data):
            if failure_stage == "write":
                raise OSError("write failed visibly")

        def stop_stream(self):
            pass

        def close(self):
            pass

    def open_stream(**_kwargs):
        if failure_stage == "open":
            raise OSError("open failed visibly")
        return BrokenStream()

    monkeypatch.setattr(ada.pya, "open", open_stream)
    loop = object.__new__(ada.AudioLoop)
    loop.audio_output_service = service
    loop.output_device_index = None
    loop.output_device_name = None
    loop.audio_in_queue = asyncio.Queue()
    loop.on_audio_data = None
    loop.on_error = errors.append
    loop._voice_output_logged = False
    if failure_stage == "write":
        await loop.audio_in_queue.put(b"pcm")

    with pytest.raises(OSError, match=f"{failure_stage} failed visibly"):
        await loop.play_audio()

    assert errors == [f"Audio output failed: {failure_stage} failed visibly"]
