"""Speaker cues are local to explicit, language-aware transcript turns."""

from local_preview.conversation_turns import detect_turns


def test_english_turns_carry_names_and_reset_on_anonymous_markers():
    lines = [
        {"text": text}
        for text in (
            "The rehearsal opened quietly.",
            "Mira Bell: Start with the drum.",
            "Then leave a beat of silence.",
            ">> Try the smaller drum.",
            "It has a softer tone.",
            ">> Ivo Reed Fox: Let's check the room.",
            "A soft echo is fine.",
            "Mira Bell: Good, let's start.",
        )
    ]
    assert detect_turns(lines, "en") == [
        {"turn_id": 1, "speaker_label": None, "cue": "continuation"},
        {"turn_id": 2, "speaker_label": "Mira Bell", "cue": "name_prefix"},
        {"turn_id": 2, "speaker_label": "Mira Bell", "cue": "continuation"},
        {"turn_id": 3, "speaker_label": None, "cue": "marker"},
        {"turn_id": 3, "speaker_label": None, "cue": "continuation"},
        {"turn_id": 4, "speaker_label": "Ivo Reed Fox", "cue": "name_prefix"},
        {"turn_id": 4, "speaker_label": "Ivo Reed Fox", "cue": "continuation"},
        {"turn_id": 5, "speaker_label": "Mira Bell", "cue": "name_prefix"},
    ]


def test_japanese_full_width_cues_and_stage_directions():
    lines = [
        {"text": text}
        for text in (
            "（拍手）",
            "（青木はな）小さい太鼓を使います。",
            "次は少し休みましょう。",
            "森川れん：椅子は奥にあります。",
            "（笑い）",
            "＞＞ そうしましょう。",
        )
    ]
    turns = detect_turns(lines, "ja-JP")
    assert [turn["speaker_label"] for turn in turns] == [
        None,
        "青木はな",
        "青木はな",
        "森川れん",
        "森川れん",
        None,
    ]
    assert [turn["turn_id"] for turn in turns] == [1, 2, 2, 3, 3, 4]


def test_prose_colons_and_empty_input():
    assert detect_turns([], "en") == []
    lines = [
        {"text": text}
        for text in (
            "Mira: check the drum.",
            "Time to check: noon.",
            "An Entire Long Headline: an announcement.",
        )
    ]
    assert all(turn["speaker_label"] is None for turn in detect_turns(lines, "en"))
