from wuwa_story.api.routes.story.shared import _choice_branch


def test_choice_branch_keeps_only_selected_sequence_and_its_return() -> None:
    params = {
        "TalkItems": [{"Id": talk_id} for talk_id in (1, 2, 3, 4, 5, 6, 7, 8)],
        "TalkSequence": [[1, 2], [3], [4, 5, 6], [7, 8]],
        "SequenceTransitions": {
            "1": [
                {"NextSequenceIndex": 2, "OptionTextKey": "ask"},
                {"NextSequenceIndex": 3, "OptionTextKey": "leave"},
            ],
            "2": [{"NextSequenceIndex": 1, "OptionTextKey": ""}],
        },
    }

    branch = _choice_branch(params, "state", 2, "talk_item:state:2:3")

    assert branch == {
        "line_ids": ["talk_item:state:2:3", "talk_item:state:2:4", "talk_item:state:2:5"],
        "continuation_line_id": "talk_item:state:2:2",
    }
    assert _choice_branch(params, "state", 2, "talk_item:state:2:6") == {
        "line_ids": ["talk_item:state:2:6", "talk_item:state:2:7"],
        "continuation_line_id": None,
    }


def test_choice_branch_does_not_guess_ambiguous_talk_ids() -> None:
    params = {"TalkItems": [{"Id": 3}, {"Id": 3}], "TalkSequence": [[3]]}
    assert _choice_branch(params, "state", 0, "talk_item:state:0:0") is None
    assert _choice_branch(params, "other", 0, "talk_item:state:0:0") is None


def test_two_choices_can_rejoin_at_one_line() -> None:
    params = {
        "TalkItems": [{"Id": talk_id} for talk_id in (1, 2, 3, 4, 5, 6)],
        "TalkSequence": [[1], [2, 3], [4, 5], [6]],
        "SequenceTransitions": {
            "1": [{"NextSequenceIndex": 3, "OptionTextKey": ""}],
            "2": [{"NextSequenceIndex": 3, "OptionTextKey": ""}],
        },
    }

    first = _choice_branch(params, "state", 0, "talk_item:state:0:1")
    second = _choice_branch(params, "state", 0, "talk_item:state:0:3")

    assert first == {"line_ids": ["talk_item:state:0:1", "talk_item:state:0:2"],
                     "continuation_line_id": "talk_item:state:0:5"}
    assert second == {"line_ids": ["talk_item:state:0:3", "talk_item:state:0:4"],
                      "continuation_line_id": "talk_item:state:0:5"}
