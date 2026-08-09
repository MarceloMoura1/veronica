import asyncio
import inspect

import pytest

import ada
from speaker_selection import resolve_voice_output


LABELS = [
    "Fones de ouvido (HyperX Cloud III)",
    "Alto-falantes (Realtek(R) Audio)",
]

REAL_BROWSER_LABELS = [
    "Default - Fones de ouvido (HyperX Cloud III) (03f0:089d)",
    "Communications - Fones de ouvido (HyperX Cloud III) (03f0:089d)",
    "27G2G4 (NVIDIA High Definition Audio)",
    "Realtek HD Audio 2nd output (Realtek(R) Audio)",
    "Fones de ouvido (HyperX Cloud III) (03f0:089d)",
]


@pytest.mark.parametrize("target", ["meu headset", "HyperX"])
def test_headset_alias_returns_existing_browser_label(target):
    result = resolve_voice_output(target, LABELS)
    assert result["status"] == "success"
    assert result["output_device_name"] == LABELS[0]
    assert result["applies"] == "next_session"


@pytest.mark.parametrize("target", ["caixa de som", "Realtek"])
def test_speaker_alias_returns_existing_browser_label(target):
    result = resolve_voice_output(target, LABELS)
    assert result["status"] == "success"
    assert result["output_device_name"] == LABELS[1]


def test_missing_target_is_unavailable_and_never_defaults():
    assert resolve_voice_output("meu headset", [LABELS[1]]) == {
        "status": "unavailable", "target": "meu headset"
    }


def test_multiple_matching_labels_are_ambiguous():
    result = resolve_voice_output("Realtek", [
        "Alto-falantes (Realtek(R) Audio)",
        "Fones de ouvido (Realtek USB Audio)",
    ])
    assert result["status"] == "ambiguous"
    assert len(result["candidates"]) == 2


def test_voice_change_does_not_touch_active_playback(monkeypatch):
    async def scenario():
        calls = []

        async def persist(target):
            calls.append(target)
            return {"status": "success", "output_device_name": LABELS[1], "applies": "next_session"}

        loop = object.__new__(ada.AudioLoop)
        loop.on_voice_output_change = persist
        loop.output_device_name = LABELS[0]
        loop.audio_in_queue = asyncio.Queue()
        active_stream = object()
        loop.audio_stream = active_stream
        queue = loop.audio_in_queue

        monkeypatch.setattr(ada.pya, "open", lambda **_kwargs: pytest.fail("tool opened a stream"))
        monkeypatch.setattr(ada.pya, "terminate", lambda: pytest.fail("tool terminated PyAudio"))
        loop.clear_audio_queue = lambda: pytest.fail("tool cleared the queue")

        result = await loop.set_voice_output_preference("caixa de som")

        assert result["status"] == "success"
        assert calls == ["caixa de som"]
        assert loop.output_device_name == LABELS[0]
        assert not hasattr(loop, "pending_output_device_name")
        assert loop.audio_stream is active_stream
        assert loop.audio_in_queue is queue
        assert loop.audio_in_queue.empty()
        assert not hasattr(loop, "desired_output_device_name")
        assert not hasattr(loop, "active_output_device_name")

    asyncio.run(scenario())


def test_legacy_player_guard_rails_are_frozen():
    source = inspect.getsource(ada.AudioLoop.play_audio)
    assert "self._resolve_audio_device(" in source
    assert "self.output_device_name" in source
    assert "pya.open" in source
    assert "format=FORMAT" in source
    assert "channels=CHANNELS" in source
    assert "rate=RECEIVE_SAMPLE_RATE" in source
    assert "stream.write, bytestream" in source
    assert source.count("pya.open") == 1
    assert "AudioOutputService" not in source
    assert "stop_stream" not in source
    assert "pending_output" not in source
    assert "OUTPUT_TURN_BOUNDARY" not in source
    assert "_apply_pending_voice_output_if_safe" not in source
    completed_turn_source = inspect.getsource(ada.AudioLoop._process_completed_assistant_turn)
    assert "audio_in_queue" not in completed_turn_source
    assert "pya.open" not in completed_turn_source
    assert ada.RECEIVE_SAMPLE_RATE == 24000
    assert ada.CHANNELS == 1
    assert ada.FORMAT == ada.pyaudio.paInt16


@pytest.mark.parametrize("target", ["pc_speakers", "personal_speakers", "computer_speakers"])
def test_canonical_speaker_targets_are_accepted(target):
    result = resolve_voice_output(target, LABELS)
    assert result["status"] == "success"
    assert result["target"] == "pc_speakers"
    assert result["output_device_name"] == LABELS[1]


@pytest.mark.parametrize(
    "target",
    ["personal_headset", "headset", "HyperX", "meu fone"],
)
def test_equivalent_hyperx_browser_roles_select_default_label(target):
    result = resolve_voice_output(target, REAL_BROWSER_LABELS)
    assert result["status"] == "success"
    assert result["output_device_name"] == REAL_BROWSER_LABELS[0]


@pytest.mark.parametrize("target", ["pc_speakers", "Realtek"])
def test_real_browser_catalog_keeps_realtek_resolution(target):
    result = resolve_voice_output(target, REAL_BROWSER_LABELS)
    assert result["status"] == "success"
    assert result["output_device_name"] == REAL_BROWSER_LABELS[3]


def test_exact_real_browser_label_is_accepted_without_alias_heuristics():
    result = resolve_voice_output(REAL_BROWSER_LABELS[0], REAL_BROWSER_LABELS)
    assert result["status"] == "success"
    assert result["output_device_name"] == REAL_BROWSER_LABELS[0]


def test_same_role_variants_of_different_hyperx_models_remain_ambiguous():
    result = resolve_voice_output("HyperX", [
        "Default - Fones de ouvido (HyperX Cloud III) (03f0:089d)",
        "Communications - Fones de ouvido (HyperX Cloud III) (03f0:089d)",
        "Fones de ouvido (HyperX Cloud Alpha) (03f0:1234)",
    ])
    assert result["status"] == "ambiguous"
    assert len(result["candidates"]) == 3
