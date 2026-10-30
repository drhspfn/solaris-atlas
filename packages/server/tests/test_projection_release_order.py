from types import SimpleNamespace

from wuwa_story.ingestion.releases import release_order


def test_chronology_precedes_import_sequence_and_versions_are_numeric():
    def release(version, sequence):
        return SimpleNamespace(game_version=version, sequence=sequence)
    assert release_order(release("3.10.0", 1)) > release_order(release("3.9.0", 100))
    assert release_order(release("3.7.0", 1)) > release_order(release("3.5.0", 100))
    assert release_order(release("3.7.0", 2)) > release_order(release("3.7.0", 1))
