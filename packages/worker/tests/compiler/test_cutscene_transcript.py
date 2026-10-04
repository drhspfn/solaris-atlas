import json

from wuwa_story_worker.compiler.flow import cutscene_transcript_links


def test_complete_caption_identity_links_transcript_without_flow_name_guessing():
    def state(key, words):
        return {"StateKey": key, "Actions": json.dumps([
            {"Name": "ShowTalk", "Params": {"TalkItems": [{"TidTalk": w} for w in words]}}
        ])}

    captions = [{"CgName": "Movie", "CaptionText": k} for k in ("a", "b")]
    states = [state("unrelated_name", ["a", "b"]), state("Movie_2", ["a"]),
              state("Movie_3", ["a", "b", "c"]), state("Movie_4", []),
              state("Movie_5", ["a", "a", "b"]), state("Movie_6", [None])]
    assert list(cutscene_transcript_links(states, captions)) == [
        ("Movie", "unrelated_name", 0, [0, 1])
    ]
