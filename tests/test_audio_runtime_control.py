import ast
import hashlib
import inspect
import subprocess
from pathlib import Path

import ada
import server


ADA_METHODS = (
    "__init__", "receive_audio", "play_audio", "listen_audio", "send_realtime",
    "run", "stop", "set_paused", "clear_audio_queue",
)
SERVER_HANDLERS = ("pause_audio", "resume_audio", "stop_audio")


def _checkpoint(path):
    return subprocess.check_output(
        ["git", "show", f"7e5f8bc:{path}"], text=True, encoding="utf-8"
    )


def _definitions(source):
    tree = ast.parse(source)
    lines = source.splitlines(True)
    result = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AudioLoop":
            result["AudioLoop"] = "".join(lines[node.lineno - 1:node.end_lineno])
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name not in result:
            result[node.name] = "".join(lines[node.lineno - 1:node.end_lineno])
    return result


def test_complete_audio_loop_matches_checkpoint_byte_for_byte():
    old = _definitions(_checkpoint("backend/ada.py"))
    current = _definitions(Path(ada.__file__).read_text(encoding="utf-8"))
    for name in ADA_METHODS:
        actual = current[name]
        if name == "receive_audio":
            actual = actual.replace(
                'print(f"[VOICE_RECEIVE] first_audio_bytes={len(data)}")',
                'print(f"[VOICE_RECEIVE] first audio received bytes={len(data)}")',
            )
        elif name == "play_audio":
            actual = actual.replace(
                '            print(f"[VOICE_OUTPUT] stream_open=success active={stream.is_active()}")\n',
                '',
            ).replace(
                'print(f"[VOICE_OUTPUT] first_write_bytes={len(bytestream)}")',
                'print(f"[VOICE_OUTPUT] first chunk played bytes={len(bytestream)}")',
            )
        assert actual == old[name], name


def test_server_voice_handlers_match_checkpoint_byte_for_byte():
    old = _definitions(_checkpoint("backend/server.py"))
    current = _definitions(Path(server.__file__).read_text(encoding="utf-8"))
    for name in SERVER_HANDLERS:
        assert current[name] == old[name], name


def test_start_audio_falls_back_to_persisted_output_when_payload_is_missing():
    source = inspect.getsource(server.start_audio)
    assert 'if not output_device_name:' in source
    assert 'output_device_name = SETTINGS.get("output_device_name")' in source
    assert "change_output_device" not in source


def test_frontend_waits_for_a_real_selected_speaker_before_auto_start():
    source = Path("src/App.jsx").read_text(encoding="utf-8")
    assert "isConnected && !isMuted && isAuthenticated && socketConnected" in source
    assert "speakerDevices.length > 0 && selectedOutputDevice" in source
    assert "const outputDeviceName = selectedOutputDevice.label;" in source


def test_speaker_tool_is_absent_from_live_runtime():
    source = Path(ada.__file__).read_text(encoding="utf-8")
    assert "set_voice_output" not in source
    assert not hasattr(ada.AudioLoop, "set_voice_output_preference")


def test_checkpoint_pcm_and_audio_modality_are_active():
    config = ada.build_live_config(tool_mode="hybrid")
    assert config.response_modalities == ["AUDIO"]
    assert ada.RECEIVE_SAMPLE_RATE == 24000
    assert ada.CHANNELS == 1
    assert ada.FORMAT == ada.pyaudio.paInt16


def test_playback_uses_one_local_stream_without_switching():
    source = inspect.getsource(ada.AudioLoop.play_audio)
    assert source.count("pya.open") == 1
    assert "stream.write, bytestream" in source
    assert "stream.close()" in source
    assert "change_output_device" not in source
    assert "_output_lock" not in source
