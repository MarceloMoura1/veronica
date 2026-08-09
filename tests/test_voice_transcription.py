from voice_transcription import VoiceTranscriptionTurns, merge_transcript, normalize_transcript


def test_exact_conversation_creates_four_distinct_owned_turns():
    turns = VoiceTranscriptionTurns()
    events = []
    for role, text in [
        ("user", "Verônica, está me ouvindo?"),
        ("assistant", "Sim, Chefe. Estou ouvindo perfeitamente."),
        ("user", "Beleza."),
        ("assistant", "Tudo certo por aqui."),
    ]:
        events.extend(turns.ingest(role, text, finished=True))
    assert [(event["role"], event["text"]) for event in events] == [
        ("user", "Verônica, está me ouvindo?"),
        ("assistant", "Sim, Chefe. Estou ouvindo perfeitamente."),
        ("user", "Beleza."),
        ("assistant", "Tudo certo por aqui."),
    ]
    assert len({event["message_id"] for event in events}) == 4


def test_user_snapshot_partials_update_one_turn():
    turns = VoiceTranscriptionTurns()
    events = sum((turns.ingest("user", text) for text in
                  ["Verônica está", "Verônica está me", "Verônica está me ouvindo?"]), [])
    assert len({event["message_id"] for event in events}) == 1
    assert events[-1]["text"] == "Verônica está me ouvindo?"


def test_assistant_delta_partials_merge_overlap_without_duplication():
    turns = VoiceTranscriptionTurns()
    turns.ingest("assistant", "Tudo certo")
    event = turns.ingest("assistant", "certo por aqui")[0]
    assert event["text"] == "Tudo certo por aqui"


def test_finished_transcript_replaces_imperfect_partial():
    turns = VoiceTranscriptionTurns()
    partial = turns.ingest("user", "Veronika está me voto?")[0]
    final = turns.ingest("user", "Verônica, está me ouvindo?", finished=True)[0]
    assert partial["message_id"] == final["message_id"]
    assert final["text"] == "Verônica, está me ouvindo?"


def test_barge_in_finalizes_assistant_before_new_user_turn():
    turns = VoiceTranscriptionTurns()
    assistant = turns.ingest("assistant", "Posso continuar?")[0]
    events = turns.ingest("user", "Espera.", finished=True)
    assert [(item["role"], item["final"]) for item in events] == [("assistant", True), ("user", True)]
    assert events[0]["message_id"] == assistant["message_id"]
    assert events[1]["message_id"] != assistant["message_id"]


def test_finalize_all_closes_each_role_independently():
    turns = VoiceTranscriptionTurns()
    user = turns.ingest("user", "Pergunta")[0]
    assistant = turns.ingest("assistant", "Resposta")[0]
    finals = turns.finalize_all()
    assert {item["message_id"] for item in finals} == {user["message_id"], assistant["message_id"]}


def test_sequence_is_backend_monotonic():
    turns = VoiceTranscriptionTurns()
    events = turns.ingest("user", "Oi") + turns.ingest("user", "Oi, Verônica", finished=True)
    assert [item["sequence"] for item in events] == [1, 2]


def test_safe_normalization_only_changes_spacing_and_punctuation():
    assert normalize_transcript("  Marcelo   Moura ,  tudo bem ? ") == "Marcelo Moura, tudo bem?"


def test_merge_ignores_repeated_older_snapshot():
    assert merge_transcript("Verônica está me ouvindo", "Verônica está") == "Verônica está me ouvindo"


def test_event_contract_has_explicit_ownership_and_lifecycle():
    event = VoiceTranscriptionTurns().ingest("assistant", "Olá", finished=True)[0]
    assert event["role"] == "assistant"
    assert event["source"] == "output_transcription"
    assert event["turn_id"] == event["message_id"]
    assert event["kind"] == "final" and event["final"] is True
