"""Render source-declared movie events while retaining their native routing proof."""

from pathlib import Path

from wuwa_story_worker.cutscene_frame_audio import render_role_stems


async def prepare_frame_stems(video, assets, work, decoder, rovers, missing_assets):
    from wuwa_story_worker.cutscene_import import confined

    results = []
    for index, event in enumerate(video.frame_audio):
        bank = confined(assets, event.bank)
        audio = confined(assets, event.audio_directory)
        for rover in ([event.rover] if event.rover else sorted(rovers) or [None]):
            destination = work / f'{index}-{rover or "shared"}'
            try:
                stems = await render_role_stems(
                    bank, audio, destination, decoder, language=event.language, rover=rover)
                for stem in stems:
                    if stem['role'] == 'voice' and event.language is None:
                        raise ValueError('Voice bus has no source-backed playback language')
                # Original, untrimmed role WAVs remain evidence, including music
                # whose lifetime extends beyond this standalone video.
                sources = [bank, *[confined(assets, path) for path in event.source_files],
                           *sorted(audio.glob('*.wem')), audio / 'Init.bnk',
                           *sorted(destination.rglob('*.json')),
                           *sorted(destination.rglob('*.xml')),
                           *sorted(destination.rglob('*.txtp'))]
                for stem in stems:
                    results.append({**stem, 'language': event.language, 'rover': rover,
                                    'start_seconds': event.start_seconds,
                                    'end_seconds': event.end_seconds,
                                    'sources': [*sources, Path(stem['path'])],
                                    'reference': event.reference, 'source': event.source})
            except ValueError as error:
                if missing_assets is None:
                    raise
                reason = f'{event.reference}: {error}'
                if reason not in missing_assets:
                    missing_assets.append(reason)
    return results
